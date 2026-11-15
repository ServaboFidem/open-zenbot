"""Directory import: a scan counts what an import would touch (and what
it skips); an import job runs the ingestor over exactly that list, one
file at a time, with per-file events, a stop, and totals."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.memory import import_dir
from src.memory.import_dir import CHUNK_BYTES_ESTIMATE, Scan, ScanStore, extraction_estimate, scan_dir, scan_file
from src.memory.ingest import IngestError


def tree(root: Path, files: dict[str, bytes | str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_text(body, encoding="utf-8")


# -- scan ---------------------------------------------------------------------


def test_scan_counts_importable_files_and_skips_the_rest(tmp_path: Path, monkeypatch):
    tree(tmp_path, {"a.md": "a", "b.txt": "b", "c.pdf": "c", ".hidden/d.md": "d", "node_modules/e.md": "e",
                    "dist/f.md": "f", "big.md": "x" * 101, "g.exe": b"\x00"})
    monkeypatch.setenv("ZEN_IMPORT_MAX_BYTES", "100")
    s = scan_dir(tmp_path, accepts=lambda p: p.suffix in {".md", ".txt", ".pdf"})
    assert [p.name for p in s.files] == ["a.md", "b.txt", "c.pdf"]
    assert s.by_suffix == {".md": 1, ".pdf": 1, ".txt": 1}
    assert s.skipped == {"hidden": 3, "no_converter": 1, "too_large": 1}
    assert s.bytes == sum(p.stat().st_size for p in s.files)
    d = s.to_dict()
    assert d["files"] == 3 and d["dir"] == str(tmp_path) and d["token"] == s.token


def test_extraction_estimate(tmp_path: Path):
    assert CHUNK_BYTES_ESTIMATE == 6000
    tree(tmp_path, {"a.md": "x" * 100, "b.md": "x" * 6000, "c.md": "x" * 6001})
    s = scan_dir(tmp_path, accepts=lambda p: p.suffix == ".md")
    assert extraction_estimate(s) == 7  # (1 + 1) + (1 + 1) + (2 + 1)
    assert s.to_dict()["extraction_calls"] == 7


def test_a_rescan_estimates_only_new_and_changed_files(tmp_path: Path):
    import hashlib

    from src.memory.import_dir import unchanged_in
    from src.memory.sync_log import SyncEntry, SyncLog

    tree(tmp_path / "d", {"a.md": "x" * 100, "b.md": "x" * 6001, "c.md": "x" * 100})
    log = SyncLog(tmp_path / "sync.json")
    a, b = (tmp_path / "d" / "a.md").resolve(), (tmp_path / "d" / "b.md").resolve()
    log.put(SyncEntry(str(a), hashlib.md5(a.read_bytes()).hexdigest(), "docs/a.md", "f1"))  # imported, unchanged
    log.put(SyncEntry(str(b), "stale", "docs/b.md", "f2"))  # imported, changed since
    s = scan_dir(tmp_path / "d", accepts=lambda p: True, unchanged=unchanged_in(log))
    assert len(s.files) == 3  # the count still shows every file the import walks
    assert extraction_estimate(s) == (2 + 1) + (1 + 1)  # b and c; a is skipped by the import
    assert s.to_dict()["extraction_calls"] == 5
    s = scan_dir(tmp_path / "d", accepts=lambda p: True, unchanged=unchanged_in(log, lambda fid: fid != "f1"))
    assert extraction_estimate(s) == 2 + (2 + 1) + (1 + 1)  # a's fact is not in the store: it is imported again


def test_scan_survives_symlink_loops_and_counts_each_file_once(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.md").write_text("a", encoding="utf-8")
    try:
        os.symlink(tmp_path, tmp_path / "sub" / "loop", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges here")
    s = scan_dir(tmp_path, accepts=lambda p: True)
    assert [p.name for p in s.files] == ["a.md"]


def test_scan_store_expires(tmp_path: Path, monkeypatch):
    tree(tmp_path, {"a.md": "a"})
    store = ScanStore(ttl=10)
    s = store.put(scan_dir(tmp_path, accepts=lambda p: True))
    assert store.get(s.token) is s
    monkeypatch.setattr(import_dir.time, "time", lambda: s.created_at + 11)
    assert store.get(s.token) is None and store.get("nope") is None


def test_scan_refuses_a_missing_dir_and_a_file(tmp_path: Path):
    with pytest.raises(IngestError):
        scan_dir(tmp_path / "nope", accepts=lambda p: True)
    f = tmp_path / "f.md"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(IngestError):
        scan_dir(f, accepts=lambda p: True)


# -- import -------------------------------------------------------------------

import asyncio
import threading

from src.generation.generator import Generator
from src.memory.exclusion import exclusion
from src.memory.graph import SHARED, LocalGraph, MemoryGraph
from src.memory.import_dir import DirImporter
from src.memory.ingest import Ingestor, PlainConverter
from src.memory.ledger import Ledger

from .conftest import ScriptedProvider, completion

SUMMARY = completion('{"summary": "A note.", "names": ["Dana"], "dates": [], "facts": ["Net 30."]}')


def importer(vault: Path, ledger: Ledger, provider, calls: list | None = None):
    graph = MemoryGraph(LocalGraph(vault / "memory"), ledger)

    def make(allowed):
        if calls is not None:
            calls.append(list(allowed))
        return Ingestor(vault, PlainConverter(), Generator(provider), graph, ledger, allowed=allowed)

    return DirImporter(make, ledger), graph


async def drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while True:
        e = await asyncio.wait_for(q.get(), 10)
        out.append(e)
        if e["kind"] == "end":
            return out


async def test_import_runs_each_file_reports_events_and_totals(vault: Path, ledger: Ledger, tmp_path: Path):
    tree(tmp_path / "docs", {"a.md": "# a", "b.txt": "b", "sub/c.md": "# c"})
    scan = scan_dir(tmp_path / "docs", PlainConverter().accepts)
    imp, graph = importer(vault, ledger, ScriptedProvider([SUMMARY] * 3))
    job = imp.start(scan)
    events = await drain(imp.subscribe(job.id))
    assert [e["status"] for e in events if e["kind"] == "file"] == ["new", "new", "new"]
    assert events[-1] == {"kind": "end", "state": "done", "imported": 3, "unchanged": 0, "refused": 0}
    assert job.state == "done" and job.done_files == 3 and len(graph.facts(SHARED)) == 3
    assert [e.data["status"] for e in ledger.tail(10, kind="import") if "status" in e.data] == ["new", "new", "new"]
    assert job.to_dict()["total"] == 3 and job.to_dict()["dir"] == str(tmp_path / "docs")


async def test_second_import_skips_unchanged_files(vault: Path, ledger: Ledger, tmp_path: Path):
    tree(tmp_path / "docs", {"a.md": "# a", "b.md": "# b"})
    scan = scan_dir(tmp_path / "docs", PlainConverter().accepts)
    imp, graph = importer(vault, ledger, ScriptedProvider([SUMMARY] * 2))
    await drain(imp.subscribe(imp.start(scan).id))
    job = imp.start(scan)
    events = await drain(imp.subscribe(job.id))
    assert events[-1] == {"kind": "end", "state": "done", "imported": 0, "unchanged": 2, "refused": 0}


async def test_a_refused_file_does_not_stop_the_run(vault: Path, ledger: Ledger, tmp_path: Path):
    tree(tmp_path / "docs", {"a.md": "# a", "b.md": "# b"})
    leaky = completion('{"summary": "Login with hunter2hunter2.", "names": [], "dates": [], "facts": []}')
    exclusion.register_secret_value("hunter2hunter2")
    scan = scan_dir(tmp_path / "docs", PlainConverter().accepts)
    imp, graph = importer(vault, ledger, ScriptedProvider([SUMMARY, leaky]))
    job = imp.start(scan)
    events = await drain(imp.subscribe(job.id))
    files = [e for e in events if e["kind"] == "file"]
    assert [e["status"] for e in files] == ["new", "refused"] and files[1]["reason"]
    assert events[-1]["refused"] == 1 and job.state == "done"


async def test_undecodable_text_and_unreadable_files_are_refused_not_fatal(vault: Path, ledger: Ledger, tmp_path: Path, monkeypatch):
    tree(tmp_path / "docs", {"bin.txt": b"\xff\xfe\x00garbage", "locked.md": "# locked", "ok.md": "# ok"})
    real = Path.read_bytes

    def read_bytes(self):
        if self.name == "locked.md":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    scan = scan_dir(tmp_path / "docs", PlainConverter().accepts)
    imp, graph = importer(vault, ledger, ScriptedProvider([SUMMARY] * 3))
    job = imp.start(scan)
    events = await drain(imp.subscribe(job.id))
    by = {Path(e["path"]).name: e for e in events if e["kind"] == "file"}
    assert by["bin.txt"]["status"] in ("new", "refused")
    assert by["locked.md"]["status"] == "refused" and "PermissionError" in by["locked.md"]["reason"]
    assert by["ok.md"]["status"] == "new" and job.state == "done"


async def test_stop_while_summarizing_drops_the_file_at_once(vault: Path, ledger: Ledger, tmp_path: Path):
    """The summary call hangs; stop must not wait for it, and nothing of
    the dropped file is written."""
    tree(tmp_path / "docs", {"a.md": "# a", "b.md": "# b"})
    started = asyncio.Event()

    class Hangs(ScriptedProvider):
        async def complete(self, messages, *, tools=None, model=None, session_id=None):
            started.set()
            await asyncio.sleep(3600)

    scan = scan_dir(tmp_path / "docs", PlainConverter().accepts)
    imp, graph = importer(vault, ledger, Hangs([]))
    job = imp.start(scan)
    q = imp.subscribe(job.id)
    await asyncio.wait_for(started.wait(), 5)
    imp.stop(job.id)
    events = await drain(q)
    assert [e["kind"] for e in events] == ["stopping", "end"]
    assert events[-1]["state"] == "stopped" and job.state == "stopped" and job.done_files == 0
    assert graph.facts(SHARED) == [] and not (vault / "docs").exists()


async def test_stop_while_writing_finishes_that_file_then_stops(vault: Path, ledger: Ledger, tmp_path: Path):
    """A file whose graph write has begun is saved whole: a fact without
    its sync-log entry would be duplicated by the next import."""
    tree(tmp_path / "docs", {"a.md": "# a", "b.md": "# b"})
    writing, release = threading.Event(), threading.Event()
    graph = MemoryGraph(LocalGraph(vault / "memory"), ledger)
    real = graph.remember

    def slow_remember(*a, **kw):
        writing.set()
        release.wait(10)
        return real(*a, **kw)
    graph.remember = slow_remember
    imp = DirImporter(lambda allowed: Ingestor(vault, PlainConverter(), Generator(ScriptedProvider([SUMMARY] * 2)), graph, ledger,
                                                allowed=allowed, offload=True), ledger)
    job = imp.start(scan_dir(tmp_path / "docs", PlainConverter().accepts))
    q = imp.subscribe(job.id)
    await asyncio.to_thread(writing.wait, 5)
    imp.stop(job.id)
    assert job.to_dict()["stopping"] is True
    release.set()
    events = await drain(q)
    assert [e.get("status") for e in events if e["kind"] == "file"] == ["new"]
    assert events[-1]["state"] == "stopped" and job.imported == 1 and len(graph.facts(SHARED)) == 1


def test_scan_file_makes_a_one_file_scan(tmp_path: Path):
    tree(tmp_path, {"notes.md": "# hi", "photo.bmp": b"BM"})
    scan = scan_file(tmp_path / "notes.md", PlainConverter().accepts)
    assert scan.files == [tmp_path / "notes.md"] and scan.dir == tmp_path and scan.to_dict()["files"] == 1
    with pytest.raises(IngestError, match="no converter"):
        scan_file(tmp_path / "photo.bmp", PlainConverter().accepts)
    with pytest.raises(IngestError, match="not a file"):
        scan_file(tmp_path, PlainConverter().accepts)
    with pytest.raises(IngestError, match="import limit"):
        scan_file(tmp_path / "notes.md", PlainConverter().accepts, max_bytes=1)


async def test_a_single_file_import_runs_as_a_job(vault: Path, ledger: Ledger, tmp_path: Path):
    tree(tmp_path / "docs", {"a.md": "# a", "b.md": "# b"})
    imp, graph = importer(vault, ledger, ScriptedProvider([SUMMARY]))
    job = imp.start(scan_file(tmp_path / "docs" / "b.md", PlainConverter().accepts))
    events = await drain(imp.subscribe(job.id))
    assert [Path(e["path"]).name for e in events if e["kind"] == "file"] == ["b.md"]
    assert events[-1]["state"] == "done" and len(graph.facts(SHARED)) == 1


async def test_only_one_import_runs_at_a_time_and_the_ingestor_is_scoped(vault: Path, ledger: Ledger, tmp_path: Path):
    tree(tmp_path / "docs", {"a.md": "# a"})
    scan = scan_dir(tmp_path / "docs", PlainConverter().accepts)
    calls: list = []
    imp, graph = importer(vault, ledger, ScriptedProvider([SUMMARY]), calls)
    job = imp.start(scan)
    assert imp.running() is job
    with pytest.raises(RuntimeError):
        imp.start(scan)
    await drain(imp.subscribe(job.id))
    assert calls == [[scan.dir]] and imp.running() is None and imp.get(job.id) is job


async def test_an_import_writes_memory_off_the_event_loop_but_calls_the_provider_on_it(vault: Path, ledger: Ledger, tmp_path: Path):
    tree(tmp_path / "docs", {"a.md": "# a"})
    seen = {}

    class Recording(LocalGraph):
        def put(self, fact, targets=None):
            seen["graph_thread"] = threading.current_thread().name
            return super().put(fact, targets)

    class LoopBound(ScriptedProvider):
        async def complete(self, messages, *, tools=None, model=None, session_id=None):
            seen["provider_loop"] = asyncio.get_running_loop()
            return await super().complete(messages, tools=tools, model=model, session_id=session_id)

    graph = MemoryGraph(Recording(vault / "memory"), ledger)
    provider = LoopBound([SUMMARY])
    imp = DirImporter(lambda allowed: Ingestor(vault, PlainConverter(), Generator(provider), graph, ledger, allowed=allowed, offload=True), ledger)
    scan = scan_dir(tmp_path / "docs", PlainConverter().accepts)
    events = await drain(imp.subscribe(imp.start(scan).id))
    assert events[-1]["imported"] == 1
    assert seen["provider_loop"] is asyncio.get_running_loop()   # the provider's shared http client stays on its loop
    assert seen["graph_thread"] != threading.main_thread().name  # the blocking store write does not


def test_scan_does_not_follow_junctions(tmp_path: Path):
    winapi = pytest.importorskip("_winapi")
    if not hasattr(winapi, "CreateJunction"):
        pytest.skip("no junctions here")
    tree(tmp_path / "in", {"a.md": "# a"})
    tree(tmp_path / "out", {"x.md": "# x"})
    winapi.CreateJunction(str(tmp_path / "out"), str(tmp_path / "in" / "elsewhere"))  # outside the root
    winapi.CreateJunction(str(tmp_path / "in"), str(tmp_path / "in" / "loop"))  # a loop
    scan = scan_dir(tmp_path / "in", lambda p: True)
    assert [p.name for p in scan.files] == ["a.md"]


async def test_a_failure_outside_the_file_loop_still_ends_the_job(ledger: Ledger, tmp_path: Path):
    tree(tmp_path / "docs", {"a.md": "# a"})

    def boom(allowed):
        raise RuntimeError("no ingestor")

    imp = DirImporter(boom, ledger)
    job = imp.start(scan_dir(tmp_path / "docs", lambda p: True))
    events = await drain(imp.subscribe(job.id))
    assert events[-1]["kind"] == "end" and "no ingestor" in events[-1]["error"]
    assert job.state == "stopped" and imp.running() is None

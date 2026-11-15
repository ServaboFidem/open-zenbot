"""Import a directory into the vault and the knowledge graph.

Two steps, so the user sees the cost before paying it. `scan_dir` walks
a directory and counts the files an import would touch, skipping hidden
and build folders, oversized files, and suffixes no converter accepts;
the result is kept in a `ScanStore` under a token for a few minutes.
`scan_file` makes the same Scan for a single file.
`DirImporter.start(scan)` then runs the existing `Ingestor` over exactly
that list, one file at a time, emitting one event per file and an `end`
event with totals. Stop is immediate while a file converts or summarizes
(nothing is written yet); a file already writing finishes first. Each import owns its own ingestor, allowed to read
the picked directory and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.providers import tracing

from .ingest import IngestError, IngestResult

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", "target"}
DEFAULT_MAX_BYTES = 20_000_000  # ZEN_IMPORT_MAX_BYTES overrides


def max_bytes() -> int:
    raw = os.environ.get("ZEN_IMPORT_MAX_BYTES", "")
    return int(raw) if raw.strip().isdigit() else DEFAULT_MAX_BYTES


# -- scan ---------------------------------------------------------------------


@dataclass
class Scan:
    token: str
    dir: Path
    files: list[Path]
    bytes: int
    by_suffix: dict[str, int]
    skipped: dict[str, int]  # hidden | no_converter | too_large
    created_at: float
    unchanged: frozenset[Path] = frozenset()  # imported before with the same bytes: the import skips them

    def to_dict(self) -> dict[str, Any]:
        return {"token": self.token, "dir": str(self.dir), "files": len(self.files), "bytes": self.bytes,
                "by_suffix": dict(self.by_suffix), "skipped": dict(self.skipped),
                "extraction_calls": extraction_estimate(self)}


CHUNK_BYTES_ESTIMATE = 6000  # a rough chunk size, for the estimate only


def extraction_calls(size: int) -> int:
    """About how many model calls extracting one document of `size` bytes
    takes: one per chunk, plus one routing call."""
    return max(1, math.ceil(size / CHUNK_BYTES_ESTIMATE)) + 1


def extraction_estimate(scan: Scan) -> int:
    """About how many extraction calls the import will queue (`extraction_calls`
    per file); a file unchanged since its last import queues none."""
    total = 0
    for p in scan.files:
        if p in scan.unchanged:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        total += extraction_calls(size)
    return total


def unchanged_in(sync_log: Any, has_fact: Callable[[str], bool] = lambda fact_id: True) -> Callable[[Path], bool]:
    """True for a file the sync log holds with the same md5 (the ingestor
    keys it by resolved path) and whose fact the store still has
    (`has_fact`): an import skips it as unchanged. One whose fact is gone
    (an upgrade from cognee) is imported again, as `Ingestor.prepare` does."""
    def unchanged(p: Path) -> bool:
        real = Path(p).resolve()
        entry = sync_log.get(str(real))
        if entry is None or not has_fact(entry.fact_id):
            return False
        try:
            return hashlib.md5(real.read_bytes()).hexdigest() == entry.md5
        except OSError:
            return False

    return unchanged


def _hidden(name: str) -> bool:
    return name.startswith(".") or name in SKIP_DIRS


def scan_dir(root: Path, accepts: Callable[[Path], bool], *, max_bytes: int | None = None,
             unchanged: Callable[[Path], bool] | None = None) -> Scan:
    """Count the files under `root` an import would take. Directory
    symlinks and junctions are not followed; a file reached twice counts once.
    `unchanged` marks files a rescan skips (`unchanged_in(sync_log)`), so the
    extraction estimate counts only new and changed files."""
    root = Path(root)
    if not root.is_dir():
        raise IngestError(f"{root} is not a directory")
    limit = max_bytes if max_bytes is not None else globals()["max_bytes"]()
    files: list[Path] = []
    seen: set[Path] = set()
    skipped = {"hidden": 0, "no_converter": 0, "too_large": 0}
    by_suffix: dict[str, int] = {}
    total = 0

    def walk(d: Path, under_hidden: bool) -> None:
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            p = Path(e.path)
            hidden = under_hidden or _hidden(e.name)
            if e.is_dir(follow_symlinks=False):
                if e.is_junction():  # Windows junctions are not symlinks; a loop or a way out of the root
                    continue
                walk(p, hidden)
                continue
            if not e.is_file():  # a dangling or directory symlink
                continue
            try:
                real = p.resolve()
                size = real.stat().st_size
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
            if hidden:
                skipped["hidden"] += 1
            elif not accepts(p):
                skipped["no_converter"] += 1
            elif size > limit:
                skipped["too_large"] += 1
            else:
                files.append(p)

    walk(root, False)
    files.sort()
    for p in files:
        suffix = p.suffix.lower()
        by_suffix[suffix] = by_suffix.get(suffix, 0) + 1
        total += p.stat().st_size
    same = frozenset(p for p in files if unchanged(p)) if unchanged is not None else frozenset()
    return Scan(secrets.token_urlsafe(8), root, files, total, dict(sorted(by_suffix.items())), skipped, time.time(), same)


def scan_file(path: Path, accepts: Callable[[Path], bool], *, max_bytes: int | None = None) -> Scan:
    """A one-file import: the same Scan the directory walk makes, so the
    file runs through the same job, progress events, and stop."""
    path = Path(path)
    if not path.is_file():
        raise IngestError(f"{path} is not a file")
    if not accepts(path):
        raise IngestError(f"no converter for {path.suffix!r}")
    limit = max_bytes if max_bytes is not None else globals()["max_bytes"]()
    size = path.stat().st_size
    if size > limit:
        raise IngestError(f"{path.name} is {size} bytes; the import limit is {limit} (ZEN_IMPORT_MAX_BYTES)")
    return Scan(secrets.token_urlsafe(8), path.parent, [path], size, {path.suffix.lower(): 1},
                {"hidden": 0, "no_converter": 0, "too_large": 0}, time.time())


class ScanStore:
    """Scans by token, dropped after `ttl` seconds: the confirm imports
    exactly the list the count described."""

    def __init__(self, ttl: float = 600.0) -> None:
        self.ttl = ttl
        self._scans: dict[str, Scan] = {}

    def put(self, scan: Scan) -> Scan:
        self._scans[scan.token] = scan
        return scan

    def get(self, token: str) -> Scan | None:
        s = self._scans.get(token)
        if s is None:
            return None
        if time.time() - s.created_at > self.ttl:
            del self._scans[token]
            return None
        return s


# -- import -------------------------------------------------------------------


@dataclass
class ImportJob:
    id: str
    scan: Scan
    state: str = "running"  # running | stopped | done
    done_files: int = 0
    imported: int = 0
    unchanged: int = 0
    refused: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    current: str | None = None
    stop_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "dir": str(self.scan.dir), "state": self.state, "total": len(self.scan.files),
                "done_files": self.done_files, "imported": self.imported, "unchanged": self.unchanged,
                "refused": self.refused, "current": self.current, "stopping": self.stop_requested and self.state == "running"}


class DirImporter:
    """Runs one import at a time, on the event loop: the provider's HTTP
    client and the MCP client live there. The ingestor it is given should
    be built with `offload=True` so the blocking graph write runs in a
    worker thread and the API stays responsive."""

    kind = "import"

    def __init__(self, make_ingestor: Callable[[list[Path]], Any], ledger: Any, *, bot_id: str = "personal-assistant") -> None:
        self.make_ingestor = make_ingestor
        self.ledger = ledger
        self.bot_id = bot_id
        self.jobs: dict[str, ImportJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._preparing: dict[str, asyncio.Task[Any]] = {}  # job id -> the current file's convert+summary, cancellable

    # -- lifecycle --------------------------------------------------------

    def start(self, scan: Scan) -> ImportJob:
        if self.running() is not None:
            raise RuntimeError("an import is already running")
        job = ImportJob(secrets.token_urlsafe(6), scan)
        self.jobs[job.id] = job
        self._tasks[job.id] = asyncio.get_running_loop().create_task(self._run(job))
        return job

    def get(self, job_id: str) -> ImportJob | None:
        return self.jobs.get(job_id)

    def running(self) -> ImportJob | None:
        return next((j for j in self.jobs.values() if j.state == "running"), None)

    def stop(self, job_id: str) -> ImportJob:
        """Stop now: a file still converting or summarizing is dropped
        (nothing of it was written); a file already writing finishes first."""
        job = self.jobs[job_id]
        if job.state == "running" and not job.stop_requested:
            job.stop_requested = True
            self._emit(job, {"kind": "stopping"})
        task = self._preparing.get(job_id)
        if task is not None:
            task.cancel()
        return job

    def stop_all(self, reason: str) -> None:
        for job in list(self.jobs.values()):
            if job.state == "running":
                self.ledger.append(self.kind, self.bot_id, "", job_id=job.id, event="stop", reason=reason)
                self.stop(job.id)

    def subscribe(self, job_id: str) -> asyncio.Queue[dict[str, Any]]:
        job = self.jobs[job_id]
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for e in job.events:
            q.put_nowait(e)
        if job.state == "running":
            self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subscribers.get(job_id, [])
        if q in subs:
            subs.remove(q)

    # -- the run ----------------------------------------------------------

    def _emit(self, job: ImportJob, event: dict[str, Any]) -> None:
        job.events.append(event)
        for q in list(self._subscribers.get(job.id, [])):
            q.put_nowait(event)
        data = {("event" if k == "kind" else k): v for k, v in event.items()}
        self.ledger.append(self.kind, self.bot_id, "", job_id=job.id, **data)

    async def _run(self, job: ImportJob) -> None:
        error = ""
        try:
            await self._run_files(job)
        except Exception as e:  # noqa: BLE001 - whatever happens, the job ends and the next import can start
            error = f"{type(e).__name__}: {e}"[:300]
        finally:
            job.current = None
            if error or (job.stop_requested and job.done_files < len(job.scan.files)):
                job.state = "stopped"
            else:
                job.state = "done"
            end: dict[str, Any] = {"kind": "end", "state": job.state, "imported": job.imported, "unchanged": job.unchanged, "refused": job.refused}
            if error:
                end["error"] = error
            self._emit(job, end)
            self._subscribers.pop(job.id, None)

    async def _run_files(self, job: ImportJob) -> None:
        ingestor = self.make_ingestor([job.scan.dir])
        for path in job.scan.files:
            if job.stop_requested:
                break
            job.current = str(path)
            # one Langfuse trace per file: its summary and routing calls nest under it (the prepare task copies
            # this context when it is created inside the block)
            with tracing.observe("import-document", as_type="chain", input={"file": path.name},
                                 metadata={"import_id": job.id, "suffix": path.suffix.lower()}) as doc:
                prep = asyncio.ensure_future(ingestor.prepare(path, bot_id=self.bot_id))
                self._preparing[job.id] = prep
                try:
                    prepared = await prep
                    if isinstance(prepared, IngestResult):  # unchanged: nothing to write
                        result = prepared
                    else:
                        result = await ingestor.commit(prepared)  # never cancelled: see Ingestor.commit
                    status = result.status
                    event = {"kind": "file", "path": str(path), "status": status}
                except asyncio.CancelledError:
                    if not prep.cancelled():
                        raise  # the job itself was cancelled, not this file
                    doc.update(output={"status": "stopped"}, level="WARNING")
                    break  # stopped while preparing: nothing of this file was written
                except Exception as e:  # noqa: BLE001 - one bad file never ends the run
                    status = "refused"
                    event = {"kind": "file", "path": str(path), "status": status, "reason": f"{type(e).__name__}: {e}"[:300]}
                finally:
                    self._preparing.pop(job.id, None)
                doc.update(output={"status": status}, **({"level": "WARNING", "status_message": event["reason"]} if "reason" in event else {}))
            job.done_files += 1
            if status in ("new", "updated"):
                job.imported += 1
            elif status == "unchanged":
                job.unchanged += 1
            else:
                job.refused += 1
            self._emit(job, event)

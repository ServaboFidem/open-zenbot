"""Live check of the Hindsight store: a real `hindsight-api` child on an embedded pg0 Postgres.

Skipped unless ZEN_HINDSIGHT_LIVE=1 and OPENROUTER_API_KEY is set. Run it on its own:
    ZEN_HINDSIGHT_LIVE=1 uv run pytest tests/test_hindsight_live.py -q -s
It uses its own ports (API 28888, Postgres 25556), a temp memory root and so its own pg0 instance,
which it drops afterwards. It proves privacy by tag (also after consolidation), forget, that the key
never reaches the log, and that close() leaves no process behind, Postgres included.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.memory.graph import SHARED, MemoryGraph, Target, Tier, make_store
from src.memory.hindsight_process import instance_name
from src.memory.ledger import Ledger
from src.memory.stores import PERSONAL, WORLD, org_store

pytestmark = pytest.mark.skipif(
    os.environ.get("ZEN_HINDSIGHT_LIVE") != "1" or not os.environ.get("OPENROUTER_API_KEY"),
    reason="live Hindsight check: set ZEN_HINDSIGHT_LIVE=1 and OPENROUTER_API_KEY",
)

API_PORT, PG_PORT = 28888, 25556
RETAIN_WAIT_S = 600.0
CONSOLIDATION_WAIT_S = 20.0

RESEARCHER, WRITER = "research-partner", "writer"
PERSONAL_FACT = "Alex wants to start a small beekeeping business on his farm in Ohio next spring."
WORLD_FACT = "Ohio requires every beekeeper to register each apiary with the Ohio Department of Agriculture every year."
RESEARCHER_FACT = "The research partner bot prefers primary sources such as state agriculture department pages."
WRITER_FACT = "The writer bot's secret draft codename is Pelican."
ORG_FACT = "Acme Corp holds its quarterly planning meeting every March in Columbus."
BEES = "How do I start a beekeeping business in Ohio?"
CODENAME = "What is the draft codename?"


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def processes() -> list[dict]:
    """Every process's pid, parent pid, name and command line. The query itself never names what we look
    for, so it cannot match itself."""
    if sys.platform == "win32":
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine"
             " | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout
        rows = json.loads(out or "[]")
        rows = rows if isinstance(rows, list) else [rows]
        return [{"pid": r["ProcessId"], "ppid": r.get("ParentProcessId"), "name": r.get("Name") or "",
                 "cmd": r.get("CommandLine") or ""} for r in rows]
    out = subprocess.run(["ps", "-eo", "pid=,ppid=,comm=,args="], capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "name": parts[2],
                         "cmd": parts[3] if len(parts) > 3 else ""})
    return rows


def instance_procs(instance_dir: Path) -> list[dict]:
    """Processes of this test's pg0 instance: its data dir or its port on the command line, and the
    postmaster's own children (their command lines name neither)."""
    marks = [str(instance_dir).lower().replace("\\", "/"), f"-p {PG_PORT}"]
    me = os.getpid()
    rows = processes()
    hits = {p["pid"]: p for p in rows
            if p["pid"] != me and any(m in p["cmd"].lower().replace("\\", "/") for m in marks)}
    hits |= {p["pid"]: p for p in rows if p["ppid"] in hits and "postgres" in p["name"].lower()}
    return list(hits.values())


def still_alive(seen: list[dict]) -> list[dict]:
    """Those of `seen` still running (same pid and name)."""
    now = {(p["pid"], p["name"]) for p in processes()}
    return [p for p in seen if (p["pid"], p["name"]) in now]


def pg0_instance_dir(name: str) -> Path:
    return Path.home() / ".pg0" / "instances" / name


def drop_instance(name: str) -> None:
    """Remove only this test's instance: pg0's own drop (stops a survivor too), then its directory."""
    assert name.startswith("zen-") and name != "zen-spike"
    try:
        import pg0
        pg0.drop(name)
    except Exception as e:  # noqa: BLE001 - fall back to the directory below
        print(f"pg0 drop {name}: {type(e).__name__}")
    d = pg0_instance_dir(name)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def texts(store, query: str, namespaces: list[str]) -> list[str]:
    """Every text one raw recall returns for this scope: facts and observations alike."""
    return [r.get("text") or "" for r in store.http.recall(query, store.scope(namespaces), timeout=60)]


def wait_all_ok(store, ids: list[str]) -> None:
    deadline = time.monotonic() + RETAIN_WAIT_S
    while True:
        out = store.reconcile()
        states = {fid: store._records.get(fid, {}).get("state") for fid in ids}
        if all(s == "ok" for s in states.values()):
            return
        assert "failed" not in states.values(), f"a retain failed: {states} ({out})"
        assert time.monotonic() < deadline, f"retains not done after {RETAIN_WAIT_S:g}s: {states}"
        time.sleep(3)


def test_live_hindsight_privacy_forget_and_clean_shutdown(tmp_path, monkeypatch):
    for port in (API_PORT, PG_PORT):
        assert not listening(port), f"port {port} is already in use; stop whatever holds it first"
    monkeypatch.setenv("ZEN_HINDSIGHT_PORT", str(API_PORT))
    monkeypatch.setenv("ZEN_HINDSIGHT_PG_PORT", str(PG_PORT))
    monkeypatch.setenv("ZEN_HINDSIGHT_UI", "0")
    monkeypatch.setenv("ZEN_HINDSIGHT_READY_TIMEOUT", "1200")  # a first run downloads models
    for name in ("ZEN_HINDSIGHT_URL", "ZEN_HINDSIGHT_KEY_REF"):
        monkeypatch.delenv(name, raising=False)

    root = tmp_path / "memory"
    root.mkdir()
    name = instance_name(root)
    instance_dir = pg0_instance_dir(name)
    assert not instance_dir.exists(), f"pg0 instance {name} already exists"
    store = make_store(root, "hindsight")
    memory = MemoryGraph(store, Ledger(tmp_path / "vault"))
    closed = False
    try:
        t0 = time.monotonic()
        store.start()
        start_s = time.monotonic() - t0
        print(f"\nHINDSIGHT START: {start_s:.1f} s (start() until ready)")

        personal = memory.remember(PERSONAL_FACT, SHARED, Tier.user_pref, targets=[Target(PERSONAL, ("Alex",))])
        world = memory.remember(WORLD_FACT, SHARED, Tier.project, targets=[Target(WORLD)])
        mine = memory.remember(RESEARCHER_FACT, f"bot:{RESEARCHER}", Tier.private, bot_id=RESEARCHER)
        secret = memory.remember(WRITER_FACT, f"bot:{WRITER}", Tier.private, bot_id=WRITER)
        org = memory.remember(ORG_FACT, SHARED, Tier.project, targets=[Target(org_store("acme"), ("Acme",))])
        ids = [personal.id, world.id, mine.id, secret.id, org.id]
        t1 = time.monotonic()
        wait_all_ok(store, ids)
        print(f"HINDSIGHT RETAINS: all 5 ok after {time.monotonic() - t1:.1f} s")

        researcher = [f"bot:{RESEARCHER}", SHARED]
        # Each store tag here has one fact, so a result tagged `personal` (`world`) is that fact or its observation
        # (consolidation may already have replaced the fact in the answer: see the writer's check below).
        raw = store.http.recall(BEES, store.scope(researcher), timeout=60)
        tagged = {t for r in raw for t in (r.get("tags") or [])}
        assert PERSONAL in tagged and WORLD in tagged, f"beekeeping recall missed a shared fact: {raw}"
        assert f"bot:{WRITER}" not in tagged
        found = {f.id for f in store.search(BEES, researcher, limit=10)}
        assert secret.id not in found
        assert personal.id in found and world.id in found, f"the beekeeping search missed a shared fact: {raw}"
        print(f"RECALL: beekeeping -> personal and world ({len(raw)} results; both as facts)")

        def no_pelican(when: str) -> None:
            hits = store.search(CODENAME, researcher, limit=10)
            assert secret.id not in {f.id for f in hits}, when
            assert not any("pelican" in f.text.lower() for f in hits), when
            raw = texts(store, CODENAME, researcher)
            assert not any("pelican" in t.lower() for t in raw), f"{when}: {raw}"
            graph = store.graph_context(CODENAME, researcher, timeout=60)
            assert not any("pelican" in t.lower() for t in graph), f"{when}: {graph}"
            print(f"PRIVACY {when}: {len(raw)} results for the researcher, none names the codename")

        no_pelican("before consolidation")
        time.sleep(CONSOLIDATION_WAIT_S)
        no_pelican("after consolidation")

        # After consolidation Hindsight answers with the observation alone (no document id); search must still
        # return the fact itself, through the observation's source facts.
        writer = [f"bot:{WRITER}"]
        raw = store.http.recall(CODENAME, writer, timeout=60)
        own = store.search(CODENAME, writer, limit=10)
        assert secret.id in {f.id for f in own}, f"the writer's search missed its own fact: {raw}"
        kinds = sorted({r.get("type") or "" for r in raw})
        print(f"PRIVACY: the writer's search returns the Pelican fact itself (recall result types: {kinds})")

        assert memory.forget(personal.id)
        assert store.get(personal.id) is None
        wait_all_ok(store, [world.id, mine.id, secret.id, org.id])
        assert personal.id not in store._records, "the forget did not finish in Hindsight"
        for query in (BEES, "Alex farm beekeeping business next spring"):
            for ns in (researcher, [f"bot:{WRITER}", SHARED], [SHARED]):
                assert personal.id not in {f.id for f in store.search(query, ns, limit=10)}
                raw = store.http.recall(query, store.scope(ns), timeout=60)
                assert personal.id not in {r.get("document_id") for r in raw}, (query, ns)
                assert not any(PERSONAL in (r.get("tags") or []) for r in raw), (query, ns, raw)  # its only fact
                assert not any("next spring" in (r.get("text") or "").lower() for r in raw), (query, ns, raw)
        print("FORGET: no recall returns the forgotten personal fact")

        api = store.server._api
        assert api is not None and api.poll() is None
        family = instance_procs(instance_dir)
        assert any("postgres" in p["name"].lower() for p in family), f"no postgres of {name} seen: {family}"
        t2 = time.monotonic()
        memory.close()
        closed = True
        print(f"CLOSE: {time.monotonic() - t2:.1f} s; {len(family)} processes of {name} were running")

        assert not listening(API_PORT), f"something still listens on {API_PORT}"
        assert not listening(PG_PORT), f"something still listens on {PG_PORT}"
        left = instance_procs(instance_dir) + still_alive(family)
        assert not left, f"processes of this pg0 instance survived close(): {left}"
        print(f"SHUTDOWN: nothing listens on {API_PORT}/{PG_PORT}; none of the {len(family)} survived, "
              f"no process names {name} or -p {PG_PORT}")

        key = os.environ["OPENROUTER_API_KEY"].encode("utf-8")
        logs = [p for p in (root / "hindsight").glob("*.log")]
        assert (root / "hindsight" / "hindsight.log") in logs
        assert all(key not in p.read_bytes() for p in logs), "the key appears in a Hindsight log"
        print(f"KEY: absent from {', '.join(p.name for p in logs)}")
    finally:
        try:
            if not closed:
                memory.close()
        finally:
            drop_instance(name)  # even when close() raised: never leave the pg0 instance behind
    assert not instance_dir.exists(), f"pg0 instance {name} was not removed"

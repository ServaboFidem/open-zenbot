"""The on-disk extraction queue. Offline: a fake ProfileMemory stands in."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.memory.exclusion import ExclusionError
from src.memory.extract_queue import ExtractQueue, Job
from src.memory.profile import OwnerUnknown


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeProfile:
    def __init__(self, route=("personal",)):
        self.owner = None
        self.route = list(route)
        self.facts = {"f1": SimpleNamespace(text="fact one"), "f2": SimpleNamespace(text="fact two")}
        self.routed: list[str] = []
        self.extracted: list[tuple[str, str, list[str] | None]] = []
        self.fail: Exception | None = None
        self.blocked = False
        self.no_attach = False
        self.fail_times = 0  # -1: fail every call
        self.people = SimpleNamespace(owner=lambda: self.owner)
        self.graph = SimpleNamespace(get=lambda fid: self.facts.get(fid))

    def preflight(self, fact_id, text):
        if self.no_attach:
            raise TypeError("the store has no attach")
        if self.blocked:
            raise ExclusionError("blocked")

    async def plan_stores(self, text):
        self.routed.append(text)
        return list(self.route)

    async def extract(self, fact_id, text, stores, stop=None):
        if fact_id not in self.facts:
            raise KeyError(fact_id)
        if self.fail is not None and self.fail_times != 0:
            self.fail_times -= 1
            raise self.fail
        self.extracted.append((fact_id, text, stores))
        return stores


@pytest.fixture
def profile():
    return FakeProfile()


def queue(tmp_path, profile, ledger, **kw):
    (tmp_path / "vault").mkdir(exist_ok=True)
    return ExtractQueue(tmp_path / "queue.json", profile, tmp_path / "vault", ledger, **kw)


def entries(ledger):
    return [e for e in ledger.iter() if e.kind == "extract"]


async def test_queue_survives_restart(tmp_path, profile, ledger):
    profile.owner = object()
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", ["world"]))
    q.add(Job("j2", "f2", "fact", "", ["world"]))
    assert [j.id for j in queue(tmp_path, profile, ledger).pending()] == ["j1", "j2"]
    assert await q.run_once() is True
    assert [j.id for j in queue(tmp_path, profile, ledger).pending()] == ["j2"]
    assert [(e.data["status"], e.data["job"]) for e in entries(ledger)] == [("queued", "j1"), ("queued", "j2"), ("done", "j1")]
    assert entries(ledger)[-1].data["stores"] == ["world"]


async def test_personal_work_waits_for_owner(tmp_path, profile, ledger):
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", ["personal"]))
    assert await q.run_once() is False
    assert [j.attempts for j in q.pending()] == [0]
    assert q.waiting_for_owner() and q.progress()["waiting_for_owner"] is True
    profile.owner = object()
    assert not q.waiting_for_owner()
    assert await q.run_once() is True
    assert q.pending() == []


async def test_world_job_runs_while_personal_waits(tmp_path, profile, ledger):
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", ["personal"]))
    q.add(Job("j2", "f2", "fact", "", ["world"]))
    assert await q.run_once() is True
    assert [j.id for j in q.pending()] == ["j1"]


async def test_doc_job_routes_once_and_attaches(tmp_path, profile, ledger):
    profile.owner = object()
    profile.route = ["personal", "world"]
    q = queue(tmp_path, profile, ledger)
    (tmp_path / "vault" / "notes").mkdir(parents=True)
    (tmp_path / "vault" / "notes" / "a.md").write_text("the document", encoding="utf-8")
    q.add(Job("j1", "f1", "doc", "notes/a.md", None))
    assert await q.run_once() is True
    assert profile.routed == ["the document"]
    assert profile.extracted == [("f1", "the document", ["personal", "world"])]
    assert q.progress()["done"] == 1 and q.pending() == []


async def test_failed_job_retries_then_fails(tmp_path, profile, ledger):
    profile.owner = object()
    profile.fail, profile.fail_times = RuntimeError("boom"), -1
    clock = Clock()
    q = queue(tmp_path, profile, ledger, clock=clock)
    q.add(Job("j1", "f1", "fact", "", None))
    for expected in (1, 2):
        assert await q.run_once() is True
        assert [j.attempts for j in q.pending()] == [expected]
        clock.now += 200
    assert await q.run_once() is True
    assert q.pending() == []
    [e] = [e for e in entries(ledger) if e.data["status"] == "failed"]
    assert e.data["status"] == "failed" and e.data["fact_id"] == "f1"
    assert q.progress()["failed"] == 1
    assert profile.routed == ["fact one"]  # routed once; the retries reused the persisted stores


async def test_retry_reuses_persisted_stores(tmp_path, profile, ledger):
    profile.owner = object()
    profile.fail, profile.fail_times = RuntimeError("boom"), 1
    clock = Clock()
    q = queue(tmp_path, profile, ledger, clock=clock)
    q.add(Job("j1", "f1", "fact", "", None))
    await q.run_once()
    assert queue(tmp_path, profile, ledger).pending()[0].stores == ["personal"]
    profile.route = ["world"]
    clock.now += 60
    await q.run_once()
    assert profile.extracted == [("f1", "fact one", ["personal"])]


async def test_forgotten_fact_job_is_skipped(tmp_path, profile, ledger):
    profile.owner = object()
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "gone", "doc", "x.md", ["world"]))
    q.add(Job("j2", "gone2", "fact", "", ["world"]))
    assert await q.run_once() is True
    assert await q.run_once() is True
    assert q.pending() == [] and profile.extracted == []
    assert {e.data["status"] for e in entries(ledger)} == {"queued"}


async def test_blocked_text_is_refused_not_retried(tmp_path, profile, ledger):
    profile.owner = object()
    profile.fail, profile.fail_times = ExclusionError("blocked"), -1
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", ["world"]))
    assert await q.run_once() is True
    assert q.pending() == []
    assert entries(ledger)[-1].data["status"] == "refused"


async def test_owner_unknown_from_extract_keeps_job(tmp_path, profile, ledger):
    profile.owner = object()
    profile.fail, profile.fail_times = OwnerUnknown("no"), 1
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", ["world"]))
    assert await q.run_once() is False
    assert [j.attempts for j in q.pending()] == [0]


async def test_run_stops_on_event(tmp_path, profile, ledger):
    profile.owner = object()
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", ["world"]))
    stop = asyncio.Event()
    task = asyncio.create_task(q.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, 3)
    assert q.pending() == []


async def test_shutdown_waits_for_the_job_in_flight_and_starts_no_other(tmp_path, profile, ledger):
    profile.owner = object()
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", ["world"]))
    q.add(Job("j2", "f2", "fact", "", ["world"]))
    real = profile.extract
    started = asyncio.Event()

    async def slow(fact_id, text, stores, stop=None):
        started.set()
        await asyncio.sleep(0.3)
        return await real(fact_id, text, stores)

    profile.extract = slow
    task = asyncio.create_task(q.run(asyncio.Event()))
    await started.wait()
    await q.shutdown(task, grace=5)
    assert task.done() and not task.cancelled()
    assert [f for f, _, _ in profile.extracted] == ["f1"]  # finished, and j2 never started
    assert [j.id for j in q.pending()] == ["j2"]


async def test_shutdown_past_the_grace_cancels(tmp_path, profile, ledger):
    profile.owner = object()
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", ["world"]))
    started = asyncio.Event()

    async def hung(fact_id, text, stores, stop=None):
        started.set()
        await asyncio.sleep(60)

    profile.extract = hung
    task = asyncio.create_task(q.run(asyncio.Event()))
    await started.wait()
    await q.shutdown(task, grace=0.1)
    assert task.cancelled()
    assert [j.id for j in q.pending()] == ["j1"]
    assert entries(ledger)[-1].data["status"] == "shutdown_cut"


async def test_blocked_text_with_no_stores_is_refused_before_routing(tmp_path, profile, ledger):
    profile.owner = object()
    profile.blocked = True
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", None))
    assert await q.run_once() is True
    assert q.pending() == [] and profile.routed == []
    assert entries(ledger)[-1].data["status"] == "refused"


async def test_keyerror_is_dropped_only_when_the_fact_is_gone(tmp_path, profile, ledger):
    profile.owner = object()
    profile.fail, profile.fail_times = KeyError("bug"), 1
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", ["world"]))
    assert await q.run_once() is True
    assert [j.attempts for j in q.pending()] == [1]  # the fact exists: an ordinary failure


async def test_failed_job_waits_for_backoff_while_others_run(tmp_path, profile, ledger):
    profile.owner = object()
    profile.fail, profile.fail_times = RuntimeError("boom"), 1
    clock = Clock()
    q = queue(tmp_path, profile, ledger, clock=clock)
    q.add(Job("j1", "f1", "fact", "", ["world"]))
    q.add(Job("j2", "f2", "fact", "", ["world"]))
    await q.run_once()  # j1 fails, due again in 30 s
    assert queue(tmp_path, profile, ledger, clock=clock).pending()[0].not_before == 1030.0
    assert await q.run_once() is True  # j2 runs meanwhile
    assert [j.id for j in q.pending()] == ["j1"]
    assert await q.run_once() is False  # j1 not due yet
    clock.now = 1031.0
    assert await q.run_once() is True
    assert q.pending() == []


async def test_store_without_attach_is_dropped_as_failed(tmp_path, profile, ledger):
    profile.owner = object()
    profile.no_attach = True
    q = queue(tmp_path, profile, ledger)
    q.add(Job("j1", "f1", "fact", "", ["world"]))
    assert await q.run_once() is True
    assert q.pending() == []
    last = entries(ledger)[-1].data
    assert last["status"] == "failed" and last["reason"] == "store cannot attach"


async def test_corrupt_queue_file_is_moved_aside(tmp_path, profile, ledger):
    (tmp_path / "queue.json").write_text("{not json", encoding="utf-8")
    q = queue(tmp_path, profile, ledger)
    assert q.pending() == []
    assert (tmp_path / "queue.json.bad").read_text(encoding="utf-8") == "{not json"


def test_rebuild_jobs_never_read_outside_the_vault(tmp_path):
    from src.memory.extract_queue import rebuild_jobs
    from src.memory.graph import SHARED, Fact, Tier

    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / "outside.md").write_text("not the vault's", encoding="utf-8")
    f = Fact(text="a summary", namespace=SHARED, tier=Tier.project, source="vault:../outside.md")
    [(job, calls)] = rebuild_jobs([f], vault)
    assert (job.kind, job.source, job.stores, calls) == ("fact", "vault:../outside.md", None, 2)  # the fact's own text


def test_shutdown_fits_under_the_launcher_kill():
    """scripts/dev.sh SIGKILLs the harness 45 s after SIGTERM; the extraction
    grace plus MemoryGraph.close's wait for the write lock must end before that."""
    import inspect

    from src.memory.extract_queue import SHUTDOWN_GRACE
    from src.memory.graph import MemoryGraph

    close_wait = inspect.signature(MemoryGraph.close).parameters["wait"].default
    assert SHUTDOWN_GRACE + close_wait < 45


async def test_remember_shared_without_a_queue_writes_directly(vault, ledger):
    """The local backend: registries but no extraction queue. A pinned org store before the owner is
    known, and an untargeted write, both land directly; neither spends a routing call."""
    from src.memory.extract_queue import remember_shared
    from src.memory.graph import LocalGraph, MemoryGraph
    from src.memory.profile import ProfileMemory
    from src.memory.registry import OrgRegistry, PeopleRegistry

    class NoRouter:
        provider = None

        async def route(self, text, orgs, owner):
            raise AssertionError("no routing call expected")

    mem = MemoryGraph(LocalGraph(vault / "memory"), ledger)
    root = vault / "memory" / "local"
    profile = ProfileMemory(mem, PeopleRegistry(root / "people.json"), OrgRegistry(root / "orgs.json"), NoRouter(), ledger)
    pinned = await remember_shared(profile, None, "Acme sells widgets.", bot_id="b", source="t", entities=["Acme"], stores=["org:acme"])
    routed = await remember_shared(profile, None, "Acme is in Ohio.", bot_id="b", source="t", entities=["Acme"])
    assert mem.get(pinned.id) is not None and mem.get(routed.id) is not None

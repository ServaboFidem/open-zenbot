"""harness-api: the GUI boundary. Loopback only, key never in a response."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from src.harness.api import PREVIEW_CSP, Harness, create_app
from src.harness.design_studio import BRIDGE_MAX_BYTES
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.memory.exclusion import exclusion
from src.memory.ledger import Ledger
from src.providers.interface import ProviderUnavailable
from src.skills.catalog import Catalog
from src.skills.loader import load_skill

from .conftest import MockScorer, ScriptedProvider, completion

KEY = "sk-or-v1-apitestkey0123456789abcdef"


@pytest.fixture
def harness(vault: Path, tmp_path: Path, example_skill_dir: Path) -> Harness:
    exclusion.register_secret_value(KEY)  # as the real provider would
    catalog = Catalog()
    catalog.add_skill(load_skill(example_skill_dir).skill)
    cfg = HarnessConfig(vault_root=vault, skills_dir=example_skill_dir.parent, shell_cwd=tmp_path)
    cfg.validate()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=catalog)
    h.add_bot(Bot(name="Research partner", role="Research things", id="bot1"))
    return h


@pytest.fixture
async def client(harness: Harness):
    transport = httpx.ASGITransport(app=create_app(harness))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


def allow_writes(h: Harness, p: float = 0.95) -> MockScorer:
    """A decider for the shell write check that finds every command writes only inside the allowed folders."""
    from src.decider.interface import ChoiceAnswer

    s = MockScorer(answer=lambda r: {"writes": ChoiceAnswer("inside", {"inside": p, "outside": 1 - p, "unsure": 0.0}),
                                     "reads": ChoiceAnswer("no", {"no": p, "yes": 1 - p, "unsure": 0.0})})
    h.loop.write_check.scorer = lambda: s
    return s


async def wait_for(client, session_id, states, tries=200):
    for _ in range(tries):
        r = await client.get(f"/sessions/{session_id}")
        if r.json()["state"] in states:
            return r.json()
        await asyncio.sleep(0.01)
    raise AssertionError(f"session never reached {states}")


def test_binds_loopback_only():
    HarnessConfig(host="127.0.0.1").validate()
    HarnessConfig(host="localhost").validate()
    with pytest.raises(ValueError, match="loopback"):
        HarnessConfig(host="0.0.0.0").validate()


async def test_health_and_bots(client):
    assert (await client.get("/health")).json()["provider"] == "mock"
    r = await client.post("/bots", json={"name": "Writer", "role": "Draft things", "skills": ["research-brief"]})
    assert r.status_code == 201 and r.json()["skills"] == ["research-brief"]
    bid = r.json()["id"]
    r = await client.patch(f"/bots/{bid}", json={"role": "Edit things", "skills": []})
    assert r.json()["role"] == "Edit things" and r.json()["skills"] == []
    assert (await client.post("/bots", json={"name": "x", "role": "y", "skills": ["nope"]})).status_code == 422
    assert (await client.get("/bots/missing")).status_code == 404
    assert [s["ref"] for s in (await client.get("/skills")).json() if s["kind"] == "skill"] == ["research-brief"]


async def test_task_runs_to_done_with_events_and_ledger(client, harness: Harness):
    allow_writes(harness)
    harness.provider.script = [
        completion(calls=[("c1", "shell", {"command": "echo api-hello"})]),
        completion("Done: api-hello"),
    ]
    r = await client.post("/bots/bot1/tasks", json={"task": "say hello"})
    assert r.status_code == 202
    sid = r.json()["session_id"]

    s = await wait_for(client, sid, {"done"})
    assert s["result"] == "Done: api-hello" and s["steps"] == 2
    assert s["messages"][-1]["role"] == "assistant"

    rows = (await client.get("/ledger", params={"session_id": sid})).json()
    assert [e["kind"] for e in rows] == ["session", "turn", "decider_score", "tool_start", "tool_call", "turn", "session"]
    assert (await client.get("/ledger", params={"kind": "tool_call"})).json()[0]["data"]["tool"] == "shell"

    ev = await client.get(f"/sessions/{sid}/events")
    frames = [json.loads(l[6:]) for l in ev.text.splitlines() if l.startswith("data: ")]
    assert frames[0]["kind"] == "session" and frames[-1] == {"kind": "end", "state": "done"}

    assert (await client.get("/sessions")).json()[0]["id"] == sid
    assert (await client.post("/bots/bot1/tasks", json={"task": "  "})).status_code == 422


async def test_approval_flow_over_http(client, harness: Harness):
    harness.bots["bot1"].attach_skill(harness.catalog.skill("research-brief"))
    harness.provider.script = [
        completion(calls=[("c1", "shell", {"command": "git push origin main"})]),
        completion("Pushed."),
    ]
    sid = (await client.post("/bots/bot1/tasks", json={"task": "push it"})).json()["session_id"]
    await wait_for(client, sid, {"waiting_approval"})

    pending = (await client.get("/approvals", params={"state": "pending"})).json()
    assert len(pending) == 1 and pending[0]["rule"] == "shell:git push*" and pending[0]["session_id"] == sid

    r = await client.post(f"/approvals/{pending[0]['id']}/decide", json={"approved": True})
    assert r.json()["state"] == "approved"
    assert (await client.post(f"/approvals/{pending[0]['id']}/decide", json={"approved": False})).status_code == 409
    assert (await client.post("/approvals/nope/decide", json={"approved": True})).status_code == 404
    assert (await wait_for(client, sid, {"done"}))["result"] == "Pushed."


async def test_pause_resume_retry_over_http(client, harness: Harness):
    harness.provider.script = [ProviderUnavailable("503"), completion("recovered")]
    harness.provider.probe_result = False
    sid = (await client.post("/bots/bot1/tasks", json={"task": "go"})).json()["session_id"]
    s = await wait_for(client, sid, {"paused"})
    assert "ProviderUnavailable" in s["pause_reason"]

    r = await client.post(f"/sessions/{sid}/retry")
    assert r.json()["resumed"] is False and r.json()["state"] == "paused"
    harness.provider.probe_result = True
    assert (await client.post(f"/sessions/{sid}/retry")).json()["resumed"] is True
    assert (await wait_for(client, sid, {"done"}))["result"] == "recovered"
    assert (await client.post(f"/sessions/{sid}/resume")).status_code == 409


async def test_stop_over_http(client, harness: Harness):
    harness.provider.script = [ProviderUnavailable("503")]
    sid = (await client.post("/bots/bot1/tasks", json={"task": "go"})).json()["session_id"]
    await wait_for(client, sid, {"paused"})
    assert (await client.post(f"/sessions/{sid}/stop")).json()["state"] in ("paused", "stopped")
    assert (await wait_for(client, sid, {"stopped"}))["state"] == "stopped"


@pytest.fixture
def scored_harness(vault: Path, tmp_path: Path, example_skill_dir: Path) -> Harness:
    from src.decider.interface import ChoiceAnswer, NoulAnswer

    def answer(request):
        if request.purpose == "routing":
            return {q: NoulAnswer(0.9 if q.endswith("research-brief") else 0.1) for q in request.questions}
        return {"role_fit": ChoiceAnswer("bot1", {"bot1": 0.8, "bot2": 0.2}, 0.8)}

    catalog = Catalog()
    catalog.add_skill(load_skill(example_skill_dir).skill)
    cfg = HarnessConfig(vault_root=vault, skills_dir=example_skill_dir.parent, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=catalog, scorer=MockScorer(answer))
    h.add_bot(Bot(name="Research partner", role="Research things", id="bot1"))
    h.add_bot(Bot(name="Writer", role="Write things", id="bot2"))
    return h


@pytest.fixture
async def scored_client(scored_harness: Harness):
    transport = httpx.ASGITransport(app=create_app(scored_harness))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def test_fit_suggest_confirm_deny_over_http(scored_client, scored_harness: Harness):
    c = scored_client
    assert (await c.get("/health")).json()["scorer"] == "mock-scorer"
    assert (await c.get("/health")).json()["selector"] == "catalog-router"

    r = await c.post("/skills/research-brief/fit")
    assert r.status_code == 201
    fit = r.json()
    assert fit["bot_ids"] == ["bot1"] and fit["state"] == "pending" and fit["role_scores"]["bot1"] == 0.8
    assert (await c.get("/bots/bot1")).json()["skills"] == [], "suggestion did not attach"

    assert (await c.post(f"/fits/{fit['id']}/confirm", json={"bot_id": "bot2"})).status_code == 409
    r = await c.post(f"/fits/{fit['id']}/confirm", json={"bot_id": "bot1"})
    assert r.json()["state"] == "confirmed" and r.json()["attached_to"] == "bot1"
    assert (await c.get("/bots/bot1")).json()["skills"] == ["research-brief"]

    fit2 = (await c.post("/skills/research-brief/fit")).json()
    assert (await c.post(f"/fits/{fit2['id']}/deny")).json()["state"] == "denied"
    assert {f["state"] for f in (await c.get("/fits")).json()} == {"confirmed", "denied"}
    assert (await c.get("/fits", params={"state": "pending"})).json() == []
    assert (await c.post("/skills/nope/fit")).status_code == 404
    assert (await c.post("/fits/nope/deny")).status_code == 404


async def test_route_detail_in_session_over_http(scored_client, scored_harness: Harness):
    scored_harness.bots["bot1"].attach_skill(scored_harness.catalog.skill("research-brief"))
    scored_harness.provider.script = [completion("Brief done.")]
    sid = (await scored_client.post("/bots/bot1/tasks", json={"task": "brief me"})).json()["session_id"]
    s = await wait_for(scored_client, sid, {"done"})
    assert s["injected"] == ["research-brief"] and s["route"]["scorer"] == "mock-scorer" and s["route"]["outcome"] == "act"
    scores = (await scored_client.get("/ledger", params={"kind": "decider_score", "session_id": sid})).json()
    assert len(scores) == 1 and scores[0]["data"]["purpose"] == "routing"


async def test_fit_unavailable_without_scorer(client):
    assert (await client.post("/skills/research-brief/fit")).status_code == 503
    assert (await client.get("/fits")).json() == []
    assert (await client.get("/health")).json()["selector"] == "inject-all"


async def test_key_never_appears_in_any_response(client, harness: Harness):
    # a tool prints the key, the model echoes it: neither the session nor the ledger may show it
    allow_writes(harness)
    harness.provider.script = [
        completion(calls=[("c1", "shell", {"command": f"echo OPENROUTER_API_KEY={KEY}"})]),
        completion(f"The key is {KEY}."),
    ]
    sid = (await client.post("/bots/bot1/tasks", json={"task": "leak"})).json()["session_id"]
    await wait_for(client, sid, {"done"})
    bodies = []
    for path in ["/health", "/bots", "/bots/bot1", "/skills", "/sessions", f"/sessions/{sid}", f"/sessions/{sid}/events", "/approvals", "/ledger"]:
        r = await client.get(path)
        assert r.status_code == 200, path
        bodies.append((path, r.text))
    for path, text in bodies:
        assert KEY not in text, f"key leaked via {path}"
    session = (await client.get(f"/sessions/{sid}")).json()
    assert "[redacted:registered-secret]" in session["result"]


# -- p3 wiring ------------------------------------------------------------


async def test_harness_wires_compaction_and_supervision(vault: Path, tmp_path: Path):
    from src.context.compaction import Compactor
    from src.decider.supervisor import LoopSupervisor

    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    with_scorer = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog(), scorer=MockScorer())
    assert isinstance(with_scorer.loop.compactor, Compactor)
    assert isinstance(with_scorer.loop.supervisor, LoopSupervisor)
    assert with_scorer.loop.supervisor.pause is with_scorer.pause  # halt pauses the same controller resume() releases
    assert with_scorer.loop.supervisor.scorer is with_scorer.scorer

    without = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    assert isinstance(without.loop.compactor, Compactor) and without.loop.supervisor is None

    app = create_app(with_scorer)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        health = (await c.get("/health")).json()
        assert health["supervisor"] == "loop-supervisor" and health["compaction"] is True


# -- directory import ----------------------------------------------------------

SUMMARY_DOC = completion('{"summary": "A note.", "names": [], "dates": [], "facts": ["x"]}')


async def test_scan_then_import_streams_progress(vault: Path, tmp_path: Path):
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=ScriptedProvider([SUMMARY_DOC] * 2), ledger=Ledger(vault), catalog=Catalog())
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# a", encoding="utf-8")
    (docs / "b.txt").write_text("b", encoding="utf-8")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        r = await c.post("/vault/scan", json={"dir": str(docs)})
        assert r.status_code == 200, r.text
        assert r.json()["files"] == 2 and r.json()["token"]
        token = r.json()["token"]
        r = await c.post("/vault/import", json={"token": token})
        assert r.status_code == 200, r.text
        import_id = r.json()["import_id"]
        lines = []
        async with c.stream("GET", f"/vault/import/{import_id}/events") as s:
            async for line in s.aiter_lines():
                if line.startswith("data: "):
                    lines.append(json.loads(line[6:]))
        assert [e["status"] for e in lines if e["kind"] == "file"] == ["new", "new"] and lines[-1]["kind"] == "end"
        st = (await c.get(f"/vault/import/{import_id}")).json()
        assert st["state"] == "done" and st["imported"] == 2 and st["total"] == 2
        assert (await c.post("/vault/import", json={"token": "nope"})).status_code == 404
        assert (await c.get("/vault/import/nope")).status_code == 404
        assert len((await c.get("/memory")).json()) == 2
        # a rescan estimates extraction for changed files only
        (docs / "b.txt").write_text("b, edited", encoding="utf-8")
        again = (await c.post("/vault/scan", json={"dir": str(docs)})).json()
        assert again["files"] == 2 and again["extraction_calls"] == 2  # b.txt alone: one chunk, one routing call
        # the shared ingestor never gained the picked directory
        assert (await c.post("/vault/ingest", json={"path": str(docs / "a.md")})).status_code == 422


async def test_import_file_runs_one_picked_file(vault: Path, tmp_path: Path):
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=ScriptedProvider([SUMMARY_DOC]), ledger=Ledger(vault), catalog=Catalog())
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# a", encoding="utf-8")
    (docs / "b.md").write_text("# b", encoding="utf-8")
    (docs / "c.bmp").write_bytes(b"BM")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        r = await c.post("/vault/import-file", json={"path": str(docs / "a.md")})
        assert r.status_code == 200, r.text
        assert r.json()["dir"] == str(docs) and r.json()["files"] == 1
        lines = []
        async with c.stream("GET", f"/vault/import/{r.json()['import_id']}/events") as s:
            async for line in s.aiter_lines():
                if line.startswith("data: "):
                    lines.append(json.loads(line[6:]))
        assert [Path(e["path"]).name for e in lines if e["kind"] == "file"] == ["a.md"]
        assert lines[-1]["state"] == "done" and len((await c.get("/memory")).json()) == 1
        assert (await c.post("/vault/import-file", json={"path": str(docs / "c.bmp")})).status_code == 422
        assert (await c.post("/vault/import-file", json={"path": str(docs)})).status_code == 422
        assert (await c.post("/vault/import-file", json={"path": str(docs / "nope.md")})).status_code == 422


async def test_scan_rejects_a_file_path_and_a_missing_dir(client, tmp_path: Path):
    f = tmp_path / "f.md"
    f.write_text("x", encoding="utf-8")
    assert (await client.post("/vault/scan", json={"dir": str(f)})).status_code == 422
    assert (await client.post("/vault/scan", json={"dir": str(tmp_path / "nope")})).status_code == 422


async def test_stop_route_ends_a_running_import(vault: Path, tmp_path: Path):
    import threading
    started, release = threading.Event(), threading.Event()

    class Slow(ScriptedProvider):
        async def complete(self, messages, *, tools=None, model=None, session_id=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return await super().complete(messages, tools=tools, model=model, session_id=session_id)

    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=Slow([SUMMARY_DOC] * 3), ledger=Ledger(vault), catalog=Catalog())
    docs = tmp_path / "docs"
    docs.mkdir()
    for n in "abc":
        (docs / f"{n}.md").write_text(f"# {n}", encoding="utf-8")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        token = (await c.post("/vault/scan", json={"dir": str(docs)})).json()["token"]
        import_id = (await c.post("/vault/import", json={"token": token})).json()["import_id"]
        assert (await c.post("/vault/import", json={"token": token})).status_code == 409
        await asyncio.to_thread(started.wait)
        r = await c.post(f"/vault/import/{import_id}/stop")
        assert r.status_code == 200 and r.json()["stopping"] is True
        for _ in range(200):  # the summary is still hanging: the stop must not wait for it
            st = (await c.get(f"/vault/import/{import_id}")).json()
            if st["state"] != "running":
                break
            await asyncio.sleep(0.02)
        release.set()
        assert st["state"] == "stopped" and st["done_files"] == 0 and (await c.get("/memory")).json() == []


async def test_the_import_uses_the_ingestor_the_lifespan_rebuilt(vault: Path, tmp_path: Path):
    """When the markdownify MCP connects at startup the lifespan rebuilds the
    ingestor; the importer must build its own from that one, not a stale copy."""
    import sys

    from .test_mcp import FIXTURE

    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", mcp_config=tmp_path / "mcp.json")
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"markdownify": {"command": sys.executable, "args": [str(FIXTURE)]}}}), encoding="utf-8")
    cfg.validate()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    app = create_app(h)
    async with app.router.lifespan_context(app):
        assert h.ingestor.converter.accepts(Path("photo.png"))
        ing = h.importer.make_ingestor([tmp_path])
        assert ing.converter is h.ingestor.converter
        assert ing.sync_log is h.ingestor.sync_log


async def test_scan_runs_off_the_event_loop(client, tmp_path: Path, monkeypatch):
    import threading

    from src.harness import api as api_mod

    seen: dict = {}
    real = api_mod.scan_dir

    def spy(root, accepts, **kw):
        seen["thread"] = threading.current_thread()
        return real(root, accepts, **kw)

    monkeypatch.setattr(api_mod, "scan_dir", spy)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.md").write_text("# a", encoding="utf-8")
    r = await client.post("/vault/scan", json={"dir": str(tmp_path / "d")})
    assert r.status_code == 200 and r.json()["files"] == 1
    assert seen["thread"] is not threading.main_thread()


async def test_the_harness_answers_while_a_memory_write_hangs(vault: Path, tmp_path: Path):
    """An import's store write held the memory lock; GET /memory waited on it
    from the event loop and froze every route, /health included."""
    import threading

    from src.memory.graph import SHARED, LocalGraph, MemoryGraph, Tier

    writing, release = threading.Event(), threading.Event()

    class Hangs(LocalGraph):
        def put(self, fact, targets=None) -> None:
            writing.set()
            release.wait(10)
            super().put(fact, targets)

    led = Ledger(vault)
    mem = MemoryGraph(Hangs(vault / "memory"), led)
    h = Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path), provider=ScriptedProvider([]), ledger=led,
                catalog=Catalog(), memory=mem)
    writer = threading.Thread(target=mem.remember, args=("Lee runs payroll.", SHARED, Tier.project))
    writer.start()
    try:
        assert await asyncio.to_thread(writing.wait, 5)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
            r = await asyncio.wait_for(c.get("/memory"), 2)
            assert r.status_code == 200 and r.json() == []
            assert (await asyncio.wait_for(c.get("/health"), 2)).status_code == 200
    finally:
        release.set()
        writer.join(5)


# -- the owner's profile and shared writes ------------------------------------


class FakeRouter:
    """Stands in for StoreRouter: no model call, a fixed answer, every routed text kept."""

    def __init__(self, stores=("personal",)):
        self.provider = None
        self.stores = list(stores)
        self.texts: list[str] = []

    async def route(self, text, orgs, owner):
        from src.memory.router import Route

        self.texts.append(text)
        return Route(list(self.stores))


def profiled(vault: Path, tmp_path: Path) -> Harness:
    """A harness on the local store with a real ProfileMemory (fake router) and a real queue injected."""
    from src.memory.extract_queue import ExtractQueue
    from src.memory.graph import LocalGraph, MemoryGraph
    from src.memory.profile import ProfileMemory
    from src.memory.registry import OrgRegistry, PeopleRegistry

    led = Ledger(vault)
    mem = MemoryGraph(LocalGraph(vault / "memory"), led)
    root = vault / "memory" / "hindsight"
    profile = ProfileMemory(mem, PeopleRegistry(root / "people.json"), OrgRegistry(root / "orgs.json"), FakeRouter(), led)
    queue = ExtractQueue(root / "pending.json", profile, vault, led)
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path, mcp_config=tmp_path / "no-mcp.json")
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=led, catalog=Catalog(), memory=mem, profile=profile, extract_queue=queue)
    h.add_bot(Bot(name="Research partner", role="Research things", id="bot1"))
    return h


def http(h: Harness) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost")


async def test_profile_endpoint_shape(vault: Path, tmp_path: Path, client):
    assert (await client.get("/profile")).json() == {"owner": None, "people": 0, "orgs": [], "extraction": None}  # registries, no queue
    assert (await client.get("/settings")).json()["owner"] is None
    h = profiled(vault, tmp_path)
    async with http(h) as c:
        assert (await c.get("/profile")).json() == {
            "owner": None, "people": 0, "orgs": [],
            "extraction": {"queued": 0, "done": 0, "failed": 0, "current": None, "waiting_for_owner": False},
        }
        h.profile.people.set_owner("Alex Rivera", ["Alex"])
        h.profile.people.add("Jamie Rivera", relation="wife", tier="close")
        h.profile.orgs.add("Acme Robotics", "owns")
        r = (await c.get("/profile")).json()
        assert r["owner"] == {"name": "Alex Rivera", "aliases": ["Alex"]} and r["people"] == 2
        assert r["orgs"] == [{"slug": "acme_robotics", "name": "Acme Robotics", "relation": "owns"}]
        assert (await c.get("/settings")).json()["owner"] == {"name": "Alex Rivera", "aliases": ["Alex"]}


async def test_set_owner_wakes_queue(vault: Path, tmp_path: Path, monkeypatch):
    from src.memory import extract_queue as eq
    from src.memory.graph import Tier

    monkeypatch.setattr(eq, "IDLE_WAIT", 60.0)  # only a wake can run the job in time
    h = profiled(vault, tmp_path)
    fact = h.memory.remember("Alex likes sailing.", "shared", Tier.project)
    h.extract_queue.add(eq.Job("j1", fact.id, "fact", "", None))
    app = create_app(h)
    async with app.router.lifespan_context(app), http(h) as c:
        await asyncio.sleep(0.05)
        assert (await c.get("/profile")).json()["extraction"]["waiting_for_owner"] is True
        r = await c.put("/profile/owner", json={"name": "Alex Rivera", "aliases": ["Alex", "Lex"]})
        assert r.status_code == 200 and r.json()["owner"] == {"name": "Alex Rivera", "aliases": ["Alex", "Lex"]}
        for _ in range(100):  # the local store cannot attach: once woken, the job runs and fails at once
            if not h.extract_queue.pending():
                break
            await asyncio.sleep(0.01)
        assert h.extract_queue.pending() == []
        assert (await c.put("/profile/owner", json={"name": " ", "aliases": []})).status_code == 422
    assert [e.data["event"] for e in h.ledger.tail(kind="settings")] == ["owner"]


async def test_shutdown_lets_the_extraction_in_flight_finish_then_closes(vault: Path, tmp_path: Path):
    """Quitting during a background extraction: the store being written
    finishes (the worker is not cancelled under it), the next store is never
    started, the job stays queued for the next start, and memory closes after."""
    import threading
    import time

    from src.memory.graph import LocalGraph, Tier

    events: list[str] = []
    started = threading.Event()

    class SlowStore(LocalGraph):
        kind = "slow"

        def attach(self, fact_id, text, target):
            events.append(f"start {target.store}")
            started.set()
            time.sleep(0.5)  # a store write in flight
            events.append(f"end {target.store}")

        def has_item(self, fact_id, store):
            return f"end {store}" in events

        def close(self):
            events.append("close")

    h = profiled(vault, tmp_path)
    h.memory.store = SlowStore(vault / "slow")
    h.profile.people.set_owner("Alex Rivera", [])
    fact = h.memory.remember("Alex likes sailing.", "shared", Tier.project)
    from src.memory.extract_queue import Job

    h.extract_queue.add(Job("j1", fact.id, "fact", "", ["world", "personal"]))
    app = create_app(h)
    async with app.router.lifespan_context(app):
        assert await asyncio.to_thread(started.wait, 5)
    assert events == ["start world", "end world", "close"]
    assert [j.id for j in h.extract_queue.pending()] == ["j1"]  # runs again at the next start
    assert [e.data["status"] for e in h.ledger.tail(kind="extract")] == ["queued"]  # not failed, not retried
    assert h.ledger.tail(kind="memory")[-1].data["event"] == "attach_stopped"


async def test_owner_bootstrapped_from_old_fact(vault: Path, tmp_path: Path):
    from src.memory.graph import Tier

    h = profiled(vault, tmp_path)
    h.memory.remember("The user's name is Alex Rivera.", "shared", Tier.project, entities=["Alex Rivera"], source="onboarding:name")
    app = create_app(h)
    async with app.router.lifespan_context(app):
        pass
    assert h.profile.people.owner().name == "Alex Rivera"
    async with app.router.lifespan_context(app):  # once: the owner is known now
        pass
    assert [e.data["event"] for e in h.ledger.tail(kind="settings")] == ["owner_from_fact"]


async def test_owner_is_not_bootstrapped_from_another_source(vault: Path, tmp_path: Path):
    from src.memory.graph import Tier

    h = profiled(vault, tmp_path)
    h.memory.remember("The user's name is Mallory.", "shared", Tier.project, source="vault:docs/x.md")
    app = create_app(h)
    async with app.router.lifespan_context(app):
        pass
    assert h.profile.people.owner() is None


async def test_shared_remember_goes_through_profile(vault: Path, tmp_path: Path):
    h = profiled(vault, tmp_path)
    h.profile.people.set_owner("Alex Rivera", [])
    async with http(h) as c:
        r = await c.post("/memory", json={"text": "Alex likes sailing.", "source": "user"})
        assert r.status_code == 200 and r.json()["namespace"] == "shared"
        assert h.profile.router.texts == ["Alex likes sailing."]
        assert h.extract_queue.pending() == []
        assert (await c.post("/memory", json={"text": f"key {KEY}"})).status_code == 422  # the filter still runs
    assert h.profile.router.texts == ["Alex likes sailing."]  # before routing, too
    assert [f.text for f in h.memory.facts("shared")] == ["Alex likes sailing."]


async def test_private_remember_unchanged(vault: Path, tmp_path: Path):
    h = profiled(vault, tmp_path)
    async with http(h) as c:
        r = await c.post("/memory", json={"text": "Draft in plain words.", "namespace": "bot:bot1", "tier": "private"})
        assert r.status_code == 200 and r.json()["namespace"] == "bot:bot1"
    assert h.profile.router.texts == [] and h.extract_queue.pending() == []


async def test_shared_remember_without_owner_queues(vault: Path, tmp_path: Path):
    h = profiled(vault, tmp_path)
    async with http(h) as c:
        r = await c.post("/memory", json={"text": "My wife is Jamie.", "source": "user"})
        assert r.status_code == 200
    fact_id = r.json()["id"]
    assert h.memory.get(fact_id).text == "My wife is Jamie."
    [job] = h.extract_queue.pending()
    assert (job.kind, job.fact_id, job.source, job.stores) == ("fact", fact_id, "user", None)


async def test_promotion_to_shared_goes_through_profile(vault: Path, tmp_path: Path):
    h = profiled(vault, tmp_path)
    async with http(h) as c:
        f = (await c.post("/memory", json={"text": "Acme pays net 30.", "namespace": "bot:bot1", "tier": "private",
                                           "bot_id": "bot1"})).json()
        assert (await c.post(f"/memory/{f['id']}/promote", params={"bot_id": "bot1"})).json()["confirm"] == "pending"
        assert (await c.post(f"/memory/{f['id']}/promote/confirm")).json()["confirm"] == "confirmed"
    [shared] = h.memory.facts("shared")
    assert shared.text == "Acme pays net 30." and h.profile.router.texts == []  # no owner: no routing call spent
    assert h.memory.facts("bot:bot1") == []
    [job] = h.extract_queue.pending()  # no owner yet: queued like any shared write
    assert job.fact_id == shared.id and job.kind == "fact"


def test_attaching_store_gets_a_profile_and_queue_beside_it(vault: Path, tmp_path: Path):
    """Built from the store's own root and config; a stand-in store says it is Hindsight, no server is started."""
    from types import SimpleNamespace

    from src.memory.graph import LocalGraph, MemoryGraph

    class HindsightLike(LocalGraph):
        kind = "hindsight"
        config = SimpleNamespace(llm_model="openrouter/meta/muse-spark-1.3-contributor")

        def attach(self, fact_id, text, target):  # the queue exists on a store that can attach
            pass

    led = Ledger(vault)
    mem = MemoryGraph(HindsightLike(vault / "memory"), led)
    h = Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path), provider=ScriptedProvider(), ledger=led, catalog=Catalog(), memory=mem)
    root = vault / "memory" / "hindsight"
    assert h.profile.people.path == root / "people.json" and h.profile.orgs.path == root / "orgs.json"
    assert h.extract_queue.path == root / "pending.json" and h.extract_queue.vault_root == vault
    router = h.profile.router
    assert router.model == "meta/muse-spark-1.3-contributor" and router.chunk_chars == h.ingestor.generator.chunk_chars
    assert router.provider is h.provider and h.ingestor.extract_queue is h.extract_queue and h.recall.profile is h.profile
    assert h.importer.make_ingestor([tmp_path]).extract_queue is h.extract_queue

    profile, queue = h.profile, h.extract_queue
    h.rebind(ScriptedProvider(), None)
    assert h.profile is profile and h.extract_queue is queue and router.provider is h.provider
    assert h.ingestor.extract_queue is queue and h.recall.profile is profile


def _hindsight_harness(vault: Path, tmp_path: Path):
    from src.memory.graph import MemoryGraph
    from src.memory.hindsight_http import HindsightHTTP
    from src.memory.hindsight_store import HindsightGraph

    def handle(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    store = HindsightGraph(vault / "memory", http=HindsightHTTP("http://hs", client=httpx.Client(transport=httpx.MockTransport(handle))))
    led = Ledger(vault)
    return Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path), provider=ScriptedProvider(), ledger=led,
                   catalog=Catalog(), memory=MemoryGraph(store, led)), store, led


def test_hindsight_store_gets_an_extraction_queue_and_an_owner_source(vault: Path, tmp_path: Path):
    from types import SimpleNamespace

    h, store, _ = _hindsight_harness(vault, tmp_path)
    root = vault / "memory" / "hindsight"
    assert h.profile.people.path == root / "people.json" and h.extract_queue is not None
    assert h.extract_queue.path == root / "pending.json"
    assert h.profile.router.model == h.config.summary_model  # a store without a config
    assert store.owner_source() is None
    h.profile.people.set_owner("Alex Rivera", ["Alex"])
    assert store.owner_source().name == "Alex Rivera"  # the live owner, not a copy
    store.config = SimpleNamespace(llm_model="openrouter/meta/x", pinned=True)
    assert h._profile_memory(100)[0].router.model == "meta/x"
    store.config = SimpleNamespace(llm_model="meta/y", pinned=True)
    assert h._profile_memory(100)[0].router.model == "meta/y"


def test_hindsight_store_rereads_a_document_body_from_its_vault_copy(vault: Path, tmp_path: Path):
    from src.memory.graph import SHARED, Fact, Tier

    _, store, _ = _hindsight_harness(vault, tmp_path)
    (vault / "docs").mkdir(exist_ok=True)
    (vault / "docs" / "plan.md").write_text("# the plan", encoding="utf-8")
    doc = Fact(text="summary", namespace=SHARED, tier=Tier.project, source="vault:docs/plan.md")
    assert store.text_source(doc) == "# the plan"
    assert store.text_source(Fact(text="s", namespace=SHARED, tier=Tier.project, source="vault:docs/gone.md")) is None
    assert store.text_source(Fact(text="s", namespace=SHARED, tier=Tier.project, source="vault:../outside.md")) is None
    assert store.text_source(Fact(text="s", namespace=SHARED, tier=Tier.project, source="session:s1")) is None


def test_registries_carry_over_from_cognee_once(vault: Path, tmp_path: Path):
    old = vault / "memory" / "cognee"  # the old cognee data folder
    old.mkdir(parents=True)
    (old / "people.json").write_text('{"people": [{"name": "Alex Rivera", "aliases": [], "is_owner": true}]}', encoding="utf-8")
    (old / "orgs.json").write_text('{"orgs": []}', encoding="utf-8")
    (old / "pending.json").write_text('{"jobs": []}', encoding="utf-8")
    h, _, led = _hindsight_harness(vault, tmp_path)
    new = vault / "memory" / "hindsight"
    assert (new / "people.json").read_text(encoding="utf-8") == (old / "people.json").read_text(encoding="utf-8")
    assert (new / "orgs.json").exists() and not (new / "pending.json").exists()
    assert h.profile.people.owner().name == "Alex Rivera"
    carried = [e for e in led.iter() if e.kind == "settings" and e.data.get("event") == "registry_carried_over"]
    assert len(carried) == 2
    (new / "people.json").write_text('{"people": []}', encoding="utf-8")  # edited since: never overwritten
    h2, _, led2 = _hindsight_harness(vault, tmp_path)
    assert (new / "people.json").read_text(encoding="utf-8") == '{"people": []}'
    assert len([e for e in led2.iter() if e.data.get("event") == "registry_carried_over"]) == 2  # nothing new was carried


def test_local_store_has_registries_but_no_extract_queue(harness: Harness, vault: Path):
    assert harness.profile is not None and harness.extract_queue is None
    assert harness.ingestor.extract_queue is None and harness.recall.profile is harness.profile
    assert harness.profile.people.path == vault / "memory" / "local" / "people.json"
    assert harness.profile.orgs.path == vault / "memory" / "local" / "orgs.json"


async def test_local_profile_owner_can_be_set(client):
    r = await client.put("/profile/owner", json={"name": "Alex Rivera", "aliases": ["Alex"]})
    assert r.status_code == 200 and r.json()["owner"] == {"name": "Alex Rivera", "aliases": ["Alex"]}


async def test_local_shared_memory_write_needs_no_owner_and_no_routing(client, harness: Harness):
    r = await client.post("/memory", json={"text": "Acme sells widgets.", "namespace": "shared", "tier": "project", "entities": ["Acme"]})
    assert r.status_code == 200
    assert harness.provider.calls == []  # no routing call on the local backend
    assert [f.text for f in harness.memory.facts("shared")] == ["Acme sells widgets."]


# -- the knowledge page: Hindsight's control plane ------------------------------


class UiStore:
    """A memory store that reports on a control plane, as HindsightGraph does."""

    def __init__(self, status):
        self.status = status

    def ui_status(self):
        return self.status


async def test_knowledge_ui_reports_the_store_status(vault: Path, tmp_path: Path, monkeypatch):
    h = profiled(vault, tmp_path)
    for status in ({"url": "http://127.0.0.1:9999", "state": "ready", "reason": ""},
                   {"url": "", "state": "unavailable", "reason": "npx was not found"}):
        monkeypatch.setattr(h.memory, "store", UiStore(status))
        async with http(h) as c:
            r = await c.get("/knowledge/ui")
        assert r.status_code == 200 and r.json() == status


async def test_knowledge_ui_without_a_control_plane(client):
    r = await client.get("/knowledge/ui")  # the local store has no ui_status
    assert r.status_code == 200
    assert r.json() == {"url": "", "state": "unavailable", "reason": "this memory store has no control plane"}


async def test_knowledge_graph_is_gone(client):
    assert (await client.get("/knowledge/graph")).status_code == 404


# -- rebuilding shared memory ---------------------------


def rebuildable(vault: Path, tmp_path: Path, monkeypatch) -> tuple[Harness, list[int], dict[str, str]]:
    """A profiled harness holding one shared fact of each source kind and a
    private one; `drop_shared_datasets` is faked and records how many jobs
    were queued when it ran."""
    from src.memory.graph import Tier

    h = profiled(vault, tmp_path)
    h.profile.people.set_owner("Alex Rivera", [])
    (vault / "docs").mkdir()
    (vault / "docs" / "cv.md").write_text("x" * 13000, encoding="utf-8")  # 3 chunks + 1 routing call
    ids = {
        "onboarding": h.memory.remember("Alex likes sailing.", "shared", Tier.project, source="onboarding:interests").id,
        "vault": h.memory.remember("Alex's CV. (vault:docs/cv.md)", "shared", Tier.project, source="vault:docs/cv.md").id,
        "gone": h.memory.remember("A note whose copy is gone.", "shared", Tier.project, source="vault:docs/gone.md").id,
        "user": h.memory.remember("Acme pays net 30.", "shared", Tier.project, source="user").id,
    }
    h.memory.remember("only bot1 knows", "bot:bot1", Tier.private, bot_id="bot1")
    drops: list[int] = []

    def drop() -> int:
        drops.append(len(h.extract_queue.pending()))
        return 3

    monkeypatch.setattr(h.memory.store, "drop_shared_datasets", drop, raising=False)
    return h, drops, ids


async def test_rebuild_preview_counts_shared_only(vault: Path, tmp_path: Path, monkeypatch):
    h, drops, _ = rebuildable(vault, tmp_path, monkeypatch)
    async with http(h) as c:
        r = await c.get("/memory/rebuild")
    # onboarding 1 (pinned to personal, no routing); the vault copy 3 + 1; a missing copy and a plain fact 1 + 1 each
    assert r.status_code == 200 and r.json() == {"facts": 4, "extraction_calls": 1 + 4 + 2 + 2}
    assert drops == [] and h.extract_queue.pending() == []  # a preview changes nothing


async def test_rebuild_queues_by_source(vault: Path, tmp_path: Path, monkeypatch):
    h, drops, ids = rebuildable(vault, tmp_path, monkeypatch)
    async with http(h) as c:
        r = await c.post("/memory/rebuild")
    assert r.status_code == 200 and r.json() == {"queued": 4}
    assert drops == [0]  # the datasets went before anything was queued
    jobs = {j.fact_id: j for j in h.extract_queue.pending()}
    assert set(jobs) == set(ids.values())
    assert (jobs[ids["onboarding"]].kind, jobs[ids["onboarding"]].stores) == ("fact", ["personal"])
    assert (jobs[ids["vault"]].kind, jobs[ids["vault"]].source, jobs[ids["vault"]].stores) == ("doc", "docs/cv.md", None)
    assert (jobs[ids["gone"]].kind, jobs[ids["gone"]].stores) == ("fact", None)  # no copy to read: the fact's own text
    assert (jobs[ids["user"]].kind, jobs[ids["user"]].stores) == ("fact", None)
    [entry] = h.ledger.tail(kind="rebuild")
    assert entry.data == {"datasets": 3, "queued": 4, "extraction_calls": 9}
    assert len(h.memory.facts("shared")) == 4  # the facts themselves stay


async def test_rebuild_drops_under_the_memory_write_lock(vault: Path, tmp_path: Path, monkeypatch):
    """No shared write can land in a dataset while it is being dropped."""
    import threading

    h, _, _ = rebuildable(vault, tmp_path, monkeypatch)
    held: list[bool] = []

    def drop() -> int:
        def probe() -> None:
            got = h.memory._write.acquire(timeout=0)
            if got:
                h.memory._write.release()
            held.append(not got)

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        return 3

    monkeypatch.setattr(h.memory.store, "drop_shared_datasets", drop, raising=False)
    async with http(h) as c:
        assert (await c.post("/memory/rebuild")).status_code == 200
    assert held == [True]


async def test_rebuild_refuses_while_running(vault: Path, tmp_path: Path, monkeypatch):
    from src.memory import extract_queue as eq

    h, drops, _ = rebuildable(vault, tmp_path, monkeypatch)
    async with http(h) as c:
        assert (await c.post("/memory/rebuild")).status_code == 200
        r = await c.post("/memory/rebuild")
        assert r.status_code == 409 and "4 extraction jobs" in r.json()["detail"]
        assert (await c.get("/memory/rebuild")).status_code == 200  # the preview still answers
        for job in h.extract_queue.pending():  # the rebuild's jobs finished; an import's job is still queued
            h.extract_queue._drop(job)
        h.extract_queue.add(eq.new_job("doc", "f1", "docs/cv.md"))
        assert (await c.post("/memory/rebuild")).status_code == 409
    assert drops == [0] and len(h.ledger.tail(kind="rebuild")) == 1


async def test_rebuild_refuses_without_the_owner_name(vault: Path, tmp_path: Path, monkeypatch):
    """Every rebuild job waits for the owner's name: without it the graph would be cleared and never refilled."""
    h, drops, _ = rebuildable(vault, tmp_path, monkeypatch)
    h.profile.people = type(h.profile.people)(vault / "memory" / "hindsight" / "nobody.json")  # no owner
    async with http(h) as c:
        r = await c.post("/memory/rebuild")
    assert r.status_code == 409 and r.json()["detail"] == "Add your name in Settings first: the rebuild re-learns your people under it"
    assert drops == [] and h.extract_queue.pending() == [] and h.ledger.tail(kind="rebuild") == []


async def test_rebuild_queued_jobs_waiting_for_the_name_get_the_name_advice(vault: Path, tmp_path: Path, monkeypatch):
    from src.memory import extract_queue as eq

    h, drops, ids = rebuildable(vault, tmp_path, monkeypatch)
    h.profile.people = type(h.profile.people)(vault / "memory" / "hindsight" / "nobody.json")  # no owner
    h.extract_queue.add(eq.new_job("fact", ids["user"], "user"))  # waits for the name, so extraction never finishes
    async with http(h) as c:
        r = await c.post("/memory/rebuild")
    assert r.status_code == 409 and r.json()["detail"].startswith("Add your name in Settings first")
    assert drops == []


async def test_rebuild_failure_is_a_502(vault: Path, tmp_path: Path, monkeypatch):
    h, _, _ = rebuildable(vault, tmp_path, monkeypatch)

    def broken() -> int:
        raise RuntimeError("lancedb went away")

    monkeypatch.setattr(h.memory.store, "drop_shared_datasets", broken, raising=False)
    async with http(h) as c:
        r = await c.post("/memory/rebuild")
    assert r.status_code == 502 and "rebuild again to finish" in r.json()["detail"]
    assert h.extract_queue.pending() == []


async def test_rebuild_needs_the_extraction_store(client):
    for r in (await client.get("/memory/rebuild"), await client.post("/memory/rebuild")):
        assert r.status_code == 503 and "Hindsight memory store" in r.json()["detail"]


async def test_rebuild_timeout_is_a_504(vault: Path, tmp_path: Path, monkeypatch):
    from src.memory.hindsight_http import HindsightTimeout

    h, _, _ = rebuildable(vault, tmp_path, monkeypatch)

    def slow() -> int:
        raise HindsightTimeout("hindsight did not finish within 600 s")

    monkeypatch.setattr(h.memory.store, "drop_shared_datasets", slow, raising=False)
    async with http(h) as c:
        r = await c.post("/memory/rebuild")
    assert r.status_code == 504 and h.extract_queue.pending() == []  # nothing queued: a retry drops what is left


def test_approval_store_restore_keeps_id_and_state():
    from src.harness.approvals import ApprovalRequest, ApprovalStore
    store = ApprovalStore()
    req = ApprovalRequest("b", "", "t", "d", "learn:loose", "learn_page", id="abc123abc123", created_at="2026-11-07T00:00:00+00:00")
    assert store.restore(req) is req and store.get("abc123abc123") is req
    assert store.restore(ApprovalRequest("b", "", "x", "y", "r", "t", id="abc123abc123")) is req
    assert store.decide("abc123abc123", True).state == "approved"


# -- learn from a crawl: the tool on the harness, decisions on holds, holds across a restart ----------


async def test_learn_page_is_a_built_in_tool(harness: Harness):
    spec = next(s for s in harness.tools.specs(set()) if s.name == "learn_page")
    assert spec.parameters["required"] == ["url", "markdown"]


async def test_approving_a_learn_hold_writes_it_and_denying_remembers_the_name(client, harness: Harness):
    from .test_learn import PAGE

    harness.profile.orgs.add("Northwind Labs", "works_at")
    harness.provider.script[:] = [PAGE, PAGE, PAGE]
    await harness.learner.learn("https://northwindlabs.example/team", "page")
    await harness.learner.drain()
    (req,) = harness.approvals.list("pending")
    r = await client.post(f"/approvals/{req.id}/decide", json={"approved": True})
    assert r.status_code == 200 and r.json()["state"] == "approved"
    assert harness.learner.holds.pending()  # the hold leaves the store only once its writes land
    await harness.learner.drain()
    assert any(f.text == "Jane studied physics." for f in harness.memory.facts("shared"))
    assert harness.learner.holds.pop(req.id) is None and harness.learner.log.get("https://northwindlabs.example/team").approved_fact_ids
    out2 = json.loads(await harness.learner.learn("https://northwindlabs.example/about", "page"))  # Jane is known now: approved names anchor
    await harness.learner.drain()
    assert out2["held"] == [] and "Jane Doe" in out2["matched"]
    from .test_learn import PAGE_ACME
    harness.provider.script[:] = [PAGE_ACME, PAGE_ACME]
    await harness.learner.learn("https://northwindlabs.example/partners", "page")
    (req2,) = harness.approvals.list("pending")
    await client.post(f"/approvals/{req2.id}/decide", json={"approved": False})
    assert harness.learner.log.is_denied("northwindlabs.example", "acme")
    out3 = json.loads(await harness.learner.learn("https://northwindlabs.example/press", "page"))
    assert out3["held"] == [] and harness.approvals.list("pending") == []
    await harness.learner.drain()
    events = [e.data.get("event") for e in harness.ledger.tail(0, kind="learn")]
    assert "held_written" in events and "held_denied" in events


async def test_decide_on_a_hold_without_a_payload_is_ledgered(client, harness: Harness):
    req = harness.approvals.create("bot1", "", "Remember X (thing) from h?", "d", "learn:loose", "learn_page")
    r = await client.post(f"/approvals/{req.id}/decide", json={"approved": True})
    assert r.status_code == 200
    assert any(e.data.get("event") == "held_missing" for e in harness.ledger.tail(0, kind="learn"))


async def test_pending_learn_holds_survive_a_restart(vault: Path, harness: Harness):
    from .test_learn import PAGE

    harness.profile.orgs.add("Northwind Labs", "works_at")
    harness.provider.script[:] = [PAGE]
    await harness.learner.learn("https://northwindlabs.example/team", "page")
    await harness.learner.drain()
    (req,) = harness.approvals.list("pending")
    fresh = Harness(config=harness.config, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    assert fresh.learner.restore() == 1
    assert fresh.approvals.get(req.id).title == req.title and fresh.approvals.get(req.id).state == "pending"
    async with http(fresh) as c:
        assert [a["id"] for a in (await c.get("/approvals")).json()] == [req.id]


async def test_rebind_keeps_learn_page_and_follows_the_provider(harness: Harness):
    fresh = ScriptedProvider()
    harness.rebind(fresh, None)
    assert "learn_page" in harness.tools.names() and harness.learner.generator.provider is fresh


async def test_rebind_keeps_the_tools_the_mcp_servers_and_music_registered(harness: Harness):
    # the MCP registry and the music service hold the ToolRunner they registered into; a rebind (a provider
    # or model change in Settings) must not swap it for an empty one, or their tools vanish from every bot
    tools = harness.tools
    tools.register("pexels.photos_search", "find", {"type": "object", "properties": {}}, lambda: "ok", group="pexels")
    harness.rebind(ScriptedProvider(), None)
    assert harness.tools is tools and harness.music.tools is tools and harness.loop.tools is tools
    assert "pexels.photos_search" in harness.tools.names() and "learn_page" in harness.tools.names()


# -- conversations ----------------------------------------------------------


async def test_a_second_message_sees_the_first(client, harness: Harness):
    harness.provider.script = [completion("a1"), completion("a2")]
    s1 = (await client.post("/bots/bot1/tasks", json={"task": "q1"})).json()["session_id"]
    assert (await wait_for(client, s1, {"done"}))["result"] == "a1"
    s2 = (await client.post("/bots/bot1/tasks", json={"task": "q2"})).json()["session_id"]
    await wait_for(client, s2, {"done"})
    turns = [(m.role, m.content) for m in harness.provider.calls[-1] if m.role in ("user", "assistant")]
    assert turns[-3:] == [("user", "q1"), ("assistant", "a1"), ("user", "q2")]
    conv = harness.conversations.current("bot1")
    assert [(e.user, e.outcome, e.reply) for e in conv.exchanges] == [("q1", "done", "a1"), ("q2", "done", "a2")]


async def test_both_tasks_carry_the_conversation_id(client, harness: Harness):
    harness.provider.script = [completion("a1"), completion("a2")]
    s1 = (await client.post("/bots/bot1/tasks", json={"task": "q1"})).json()["session_id"]
    await wait_for(client, s1, {"done"})
    s2 = (await client.post("/bots/bot1/tasks", json={"task": "q2"})).json()["session_id"]
    await wait_for(client, s2, {"done"})
    cid = harness.conversations.current("bot1").id
    assert harness.provider.session_ids == [cid, cid]
    assert harness.sessions[s1].conversation_id == harness.sessions[s2].conversation_id == cid


async def test_a_named_conversation_is_used(client, harness: Harness):
    first = harness.conversations.start_new("bot1")
    harness.conversations.start_new("bot1")  # now current
    harness.provider.script = [completion("a1")]
    r = await client.post("/bots/bot1/tasks", json={"task": "q1", "conversation_id": first.id})
    await wait_for(client, r.json()["session_id"], {"done"})
    assert [e.user for e in first.exchanges] == ["q1"]
    assert harness.provider.session_ids == [first.id]


async def test_a_routine_task_joins_no_conversation(client, harness: Harness):
    harness.provider.script = [completion("did it")]
    sid = (await client.post("/bots/bot1/tasks", json={"task": "routine", "conversation": False})).json()["session_id"]
    await wait_for(client, sid, {"done"})
    assert harness.conversations.for_owner("bot1") == []
    assert harness.provider.session_ids == [None]
    assert harness.sessions[sid].conversation_id == ""


async def test_a_stopped_task_records_its_outcome(client, harness: Harness):
    harness.bots["bot1"].attach_skill(harness.catalog.skill("research-brief"))  # its rules gate git push behind approval
    harness.provider.script = [completion(calls=[("c1", "shell", {"command": "git push origin main"})])]
    sid = (await client.post("/bots/bot1/tasks", json={"task": "push it"})).json()["session_id"]
    await wait_for(client, sid, {"waiting_approval"})
    await client.post(f"/sessions/{sid}/stop")
    await wait_for(client, sid, {"stopped"})
    try:
        await harness.tasks[sid]
    except asyncio.CancelledError:
        pass
    ex = harness.conversations.current("bot1").exchanges[-1]
    assert (ex.session_id, ex.outcome, ex.reply) == (sid, "stopped", "")


async def test_a_failed_task_records_its_error(client, harness: Harness):
    harness.provider.script = []  # the scripted provider raises when it runs out
    sid = (await client.post("/bots/bot1/tasks", json={"task": "go"})).json()["session_id"]
    s = await wait_for(client, sid, {"failed"})
    ex = harness.conversations.current("bot1").exchanges[-1]
    assert (ex.outcome, ex.reply) == ("failed", s["error"])


async def test_scope_reject_carries_the_protected_block(client, harness: Harness):
    harness.provider.script = [completion("shorter X")]
    protected = {"task": "write X", "constraints": ["refine: shorter"]}
    sid = (await client.post("/bots/bot1/tasks", json={"task": "shorter", "protected": protected})).json()["session_id"]
    s = await wait_for(client, sid, {"done"})
    assert s["protected"] == protected
    assert harness.conversations.current("bot1").exchanges[-1].user == "shorter"


async def test_a_bad_protected_block_or_conversation_is_refused(client, harness: Harness):
    assert (await client.post("/bots/bot1/tasks", json={"task": "x", "protected": {"goal": "y"}})).status_code == 422
    assert (await client.post("/bots/bot1/tasks", json={"task": "x", "conversation_id": "nope"})).status_code == 404
    assert harness.sessions == {}


async def test_a_task_that_breaks_before_the_loop_records_failed(client, harness: Harness, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("selector down")

    monkeypatch.setattr(harness.loop.selector, "select", boom)
    sid = (await client.post("/bots/bot1/tasks", json={"task": "go"})).json()["session_id"]
    s = await wait_for(client, sid, {"failed"})
    assert "selector down" in s["error"]
    ex = harness.conversations.current("bot1").exchanges[-1]
    assert ex.outcome == "failed" and "selector down" in ex.reply


async def _ask(client, harness: Harness, text: str, bot: str = "bot1") -> str:
    harness.provider.script = [completion("re: " + text)]
    sid = (await client.post(f"/bots/{bot}/tasks", json={"task": text})).json()["session_id"]
    await wait_for(client, sid, {"done"})
    return sid


async def test_list_new_and_select_conversations(client, harness: Harness):
    await _ask(client, harness, "first")
    first = harness.conversations.current("bot1")
    new = (await client.post("/bots/bot1/conversations")).json()
    assert new["exchanges"] == 0 and set(new) == {"id", "title", "created_at", "updated_at", "exchanges"}
    await _ask(client, harness, "second")
    listing = (await client.get("/bots/bot1/conversations")).json()
    assert listing["current"] == new["id"]
    assert {c["id"] for c in listing["conversations"]} == {first.id, new["id"]}
    assert listing["conversations"][0]["id"] == new["id"]
    rec = (await client.post(f"/bots/bot1/conversations/{first.id}/current")).json()
    assert rec["id"] == first.id and [e["user"] for e in rec["exchanges"]] == ["first"]
    assert (await client.get("/bots/bot1/conversations")).json()["current"] == first.id
    assert (await client.get(f"/conversations/{new['id']}")).json()["exchanges"][0]["reply"] == "re: second"


async def test_recent_conversations_across_bots(client, harness: Harness):
    harness.add_bot(Bot(name="Writer", role="Write things", id="bot2"))
    await _ask(client, harness, "oldest")
    oldest = harness.conversations.current("bot1")
    harness.conversations.start_new("bot1")
    await _ask(client, harness, "middle", bot="bot2")
    await _ask(client, harness, "newest")
    harness.conversations.start_new("bot2")  # empty: not listed
    # times are to the second, so pin them: the order under test is by last activity, not by creation
    newest, middle = harness.conversations.current("bot1"), harness.conversations.for_owner("bot2")[-1]
    for c, t in ((oldest, "09:00"), (middle, "10:00"), (newest, "11:00")):
        c.updated_at = f"2026-11-15T{t}:00+00:00"

    recent = (await client.get("/conversations")).json()["conversations"]
    assert [(c["owner"], c["title"]) for c in recent] == [("bot1", "newest"), ("bot2", "middle"), ("bot1", "oldest")]
    assert recent[-1]["id"] == oldest.id and recent[0]["exchanges"] == 1
    capped = (await client.get("/conversations", params={"limit": 2})).json()["conversations"]
    assert [c["title"] for c in capped] == ["newest", "middle"]


async def test_recent_conversations_skip_removed_bots(client, harness: Harness):
    harness.add_bot(Bot(name="Writer", role="Write things", id="bot2"))
    await _ask(client, harness, "kept")
    await _ask(client, harness, "gone", bot="bot2")
    harness.bots.pop("bot2")
    assert [c["title"] for c in (await client.get("/conversations")).json()["conversations"]] == ["kept"]


async def test_conversations_are_redacted(client, harness: Harness):
    from src.memory.exclusion import exclusion

    exclusion.register_secret_value("hunter2222")
    await _ask(client, harness, "my secret hunter2222")
    cid = harness.conversations.current("bot1").id
    assert "hunter2222" not in (await client.get(f"/conversations/{cid}")).text


async def test_unknown_bot_conversations_404(client):
    assert (await client.get("/bots/nope/conversations")).status_code == 404
    assert (await client.post("/bots/nope/conversations")).status_code == 404
    assert (await client.post("/bots/nope/conversations/x/current")).status_code == 404
    assert (await client.get("/conversations/nope")).status_code == 404


async def test_selecting_another_bots_conversation_404(client, harness: Harness):
    await _ask(client, harness, "mine")
    cid = harness.conversations.current("bot1").id
    harness.add_bot(Bot(name="Other", role="Other", id="bot2"))
    other = "bot2"
    assert (await client.post(f"/bots/{other}/conversations/{cid}/current")).status_code == 404
    assert (await client.post("/bots/bot1/conversations/nope/current")).status_code == 404


async def _attach_task(client, harness: Harness, tmp_path: Path) -> tuple[str, str]:
    (tmp_path / "flow.html").write_text("<svg>flow</svg>", encoding="utf-8")
    harness.provider.script = [
        completion(calls=[("c1", "attach_file", {"path": "flow.html", "title": "Flow"})]),
        completion("Here it is."),
    ]
    sid = (await client.post("/bots/bot1/tasks", json={"task": "draw the flow"})).json()["session_id"]
    await wait_for(client, sid, {"done"})
    await harness.tasks[sid]  # the exchange is finished in the task's finally
    return sid, harness.conversations.current("bot1").id


async def _attach_design(client, harness: Harness, bot_id: str) -> tuple[str, dict]:
    f = harness.config.vault_root / "designs" / "landing" / "index.html"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("<p>one</p>", encoding="utf-8")
    harness.provider.script = [
        completion(calls=[("c1", "attach_file", {"path": "designs/landing/index.html", "title": "Landing"})]),
        completion("Here it is."),
    ]
    sid = (await client.post(f"/bots/{bot_id}/tasks", json={"task": "design it"})).json()["session_id"]
    await wait_for(client, sid, {"done"})
    await harness.tasks[sid]
    f.write_text("<p>two</p>", encoding="utf-8")
    cid = harness.conversations.current(bot_id).id
    att = (await client.get(f"/conversations/{cid}")).json()["exchanges"][-1]["attachments"][0]
    return cid, att


async def test_studio_attach_keeps_a_numbered_copy(client, harness: Harness):
    harness.add_bot(Bot(name="Designer", role="Designs", id="des", studio=True))
    cid, att = await _attach_design(client, harness, "des")
    assert {k: att[k] for k in ("design", "version", "name")} == {"design": "landing", "version": 1, "name": "index.html"}
    assert (await client.get(f"/conversations/{cid}/attachments/{att['id']}")).text == "<p>one</p>"


async def test_non_studio_attach_serves_the_live_file(client, harness: Harness, tmp_path: Path):
    # a non-studio bot's relative path still resolves against the working folder
    wf = tmp_path / "designs" / "landing" / "index.html"
    wf.parent.mkdir(parents=True)
    wf.write_text("<p>work</p>", encoding="utf-8")
    harness.provider.script = [
        completion(calls=[("c1", "attach_file", {"path": "designs/landing/index.html", "title": "Landing"})]),
        completion("Here it is."),
    ]
    sid = (await client.post("/bots/bot1/tasks", json={"task": "show it"})).json()["session_id"]
    await wait_for(client, sid, {"done"})
    await harness.tasks[sid]
    cid = harness.conversations.current("bot1").id
    att = (await client.get(f"/conversations/{cid}")).json()["exchanges"][-1]["attachments"][0]
    assert "version" not in att and att["name"] == "index.html"
    wf.write_text("<p>live</p>", encoding="utf-8")
    assert (await client.get(f"/conversations/{cid}/attachments/{att['id']}")).text == "<p>live</p>"
    assert not (harness.config.vault_root / "designs").exists()


async def test_one_to_one_attach_shows_on_the_exchange_and_serves(client, harness: Harness, tmp_path: Path):
    _, cid = await _attach_task(client, harness, tmp_path)
    r = await client.get(f"/conversations/{cid}")
    atts = r.json()["exchanges"][-1]["attachments"]
    assert atts == [{"id": atts[0]["id"], "title": "Flow", "name": "flow.html"}]
    assert str(tmp_path) not in r.text and '"path"' not in r.text
    f = await client.get(f"/conversations/{cid}/attachments/{atts[0]['id']}")
    assert f.status_code == 200 and f.text == "<svg>flow</svg>"
    assert f.headers["content-security-policy"] == "sandbox allow-scripts"
    assert f.headers["x-content-type-options"] == "nosniff"
    assert (await client.get(f"/conversations/{cid}/attachments/nope")).status_code == 404
    assert (await client.get(f"/conversations/nope/attachments/{atts[0]['id']}")).status_code == 404


async def test_one_to_one_attachment_gone_is_404(client, harness: Harness, tmp_path: Path):
    _, cid = await _attach_task(client, harness, tmp_path)
    att = (await client.get(f"/conversations/{cid}")).json()["exchanges"][-1]["attachments"][0]
    (tmp_path / "flow.html").unlink()
    assert (await client.get(f"/conversations/{cid}/attachments/{att['id']}")).status_code == 404


async def test_attach_refused_outside_the_workspace_and_for_routines(harness: Harness, tmp_path: Path):
    from src.harness.loop import Session

    (tmp_path / "x.html").write_text("x", encoding="utf-8")
    routine = Session(bot_id="bot1", task="t")
    harness.sessions[routine.id] = routine
    assert harness.attach(routine.id, "x.html", "") == "attach_file works only in a conversation or a room turn."
    assert harness.attach("missing", "x.html", "") == "attach_file works only in a conversation or a room turn."
    chat = Session(bot_id="bot1", task="t")
    chat.conversation_id = "c1"
    harness.sessions[chat.id] = chat
    assert "outside" in harness.attach(chat.id, "../x.html", "")
    assert harness.attach(chat.id, "x.html", "") == "Attached 'x.html'; it will show under your reply."


async def test_attachment_preview_is_served_network_closed(client, harness: Harness, tmp_path: Path):
    """A diagram thumbnail runs on its own as the reply renders, so its page may reach no network:
    a bot steered by a hostile page cannot send conversation details out through an <img>."""
    _, cid = await _attach_task(client, harness, tmp_path)
    att = (await client.get(f"/conversations/{cid}")).json()["exchanges"][-1]["attachments"][0]
    f = await client.get(f"/conversations/{cid}/attachments/{att['id']}?preview=1")
    csp = f.headers["content-security-policy"]
    assert f.status_code == 200 and csp.startswith("sandbox allow-scripts;")
    assert "default-src 'none'" in csp and "connect-src" not in csp.replace("default-src 'none'", "")
    assert "http" not in csp  # no origin is named: nothing loads from anywhere
    full = await client.get(f"/conversations/{cid}/attachments/{att['id']}")
    assert full.headers["content-security-policy"] == "sandbox allow-scripts"  # View, on the user's click


async def test_studio_attachment_gets_the_bridge_under_the_preview_policy(client, harness: Harness, tmp_path: Path):
    _, cid = await _attach_task(client, harness, tmp_path)
    att = (await client.get(f"/conversations/{cid}")).json()["exchanges"][-1]["attachments"][0]
    f = await client.get(f"/conversations/{cid}/attachments/{att['id']}?studio=1")
    assert f.status_code == 200 and f.headers["content-security-policy"] == PREVIEW_CSP
    assert f.headers["x-content-type-options"] == "nosniff" and f.text.count("data-zen-bridge") == 1
    plain = await client.get(f"/conversations/{cid}/attachments/{att['id']}")
    assert "data-zen-bridge" not in plain.text and plain.text == "<svg>flow</svg>"
    assert plain.headers["content-security-policy"] == "sandbox allow-scripts"


async def test_studio_attachment_over_the_cap_is_served_unchanged(client, harness: Harness, tmp_path: Path):
    _, cid = await _attach_task(client, harness, tmp_path)
    att = (await client.get(f"/conversations/{cid}")).json()["exchanges"][-1]["attachments"][0]
    big = b"<p>" + b"x" * BRIDGE_MAX_BYTES
    Path(harness.conversations.attachment(cid, att["id"])["path"]).write_bytes(big)
    f = await client.get(f"/conversations/{cid}/attachments/{att['id']}?studio=1")
    assert f.content == big and f.headers["content-security-policy"] == PREVIEW_CSP


ROUTINE = {"name": "Digest", "bot": "bot1", "skill": "research-brief",
           "schedule": {"kind": "weekly", "days": [4], "time": "09:00"}}


def attach(harness, bot="bot1", slug="research-brief"):
    harness.bots[bot].attach_skill(harness.catalog.skill(slug))


async def test_routines_crud(client, harness):
    attach(harness)
    r = await client.post("/routines", json=ROUTINE)
    assert r.status_code == 201
    rid = r.json()["id"]
    assert r.json()["next_run"] and r.json()["last_run"] is None
    assert [x["id"] for x in (await client.get("/routines")).json()] == [rid]
    r = await client.put(f"/routines/{rid}", json={**ROUTINE, "name": "Digest 2", "enabled": False})
    assert r.json()["name"] == "Digest 2" and r.json()["next_run"] is None
    assert (await client.post("/routines", json={**ROUTINE, "schedule": {"kind": "daily", "time": "25:00"}})).status_code == 422
    assert (await client.post("/routines", json={**ROUTINE, "schedule": {"kind": "interval", "hours": 0}})).status_code == 422
    assert (await client.put("/routines/nope", json=ROUTINE)).status_code == 404
    assert (await client.delete(f"/routines/{rid}")).status_code == 204
    assert (await client.get("/routines")).json() == []
    assert (await client.delete(f"/routines/{rid}")).status_code == 404
    # saved with the harness settings, not in the vault
    assert (harness.config.vault_root.parent / "vault-settings" / "routines.json").exists()


async def test_run_now_pins_skill(client, harness):
    harness.bots["bot1"].attach_skill(harness.catalog.skill("research-brief"))
    use = harness.catalog.skill("research-brief").use
    rid = (await client.post("/routines", json=ROUTINE)).json()["id"]
    r = await client.post(f"/routines/{rid}/run")
    assert r.status_code == 200
    sid = r.json()["session_id"]
    s = harness.sessions[sid]
    assert s.pinned_skills == ["research-brief"] and s.task == f"Digest: {use}" and not s.conversation_id
    assert r.json()["routine"]["last_run"]["outcome"] == "started"
    await asyncio.gather(*harness.tasks.values(), return_exceptions=True)


async def test_run_now_errors_are_clear(client, harness):
    attach(harness)
    rid = (await client.post("/routines", json=ROUTINE)).json()["id"]
    harness.bots["bot1"].detach_skill("research-brief")  # detached since
    r = await client.post(f"/routines/{rid}/run")
    assert r.status_code == 409 and "research-brief" in r.json()["detail"]
    assert (await client.get("/routines")).json()[0]["last_run"]["outcome"] == "failed"
    assert (await client.post("/routines/nope/run")).status_code == 404


async def test_a_routine_pins_only_a_skill_attached_to_its_bot(client, harness):
    r = await client.post("/routines", json=ROUTINE)
    assert r.status_code == 422 and r.json()["detail"] == "Skill 'research-brief' is not attached to bot1"
    r = await client.post("/routines", json={**ROUTINE, "bot": "ghost"})
    assert r.status_code == 422 and r.json()["detail"] == "Skill 'research-brief' is not attached to ghost"
    attach(harness)
    rid = (await client.post("/routines", json=ROUTINE)).json()["id"]
    harness.bots["bot1"].detach_skill("research-brief")
    assert (await client.put(f"/routines/{rid}", json=ROUTINE)).status_code == 422


async def test_an_imported_routine_with_an_unattached_skill_is_kept_paused_with_a_note(client, harness):
    body = (await client.post("/routines/import", json={"routines": [
        {"name": "A", "bot": "bot1", "skill": "research-brief", "schedule": "Fridays at 09:00", "enabled": True}]})).json()
    [r] = body["imported"]
    assert r["enabled"] is False and "Skill 'research-brief' is not attached to bot1" in r["note"]
    attach(harness)
    body = (await client.post("/routines/import", json={"routines": [
        {"name": "B", "bot": "bot1", "skill": "research-brief", "schedule": "Fridays at 09:00"}]})).json()
    assert "note" not in body["imported"][0]


async def test_routines_import(client):
    r = await client.post("/routines/import", json={"routines": [
        {"name": "A", "bot": "bot1", "skill": "research-brief", "schedule": "Fridays 9:00", "enabled": True},
        {"name": "B", "bot": "bot1", "skill": "research-brief", "schedule": "when the moon is full", "enabled": True},
    ]})
    body = r.json()
    assert [x["name"] for x in body["imported"]] == ["A"] and [x["name"] for x in body["unparsed"]] == ["B"]
    assert body["imported"][0]["enabled"] is False and body["imported"][0]["next_run"] is None  # arrives paused
    again = (await client.post("/routines/import", json={"routines": [
        {"name": "A", "bot": "bot1", "skill": "research-brief", "schedule": "Fridays 9:00"}]})).json()
    assert again["imported"] == [] and again["existing"] == ["A"]


async def test_routines_import_takes_the_old_gui_strings_and_a_remade_routine(client, harness):
    attach(harness)
    olds = [{"name": n, "bot": "bot1", "skill": "research-brief", "schedule": s, "enabled": True}
            for n, s in (("W", "Weekdays at 09:00"), ("D", "Daily at 12:00"), ("F", "Fridays at 16:00"), ("M", "Mondays at 09:00"))]
    body = (await client.post("/routines/import", json={"routines": olds})).json()
    assert [x["name"] for x in body["imported"]] == ["W", "D", "F", "M"] and body["unparsed"] == []
    assert {x["name"]: x["schedule"]["days"] for x in body["imported"]}["W"] == [0, 1, 2, 3, 4]
    # an unparsed one the user has since made again here (same name and bot) is settled, not unparsed again
    await client.post("/routines", json={**ROUTINE, "name": "Odd"})
    again = (await client.post("/routines/import", json={"routines": [
        {"name": "Odd", "bot": "bot1", "skill": "research-brief", "schedule": "when it rains"}]})).json()
    assert again["existing"] == ["Odd"] and again["unparsed"] == []


async def test_routines_reject_empty_fields_and_weekly_without_days(client, harness):
    attach(harness)
    for bad in ({"name": "  "}, {"bot": ""}, {"skill": " "}, {"schedule": {"kind": "weekly", "days": []}}):
        assert (await client.post("/routines", json={**ROUTINE, **bad})).status_code == 422, bad
    rid = (await client.post("/routines", json=ROUTINE)).json()["id"]
    assert (await client.put(f"/routines/{rid}", json={**ROUTINE, "name": ""})).status_code == 422
    assert (await client.put(f"/routines/{rid}", json={**ROUTINE, "schedule": {"kind": "weekly"}})).status_code == 422


async def test_list_survives_a_bad_hand_edited_routine(client, harness):
    import json
    bad = {"id": "w", "name": "Bad", "bot": "bot1", "skill": "research-brief", "enabled": True,
           "schedule": {"kind": "weekly", "days": [], "time": "09:00", "hours": 0}, "last_run": None, "managed_by": ""}
    bad2 = {**bad, "id": "i", "schedule": {"kind": "interval", "days": [], "time": "09:00", "hours": 0}}
    d = harness.config.vault_root.parent / "vault-settings"
    d.mkdir(exist_ok=True)
    (d / "routines.json").write_text(json.dumps({"routines": [bad, bad2]}))
    harness2 = Harness(config=harness.config, provider=ScriptedProvider(), ledger=Ledger(harness.config.vault_root), catalog=harness.catalog)
    transport = httpx.ASGITransport(app=create_app(harness2))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        r = await c.get("/routines")
        assert r.status_code == 200
        assert [(x["id"], x["next_run"]) for x in r.json()] == [("w", None), ("i", None)] and r.json()[0]["error"]
        assert (await c.delete("/routines/w")).status_code == 204


async def test_an_unreadable_routines_file_is_reported_and_left_alone(harness):
    d = harness.config.vault_root.parent / "vault-settings"
    d.mkdir(exist_ok=True)
    (d / "routines.json").write_text("{broken")
    harness2 = Harness(config=harness.config, provider=ScriptedProvider(), ledger=Ledger(harness.config.vault_root), catalog=harness.catalog)
    transport = httpx.ASGITransport(app=create_app(harness2))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        st = (await c.get("/routines/store")).json()
        assert st["read_only"] is True and "could not be read" in st["errors"][0]
        r = await c.post("/routines", json=ROUTINE)
        assert r.status_code == 409 and "could not be read" in r.json()["detail"]
        assert (await c.post("/routines/import", json={"routines": []})).status_code == 409
    assert (d / "routines.json").read_text() == "{broken"
    assert any("could not be read" in (e.data.get("error") or "") for e in harness2.ledger.iter() if e.kind == "error")


async def test_studio_flag_survives_a_bot_edit(monkeypatch, vault: Path, tmp_path: Path):
    from src.harness.main import build_harness

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ZEN_MEMORY_BACKEND", "local")
    prompts = tmp_path / "bot-prompts"
    prompts.mkdir()
    (prompts / "designer.md").write_text("---\nname: Designer\nrole: Old role\nstudio: true\n---\nDesign.\n", encoding="utf-8")
    h = build_harness(HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", bot_prompts_dir=prompts))
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        assert (await c.patch("/bots/designer", json={"role": "New role"})).status_code == 200
        bots = {b["id"]: b for b in (await c.get("/bots")).json()}
    assert bots["designer"]["studio"] is True and bots["designer"]["role"] == "New role"
    assert "studio: true" in (prompts / "designer.md").read_text(encoding="utf-8")


# design comments on a task

def _comment(att_id: str, note: str = "Make this bigger") -> dict:
    return {"attachment_id": att_id, "element_id": "hero", "selector": "body > h1", "tag": "h1", "text": "Hi", "note": note}


async def _two_revisions(client, harness: Harness) -> tuple[str, dict, dict]:
    harness.add_bot(Bot(name="Designer", role="Designs", id="des", studio=True))
    cid, first = await _attach_design(client, harness, "des")
    harness.provider.script = [
        completion(calls=[("c2", "attach_file", {"path": "designs/landing/index.html", "title": "Landing"})]),
        completion("Again."),
    ]
    sid = (await client.post("/bots/des/tasks", json={"task": "redo it"})).json()["session_id"]
    await wait_for(client, sid, {"done"})
    await harness.tasks[sid]
    second = (await client.get(f"/conversations/{cid}")).json()["exchanges"][-1]["attachments"][0]
    assert (first["version"], second["version"]) == (1, 2)
    return cid, first, second


async def test_task_with_comments_reaches_the_bot_and_the_next_history(client, harness: Harness):
    cid, first, second = await _two_revisions(client, harness)
    harness.provider.script = [completion("Done.")]
    r = await client.post("/bots/des/tasks", json={"task": "tweak", "design_comments": [_comment(second["id"])]})
    assert r.status_code == 202
    sid = r.json()["session_id"]
    await wait_for(client, sid, {"done"})
    await harness.tasks[sid]
    last = harness.provider.calls[-1][-1]
    f = harness.config.vault_root / "designs" / "landing" / "index.html"
    assert last.role == "user" and last.content.startswith("tweak\n\n")
    assert f"Comments on the design ({f}, version 2):" in last.content
    assert '1. [hero] <h1> "Hi": Make this bigger' in last.content and ".versions" not in last.content
    assert harness.sessions[sid].task == "tweak" and harness.sessions[sid].protected["task"] == "tweak"
    text = (await client.get(f"/conversations/{cid}")).text
    ex = json.loads(text)["exchanges"][-1]
    assert ex["user"] == "tweak" and ex["comments"] == [_comment(second["id"])] and "addendum" not in ex
    assert "addendum" not in text and str(harness.config.vault_root) not in text
    harness.provider.script = [completion("Next.")]
    sid = (await client.post("/bots/des/tasks", json={"task": "more"})).json()["session_id"]
    await wait_for(client, sid, {"done"})
    await harness.tasks[sid]
    assert any(m.role == "user" and "Comments on the design (" in m.content for m in harness.provider.calls[-1][:-1])


async def test_comments_on_the_wrong_attachment_are_refused_before_anything_starts(client, harness: Harness):
    cid, first, second = await _two_revisions(client, harness)
    harness.add_bot(Bot(name="Other", role="Designs", id="oth", studio=True))
    _, other_att = await _attach_design(client, harness, "oth")
    sessions, exchanges = len(harness.sessions), len(harness.conversations.get(cid).exchanges)
    for att_id in (first["id"], other_att["id"], "nope"):
        r = await client.post("/bots/des/tasks", json={"task": "tweak", "design_comments": [_comment(att_id)]})
        assert r.status_code == 422, att_id
    r = await client.post("/bots/des/tasks", json={"task": "tweak", "conversation": False,
                                                   "design_comments": [_comment(second["id"])]})
    assert r.status_code == 422
    assert len(harness.sessions) == sessions and len(harness.conversations.get(cid).exchanges) == exchanges


async def test_comments_need_a_design_revision_in_the_conversation(client, harness: Harness, tmp_path: Path):
    r = await client.post("/bots/bot1/tasks", json={"task": "x", "design_comments": [_comment("abc")]})
    assert r.status_code == 422  # an empty conversation
    sid, cid = await _attach_task(client, harness, tmp_path)  # a plain, non-design attachment
    plain = (await client.get(f"/conversations/{cid}")).json()["exchanges"][-1]["attachments"][0]
    n = len(harness.sessions)
    r = await client.post("/bots/bot1/tasks", json={"task": "x", "design_comments": [_comment(plain["id"])]})
    assert r.status_code == 422 and len(harness.sessions) == n


async def test_comment_caps(client, harness: Harness):
    cid, first, second = await _two_revisions(client, harness)
    n = len(harness.sessions)
    for comments in ([_comment(second["id"])] * 21, [_comment(second["id"], "x" * 1001)], [_comment(second["id"], "")]):
        r = await client.post("/bots/des/tasks", json={"task": "tweak", "design_comments": comments})
        assert r.status_code == 422
    assert len(harness.sessions) == n

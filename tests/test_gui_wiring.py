"""gui-wiring, harness side: the GUI binds the provider and key at runtime
over localhost; the key never comes back; CORS admits the GUI origin only;
scope changes are checked and confirmed through the API; a finished
session's result lands in the session cache."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from src.decider import registry as scorers
from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.providers import registry
from src.skills.catalog import Catalog

from .conftest import MockScorer, ScriptedProvider, completion

GUI_KEY = "sk-or-v1-guikey9876543210fedcba9876543210"


class TestProvider(ScriptedProvider):
    kind = "test-prov"
    made_with: list[str] = []

    @classmethod
    def factory(cls, *, key_ref: str, model=None, **kw):
        cls.made_with.append(key_ref)
        return cls([completion("bound and answering")])


class TestScorer(MockScorer):
    kind = "test-scorer"

    @classmethod
    def factory(cls, *, key_ref: str, model=None, **kw):
        from src.decider.interface import NoulAnswer

        return cls(lambda request: {q: NoulAnswer(0.01) for q in request.questions})  # the built-in image tool is always scored


@pytest.fixture(autouse=True)
def _register():
    registry.register(TestProvider.kind, TestProvider.factory)
    scorers.register(TestScorer.kind, TestScorer.factory)


@pytest.fixture
def unbound(vault: Path, tmp_path: Path) -> Harness:
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", shell_cwd=tmp_path)
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    h.add_bot(Bot(name="Research partner", role="Research things", id="bot1"))
    return h


@pytest.fixture
async def client(unbound: Harness):
    transport = httpx.ASGITransport(app=create_app(unbound))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def test_unbound_harness_reports_and_refuses_tasks(client):
    s = (await client.get("/settings")).json()
    assert s["provider"] == "unbound" and s["scorer"] is None and s["key_set"] is False
    assert "openrouter" in s["provider_kinds"] and TestProvider.kind in s["provider_kinds"]
    assert (await client.get("/health")).json()["provider"] == "unbound"
    r = await client.post("/bots/bot1/tasks", json={"task": "anything"})
    assert r.status_code == 503 and "Settings" in r.json()["detail"]


async def test_settings_bind_provider_and_scorer_and_never_echo_the_key(client, unbound: Harness):
    r = await client.post("/settings/provider", json={"kind": TestProvider.kind, "key": GUI_KEY, "model": "m1", "scorer": TestScorer.kind})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == TestProvider.kind and body["scorer"] == TestScorer.kind and body["key_set"] is True and body["model"] == "m1"
    assert GUI_KEY not in r.text
    assert TestProvider.made_with[-1].startswith("env:")  # the key travels as a reference, not a value
    assert (await client.get("/health")).json()["provider"] == TestProvider.kind
    # the loop runs on the new provider
    r = await client.post("/bots/bot1/tasks", json={"task": "say hi"})
    assert r.status_code == 202
    sid = r.json()["session_id"]
    for _ in range(200):
        s = (await client.get(f"/sessions/{sid}")).json()
        if s["state"] in ("done", "failed"):
            break
        await asyncio.sleep(0.01)
    assert s["state"] == "done" and s["result"] == "bound and answering", s
    # nothing anywhere carries the key: not the ledger, not the settings echo
    assert GUI_KEY not in (await client.get("/ledger")).text
    assert GUI_KEY not in (await client.get("/settings")).text
    assert GUI_KEY not in (unbound.ledger.file.read_text(encoding="utf-8"))
    kinds = [e["kind"] for e in (await client.get("/ledger")).json()]
    assert "settings" in kinds


async def test_settings_reject_unknown_provider_and_empty_key(client):
    assert (await client.post("/settings/provider", json={"kind": "nope", "key": GUI_KEY})).status_code == 422
    assert (await client.post("/settings/provider", json={"kind": TestProvider.kind, "key": "   "})).status_code == 422


async def test_cors_admits_the_gui_origin_only(client):
    r = await client.get("/health", headers={"Origin": "app://openzenbot"})
    assert r.headers.get("access-control-allow-origin") == "app://openzenbot"
    r = await client.options("/settings/provider", headers={"Origin": "app://openzenbot", "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200 and r.headers.get("access-control-allow-origin") == "app://openzenbot"
    r = await client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_launcher_starts_unbound_without_a_key(monkeypatch, vault: Path, tmp_path: Path):
    from src.harness.main import build_harness

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ZEN_MEMORY_BACKEND", "local")
    h = build_harness(HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", shell_cwd=tmp_path))
    assert h.provider.kind == "unbound" and h.scorer is None and h.bound is False


@pytest.fixture
def bound(vault: Path, tmp_path: Path) -> Harness:
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", shell_cwd=tmp_path)
    cfg.validate()
    h = Harness(config=cfg, provider=ScriptedProvider([completion("the result text")]), ledger=Ledger(vault), catalog=Catalog(),
                scorer=MockScorer(lambda req: {k: __import__("src.decider.interface", fromlist=["NoulAnswer"]).NoulAnswer(0.9 if "goal" in k else 0.1) for k in req.questions}))
    h.add_bot(Bot(name="Research partner", role="Research things", id="bot1"))
    return h


@pytest.fixture
async def bclient(bound: Harness):
    transport = httpx.ASGITransport(app=create_app(bound))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def _finish(c, sid):
    for _ in range(200):
        s = (await c.get(f"/sessions/{sid}")).json()
        if s["state"] in ("done", "failed", "stopped"):
            return s
        await asyncio.sleep(0.01)
    raise AssertionError("session never finished")


async def test_scope_check_then_confirm_rewrites_the_protected_block(bclient, bound: Harness):
    sid = (await bclient.post("/bots/bot1/tasks", json={"task": "summarize the brief"})).json()["session_id"]
    await _finish(bclient, sid)
    r = await bclient.post(f"/sessions/{sid}/scope", json={"text": "actually, write the launch email instead"})
    assert r.status_code == 200, r.text
    change = r.json()
    assert change["changed"] is True and change["new_goal"] is True and change["confirm"] == "pending" and change["probability"] == 0.9
    assert (await bclient.get(f"/sessions/{sid}")).json()["scope_pending"]["changed"] is True
    r = await bclient.post(f"/sessions/{sid}/scope/confirm", json={"task": "write the launch email"})
    assert r.status_code == 200
    s = (await bclient.get(f"/sessions/{sid}")).json()
    assert s["protected"]["task"] == "write the launch email" and s["scope_pending"] is None
    kinds = [(e["kind"], e.get("data", {}).get("event")) for e in (await bclient.get("/ledger")).json()]
    assert ("scope_change", "confirmed") in kinds
    # nothing pending: confirm and reject are 409
    assert (await bclient.post(f"/sessions/{sid}/scope/confirm", json={})).status_code == 409
    assert (await bclient.post(f"/sessions/{sid}/scope/reject")).status_code == 409


async def test_scope_reject_keeps_the_block(bclient):
    sid = (await bclient.post("/bots/bot1/tasks", json={"task": "summarize the brief"})).json()["session_id"]
    await _finish(bclient, sid)
    await bclient.post(f"/sessions/{sid}/scope", json={"text": "do something else entirely"})
    assert (await bclient.post(f"/sessions/{sid}/scope/reject")).status_code == 200
    s = (await bclient.get(f"/sessions/{sid}")).json()
    assert s["protected"]["task"] == "summarize the brief" and s["scope_pending"] is None


async def test_finished_session_result_enters_the_session_cache(bclient, bound: Harness):
    sid = (await bclient.post("/bots/bot1/tasks", json={"task": "summarize the brief"})).json()["session_id"]
    s = await _finish(bclient, sid)
    assert s["state"] == "done"
    facts = bound.cache.facts(sid)
    assert len(facts) == 1 and facts[0].text == "the result text" and facts[0].bot_id == "bot1"
    r = await bclient.get("/memory/cache", params={"session_id": sid})
    assert r.status_code == 200 and r.json()[0]["text"] == "the result text"


# final-review fixes


async def test_rebind_keeps_pause_state_rooms_fits_and_pending(bclient, bound: Harness):
    from src.harness.pause import PauseController
    bound.add_bot(Bot(name="Writer", role="Write things", id="bot2"))
    r = await bclient.post("/rooms", json={"name": "r", "members": ["bot1", "bot2"]})
    assert r.status_code == 200, r.text
    room = r.json()
    pause_before = bound.pause
    bound.rooms.rooms["keep"] = object()
    bound.fit._suggestions["keep"] = object()
    bound.recall.pending["keep"] = object()
    r = await bclient.post("/settings/provider", json={"kind": TestProvider.kind, "key": GUI_KEY, "scorer": TestScorer.kind})
    assert r.status_code == 200, r.text
    assert isinstance(bound.pause, PauseController) and bound.pause is pause_before
    assert (await bclient.get(f"/rooms/{room['id']}/turn")).status_code == 200
    assert "keep" in bound.rooms.rooms and "keep" in bound.fit._suggestions and "keep" in bound.recall.pending


async def test_key_set_follows_bound_for_any_key_ref(bound: Harness):
    bound.config.key_ref = "vault:openrouter"
    assert bound.settings()["key_set"] is True


async def test_settings_empty_scorer_clears_the_scorer(client, unbound: Harness):
    await client.post("/settings/provider", json={"kind": TestProvider.kind, "key": GUI_KEY, "scorer": TestScorer.kind})
    assert unbound.scorer is not None
    r = await client.post("/settings/provider", json={"kind": TestProvider.kind, "key": GUI_KEY, "scorer": ""})
    assert r.status_code == 200 and r.json()["scorer"] is None and unbound.scorer is None


async def test_shell_tool_does_not_inherit_secret_env(monkeypatch, tmp_path: Path):
    from src.harness.tools import ToolCall, ToolRunner
    monkeypatch.setenv("ZEN_GUI_KEY", GUI_KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-startup0000000000000000000000000000")
    monkeypatch.setenv("VAULT_TOKEN", "hvs.secret")
    monkeypatch.setenv("ZEN_PLAIN", "visible")
    out = await ToolRunner(cwd=tmp_path).run(ToolCall(id="c1", name="shell", arguments={"command": "python -c \"import os; print(sorted(k for k in os.environ if k in ('ZEN_GUI_KEY','OPENROUTER_API_KEY','VAULT_TOKEN','ZEN_PLAIN')))\""}))
    assert out.ok, out.error
    assert out.output.strip() == "['ZEN_PLAIN']"


async def test_foreign_host_header_is_refused(client):
    r = await client.get("/health", headers={"host": "evil.example"})
    assert r.status_code == 400
    assert (await client.get("/health", headers={"host": "localhost:8765"})).status_code == 200


async def test_binding_a_key_in_settings_rebinds_the_memory_store(vault: Path, tmp_path: Path):
    from src.memory.graph import LocalGraph, MemoryGraph

    class Store(LocalGraph):
        refs: list[str] = []

        def rebind_key(self, ref: str) -> None:
            Store.refs.append(ref)

    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", shell_cwd=tmp_path)
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog(), memory=MemoryGraph(Store(vault / "memory"), Ledger(vault)))
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        r = await c.post("/settings/provider", json={"kind": TestProvider.kind, "key": GUI_KEY})
        assert r.status_code == 200, r.text
    assert Store.refs == ["env:ZEN_GUI_KEY"]


def test_csp_frames_only_the_hindsight_control_plane_and_the_harness():
    """Frames: the memory dashboard, and bot-made diagrams the harness serves sandboxed. Nothing else."""
    html = (Path(__file__).resolve().parent.parent / "src" / "gui" / "index.html").read_text(encoding="utf-8")
    csp = html.split('http-equiv="Content-Security-Policy" content="')[1].split('"')[0]
    frame_src = next(d.strip() for d in csp.split(";") if d.strip().startswith("frame-src"))
    assert frame_src.split()[1:] == ["http://127.0.0.1:9999", "http://localhost:9999", "http://127.0.0.1:8765", "http://localhost:8765"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "object-src 'none'" in csp

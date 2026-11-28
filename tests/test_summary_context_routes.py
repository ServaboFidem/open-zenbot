"""The summary model's context through the Harness: the settings route, the flag on the chat, and a rebind."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

import src.providers.openai_compatible  # noqa: F401 - registered before a test swaps in its fake factory
from src.context.fit import Overrun
from src.context.protected import ProtectedBlock
from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.harness.loop import Session
from src.harness.summary_context import SummaryContext
from src.memory.ledger import Ledger
from src.providers import registry
from src.providers.interface import Message
from src.providers.models import ModelInfo
from src.providers.store import ProviderEntry
from src.skills.catalog import Catalog

from .conftest import ScriptedProvider, completion
from .test_compaction import RefusingProvider

LOCAL = "http://127.0.0.1:1234/v1"


def lm_studio(loaded: int | None = 4096, maximum: int = 4096):
    """A stand-in LM Studio: its model list, and a record of every path asked."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path != "/api/v1/models":
            return httpx.Response(404)
        inst = [{"config": {"context_length": loaded}}] if loaded else []
        return httpx.Response(200, json={"models": [
            {"key": "m", "type": "llm", "max_context_length": maximum, "loaded_instances": inst},
            {"key": "big", "type": "llm", "max_context_length": 131072, "loaded_instances": []},
        ]})

    return handler, paths


def context(entry: ProviderEntry, handler) -> SummaryContext:
    return SummaryContext(entry=entry, model="m", key=lambda: None,
                          client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def build(vault: Path, tmp_path: Path, entry: ProviderEntry, handler, summary_provider=None) -> Harness:
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path / "workspace")
    cfg.validate()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog(),
                summary_provider=summary_provider, summary_context=context(entry, handler))
    h.add_bot(Bot(name="Research partner", role="Research things", id="bot1"))
    return h


def local_entry() -> ProviderEntry:
    return ProviderEntry(id="local", name="LM Studio", kind="openai-compatible", base_url=LOCAL, no_key=True,
                         models=[ModelInfo(id="big", name="big", kinds=["text"])])


def hosted_entry() -> ProviderEntry:
    return ProviderEntry(id="openrouter", name="OpenRouter", kind="openrouter", base_url="https://openrouter.ai/api/v1")


def _local_provider() -> ScriptedProvider:
    p = ScriptedProvider()
    p.kind = "openai-compatible"
    return p


def api(h: Harness) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost")


def conversation(h: Harness, n: int):
    conv = None
    for i in range(n):
        conv, _ = h.conversations.begin("bot1", f"question {i} " + "x" * 200, session_id=f"s{i}",
                                        conv_id=conv.id if conv else None)
        h.conversations.finish(conv.id, f"s{i}", "done", f"answer {i} " + "y" * 200)
    return conv


async def idle(h: Harness) -> None:
    for _ in range(500):
        if not h.busy():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("harness stayed busy")


async def send(c: httpx.AsyncClient, h: Harness, conv_id: str) -> None:
    h.provider.script.append(completion("ok"))
    r = await c.post("/bots/bot1/tasks", json={"task": "next", "conversation_id": conv_id})
    assert r.status_code == 202, r.text
    await idle(h)


async def test_route_reports_lm_studio(vault, tmp_path):
    handler, _ = lm_studio()
    h = build(vault, tmp_path, local_entry(), handler)
    async with api(h) as c:
        r = await c.get("/settings/models/summary/context?fresh=1")
    assert r.status_code == 200
    body = r.json()
    assert body["server"] == "lmstudio" and body["warning"] == "at_max" and body["recommended_context"] == 4096
    assert body["loaded_context"] == 4096 and body["model"] == "m"
    assert body["alternatives"] == [{"id": "big", "max_context": 131072, "loaded": False, "enabled": True}]


async def test_route_for_hosted(vault, tmp_path):
    handler, paths = lm_studio()
    h = build(vault, tmp_path, hosted_entry(), handler)
    async with api(h) as c:
        body = (await c.get("/settings/models/summary/context?fresh=1")).json()
    assert body["server"] == "hosted" and body["warning"] == ""
    assert paths == []


async def test_fold_overrun_flags_the_running_exchange(vault, tmp_path):
    handler, _ = lm_studio()
    h = build(vault, tmp_path, local_entry(), handler, summary_provider=RefusingProvider(max_user=50))
    h.compactor.config.conversation_budget_chars = 3000
    conv = conversation(h, 20)
    async with api(h) as c:
        await send(c, h, conv.id)
        view = (await c.get(f"/conversations/{conv.id}")).json()
    flags = [e["context_overrun"] for e in view["exchanges"]]
    assert flags[-1] is True and not any(flags[:-1])
    assert h.summary_context._overrun["where"] == "fold"


async def test_overrun_flags_the_exchange_once(vault, tmp_path):
    handler, _ = lm_studio()
    h = build(vault, tmp_path, local_entry(), handler, summary_provider=RefusingProvider(max_user=50))
    h.compactor.config.conversation_budget_chars = 3000
    conv = conversation(h, 20)
    async with api(h) as c:
        await send(c, h, conv.id)
        await send(c, h, conv.id)
        view = (await c.get(f"/conversations/{conv.id}")).json()
    flags = [e["context_overrun"] for e in view["exchanges"]]
    assert flags.count(True) == 1 and flags[-2] is True


async def test_task_compaction_overrun_flags_the_exchange(vault, tmp_path):
    handler, _ = lm_studio()
    h = build(vault, tmp_path, local_entry(), handler, summary_provider=RefusingProvider(max_user=50))
    conv, _ = h.conversations.begin("bot1", "hello", session_id="sess")
    h.sessions["sess"] = Session(bot_id="bot1", task="hello", id="sess", conversation_id=conv.id)
    h.compactor.config.budget_chars = 500
    msgs = [Message("system", "sys")]
    for i in range(6):
        msgs += [Message("user", f"q{i} " + "x" * 300), Message("assistant", f"a{i} " + "y" * 300)]
    res = await h.compactor.maybe_compact(msgs, ProtectedBlock(task="t"), bot_id="bot1", session_id="sess")
    assert res.overrun is not None
    assert h.conversations.get(conv.id).exchanges[0].context_overrun is True


async def test_room_and_hosted_are_not_flagged(vault, tmp_path):
    handler, _ = lm_studio()
    h = build(vault, tmp_path, local_entry(), handler)
    conv, _ = h.conversations.begin("bot1", "hello", session_id="rs")
    h.sessions["rs"] = Session(bot_id="bot1", task="t", id="rs", room_id="room1")
    h._compaction_overrun("bot1", "rs", Overrun(4096, 5000))  # a room turn: recorded, never flagged
    assert h.conversations.get(conv.id).exchanges[0].context_overrun is False
    assert h.summary_context._overrun is not None
    hosted = context(hosted_entry(), handler)  # a hosted summary model: nothing recorded, nothing flagged
    h.summary_context = hosted
    h.sessions["cs"] = Session(bot_id="bot1", task="t", id="cs", conversation_id=conv.id)
    h._compaction_overrun("bot1", "cs", Overrun(4096, 5000))
    assert h.conversations.get(conv.id).exchanges[0].context_overrun is False
    assert hosted._overrun is None


async def test_changing_the_summary_model_clears_the_state(vault, tmp_path, monkeypatch):
    handler, _ = lm_studio()
    h = build(vault, tmp_path, local_entry(), handler)
    monkeypatch.setitem(registry._factories, "openai-compatible", lambda **kw: _local_provider())
    store = h.provider_store
    store.add("Local", "openai-compatible", LOCAL, no_key=True)
    store.set_models("local", [ModelInfo(id="m", name="m", kinds=["text"]), ModelInfo(id="other", name="other", kinds=["text"])])
    h.summary_context.rebind(store.get("local"), "m")
    h.summary_context.record_overrun("task", Overrun(4096, 5000))
    async with api(h) as c:
        assert (await c.get("/settings/models/summary/context")).json()["last_overrun"] is not None
        r = await c.put("/settings/models", json={"summary": {"provider": "local", "model": "other"}})
        assert r.status_code == 200, r.text
        body = (await c.get("/settings/models/summary/context")).json()
    assert body["last_overrun"] is None and body["model"] == "other"

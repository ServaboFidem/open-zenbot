"""The /dream routes and the harness's wiring of the dreamer: settings, status, the idle report, "Dream now",
the list of dreamt facts, and what counts as the harness being busy."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.harness.dream_routes import dream_router
from src.harness.loop import Session, SessionState
from src.harness.model_roles import Choice
from src.memory.graph import SHARED, Fact, Tier
from src.memory.ledger import Ledger
from src.providers.models import ModelInfo
from src.skills.catalog import Catalog

from .conftest import ScriptedProvider


@pytest.fixture
def harness(vault: Path, tmp_path: Path) -> Harness:
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path / "workspace")
    cfg.validate()
    return Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())


@pytest.fixture
async def client(harness: Harness):
    transport = httpx.ASGITransport(app=create_app(harness))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c
    await harness.dreamer.close()


def choose_dream_model(h: Harness, model: str = "qwen3-14b") -> None:
    """A local provider that needs no key, the model enabled on it, and chosen as the Dreaming model."""
    store = h.provider_store
    if "local" not in store.entries:
        store.add("Local", "openai-compatible", "http://localhost:1234/v1", no_key=True)
    store.set_models("local", [ModelInfo(id=m, name=m, kinds=["text"]) for m in ("qwen3-14b", "llama-3")])
    roles = h.model_roles()
    roles.dream = Choice("local", model)
    roles.save(h.config.vault_root)


def post_paths(make_router) -> list[str]:
    """Every POST route of the router, read from it so a route added later is covered."""
    return sorted(r.path for r in make_router(None).routes if "POST" in (getattr(r, "methods", None) or ()))


async def test_every_dream_post_needs_a_json_body(client):
    paths = post_paths(dream_router)
    assert {"/dream/idle", "/dream/run"} <= set(paths)
    for path in paths:
        assert (await client.post(path)).status_code == 422
        assert (await client.post(path, content="x", headers={"content-type": "text/plain"})).status_code == 422
        assert (await client.post(path, content="{}", headers={"content-type": "text/plain"})).status_code == 422


async def test_status_starts_off_and_not_ready(client):
    s = (await client.get("/dream/status")).json()
    assert (s["enabled"], s["idle_minutes"], s["ready"], s["dreaming"], s["model"], s["error"]) == (False, 5, False, False, None, "")
    assert (s["idle"], s["waiting"], s["learned"], s["last_dream_at"], s["idle_reported"]) == (False, 0, 0, "", False)


async def test_settings_range_and_needing_a_model(client, harness):
    r = await client.put("/dream/settings", json={"enabled": False, "idle_minutes": 121})
    assert r.status_code == 422 and "1 to 120" in r.json()["detail"]
    assert (await client.put("/dream/settings", json={"enabled": False, "idle_minutes": 0})).status_code == 422
    assert (await client.put("/dream/settings", json={"enabled": "yes", "idle_minutes": 5})).status_code == 422
    assert (await client.put("/dream/settings", json={"enabled": False, "idle_minutes": 5.5})).status_code == 422
    r = await client.put("/dream/settings", json={"enabled": True, "idle_minutes": 5})
    assert r.status_code == 409 and r.json()["detail"] == "Choose a dreaming model first."
    assert harness.dreamer.settings.enabled is False


async def test_settings_save_and_are_ledgered(client, harness):
    choose_dream_model(harness)
    r = await client.put("/dream/settings", json={"enabled": True, "idle_minutes": 12})
    assert r.status_code == 200
    s = r.json()
    assert (s["enabled"], s["idle_minutes"], s["model"]) == (True, 12, {"provider": "local", "model": "qwen3-14b"})
    from src.memory.dream import DreamSettings

    assert DreamSettings.load(harness.config.vault_root) == DreamSettings(True, 12)
    entry = [e for e in harness.ledger.iter() if e.kind == "settings" and e.data.get("event") == "dream"][-1]
    assert (entry.data["enabled"], entry.data["idle_minutes"]) == (True, 12)
    # switching off needs no model
    assert (await client.put("/dream/settings", json={"enabled": False, "idle_minutes": 12})).json()["enabled"] is False


async def test_idle_report_range(client, harness):
    assert (await client.post("/dream/idle", json={"idle_s": 600})).json() == {"ok": True}
    assert (await client.get("/dream/status")).json()["idle"] is True  # 600 s is past the default 5 minutes
    assert (await client.post("/dream/idle", json={"idle_s": -1})).status_code == 422
    assert (await client.post("/dream/idle", json={"idle_s": 10_000_001})).status_code == 422
    assert (await client.post("/dream/idle", json={})).status_code == 422
    from pydantic import ValidationError

    from src.harness.dream_routes import IdleIn

    for bad in (float("nan"), float("inf")):  # json.loads reads NaN and Infinity; neither is an idle time
        with pytest.raises(ValidationError):
            IdleIn(idle_s=bad)


async def test_run_refused_when_not_set_up(client):
    r = await client.post("/dream/run", json={})
    assert r.status_code == 409 and r.json()["detail"] == "Dreaming is not set up."


async def test_run_starts_a_pass_and_a_second_is_refused(client, harness, monkeypatch):
    choose_dream_model(harness)
    harness.profile.people.set_owner("Gordon", [])
    assert (await client.put("/dream/settings", json={"enabled": True, "idle_minutes": 5})).status_code == 200
    gate = asyncio.Event()
    calls = []

    async def held_pass(*, manual=False):
        calls.append(manual)
        await gate.wait()
        return {}

    monkeypatch.setattr(harness.dreamer, "dream", held_pass)
    r = await client.post("/dream/run", json={})
    assert r.status_code == 200 and r.json()["dreaming"] is True
    r = await client.post("/dream/run", json={})
    assert r.status_code == 409 and r.json()["detail"] == "A dream is already under way."
    gate.set()
    await harness.dreamer.close()
    assert calls == [True]  # a manual pass, started once


async def test_run_refused_while_zenbot_is_busy(client, harness):
    choose_dream_model(harness)
    harness.profile.people.set_owner("Gordon", [])
    assert (await client.put("/dream/settings", json={"enabled": True, "idle_minutes": 5})).status_code == 200
    s = Session(bot_id="bot1", task="t")
    s.state = SessionState.running
    harness.sessions[s.id] = s
    r = await client.post("/dream/run", json={})
    assert r.status_code == 409 and r.json()["detail"] == "ZenBot is busy. Try again when it is idle."
    assert (await client.get("/dream/status")).json()["dreaming"] is False
    s.state = SessionState.done
    r = await client.post("/dream/run", json={})
    assert r.status_code == 200


async def test_facts_lists_only_dreamt_facts_newest_first(client, harness):
    conv = harness.conversations.start_new("bot1")
    conv.title = "Moving house"
    store = harness.memory.store
    store.put(Fact("Gordon lives in Columbus.", SHARED, Tier.project, source=f"dream:{conv.id}", at="2026-11-18T10:00:00+00:00"))
    store.put(Fact("Gordon prefers metric.", SHARED, Tier.project, source=f"dream:{conv.id}", at="2026-11-19T10:00:00+00:00"))
    store.put(Fact("Ann is Gordon's sister.", SHARED, Tier.project, source="dream:gone", at="2026-11-17T10:00:00+00:00"))
    store.put(Fact("Columbus is in Ohio.", SHARED, Tier.project, source="web:x", at="2026-11-19T11:00:00+00:00"))
    store.put(Fact("A private dream.", "bot1", Tier.project, source=f"dream:{conv.id}"))
    got = (await client.get("/dream/facts")).json()
    assert [f["text"] for f in got] == ["Gordon prefers metric.", "Gordon lives in Columbus.", "Ann is Gordon's sister."]
    first = got[0]
    assert set(first) == {"id", "text", "at", "conversation", "title"}
    assert (first["conversation"], first["title"], first["at"]) == (conv.id, "Moving house", "2026-11-19T10:00:00+00:00")
    assert (got[2]["conversation"], got[2]["title"]) == ("gone", "")


async def test_facts_list_at_most_two_hundred(client, harness):
    for i in range(205):
        harness.memory.store.put(Fact(f"Fact {i}.", SHARED, Tier.project, source="dream:c",
                                      at=f"2026-11-19T10:{i // 60:02d}:{i % 60:02d}+00:00"))
    got = (await client.get("/dream/facts")).json()
    assert len(got) == 200 and got[0]["text"] == "Fact 204." and got[-1]["text"] == "Fact 5."


def test_busy_while_a_session_runs(harness):
    assert harness.busy() is False
    s = Session(bot_id="bot1", task="t")
    harness.sessions[s.id] = s
    for state in (SessionState.running, SessionState.waiting_approval, SessionState.paused):
        s.state = state
        assert harness.busy() is True
    for state in (SessionState.done, SessionState.failed, SessionState.stopped):
        s.state = state
        assert harness.busy() is False


async def test_busy_while_a_task_is_still_finishing(harness):
    gate = asyncio.Event()
    harness.tasks["s1"] = asyncio.create_task(gate.wait())  # its session ended; the exchange is still being written
    assert harness.busy() is True
    gate.set()
    await harness.tasks["s1"]
    assert harness.busy() is False


async def test_busy_while_a_room_turn_is_scheduled(harness):
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    harness.rooms._tasks.setdefault("room1", set()).add(task)  # a turn waiting for the floor has no session yet
    assert harness.busy() is True
    gate.set()
    await task
    assert harness.busy() is False


def test_busy_while_an_import_a_delegation_or_extraction_is_under_way(harness, monkeypatch):
    monkeypatch.setattr(harness.importer, "running", lambda: object())
    assert harness.busy() is True
    monkeypatch.setattr(harness.importer, "running", lambda: None)
    harness.delegator.runner.live["s1"] = object()
    assert harness.busy() is True
    harness.delegator.runner.live.clear()

    class Queue:
        jobs: list = ["job"]

        def pending(self):
            return list(self.jobs)

    q = Queue()
    harness.extract_queue = q
    assert harness.busy() is True
    q.jobs = []
    assert harness.busy() is False


def test_generator_is_built_once_and_again_when_the_choice_changes(harness):
    assert harness.dreamer._generator() is None  # no Dreaming model: never filled from another role
    choose_dream_model(harness)
    g = harness.dreamer._generator()
    assert g is not None and g.model == "qwen3-14b"
    assert harness.dreamer._generator() is g
    choose_dream_model(harness, "llama-3")
    g2 = harness.dreamer._generator()
    assert g2 is not g and g2.model == "llama-3"
    harness.provider_store.update("local", base_url="http://localhost:5678/v1")
    assert harness.dreamer._generator() is not g2


def test_a_broken_dream_choice_raises_for_the_dreamer(harness):
    from src.providers.store import ProviderStoreError

    choose_dream_model(harness)
    harness.provider_store.set_models("local", [])  # the model is no longer enabled
    with pytest.raises(ProviderStoreError):
        harness.dreamer._generator()
    assert harness.dreamer.status()["error"] == "setup"


def test_the_dreamer_survives_a_rebind_and_reads_the_harness_now(harness):
    d = harness.dreamer
    harness.rebind(ScriptedProvider(), None)
    assert harness.dreamer is d
    q = object()
    harness.extract_queue = q
    assert d._queue() is q and d._profile() is harness.profile
    conv = harness.conversations.start_new("bot1")
    assert conv in d._conversations()


def test_the_marks_live_in_the_ledger_folder(harness):
    from src.memory.ledger import ledger_path_for

    assert harness.dreamer.marks.path == ledger_path_for(harness.config.vault_root) / "dreams.json"


async def test_facts_redact_the_conversation_title(client, harness):
    conv = harness.conversations.start_new("bot1")
    conv.title = "password=example123 for the router"
    harness.memory.store.put(Fact("Gordon has a router.", SHARED, Tier.project, source=f"dream:{conv.id}"))
    [got] = (await client.get("/dream/facts")).json()
    assert "example123" not in got["title"] and got["text"] == "Gordon has a router."


async def test_a_room_turn_still_counts_as_busy_after_a_rebind(harness):
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    harness.rooms._tasks.setdefault("room1", set()).add(task)  # a turn waiting for the floor
    harness.rebind(ScriptedProvider(), None)
    assert harness.busy() is True
    gate.set()
    await task
    assert harness.busy() is False


def test_a_rebind_builds_the_dream_generator_again(harness):
    choose_dream_model(harness)
    g = harness.dreamer._generator()
    harness.rebind(ScriptedProvider(), None)  # a key replaced in the environment arrives with a rebind
    assert harness.dreamer._generator() is not g

"""Harness wiring and API for the orchestrator: /orchestrate, persistent rooms, archive/delete, attachments."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from src.decider.interface import NoulAnswer
from src.harness.api import PREVIEW_CSP, Harness, create_app
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.providers.interface import ProviderUnavailable
from src.rooms.room import Attachment
from src.skills.catalog import Catalog

from .conftest import MockScorer, ScriptedProvider, completion


def scores(**by_id: float):
    return lambda req: {k: NoulAnswer(by_id.get(k, 0.0)) for k in req.questions}


def build(vault: Path, tmp_path: Path, scorer, provider=None) -> Harness:
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    cfg.validate()
    provider = provider or ScriptedProvider([completion("ok") for _ in range(20)])
    h = Harness(config=cfg, provider=provider, ledger=Ledger(vault), catalog=Catalog(), scorer=scorer)
    h.add_bot(Bot(name="ZenBot", role="Assistant", id="personal-assistant"))
    h.add_bot(Bot(name="Research partner", role="Research", id="research-partner"))
    return h


def topic_provider() -> ScriptedProvider:
    return ScriptedProvider([completion('{"name": "Ohio beekeeping", "topic": "Becoming a beekeeper in Ohio"}')]
                            + [completion("ok") for _ in range(20)])


@pytest.fixture
def harness(vault: Path, tmp_path: Path) -> Harness:
    return build(vault, tmp_path, MockScorer(scores(**{"bot:research-partner": 0.9})), topic_provider())


@pytest.fixture
async def client(harness: Harness):
    transport = httpx.ASGITransport(app=create_app(harness))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def test_orchestrate_opens_a_room_and_rooms_survive_a_restart(client, harness: Harness, vault: Path, tmp_path: Path):
    r = await client.post("/orchestrate", json={"text": "How do I become a beekeeper in Ohio?"})
    assert r.status_code == 200
    assert r.json()["outcome"] == "room" and r.json()["created"] is True
    await harness.rooms.settle(r.json()["room_id"])
    h2 = build(vault, tmp_path, None)
    assert [x.id for x in h2.rooms.list()] == [r.json()["room_id"]]
    assert "invite_to_room" in h2.tools.names()


async def test_orchestrate_direct_returns_task(vault: Path, tmp_path: Path):
    h = build(vault, tmp_path, MockScorer(scores()))
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        r = await c.post("/orchestrate", json={"text": "what is 2+2"})
    assert r.json()["outcome"] == "task" and not h.sessions and not h.rooms.list()


async def test_orchestrate_provider_error_falls_back_to_task(vault: Path, tmp_path: Path):
    h = build(vault, tmp_path, MockScorer(scores(**{"bot:research-partner": 0.9})),
              ScriptedProvider([ProviderUnavailable("topic model down")]))
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        r = await c.post("/orchestrate", json={"text": "bees?"})
    assert r.status_code == 200 and r.json()["outcome"] == "task" and "topic model down" in r.json()["fallback"]
    assert any(e.data.get("where") == "orchestrate" for e in h.ledger.tail(kind="error"))


async def test_orchestrate_validates(client, vault: Path, tmp_path: Path):
    assert (await client.post("/orchestrate", json={"text": ""})).status_code == 422
    h = Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path), provider=None, ledger=Ledger(vault), catalog=Catalog())
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        assert (await c.post("/orchestrate", json={"text": "hi"})).status_code == 503


async def test_archive_delete_and_busy(client, harness: Harness):
    room = harness.rooms.create("r", ["personal-assistant", "research-partner"], lead="personal-assistant")
    r = await client.put(f"/rooms/{room.id}/archived", json={"archived": True})
    assert r.status_code == 200 and r.json()["archived"] is True
    assert (await client.put("/rooms/nope/archived", json={"archived": True})).status_code == 404
    room.waiters.append("research-partner")  # a turn is waiting
    r = await client.delete(f"/rooms/{room.id}")
    assert r.status_code == 409 and "busy" in r.json()["detail"]
    room.waiters.clear()
    assert (await client.delete(f"/rooms/{room.id}")).status_code == 204
    assert (await client.delete(f"/rooms/{room.id}")).status_code == 404


async def test_rooms_list_newest_activity_first(client, harness: Harness):
    a = harness.rooms.create("a", ["personal-assistant", "research-partner"])
    b = harness.rooms.create("b", ["personal-assistant", "research-partner"])
    a.last_active, b.last_active = "2026-01-02T00:00:00+00:00", "2026-01-01T00:00:00+00:00"
    assert [x["id"] for x in (await client.get("/rooms")).json()] == [a.id, b.id]


async def test_attachment_is_served_by_id(client, harness: Harness, tmp_path: Path):
    room = harness.rooms.create("r", ["personal-assistant", "research-partner"])
    f = tmp_path / "diagram.txt"
    f.write_bytes(b"hello bytes")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_bytes(b"secret")
    post = room.add_post(author="research-partner", text="here", nonce=1)
    post.attachments = [Attachment("a1", "Diagram", str(f)), Attachment("a2", "Gone", str(tmp_path / "gone.txt")),
                        Attachment("a3", "Outside", str(outside))]
    r = await client.get(f"/rooms/{room.id}/attachments/a1")
    assert r.status_code == 200 and r.content == b"hello bytes"
    assert (await client.get(f"/rooms/{room.id}/attachments/a2")).status_code == 404
    assert (await client.get(f"/rooms/{room.id}/attachments/a3")).status_code == 404
    assert (await client.get(f"/rooms/{room.id}/attachments/zzz")).status_code == 404
    assert (await client.get("/rooms/nope/attachments/a1")).status_code == 404
    assert str(f) not in (await client.get("/rooms")).text


async def test_attachment_replaced_by_symlink_is_refused(client, harness: Harness, tmp_path: Path):
    room = harness.rooms.create("r", ["personal-assistant", "research-partner"])
    f = tmp_path / "doc.txt"
    f.write_bytes(b"ok")
    outside = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    outside.write_bytes(b"secret")
    room.add_post(author="research-partner", text="x", nonce=1).attachments = [Attachment("a1", "Doc", str(f))]
    f.unlink()
    try:
        os.symlink(outside, f)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert (await client.get(f"/rooms/{room.id}/attachments/a1")).status_code == 404


async def test_recent_exchange_reads_zenbots_conversation(harness: Harness):
    assert harness.recent_exchange("personal-assistant") == ""
    harness.conversations.record("personal-assistant", "direct q", "done", "direct a")
    assert harness.recent_exchange("personal-assistant") == "User: direct q\nZenBot: direct a"
    harness.conversations.record("personal-assistant", "later q", "failed", "boom")  # only a done exchange counts
    assert harness.recent_exchange("personal-assistant") == "User: direct q\nZenBot: direct a"


async def test_rebind_keeps_rooms_and_reregisters_tools(harness: Harness):
    room = harness.rooms.create("r", ["personal-assistant", "research-partner"])
    harness.rebind(ScriptedProvider(), MockScorer(scores()))
    assert [r.id for r in harness.rooms.list()] == [room.id]
    assert harness.rooms.store is not None and "attach_file" in harness.tools.names()
    assert harness.orchestrator is not None and harness.orchestrator.rooms is harness.rooms
    assert harness.rooms.on_lead_post == harness.orchestrator.refresh_topic


def direct_exchange(harness: Harness, task: str, result: str, age_minutes: float = 0.0):
    from datetime import datetime, timedelta, timezone

    ex = harness.conversations.record("personal-assistant", task, "done", result)
    ex.at = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat(timespec="seconds")
    return ex


async def test_recent_exchange_only_while_the_chat_is_recent(harness: Harness, monkeypatch):
    direct_exchange(harness, "old q", "old a", age_minutes=31)
    assert harness.recent_exchange("personal-assistant") == ""
    direct_exchange(harness, "new q", "new a", age_minutes=29)
    assert harness.recent_exchange("personal-assistant") == "User: new q\nZenBot: new a"
    monkeypatch.setenv("ZEN_ROUTE_CONTEXT_MINUTES", "5")
    direct_exchange(harness, "ten q", "ten a", age_minutes=10)
    assert harness.recent_exchange("personal-assistant") == ""


async def test_recent_exchange_is_dropped_after_a_routed_message(client, harness: Harness):
    direct_exchange(harness, "direct q", "direct a")
    assert harness.recent_exchange("personal-assistant") == "User: direct q\nZenBot: direct a"
    r = await client.post("/orchestrate", json={"text": "How do I become a beekeeper in Ohio?"})
    assert r.json()["outcome"] == "room"
    await harness.rooms.settle(r.json()["room_id"])
    assert harness.recent_exchange("personal-assistant") == ""


async def test_recent_exchange_ignores_one_before_a_saved_routed_exchange(harness: Harness):
    # after a restart the in-memory routed time is gone; the saved routed exchange still counts
    direct_exchange(harness, "direct q", "direct a")
    harness.conversations.record("personal-assistant", "beekeeping?", "routed", "(Sent to the room 'Bees'.)")
    harness._last_routed_at = None
    assert harness.recent_exchange("personal-assistant") == ""
    direct_exchange(harness, "after q", "after a")  # a direct exchange after the routed one counts again
    assert harness.recent_exchange("personal-assistant") == "User: after q\nZenBot: after a"


async def test_routed_message_is_recorded_for_zenbot(client, harness: Harness):
    r = await client.post("/orchestrate", json={"text": "How do I become a beekeeper in Ohio?"})
    assert r.json()["outcome"] == "room"
    await harness.rooms.settle(r.json()["room_id"])
    name = harness.rooms.get(r.json()["room_id"]).name
    ex = harness.conversations.current("personal-assistant").exchanges[-1]
    assert (ex.user, ex.outcome, ex.reply) == ("How do I become a beekeeper in Ohio?", "routed", f"(Sent to the room {name!r}.)")


async def test_orchestrate_refused_text_falls_back_to_task(client, harness: Harness):
    r = await client.post("/orchestrate", json={"text": "my wifi password: hunter2222"})
    assert r.status_code == 200 and r.json()["outcome"] == "task" and "refused" in r.json()["fallback"]
    assert harness.rooms.list() == []


async def test_orchestrate_room_error_falls_back_to_task(client, harness: Harness, monkeypatch):
    from src.rooms.room import RoomError

    async def boom(*a, **k):
        raise RoomError("a room carries text only; refused: credential-assignment")

    monkeypatch.setattr(harness.orchestrator, "route", boom)
    r = await client.post("/orchestrate", json={"text": "and the hive?"})
    assert r.status_code == 200 and r.json()["outcome"] == "task" and "refused" in r.json()["fallback"]
    errs = [e.data for e in harness.ledger.tail(kind="error") if e.data.get("where") == "orchestrate"]
    assert errs and "refused" in errs[-1]["error"]


async def test_delete_refused_while_a_turn_holds_the_floor(client, harness: Harness):
    room = harness.rooms.create("r", ["personal-assistant", "research-partner"], lead="personal-assistant")
    turn = harness.rooms.turns.request(room, "research-partner")
    r = await client.delete(f"/rooms/{room.id}")
    assert r.status_code == 409 and "busy" in r.json()["detail"] and room.open_turn is not None
    harness.rooms.turns.release(room, turn.nonce, reason="test")
    assert (await client.delete(f"/rooms/{room.id}")).status_code == 204


async def test_attachment_is_served_inline_in_a_sandbox(client, harness: Harness, tmp_path: Path):
    room = harness.rooms.create("r", ["personal-assistant", "research-partner"])
    f = tmp_path / "diagram.html"
    f.write_text("<script>alert(1)</script>", encoding="utf-8")
    room.add_post(author="research-partner", text="here", nonce=1).attachments = [Attachment("a1", "Diagram", str(f))]
    r = await client.get(f"/rooms/{room.id}/attachments/a1")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["content-security-policy"] == PREVIEW_CSP  # a bot-made page is network-closed until opened
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_a_failed_routed_record_still_returns_the_route(client, harness: Harness, monkeypatch):
    def broken(c):
        raise OSError("disk full")

    monkeypatch.setattr(harness.conversations.store, "save", broken)
    r = await client.post("/orchestrate", json={"text": "How do I become a beekeeper in Ohio?"})
    assert r.status_code == 200 and r.json()["outcome"] == "room"
    monkeypatch.undo()
    await harness.rooms.settle(r.json()["room_id"])
    errs = [e.data for e in harness.ledger.tail(kind="error") if e.data.get("where") == "orchestrate_record"]
    assert errs and errs[0]["error"] == "OSError: disk full"

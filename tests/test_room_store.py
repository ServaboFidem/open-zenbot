"""Room persistence: fields, attachments, the store, archive and delete."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.harness.approvals import ApprovalStore
from src.harness.bot import Bot
from src.harness.loop import TaskLoop
from src.harness.tools import ToolRunner
from src.memory.ledger import Ledger
from src.rooms.room import Attachment, RoomConfig, RoomError, RoomService
from src.rooms.store import RoomStore

from .conftest import ScriptedProvider


def make(provider, ledger, tmp_path, store=None, **cfg) -> tuple[RoomService, dict[str, Bot]]:
    names = ["lead", "writer", "critic"]
    bs = {n: Bot(name=n.title(), role=f"{n} work", id=n) for n in names}
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path, timeout=10), max_steps=5)
    return RoomService(ledger, loop, bs, RoomConfig(**cfg), store=store), bs


def test_room_round_trips_through_the_store(ledger: Ledger, tmp_path: Path):
    store = RoomStore(tmp_path / "rooms")
    svc, _ = make(ScriptedProvider(), ledger, tmp_path, store=store)
    room = svc.create("Becoming a beekeeper in Ohio", ["lead", "writer"], lead="lead", topic="Ohio beekeeping")
    post = room.add_post(author="writer", text="Register with ODA.", nonce=1)
    post.attachments.append(Attachment(id="a1", title="Steps", path=str(tmp_path / "d.html")))
    svc._persist(room)
    again = RoomService(ledger, svc.loop, svc.bots, store=store)
    assert again.load() == 1
    r = again.get(room.id)
    assert (r.name, r.lead, r.topic, r.archived, r.members) == (room.name, "lead", "Ohio beekeeping", False, ["lead", "writer"])
    assert [p.text for p in r.posts] == ["Register with ODA."] and r.posts[0].attachments[0].path.endswith("d.html")
    assert "path" not in r.to_dict()["posts"][0]["attachments"][0]
    assert r.to_dict()["posts"][0]["attachments"][0]["name"] == "d.html"
    assert r.to_dict()["last_session"] is None


@pytest.mark.asyncio
async def test_refused_posts_never_reach_disk(ledger: Ledger, tmp_path: Path):
    store = RoomStore(tmp_path / "rooms")
    svc, _ = make(ScriptedProvider(), ledger, tmp_path, store=store)
    room = svc.create("pair", ["lead", "writer"])
    with pytest.raises(RoomError):
        await svc.post(room.id, "user", "key sk-or-v1-abcdefghijklmnopqrstuvwxyz")
    saved = json.loads((tmp_path / "rooms" / f"{room.id}.json").read_text(encoding="utf-8"))
    assert saved["posts"] == []


@pytest.mark.asyncio
async def test_user_posts_are_their_own_root_and_persist(ledger: Ledger, tmp_path: Path):
    store = RoomStore(tmp_path / "rooms")
    svc, _ = make(ScriptedProvider(), ledger, tmp_path, store=store)
    room = svc.create("pair", ["lead", "writer"])
    post = await svc.post(room.id, "user", "hello")
    assert post.root == post.id
    saved = json.loads((tmp_path / "rooms" / f"{room.id}.json").read_text(encoding="utf-8"))
    assert [p["text"] for p in saved["posts"]] == ["hello"]


def test_archive_and_delete(ledger: Ledger, tmp_path: Path):
    store = RoomStore(tmp_path / "rooms")
    svc, _ = make(ScriptedProvider(), ledger, tmp_path, store=store)
    room = svc.create("pair", ["lead", "writer"])
    path = tmp_path / "rooms" / f"{room.id}.json"
    assert svc.archive(room.id, True).archived is True
    assert json.loads(path.read_text(encoding="utf-8"))["archived"] is True
    room.waiters = ["lead"]
    with pytest.raises(RoomError, match="room is busy"):
        svc.delete(room.id)
    room.waiters = []
    svc.delete(room.id)
    assert not path.exists() and svc.list() == []


def test_a_corrupt_room_file_is_skipped(ledger: Ledger, tmp_path: Path):
    (tmp_path / "rooms").mkdir()
    (tmp_path / "rooms" / "bad.json").write_text("{not json", encoding="utf-8")
    svc, _ = make(ScriptedProvider(), ledger, tmp_path, store=RoomStore(tmp_path / "rooms"))
    assert svc.load() == 0 and ledger.tail(kind="error")[-1].data["where"] == "room_store"


def test_room_turn_default_deadline_is_ten_minutes():
    assert RoomConfig().turn_deadline_s == 600
    assert RoomConfig().max_invites == 2


def test_persist_after_delete_writes_nothing(ledger: Ledger, tmp_path: Path):
    svc, _ = make(ScriptedProvider(), ledger, tmp_path, store=RoomStore(tmp_path / "rooms"))
    room = svc.create("r", ["lead", "writer"], lead="lead")
    svc.delete(room.id)
    svc._persist(room)
    assert not (tmp_path / "rooms" / f"{room.id}.json").exists()


async def test_a_member_whose_bot_is_gone_does_not_wedge_the_room(ledger: Ledger, tmp_path: Path):
    store = RoomStore(tmp_path / "rooms")
    svc, bots = make(ScriptedProvider(), ledger, tmp_path, store=store)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    # restart with the writer's prompt gone
    again = RoomService(ledger, svc.loop, {"lead": bots["lead"]}, RoomConfig(), store=store)
    again.load()
    await again.post(room.id, "user", "@writer go")
    await again.settle(room.id)
    r = again.get(room.id)
    assert r.open_turn is None and r.waiters == []
    skipped = [e.data for e in ledger.tail(kind="room_turn", session_id=room.id) if e.data.get("event") == "skipped"]
    assert skipped and skipped[-1]["reason"] == "unknown bot"
    assert "You are writer (@writer)" in again.thread_text(r, "writer", r.posts[-1])  # name falls back to the id
    again.delete(room.id)
    assert again.list() == []

"""rooms: 2 to 6 bots, one active speaker with a deadline and a parent
chain, mentions force a bot to act, replies steer the thread, direct
handoffs by reference with one owner per stage, silent helpers that
never post, text-only posts that refuse secrets and approvals, ledger
for every turn and handoff, and the API endpoints."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.approvals import ApprovalStore
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.harness.loop import SessionState, TaskLoop
from src.harness.tools import ToolRunner
from src.memory.exclusion import exclusion
from src.memory.ledger import Ledger
from src.rooms.handoff import HandoffError, HandoffState, HandoffStore
from src.rooms.helpers import SilentHelper
from src.rooms.room import RoomConfig, RoomError, RoomService
from src.rooms.turns import TurnState
from src.skills.catalog import Catalog

from .conftest import ScriptedProvider, completion


def bots(n: int) -> list[Bot]:
    names = ["lead", "writer", "critic", "ops", "data", "qa", "extra"]
    return [Bot(name=names[i].title(), role=f"{names[i]} work", id=names[i]) for i in range(n)]


def make(provider, ledger, tmp_path, members=None, **cfg) -> tuple[RoomService, dict[str, Bot]]:
    bs = {b.id: b for b in (members or bots(3))}
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path, timeout=10), max_steps=5)
    return RoomService(ledger, loop, bs, RoomConfig(**cfg)), bs


# -- membership ------------------------------------------------------------


def test_room_holds_two_to_six_bots(ledger: Ledger, tmp_path: Path):
    svc, _ = make(ScriptedProvider(), ledger, tmp_path, members=bots(7))
    with pytest.raises(RoomError):
        svc.create("solo", ["lead"])
    with pytest.raises(RoomError):
        svc.create("crowd", ["lead", "writer", "critic", "ops", "data", "qa", "extra"])
    room = svc.create("pair", ["lead", "writer"])
    assert room.members == ["lead", "writer"] and room.active_speaker is None and room.running_sessions == {}
    svc.set_members(room.id, ["lead", "writer", "critic"])
    assert svc.get(room.id).members == ["lead", "writer", "critic"]
    with pytest.raises(RoomError):
        svc.set_members(room.id, ["lead", "ghost"])
    assert [e.data["event"] for e in ledger.tail(kind="room", session_id=room.id)] == ["created", "members"]


# -- turns -----------------------------------------------------------------


def test_one_active_speaker_with_deadline_and_parent_chain(ledger: Ledger, tmp_path: Path):
    svc, _ = make(ScriptedProvider(), ledger, tmp_path, turn_deadline_s=30)
    room = svc.create("r", ["lead", "writer"])
    t1 = svc.turns.request(room, "lead")
    assert t1.nonce == 1 and t1.parent_nonce is None and room.active_speaker == "lead"
    assert t1.deadline - t1.started_at == pytest.approx(30, abs=0.5)
    with pytest.raises(RoomError):
        svc.turns.request(room, "writer")  # one speaker at a time
    with pytest.raises(RoomError):
        svc.turns.deliver(room, nonce=99, text="wrong nonce")
    post = svc.turns.deliver(room, nonce=1, text="plan: three steps")
    assert post.nonce == 1 and post.author == "lead" and room.active_speaker is None
    t2 = svc.turns.request(room, "writer")
    assert t2.nonce == 2 and t2.parent_nonce == 1
    events = [e.data["event"] for e in ledger.tail(kind="room_turn", session_id=room.id)]
    assert events == ["requested", "delivered", "requested"]


def test_expired_turn_frees_the_floor(ledger: Ledger, tmp_path: Path):
    svc, _ = make(ScriptedProvider(), ledger, tmp_path, turn_deadline_s=0.01)
    room = svc.create("r", ["lead", "writer"])
    t1 = svc.turns.request(room, "lead")
    time.sleep(0.02)
    t2 = svc.turns.request(room, "writer")  # lead's deadline passed, the floor is free
    assert t2.nonce == 2 and room.active_speaker == "writer" and t1.state == TurnState.expired
    with pytest.raises(RoomError):
        svc.turns.deliver(room, nonce=1, text="too late")
    assert ledger.tail(kind="room_turn", session_id=room.id)[1].data["event"] == "expired"


# -- posts, mentions, replies ----------------------------------------------


async def test_mention_forces_the_bot_to_act(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("Writer here: draft attached in words.")])
    svc, _ = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer", "critic"])

    p1 = await svc.post(room.id, "user", "@writer please draft the intro")
    assert p1.mentions == ["writer"] and p1.author == "user"
    await svc.settle(room.id)
    posts = svc.get(room.id).posts
    assert [p.author for p in posts] == ["user", "writer"] and posts[1].parent == p1.id
    assert "Writer here" in posts[1].text
    # the writer's session ran on its own loop with the thread as its task
    assert "@writer please draft the intro" in provider.calls[0][-1].content
    assert svc.get(room.id).running_sessions == {}  # finished


async def test_a_room_turn_recalls_on_the_message_not_the_turn_prompt(ledger: Ledger, tmp_path: Path):
    """The turn's task is the whole thread plus instructions; embedding that for recall was slow and
    matched the room's boilerplate. Recall searches the post the bot was asked to act on."""
    asked: list[str] = []

    class Spy:
        kind = "spy"

        async def recall(self, bot, task, session_id=""):
            asked.append(task)
            return []

    svc, _ = make(ScriptedProvider([completion("Writer: done.")]), ledger, tmp_path)
    svc.loop.recall = Spy()
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "@writer how do I keep bees in Ohio?")
    await svc.settle(room.id)
    assert asked == ["@writer how do I keep bees in Ohio?"]


def test_a_session_outside_a_room_recalls_on_its_task():
    from src.harness.loop import Session

    s = Session(bot_id="b", task="the task")
    assert s.recall_query == ""  # empty: the loop recalls on the task


async def test_reply_steers_the_thread_to_the_replied_author(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("Writer: first draft."), completion("Writer: tightened.")])
    svc, _ = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer", "critic"])
    await svc.post(room.id, "user", "@writer draft it")
    await svc.settle(room.id)
    first = svc.get(room.id).posts[-1]
    p = await svc.post(room.id, "user", "tighten the second line", reply_to=first.id)
    assert p.reply_to == first.id and p.mentions == []
    await svc.settle(room.id)
    posts = svc.get(room.id).posts
    assert [x.author for x in posts] == ["user", "writer", "user", "writer"] and posts[-1].parent == p.id
    assert len(provider.calls) == 2  # nobody else was woken
    with pytest.raises(RoomError):
        await svc.post(room.id, "user", "x", reply_to="nope")


async def test_bot_turn_is_bounded_and_ledgered(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("done")])
    svc, _ = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "@lead go")
    await svc.settle(room.id)
    turn = ledger.tail(kind="room_turn", session_id=room.id)
    assert [e.data["event"] for e in turn] == ["requested", "delivered"]
    assert turn[1].data["speaker"] == "lead" and turn[1].data["nonce"] == 1 and turn[1].data["task_session"]
    posts = ledger.tail(kind="room_post", session_id=room.id)
    assert len(posts) == 2 and posts[0].data["author"] == "user"


async def test_paused_bot_holds_the_floor_until_stopped(ledger: Ledger, tmp_path: Path):
    """A provider loss pauses the turn, and the paused time does not count
    against the deadline: the floor is held, visible as the active speaker,
    until the user resumes or stops the session. A stop releases it."""
    from src.providers.interface import ProviderAuthError

    provider = ScriptedProvider([ProviderAuthError("bad key")])
    svc, _ = make(provider, ledger, tmp_path, turn_deadline_s=0.05)
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "@lead go")
    await asyncio.sleep(0.2)  # well past the deadline
    sid = room.running_sessions.get("lead")
    assert sid and svc.sessions[sid].state == SessionState.paused and room.active_speaker == "lead"
    svc.sessions[sid].stop_requested = True
    svc.loop.pause.release_for_stop(sid)
    await svc.settle(room.id)
    assert room.active_speaker is None and room.running_sessions == {}
    assert [p.author for p in room.posts] == ["user"]
    assert ledger.tail(kind="room_turn", session_id=room.id)[-1].data["event"] == "released"


async def test_cancelled_turn_releases_the_floor(ledger: Ledger, tmp_path: Path):
    from src.providers.interface import ProviderUnavailable

    provider = ScriptedProvider([ProviderUnavailable("offline")])
    svc, _ = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "@lead go")
    await asyncio.sleep(0.1)
    sid = room.running_sessions["lead"]
    svc.tasks[sid].cancel()  # what Harness.stop does to a session waiting on an approval
    await svc.settle(room.id)
    assert room.active_speaker is None and room.running_sessions == {}
    assert ledger.tail(kind="room_turn", session_id=room.id)[-1].data["event"] == "released"


# -- text only, secrets and approvals refused ------------------------------


async def test_room_post_refuses_secrets_and_approvals(ledger: Ledger, tmp_path: Path):
    svc, _ = make(ScriptedProvider(), ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"])
    with pytest.raises(RoomError, match="openai-style-key"):
        await svc.post(room.id, "user", "use key sk-or-v1-abcdefghijklmnopqrstuvwxyz for the api")
    exclusion.register_secret_value("hunter2hunter2hunter2")
    with pytest.raises(RoomError, match="registered-secret"):
        await svc.post(room.id, "lead", "the password is hunter2hunter2hunter2")
    with pytest.raises(RoomError, match="approval"):
        await svc.post(room.id, "user", "approved", kind="approval_grant")
    assert svc.get(room.id).posts == []
    assert ledger.tail(kind="room_post", session_id=room.id) == []
    # text is all a room carries
    p = await svc.post(room.id, "user", "plain words only")
    assert set(p.to_dict()) == {"id", "room_id", "nonce", "author", "text", "mentions", "reply_to", "parent", "at", "attachments"}


# -- handoffs ----------------------------------------------------------------


def test_direct_handoff_by_reference_one_owner_per_stage(ledger: Ledger):
    store = HandoffStore(ledger)
    h = store.create("lead", "writer", "Draft the intro from the brief", inputs=["post:abc123", "file:briefs/q3.md"])
    assert h.request_id and h.owner == "writer" and h.state == HandoffState.pending and h.parent is None
    with pytest.raises(HandoffError, match="reference"):
        store.create("lead", "writer", "x", inputs=["here is the whole brief text pasted in\nline two"])
    with pytest.raises(HandoffError, match="reference"):
        store.create("lead", "writer", "x", inputs=["sk-or-v1-abcdefghijklmnopqrstuvwxyz"])
    with pytest.raises(HandoffError, match="openai-style-key"):
        store.create("lead", "writer", "use sk-or-v1-abcdefghijklmnopqrstuvwxyz", inputs=[])
    with pytest.raises(HandoffError, match="owner"):
        store.complete(h.request_id, by="critic", result_ref="post:zzz")
    store.accept(h.request_id, by="writer")
    done = store.complete(h.request_id, by="writer", result_ref="post:def456")
    assert done.state == HandoffState.done and done.result_ref == "post:def456"
    h2 = store.create("writer", "critic", "Review the intro", inputs=["post:def456"], parent=h.request_id)
    assert h2.parent == h.request_id and h2.owner == "critic"
    with pytest.raises(HandoffError):
        store.complete(h.request_id, by="writer", result_ref="post:again")  # already done
    events = [e.data["event"] for e in ledger.tail(kind="handoff")]
    assert events == ["created", "accepted", "done", "created"]
    assert all(":" in i for e in ledger.tail(kind="handoff") for i in e.data.get("inputs", []))


# -- silent helpers ----------------------------------------------------------


async def test_silent_helper_never_posts_to_the_room(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("helper result: 42 rows")])
    svc, bs = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"])
    helper = SilentHelper(bs["writer"], svc.loop, ledger)
    runner = asyncio.create_task(helper.run())
    await helper.control.put("start")
    await helper.data.put("count the rows in session:lead-1")
    result = await asyncio.wait_for(helper.result.get(), 2)
    await helper.control.put("stop")
    await asyncio.wait_for(runner, 2)
    assert result.text == "helper result: 42 rows" and result.state == SessionState.done
    assert svc.get(room.id).posts == [] and svc.get(room.id).running_sessions == {}
    assert ledger.tail(kind="room_post") == []
    assert [e.data["event"] for e in ledger.tail(kind="helper")] == ["started", "result", "stopped"]


# -- api ---------------------------------------------------------------------


async def test_room_endpoints(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([completion("Lead: on it.")])
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=provider, ledger=Ledger(vault), catalog=Catalog())
    for b in bots(2):
        h.add_bot(b)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        r = await c.post("/rooms", json={"name": "war room", "members": ["lead"]})
        assert r.status_code == 422
        r = await c.post("/rooms", json={"name": "war room", "members": ["lead", "writer"]})
        assert r.status_code == 200
        room_id = r.json()["id"]
        r = await c.put(f"/rooms/{room_id}/members", json={"members": ["writer", "lead"]})
        assert r.json()["members"] == ["writer", "lead"]
        r = await c.post(f"/rooms/{room_id}/posts", json={"text": "key sk-or-v1-abcdefghijklmnopqrstuvwxyz"})
        assert r.status_code == 422 and "openai-style-key" in r.json()["detail"]
        r = await c.post(f"/rooms/{room_id}/posts", json={"text": "@lead kick off"})
        assert r.status_code == 200 and r.json()["mentions"] == ["lead"]
        for _ in range(300):
            status = (await c.get(f"/rooms/{room_id}/turn")).json()
            if status["active_speaker"] is None and len(status["posts"]) == 2:
                break
            await asyncio.sleep(0.01)
        assert status["posts"][-1]["author"] == "lead" and status["last_nonce"] == 1 and status["running_sessions"] == {}
        assert (await c.get("/rooms")).json()[0]["id"] == room_id


# -- review fixes ------------------------------------------------------------


class SlowProvider(ScriptedProvider):
    def __init__(self, script, delay: float):
        super().__init__(script)
        self.delay = delay

    async def complete(self, messages, *, tools=None, model=None, session_id=None):
        await asyncio.sleep(self.delay)
        return await super().complete(messages, tools=tools, model=model, session_id=session_id)


async def test_every_mentioned_bot_answers_in_mention_order(ledger: Ledger, tmp_path: Path):
    provider = SlowProvider([completion("Lead: here."), completion("Writer: here."), completion("Critic: here.")], delay=0.3)
    # Three turns (0.9 s) outlast one deadline, so a shared deadline would skip a bot; each turn alone has 0.5 s to
    # spare, which a slow CI runner needs.
    svc, _ = make(provider, ledger, tmp_path, turn_deadline_s=0.8)
    room = svc.create("r", ["lead", "writer", "critic"])
    await svc.post(room.id, "user", "@lead @writer @critic sound off")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "lead", "writer", "critic"]
    assert "skipped" not in [e.data["event"] for e in ledger.tail(kind="room_turn", session_id=room.id)]


async def test_turn_deadline_pauses_while_the_session_is_paused(ledger: Ledger, tmp_path: Path):
    from src.providers.interface import ProviderUnavailable

    provider = ScriptedProvider([ProviderUnavailable("offline"), completion("Lead: back online.")])
    svc, _ = make(provider, ledger, tmp_path, turn_deadline_s=0.05)
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "@lead go")
    await asyncio.sleep(0.2)  # well past the deadline
    sid = room.running_sessions.get("lead")
    assert sid and svc.sessions[sid].state == SessionState.paused and room.active_speaker == "lead"
    assert sid in svc.tasks  # stop() can reach it
    assert svc.loop.pause.resume(sid, reason="user")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "lead"]


async def test_bot_mentions_chain_with_a_hop_cap(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("@writer draft it"), completion("@lead done, your turn"), completion("@writer again")])
    svc, _ = make(provider, ledger, tmp_path, max_hops=2)
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "@lead kick off")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "lead", "writer"]
    assert len(provider.calls) == 2  # the writer's mention of lead sits at the hop cap and is inert


async def test_helper_keeps_work_that_arrives_with_a_control_item(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("r1")])
    svc, bs = make(provider, ledger, tmp_path)
    helper = SilentHelper(bs["writer"], svc.loop, ledger)
    await helper.control.put("start")
    await helper.control.put("ping")
    await helper.data.put("work 1")
    runner = asyncio.create_task(helper.run())
    result = await asyncio.wait_for(helper.result.get(), 2)
    assert result.text == "r1"
    await helper.control.put("stop")
    await asyncio.wait_for(runner, 2)


# -- conversations -----------------------------------------------------------


def test_an_old_room_record_becomes_one_conversation(ledger: Ledger):
    from src.rooms.room import Room

    old = {
        "id": "r1", "name": "Bees", "members": ["lead", "writer"], "last_nonce": 1, "lead": "lead",
        "topic": "Beekeeping in Ohio", "archived": False, "created_at": "2026-11-01T00:00:00+00:00",
        "last_active": "2026-11-02T00:00:00+00:00",
        "posts": [
            {"id": "p1", "room_id": "r1", "nonce": 0, "author": "user", "text": "How do I keep bees?", "at": "2026-11-01T00:00:00+00:00"},
            {"id": "p2", "room_id": "r1", "nonce": 1, "author": "lead", "text": "Start with a hive.", "at": "2026-11-02T00:00:00+00:00"},
        ],
    }
    room = Room.from_record(old, ledger)
    assert len(room.conversations) == 1
    conv = room.conversations[0]
    assert room.conversation_id == conv.id and conv.topic == "Beekeeping in Ohio" and room.topic == "Beekeeping in Ohio"
    assert conv.title == "How do I keep bees?"
    assert [p.conversation for p in room.posts] == [conv.id, conv.id]
    assert [p.id for p in room.current_posts()] == ["p1", "p2"]
    again = Room.from_record(room.to_record(), ledger)  # the new shape round-trips
    assert again.conversation_id == conv.id and [p.conversation for p in again.posts] == [conv.id, conv.id]
    assert again.topic == "Beekeeping in Ohio" and [c.id for c in again.conversations] == [conv.id]


def test_a_migrated_room_keeps_its_conversation_id_across_loads(ledger: Ledger, tmp_path: Path):
    import json

    from src.rooms.store import RoomStore

    old = {
        "id": "r1", "name": "Bees", "members": ["b0", "b1"], "last_nonce": 0, "lead": "b0", "topic": "Bees",
        "posts": [{"id": "p1", "room_id": "r1", "nonce": 0, "author": "user", "text": "How do I keep bees?", "at": "2026-11-01T00:00:00+00:00"}],
    }
    (tmp_path / "rooms").mkdir()
    (tmp_path / "rooms" / "r1.json").write_text(json.dumps(old))

    def load_once() -> str:
        svc, _ = make(ScriptedProvider(), ledger, tmp_path)
        svc.store = RoomStore(tmp_path / "rooms")
        assert svc.load() == 1
        return svc.get("r1").conversation_id

    first = load_once()
    assert "conversations" in json.loads((tmp_path / "rooms" / "r1.json").read_text())  # saved in the new shape
    assert load_once() == first


async def test_new_conversation_hides_old_posts_from_the_thread(ledger: Ledger, tmp_path: Path):
    svc, _ = make(ScriptedProvider(), ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "OLD-POST about hives", schedule=False)
    svc.new_conversation(room.id)
    trigger = await svc.post(room.id, "user", "fresh start", schedule=False)
    task = svc.thread_text(room, "writer", trigger)
    assert "OLD-POST" not in task and "fresh start" in task
    svc.new_conversation(room.id)
    assert room.to_dict()["posts"] == [] and len(room.posts) == 2  # the old posts are kept, just not shown


async def test_resuming_restores_posts_and_topic(ledger: Ledger, tmp_path: Path):
    svc, _ = make(ScriptedProvider(), ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], topic="Bees")
    first = room.conversation_id
    await svc.post(room.id, "user", "about bees", schedule=False)
    second = svc.new_conversation(room.id)
    room.topic = "Taxes"
    await svc.post(room.id, "user", "about taxes", schedule=False)
    svc.select_conversation(room.id, first)
    assert room.conversation_id == first and room.topic == "Bees"
    assert [p["text"] for p in room.to_dict()["posts"]] == ["about bees"]
    svc.select_conversation(room.id, second.id)
    assert room.topic == "Taxes" and [p.text for p in room.current_posts()] == ["about taxes"]
    with pytest.raises(RoomError):
        svc.select_conversation(room.id, "nope")
    d = room.to_dict()
    assert d["conversation_id"] == second.id
    assert [c["id"] for c in d["conversations"]] == [second.id, first]  # newest first
    assert [c["title"] for c in d["conversations"]] == ["about taxes", "about bees"]


async def test_posts_are_stamped_with_the_current_conversation(ledger: Ledger, tmp_path: Path):
    svc, _ = make(ScriptedProvider(), ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"])
    p1 = await svc.post(room.id, "user", "one", schedule=False)
    conv = svc.new_conversation(room.id)
    p2 = await svc.post(room.id, "user", "two", schedule=False)
    assert p1.conversation != conv.id and p2.conversation == conv.id == room.conversation_id
    assert p2.to_record()["conversation"] == conv.id


async def test_a_turn_running_across_a_switch_posts_where_it_started(ledger: Ledger, tmp_path: Path):
    provider = SlowProvider([completion("Writer: done.")], delay=0.1)
    svc, _ = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"])
    started = room.conversation_id
    await svc.post(room.id, "user", "@writer draft it")
    await asyncio.sleep(0.03)  # the turn is running
    svc.new_conversation(room.id)
    await svc.settle(room.id)
    reply = room.posts[-1]
    assert reply.author == "writer" and reply.conversation == started != room.conversation_id
    assert room.current_posts() == [] and provider.session_ids == [started]


async def test_room_turns_carry_the_room_conversation_id(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("Writer: here.")])
    svc, _ = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "@writer hello")
    await svc.settle(room.id)
    assert room.conversation_id and provider.session_ids == [room.conversation_id]


def test_new_conversation_starts_with_no_topic(ledger: Ledger, tmp_path: Path):
    svc, _ = make(ScriptedProvider(), ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], topic="Bees")
    assert room.topic == "Bees" and room.conversations[0].topic == "Bees"
    conv = svc.new_conversation(room.id)
    assert room.topic == "" and conv.topic == "" and room.conversation_id == conv.id


async def test_room_conversation_endpoints(vault: Path, tmp_path: Path):
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    for b in bots(2):
        h.add_bot(b)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        room_id = (await c.post("/rooms", json={"name": "r", "members": ["lead", "writer"]})).json()["id"]
        r = await c.post(f"/rooms/{room_id}/posts", json={"text": "first thing"})
        first = (await c.get(f"/rooms/{room_id}/turn")).json()["conversation_id"]
        r = await c.post(f"/rooms/{room_id}/conversations")
        assert r.status_code == 200 and r.json()["posts"] == [] and r.json()["conversation_id"] != first
        assert len(r.json()["conversations"]) == 2
        r = await c.post(f"/rooms/{room_id}/conversations/{first}/current")
        assert r.status_code == 200 and r.json()["conversation_id"] == first
        assert [p["text"] for p in r.json()["posts"]] == ["first thing"]
        assert (await c.post(f"/rooms/{room_id}/conversations/nope/current")).status_code == 422
        assert (await c.post("/rooms/nope/conversations")).status_code == 404


def test_a_closed_bot_cannot_be_a_room_member(ledger: Ledger, tmp_path: Path):
    svc, bs = make(ScriptedProvider(), ledger, tmp_path)
    bs["critic"].closed = True
    assert svc.joinable(bs["writer"]) and not svc.joinable(bs["critic"])
    with pytest.raises(ValueError, match="critic"):
        svc.create("r", ["lead", "critic"])
    room = svc.create("r", ["lead", "writer"])
    with pytest.raises(ValueError, match="critic"):
        svc.set_members(room.id, ["lead", "writer", "critic"])
    assert room.members == ["lead", "writer"]


async def test_a_closed_member_started_no_turn(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("Writer here.")])
    svc, bs = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer", "critic"])
    bs["critic"].closed = True  # closed after it joined (a saved room, or a prompt edited since)
    await svc.post(room.id, "user", "@critic take this")
    await svc.settle(room.id)
    assert [p.author for p in svc.get(room.id).posts] == ["user"] and provider.calls == []


async def test_room_routes_refuse_a_closed_bot(vault: Path, tmp_path: Path):
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    for b in bots(3):
        h.add_bot(b)
    h.bots["critic"].closed = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        r = await c.post("/rooms", json={"name": "r", "members": ["lead", "critic"]})
        assert r.status_code == 422 and "critic" in r.json()["detail"]
        room_id = (await c.post("/rooms", json={"name": "r", "members": ["lead", "writer"]})).json()["id"]
        r = await c.put(f"/rooms/{room_id}/members", json={"members": ["lead", "writer", "critic"]})
        assert r.status_code == 422 and "critic" in r.json()["detail"]


def test_a_narrator_cannot_be_a_room_member(ledger: Ledger, tmp_path: Path):
    svc, bs = make(ScriptedProvider(), ledger, tmp_path)
    bs["critic"].narrates = True
    assert svc.joinable(bs["writer"]) and not svc.joinable(bs["critic"])


def test_a_skill_tuner_cannot_be_a_room_member(ledger: Ledger, tmp_path: Path):
    svc, bs = make(ScriptedProvider(), ledger, tmp_path)
    bs["critic"].tunes_skills = True
    assert svc.joinable(bs["writer"]) and not svc.joinable(bs["critic"])

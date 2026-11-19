"""Room-only tools: the lead invites a bot mid-conversation; any member
attaches a file it made so the GUI can open it from the post."""

from __future__ import annotations

import os
from pathlib import Path

from src.harness.approvals import ApprovalStore
from src.harness.bot import Bot
from src.harness.loop import TaskLoop
from src.harness.tools import ToolRunner, current_call
from src.memory.ledger import Ledger
from src.providers.interface import ToolCall
from src.rooms.room import PASS, RoomConfig, RoomService
from src.rooms.store import RoomStore

from .conftest import ScriptedProvider, completion


def make(provider, ledger, cwd: Path, store: RoomStore | None = None, **cfg) -> RoomService:
    names = ["lead", "writer", "critic", "ops", "artist", "coder"]
    bs = {n: Bot(name=n.title(), role=f"{n} work", id=n) for n in names}
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(cwd, timeout=10), max_steps=5)
    svc = RoomService(ledger, loop, bs, RoomConfig(**cfg), store=store)
    svc.register_tools(loop.tools)
    return svc


async def call(svc: RoomService, caller: str, session_id: str, name: str, **arguments) -> str:
    token = current_call.set((caller, session_id))
    try:
        result = await svc.loop.tools.run(ToolCall(id="t", name=name, arguments=arguments))
    finally:
        current_call.reset(token)
    assert result.ok, result.error
    return result.output


def test_room_only_tools_are_hidden_outside_rooms(tmp_path: Path):
    t = ToolRunner(tmp_path)
    t.register("invite_to_room", "d", {"type": "object"}, lambda **k: "", room_only=True)
    assert "invite_to_room" not in [s.name for s in t.specs()]
    assert "invite_to_room" in [s.name for s in t.specs(room=True)]
    assert "shell" in [s.name for s in t.specs(room=True)]


def test_attach_file_is_offered_everywhere_invite_only_in_rooms(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider(), ledger, tmp_path)
    outside = [s.name for s in svc.loop.tools.specs()]
    inside = [s.name for s in svc.loop.tools.specs(room=True)]
    assert "invite_to_room" not in outside and "attach_file" in outside
    assert "invite_to_room" in inside and "attach_file" in inside


async def test_attach_file_outside_a_room_goes_to_elsewhere(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider(), ledger, tmp_path)
    svc.register_tools(svc.loop.tools, elsewhere=lambda s, p, t: f"elsewhere {s} {p} {t}")
    assert await call(svc, "writer", "x", "attach_file", path="a.html") == "elsewhere x a.html "


async def test_attach_file_outside_a_room_without_elsewhere_refuses(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider(), ledger, tmp_path)
    assert await call(svc, "writer", "x", "attach_file", path="a.html") == "attach_file works only during a room turn."


class ToolsSeen(ScriptedProvider):
    def __init__(self, script):
        super().__init__(script)
        self.tools_seen: list[list[str]] = []

    async def complete(self, messages, *, tools=None, model=None, session_id=None):
        self.tools_seen.append([t.name for t in tools or []])
        return await super().complete(messages, tools=tools, model=model, session_id=session_id)


async def test_room_sessions_see_room_tools_and_others_do_not(ledger: Ledger, tmp_path: Path):
    from src.harness.loop import Session

    provider = ToolsSeen([completion("Writer: done."), completion("plain answer")])
    svc = make(provider, ledger, tmp_path)
    room = svc.create("r", ["writer", "critic"])
    await svc.post(room.id, "user", "@writer go")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "writer"]
    assert svc.turn_of == {}  # removed when the turn's session ended
    await svc.loop.run(svc.bots["writer"], "hi", Session(bot_id="writer", task="hi"))
    in_room, outside = provider.tools_seen
    assert "invite_to_room" in in_room and "attach_file" in in_room
    assert "invite_to_room" not in outside and "attach_file" in outside and "shell" in outside


async def test_lead_invites_a_bot_and_its_mention_starts_it(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Writer: here are the steps."),
        completion("", [("c1", "invite_to_room", {"bot_id": "critic", "job": "diagram it"})]),
        completion("Over to you, @critic."),
        completion("Critic: diagram done."),
        completion(PASS),
    ])
    store = RoomStore(tmp_path / "rooms")
    svc = make(provider, ledger, tmp_path, store=store)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "@writer explain it")
    await svc.settle(room.id)
    assert "critic" in room.members
    assert [p.author for p in room.posts] == ["user", "writer", "lead", "critic"]
    assert room.posts[2].mentions == ["critic"] and room.posts[3].parent == room.posts[2].id
    invited = [e.data for e in ledger.tail(kind="room", session_id=room.id) if e.data.get("event") == "invited"]
    assert invited == [{"event": "invited", "bot": "critic", "job": "diagram it", "root": room.posts[0].id}]
    assert "critic" in RoomStore(tmp_path / "rooms").load_all(ledger)[0].members  # persisted
    tool_msgs = [m.content for m in provider.calls[2] if m.role == "tool"]
    assert tool_msgs == ["Added @critic. Mention @critic with the job in your reply to start it."]
    assert svc.turn_of == {}


async def test_invite_refusals(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider(), ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    svc.turn_of["s1"] = (room.id, "root1")
    svc.turn_of["s2"] = (room.id, "root2")

    out = await call(svc, "lead", "nope", "invite_to_room", bot_id="critic", job="j")
    assert "room turn" in out and room.members == ["lead", "writer"]
    out = await call(svc, "writer", "s1", "invite_to_room", bot_id="critic", job="j")
    assert "lead" in out and room.members == ["lead", "writer"]
    out = await call(svc, "lead", "s1", "invite_to_room", bot_id="ghost", job="j")
    assert "ghost" in out and room.members == ["lead", "writer"]
    out = await call(svc, "lead", "s1", "invite_to_room", bot_id="writer", job="j")
    assert "already" in out and room.members == ["lead", "writer"]

    assert (await call(svc, "lead", "s1", "invite_to_room", bot_id="critic", job="j")).startswith("Added @critic")
    assert (await call(svc, "lead", "s1", "invite_to_room", bot_id="ops", job="j")).startswith("Added @ops")
    out = await call(svc, "lead", "s1", "invite_to_room", bot_id="artist", job="j")
    assert "invite" in out and "artist" not in room.members  # the third invite for one root
    assert (await call(svc, "lead", "s2", "invite_to_room", bot_id="artist", job="j")).startswith("Added @artist")

    small = make(ScriptedProvider(), ledger, tmp_path, max_members=3)
    full = small.create("full", ["lead", "writer", "critic"], lead="lead")
    small.turn_of["s3"] = (full.id, "root3")
    out = await call(small, "lead", "s3", "invite_to_room", bot_id="ops", job="j")
    assert "full" in out and full.members == ["lead", "writer", "critic"]
    invited = [e.data["bot"] for e in ledger.tail(kind="room") if e.data.get("event") == "invited"]
    assert invited == ["critic", "ops", "artist"]


async def test_invite_refuses_a_closed_bot(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider(), ledger, tmp_path)
    svc.bots["critic"].closed = True
    room = svc.create("r", ["lead", "writer"], lead="lead")
    svc.turn_of["s1"] = (room.id, "root1")
    out = await call(svc, "lead", "s1", "invite_to_room", bot_id="critic", job="j")
    assert "critic" in out and "closed" in out and room.members == ["lead", "writer"]
    assert [e for e in ledger.tail(kind="room") if e.data.get("event") == "invited"] == []


async def test_attach_file_lands_on_the_post(ledger: Ledger, tmp_path: Path):
    (tmp_path / "diagram.html").write_text("<svg></svg>", encoding="utf-8")
    provider = ScriptedProvider([
        completion("", [("c1", "attach_file", {"path": "diagram.html", "title": "Steps"})]),
        completion("Here it is."),
    ])
    store = RoomStore(tmp_path / "rooms")
    svc = make(provider, ledger, tmp_path, store=store)
    room = svc.create("r", ["writer", "critic"])
    await svc.post(room.id, "user", "@writer draw it")
    await svc.settle(room.id)
    post = room.posts[-1]
    assert post.author == "writer" and post.text == "Here it is."
    assert len(post.attachments) == 1 and post.attachments[0].title == "Steps"
    assert Path(post.attachments[0].path) == (tmp_path / "diagram.html").resolve()
    assert post.to_dict()["attachments"] == [{"id": post.attachments[0].id, "title": "Steps", "name": "diagram.html"}]
    saved = RoomStore(tmp_path / "rooms").load_all(ledger)[0].posts[-1]
    assert saved.attachments[0].title == "Steps"
    assert svc._attachments == {}


async def test_attach_file_title_defaults_to_the_file_name(ledger: Ledger, tmp_path: Path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "chart.svg").write_text("<svg/>", encoding="utf-8")
    svc = make(ScriptedProvider(), ledger, tmp_path)
    room = svc.create("r", ["writer", "critic"])
    svc.turn_of["s1"] = (room.id, "root1")
    out = await call(svc, "writer", "s1", "attach_file", path=str(tmp_path / "out" / "chart.svg"))
    assert "chart.svg" in out
    assert [a.title for a in svc._attachments["s1"]] == ["chart.svg"]


async def test_attachments_are_dropped_when_the_turn_posts_nothing(ledger: Ledger, tmp_path: Path):
    (tmp_path / "diagram.html").write_text("<svg></svg>", encoding="utf-8")
    provider = ScriptedProvider([
        completion("", [("c1", "attach_file", {"path": "diagram.html"})]),
        completion(""),
        completion("Writer: second try."),
    ])
    svc = make(provider, ledger, tmp_path)
    room = svc.create("r", ["writer", "critic"])
    await svc.post(room.id, "user", "@writer draw it")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user"]
    assert svc._attachments == {} and svc.turn_of == {}
    await svc.post(room.id, "user", "@writer again")
    await svc.settle(room.id)
    assert room.posts[-1].attachments == []  # nothing leaked into the next turn


async def test_attach_file_refuses_escapes(ledger: Ledger, tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sub").mkdir()
    (tmp_path / "x.html").write_text("outside", encoding="utf-8")
    svc = make(ScriptedProvider(), ledger, ws)
    room = svc.create("r", ["writer", "critic"])
    svc.turn_of["s1"] = (room.id, "root1")

    assert "room turn" in await call(svc, "writer", "nope", "attach_file", path="x.html")
    assert "outside" in await call(svc, "writer", "s1", "attach_file", path="../x.html")
    assert "outside" in await call(svc, "writer", "s1", "attach_file", path=str(tmp_path / "x.html"))
    assert "not a file" in await call(svc, "writer", "s1", "attach_file", path="sub")
    assert "no file" in await call(svc, "writer", "s1", "attach_file", path="missing.html")
    try:
        os.symlink(tmp_path / "x.html", ws / "link.html")
    except (OSError, NotImplementedError):
        pass
    else:
        assert "outside" in await call(svc, "writer", "s1", "attach_file", path="link.html")
    assert svc._attachments == {}


async def test_at_the_hop_cap_the_lead_review_offers_no_invite(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider(), ledger, tmp_path, max_hops=2)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    room.add_post(author="writer", text="Writer: steps.", nonce=1)
    trigger = room.posts[-1]
    trigger.hop = 1  # the lead's review post would be hop 2, the cap
    capped = svc.thread_text(room, "lead", trigger, "review")
    assert "invite_to_room" not in capped and "@mention" not in capped
    assert "used up" in capped and PASS in capped
    trigger.hop = 0
    assert "invite_to_room" in svc.thread_text(room, "lead", trigger, "review")


async def test_invite_is_refused_once_the_hand_offs_are_used_up(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Writer: here are the steps."),
        completion("", [("c1", "invite_to_room", {"bot_id": "critic", "job": "diagram it"})]),
        completion("Noted: the steps cover it."),
    ])
    svc = make(provider, ledger, tmp_path, max_hops=2)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "@writer explain it")
    await svc.settle(room.id)
    assert room.members == ["lead", "writer"]
    assert [p.author for p in room.posts] == ["user", "writer", "lead"]
    tool_msgs = [m.content for m in provider.calls[2] if m.role == "tool"]
    assert tool_msgs == ["This conversation has used up its hand-offs for now; post a short note instead."]
    assert svc.turn_of == {}

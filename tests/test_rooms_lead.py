"""The lead's turns inside a room: it takes unaddressed user posts,
reviews each specialist post once, and gets one turn to recover when a
specialist's turn fails. Rooms without a lead behave as before."""

from __future__ import annotations

from pathlib import Path

from src.harness.approvals import ApprovalStore
from src.harness.bot import Bot
from src.harness.loop import TaskLoop
from src.harness.tools import ToolRunner
from src.memory.ledger import Ledger
from src.rooms.room import PASS, Attachment, Post, RoomConfig, RoomService

from .conftest import ScriptedProvider, completion


def make(provider, ledger, tmp_path, **cfg) -> RoomService:
    names = ["lead", "writer", "critic", "ops"]
    bs = {n: Bot(name=n.title(), role=f"{n} work", id=n) for n in names}
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path, timeout=10), max_steps=5)
    return RoomService(ledger, loop, bs, RoomConfig(**cfg))


def lead_sessions(svc: RoomService) -> list:
    return [s for s in svc.sessions.values() if s.bot_id == "lead"]


def turn_events(ledger: Ledger, room_id: str) -> list[dict]:
    return [e.data for e in ledger.tail(kind="room_turn", session_id=room_id)]


async def test_unaddressed_user_post_goes_to_the_lead(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("Lead: let's start with the basics.")])
    svc = make(provider, ledger, tmp_path)
    led = svc.create("led", ["lead", "writer"], lead="lead")
    await svc.post(led.id, "user", "what next?")
    await svc.settle(led.id)
    assert [p.author for p in led.posts] == ["user", "lead"]
    task = lead_sessions(svc)[0].task
    assert "@ops — Ops: ops work" in task and "@critic — Critic: critic work" in task
    assert "@writer — " not in task  # only bots not already in the room
    assert "invite_to_room" in task

    plain = svc.create("plain", ["lead", "writer"])
    await svc.post(plain.id, "user", "what next?")
    await svc.settle(plain.id)
    assert [p.author for p in plain.posts] == ["user"]
    assert len(provider.calls) == 1


async def test_post_with_schedule_false_schedules_nothing(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([])
    svc = make(provider, ledger, tmp_path)
    led = svc.create("led", ["lead", "writer"], lead="lead")
    post = await svc.post(led.id, "user", "@writer hello", schedule=False)
    await svc.settle(led.id)
    assert [p.author for p in led.posts] == ["user"] and post.root == post.id
    assert provider.calls == []


async def test_lead_reviews_a_specialist_post_once(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("Writer: here is the draft."), completion("Focus on the second step first.")])
    svc = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "@writer draft it")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "writer", "lead"]
    assert len(lead_sessions(svc)) == 1  # the lead's own post triggers no review
    task = lead_sessions(svc)[0].task
    assert "@writer's post" in task and PASS in task
    writer_post, lead_post = room.posts[1], room.posts[2]
    assert lead_post.parent == writer_post.id and lead_post.hop == writer_post.hop + 1
    assert lead_post.root == room.posts[0].id and not lead_post.recovery


async def test_lead_review_pass_posts_nothing(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("Writer: done."), completion("  pass \n")])
    svc = make(provider, ledger, tmp_path)
    called: list[Post] = []

    async def on_lead_post(room, post):
        called.append(post)

    svc.on_lead_post = on_lead_post
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "@writer go")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "writer"]
    released = [e for e in turn_events(ledger, room.id) if e["event"] == "released"]
    assert [(e["speaker"], e["reason"]) for e in released] == [("lead", "pass")]
    assert called == []  # a pass delivers nothing, so nothing to refresh


async def test_on_lead_post_runs_after_a_review_post_and_its_errors_are_logged(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("Writer: done."), completion("Check the dates.")])
    svc = make(provider, ledger, tmp_path)
    seen: list[str] = []

    async def on_lead_post(room, post):
        seen.append(post.text)
        raise RuntimeError("topic refresh broke")

    svc.on_lead_post = on_lead_post
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "@writer go")
    await svc.settle(room.id)
    assert seen == ["Check the dates."]
    assert [p.author for p in room.posts] == ["user", "writer", "lead"]
    errors = [e.data for e in ledger.tail(kind="error", session_id=room.id)]
    assert errors and errors[-1]["where"] == "on_lead_post" and "topic refresh broke" in errors[-1]["error"]
    assert room.active_speaker is None


async def test_on_lead_post_is_not_called_for_an_act_turn(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("Lead: answered.")])
    svc = make(provider, ledger, tmp_path)
    called: list[Post] = []

    async def on_lead_post(room, post):
        called.append(post)

    svc.on_lead_post = on_lead_post
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "what next?")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "lead"] and called == []


async def test_a_post_mentioning_the_lead_schedules_it_once(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("@lead done"), completion("Lead: thanks, that covers it.")])
    svc = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "@writer go")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "writer", "lead"]
    assert len(lead_sessions(svc)) == 1
    assert "asked you to act with: @lead done" in lead_sessions(svc)[0].task  # Review Focus 1: the act path, not review
    assert len(provider.calls) == 2


async def test_failed_specialist_turn_gives_the_lead_one_turn(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion(""), completion("@writer again"), completion("")])
    svc = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "@writer go")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "lead"]
    assert len(lead_sessions(svc)) == 1
    task = lead_sessions(svc)[0].task
    assert "@writer's turn ended without a post" in task and "no text" in task
    recovery = room.posts[1]
    assert recovery.recovery and recovery.parent == room.posts[0].id and recovery.hop == 1
    released = [e for e in turn_events(ledger, room.id) if e["event"] == "released"]
    assert [e["speaker"] for e in released] == ["writer", "writer"]
    assert len(provider.calls) == 3


async def test_failed_turn_in_a_plain_room_gets_no_lead_turn(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("")])
    svc = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "@writer go")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user"] and len(provider.calls) == 1


async def test_hop_limit_stops_the_chain(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("Writer: draft."), completion("@writer tighten it"), completion("never")])
    svc = make(provider, ledger, tmp_path, max_hops=2)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "@writer go")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "writer", "lead"]
    assert [p.hop for p in room.posts] == [0, 1, 2]
    assert len(provider.calls) == 2


async def test_hop_limit_stops_the_failure_turn(ledger: Ledger, tmp_path: Path):
    # writer (hop 1) hands to critic and is reviewed by the lead; the critic's turn
    # fails, but its trigger sits at hop 1 and 1 + 1 is not under 2: no failure turn.
    provider = ScriptedProvider([completion("@critic over to you"), completion(""), completion(PASS)])
    svc = make(provider, ledger, tmp_path, max_hops=2)
    room = svc.create("r", ["lead", "writer", "critic"], lead="lead")
    await svc.post(room.id, "user", "@writer go")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "writer"]
    assert len(lead_sessions(svc)) == 1 and "ended without a post" not in lead_sessions(svc)[0].task
    assert len(provider.calls) == 3


async def test_review_prompt_lists_bots_not_in_the_room(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider(), ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    room.add_post(author="writer", text="Writer: draft.", nonce=0)
    task = svc.thread_text(room, "lead", room.posts[-1], "review")
    assert "@ops — Ops: ops work" in task and "@critic — Critic: critic work" in task
    assert "@writer — " not in task and "invite_to_room" in task


async def test_pass_is_honoured_only_in_review_mode(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("PASS")])
    svc = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "what next?")
    await svc.settle(room.id)
    assert [(p.author, p.text) for p in room.posts] == [("user", "what next?"), ("lead", "PASS")]  # act: posted as text

    provider = ScriptedProvider([completion(""), completion("pass")])
    svc = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "@writer go")
    await svc.settle(room.id)
    assert [(p.author, p.text) for p in room.posts] == [("user", "@writer go"), ("lead", "pass")]  # failure: posted


async def test_review_pass_tolerates_trailing_punctuation(ledger: Ledger, tmp_path: Path):
    for reply, passes in [("pass.", True), (" Pass! \n", True), ("PASS .", True),
                          ("Pass — nothing to add", False), ("passing on this", False)]:
        provider = ScriptedProvider([completion("Writer: done."), completion(reply)])
        svc = make(provider, ledger, tmp_path)
        room = svc.create("r", ["lead", "writer"], lead="lead")
        await svc.post(room.id, "user", "@writer go")
        await svc.settle(room.id)
        assert [p.author for p in room.posts] == (["user", "writer"] if passes else ["user", "writer", "lead"]), reply


async def test_thread_names_attached_files_without_their_paths(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider(), ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    room.add_post(author="writer", text="Here's your map.", nonce=0)
    room.posts[-1].attachments.append(Attachment("a1", "Steps diagram", str(tmp_path / "out" / "steps.html")))
    task = svc.thread_text(room, "lead", room.posts[-1], "review")
    assert "[writer] Here's your map. [attached: Steps diagram (steps.html)]" in task
    assert str(tmp_path) not in task


async def test_the_lead_gets_no_failure_turn_for_its_own_failed_turn(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("")])
    svc = make(provider, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "what next?")
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user"]
    assert len(lead_sessions(svc)) == 1 and len(provider.calls) == 1


async def test_invited_bot_prompt_tells_it_to_do_the_job(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider(), ledger, tmp_path)
    led = svc.create("led", ["lead", "writer"], lead="lead")
    plain = svc.create("plain", ["lead", "writer"])
    for room in (led, plain):
        room.add_post(author="user", text="@writer go", nonce=0)
    trigger = led.posts[-1]
    assert "Do it now" in svc.thread_text(led, "writer", trigger)
    invited = svc.thread_text(led, "writer", trigger)
    assert "attach_file" in invited and "diagram" in invited and "offer to do it later" in invited
    assert "Do it now" not in svc.thread_text(plain, "writer", plain.posts[-1])
    assert "Do it now" not in svc.thread_text(led, "lead", trigger)


async def test_a_crash_building_the_turn_releases_the_floor(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider([]), ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"], lead="lead")

    def broken(*a, **k):
        raise RuntimeError("prompt build failed")

    svc.thread_text = broken
    await svc.post(room.id, "user", "@writer go")
    await svc.settle(room.id)
    assert room.open_turn is None and room.waiters == [] and room.running_sessions == {}
    svc.delete(room.id)

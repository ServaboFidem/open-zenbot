"""turn-notes: a bot's private note to its next turn in the same conversation.

Written when every turn ends (done, failed, stopped, cut by the room deadline) from the turn's tool
calls and results by the summary model, with a mechanical fallback; redacted and capped; read back
into the next turn's context message and the router's task; only that bot sees it."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.generation.generator import Generator
from src.harness.approvals import ApprovalStore
from src.harness.bot import Bot
from src.harness.loop import Session, SessionState, TaskLoop
from src.harness.stubs import InjectAllSelector
from src.harness.tools import ToolRunner
from src.harness.turn_notes import NOTE_HEADING, TurnNoteConfig, TurnNotes, mechanical_note, outcome_of, render_turn
from src.memory.ledger import Ledger
from src.providers.interface import Message, ProviderUnavailable, ToolCall
from src.rooms.room import Room, RoomConfig, RoomService

from .conftest import ScriptedProvider, completion

ON = TurnNoteConfig(enabled=True, max_chars=1500, timeout_s=5)


def worked_session() -> Session:
    """A turn that found a folder, made a proof, then had its batch killed."""
    s = Session(bot_id="darkroom", task="optimise the jpgs")
    s.messages = [
        Message("system", "You are DarkroomBot."),
        Message("user", "optimise the jpgs"),
        Message("assistant", "Looking.", tool_calls=[ToolCall("c1", "shell", {"command": "dir C:\\Pics\\2024-11-25"})]),
        Message("tool", "DSC00547.JPG\nDSC00548.JPG\n", tool_call_id="c1", name="shell"),
        Message("assistant", "", tool_calls=[ToolCall("c2", "magick.magick", {"command": "in.jpg -auto-level proof.jpg"})]),
        Message("tool", "", tool_call_id="c2", name="magick.magick"),
        Message("assistant", "", tool_calls=[ToolCall("c3", "magick.magick", {"command": "mogrify -auto-level magazine/*.JPG"})]),
        Message("tool", "RuntimeError: magick ran past 150s and was stopped", tool_call_id="c3", name="magick.magick"),
    ]
    return s


class SlowProvider(ScriptedProvider):
    def __init__(self, script, delay: float):
        super().__init__(script)
        self.delay = delay

    async def complete(self, messages, *, tools=None, model=None, session_id=None):
        await asyncio.sleep(self.delay)
        return await super().complete(messages, tools=tools, model=model, session_id=session_id)


# -- writing a note ----------------------------------------------------------------


def test_the_turn_renders_from_its_tool_calls_and_results():
    text = render_turn(worked_session())
    assert "shell" in text and "dir C:\\Pics\\2024-11-25" in text and "DSC00547.JPG" in text
    assert "mogrify -auto-level magazine/*.JPG" in text and "magick ran past 150s" in text
    assert "You are DarkroomBot." not in text  # the system prompt is not the bot's work


def test_how_a_turn_ended():
    s = Session(bot_id="b", task="t")
    s.state = SessionState.done
    assert outcome_of(s) == "done"
    s.state, s.error = SessionState.failed, "max steps (50) reached"
    assert outcome_of(s) == "failed: max steps (50) reached"
    s.state = SessionState.stopped
    assert outcome_of(s) == "stopped"
    assert outcome_of(s, deadline_s=600) == "deadline after 600 s"


def test_the_mechanical_note_is_built_only_from_tool_results():
    note = mechanical_note(worked_session(), "deadline after 600 s", 1500)
    assert "deadline after 600 s" in note
    assert "magick.magick" in note and "magick ran past 150s" in note  # the failure is said plainly
    assert "Looking." not in note  # the bot's own narration is not evidence


async def test_the_summary_model_writes_the_note_from_the_previous_note_and_this_turn(ledger: Ledger):
    summaries = ScriptedProvider([completion("Found: C:\\Pics\\2024-11-25, 2 JPG.\nLeft: the batch.")])
    notes = TurnNotes(Generator(summaries, model="sum/model"), ledger, ON)
    s = worked_session()
    saved: list[str] = []
    note = await notes.write(Bot(name="Darkroom", role="r", id="darkroom"), s, outcome="deadline after 600 s",
                             previous="Found: an older folder.", save=saved.append)
    assert note == saved[0] == "Found: C:\\Pics\\2024-11-25, 2 JPG.\nLeft: the batch."
    system, user = summaries.calls[0]
    assert "Made" in system.content and "Left" in system.content
    assert "Found: an older folder." in user.content and "deadline after 600 s" in user.content
    assert "magick ran past 150s" in user.content
    e = ledger.tail(kind="turn_note", session_id=s.id)[0]
    assert e.bot_id == "darkroom" and e.data["note"] == note and e.data["outcome"] == "deadline after 600 s"
    assert e.data["source"] == "model"


async def test_a_failed_or_slow_summary_falls_back_to_the_mechanical_note(ledger: Ledger):
    bot = Bot(name="Darkroom", role="r", id="darkroom")
    down = TurnNotes(Generator(ScriptedProvider([ProviderUnavailable("down")])), ledger, ON)
    note = await down.write(bot, worked_session(), outcome="stopped", save=lambda n: None)
    assert "magick ran past 150s" in note and "stopped" in note
    slow = TurnNotes(Generator(SlowProvider([completion("late")], delay=1.0)), ledger,
                     TurnNoteConfig(enabled=True, max_chars=1500, timeout_s=0.05))
    note = await slow.write(bot, worked_session(), outcome="done", save=lambda n: None)
    assert note != "late" and "magick.magick" in note
    assert [e.data["source"] for e in ledger.tail(kind="turn_note")] == ["fallback", "fallback"]


async def test_the_note_is_redacted_and_capped(ledger: Ledger):
    key = "sk-" + "a1b2c3d4" * 4
    long = "Found: " + "x" * 3000 + f" key {key}"
    notes = TurnNotes(Generator(ScriptedProvider([completion(long)])), ledger,
                      TurnNoteConfig(enabled=True, max_chars=200, timeout_s=5))
    note = await notes.write(Bot(name="b", role="r", id="b"), worked_session(), outcome="done", save=lambda n: None)
    assert len(note) <= 200
    notes2 = TurnNotes(Generator(ScriptedProvider([completion(f"Made: out.jpg with key {key}")])), ledger, ON)
    note2 = await notes2.write(Bot(name="b", role="r", id="b"), worked_session(), outcome="done", save=lambda n: None)
    assert key not in note2 and "Made: out.jpg" in note2


async def test_a_turn_with_no_tool_calls_keeps_the_previous_note(ledger: Ledger):
    summaries = ScriptedProvider([])
    notes = TurnNotes(Generator(summaries), ledger, ON)
    s = Session(bot_id="b", task="hi")
    s.messages = [Message("system", "sys"), Message("user", "hi"), Message("assistant", "hello")]
    saved: list[str] = []
    note = await notes.write(Bot(name="b", role="r", id="b"), s, outcome="done", previous="Made: a.jpg", save=saved.append)
    assert note == "Made: a.jpg" and saved == [] and summaries.calls == []  # nothing new: no call, no write


async def test_notes_off_writes_nothing(ledger: Ledger):
    summaries = ScriptedProvider([completion("x")])
    notes = TurnNotes(Generator(summaries), ledger, TurnNoteConfig(enabled=False))
    saved: list[str] = []
    assert await notes.write(Bot(name="b", role="r", id="b"), worked_session(), outcome="done", save=saved.append) == ""
    assert saved == [] and summaries.calls == [] and ledger.tail(kind="turn_note") == []


def test_notes_are_off_in_the_test_suite_unless_a_test_turns_them_on():
    assert TurnNoteConfig().enabled is False  # conftest sets ZEN_TURN_NOTES=0 so other tests' scripts are not consumed


# -- reading a note ----------------------------------------------------------------


class RecordingSelector(InjectAllSelector):
    def __init__(self):
        self.tasks: list[str] = []

    async def select(self, bot, task, session_id=""):
        self.tasks.append(task)
        return await super().select(bot, task, session_id)


async def test_the_note_goes_into_the_context_message_and_the_routers_task(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("On it.")])
    selector = RecordingSelector()
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path), selector=selector)
    s = Session(bot_id="darkroom", task="do the rest")
    s.note = "Made: workspace\\magazine\\ (52 copies).\nLeft: process them."
    await loop.run(Bot(name="Darkroom", role="r", id="darkroom"), "do the rest", s)
    sent = provider.calls[0]
    assert NOTE_HEADING not in sent[0].content  # never the system prompt: its prefix stays cacheable
    context = sent[-2].content
    assert NOTE_HEADING in context and "Left: process them." in context and sent[-1].content == "do the rest"
    assert selector.tasks[0].startswith("do the rest") and "Left: process them." in selector.tasks[0]


async def test_no_note_leaves_the_task_and_context_as_they_were(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("hi")])
    selector = RecordingSelector()
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path), selector=selector)
    await loop.run(Bot(name="b", role="r", id="b"), "hello")
    assert selector.tasks == ["hello"] and all(NOTE_HEADING not in m.content for m in provider.calls[0])


# -- rooms ----------------------------------------------------------------------------


def look_tool(tools: ToolRunner) -> None:
    tools.register("look", "look around", {"type": "object", "properties": {}}, lambda: "found a.jpg and b.jpg")


def room_service(provider, summaries, ledger, tmp_path, **cfg) -> RoomService:
    tools = ToolRunner(tmp_path, timeout=10)
    look_tool(tools)
    loop = TaskLoop(provider, ledger, ApprovalStore(), tools, max_steps=5)
    bs = {b.id: b for b in [Bot(name="Lead", role="leads", id="lead"), Bot(name="Writer", role="writes", id="writer")]}
    svc = RoomService(ledger, loop, bs, RoomConfig(**cfg))
    svc.register_tools(tools)
    svc.notes = TurnNotes(Generator(summaries), ledger, ON)
    return svc


async def test_a_room_turn_leaves_a_note_for_that_bot_only(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([
        completion(calls=[("c1", "look", {})]), completion("Found two."),  # writer, turn 1
        completion("Doing the rest."),  # writer, turn 2
        completion("Hello."),  # lead
    ])
    summaries = ScriptedProvider([completion("Found: a.jpg, b.jpg.")])
    svc = room_service(provider, summaries, ledger, tmp_path)
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "@writer find the pictures")
    await svc.settle(room.id)
    assert room.conversation.notes == {"writer": "Found: a.jpg, b.jpg."}
    assert "found a.jpg and b.jpg" in summaries.calls[0][-1].content

    await svc.post(room.id, "user", "@writer do the rest")
    await svc.settle(room.id)
    assert "Found: a.jpg, b.jpg." in provider.calls[2][-2].content  # writer's second turn: the context message
    assert room.conversation.notes == {"writer": "Found: a.jpg, b.jpg."}  # no tool calls: the note stands

    await svc.post(room.id, "user", "@lead say hi")
    await svc.settle(room.id)
    assert all("Found: a.jpg" not in m.content for m in provider.calls[3])  # another bot never sees it


async def test_notes_round_trip_through_the_room_record_and_a_new_conversation_starts_empty(ledger: Ledger):
    room = Room(name="r", members=["lead", "writer"])
    room.conversation.notes["writer"] = "Made: out.jpg"
    back = Room.from_record(room.to_record(), ledger)
    assert back.conversation.notes == {"writer": "Made: out.jpg"}
    back.new_conversation()
    assert back.conversation.notes == {}
    old = room.to_record()
    for c in old["conversations"]:
        c.pop("notes", None)
    assert Room.from_record(old, ledger).conversation.notes == {}  # a record saved before notes loads


class StallsOnSecondCall(ScriptedProvider):
    """The writer's second model call never comes back, so its turn hits the deadline."""

    async def complete(self, messages, *, tools=None, model=None, session_id=None):
        if len(self.calls) == 1:
            self.calls.append(list(messages))
            await asyncio.sleep(30)
        return await super().complete(messages, tools=tools, model=model, session_id=session_id)


async def test_a_deadline_turn_writes_a_note_the_lead_sees_and_keeps_its_attachments(ledger: Ledger, tmp_path: Path):
    (tmp_path / "proof.jpg").write_bytes(b"\xff\xd8\xff")
    provider = StallsOnSecondCall([
        completion(calls=[("c1", "attach_file", {"path": "proof.jpg"})]),  # writer, then stalls
        completion("Writer ran out of time; the proof is attached."),  # lead's failure turn
    ])
    summaries = ScriptedProvider([completion(
        "Made: C:\\Users\\someone\\.zen-agents\\vault\\workspace\\proof.jpg (attached).\n"
        "Failed: nothing yet.\nLeft: the other 51 files.")])
    svc = room_service(provider, summaries, ledger, tmp_path, turn_deadline_s=0.5)
    room = svc.create("r", ["lead", "writer"], lead="lead")
    await svc.post(room.id, "user", "@writer edit the photos")
    await svc.settle(room.id)

    assert "Left: the other 51 files." in room.conversation.notes["writer"]
    assert ledger.tail(kind="turn_note")[0].data["outcome"].startswith("deadline after")
    lead_task = provider.calls[-1][-1].content
    assert "Left: the other 51 files." in lead_task and "proof.jpg" in lead_task
    assert "C:\\Users\\someone" not in lead_task  # another bot gets file names, never directories
    lead_post = room.posts[-1]
    assert lead_post.author == "lead" and [Path(a.path).name for a in lead_post.attachments] == ["proof.jpg"]


async def test_a_room_without_a_note_service_works_as_before(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion(calls=[("c1", "look", {})]), completion("Found two.")])
    svc = room_service(provider, ScriptedProvider([]), ledger, tmp_path)
    svc.notes = None
    room = svc.create("r", ["lead", "writer"])
    await svc.post(room.id, "user", "@writer look")
    await svc.settle(room.id)
    assert room.conversation.notes == {} and [p.author for p in room.posts] == ["user", "writer"]


# -- one-to-one -------------------------------------------------------------------------


@pytest.fixture
def harness(vault: Path, tmp_path: Path):
    from src.harness.api import Harness
    from src.harness.config import HarnessConfig
    from src.skills.catalog import Catalog

    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog(),
                summary_provider=ScriptedProvider())
    h.turn_notes.config = ON
    h.add_bot(Bot(name="Darkroom", role="edits", id="darkroom"))
    look_tool(h.tools)
    return h


async def until(predicate, tries: int = 300) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never held")


async def test_a_one_to_one_task_leaves_a_note_the_next_message_reads(harness):
    h = harness
    h.provider.script = [completion(calls=[("c1", "look", {})]), completion("Found two."), completion("Done.")]
    h.summary_provider.script = [completion("Found: a.jpg, b.jpg.")]
    s1 = h.start_task(h.bots["darkroom"], "find the pictures")
    await asyncio.wait_for(h.tasks[s1.id], 5)
    conv = h.conversations.get(s1.conversation_id)
    assert s1.state == SessionState.done and conv.note == "Found: a.jpg, b.jpg."
    s2 = h.start_task(h.bots["darkroom"], "do the rest")
    await asyncio.wait_for(h.tasks[s2.id], 5)
    assert "Found: a.jpg, b.jpg." in h.provider.calls[-1][-2].content

    h.conversations.load()  # the note survives a reload
    assert h.conversations.get(s1.conversation_id).note == "Found: a.jpg, b.jpg."


async def test_a_stopped_one_to_one_task_still_leaves_a_note(harness):
    h = harness
    h.provider.script = [completion(calls=[("c1", "look", {})]), ProviderUnavailable("offline")]
    h.summary_provider.script = [completion("Found: a.jpg.\nLeft: everything else.")]
    s = h.start_task(h.bots["darkroom"], "find the pictures")
    await until(lambda: s.state == SessionState.paused)
    h.stop(s.id)
    await asyncio.wait_for(h.tasks[s.id], 5)
    assert s.state == SessionState.stopped
    assert h.conversations.get(s.conversation_id).note == "Found: a.jpg.\nLeft: everything else."
    assert h.ledger.tail(kind="turn_note", session_id=s.id)[0].data["outcome"] == "stopped"

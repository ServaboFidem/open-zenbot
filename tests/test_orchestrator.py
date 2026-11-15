"""The orchestrator: one scoring call decides whether a message continues a
topic room, opens a new one with the best-fit specialists, or is answered
by ZenBot directly."""

from __future__ import annotations

from pathlib import Path

from src.decider.interface import NoulAnswer
from src.generation.generator import Generator
from src.harness.approvals import ApprovalStore
from src.harness.bot import Bot
from src.harness.loop import TaskLoop
from src.harness.tools import ToolRunner
from src.memory.ledger import Ledger
from src.providers.interface import ProviderUnavailable
from src.rooms.orchestrator import Orchestrator, OrchestratorConfig
from src.rooms.room import RoomConfig, RoomService

from .conftest import MockScorer, ScriptedProvider, completion


def make(provider, ledger, tmp_path, names=("lead", "writer", "critic")) -> RoomService:
    bs = {n: Bot(name=n.title(), role=f"{n} work", id=n) for n in names}
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path, timeout=10), max_steps=5)
    return RoomService(ledger, loop, bs, RoomConfig())


async def topic_writer(text: str) -> tuple[str, str]:
    return ("Ohio beekeeping", "Becoming a beekeeper in Ohio")


def orch(scorer, svc, ledger, **kw) -> Orchestrator:
    return Orchestrator(scorer, svc, svc.bots, ledger, topic_writer, "lead", **kw)


def scores(**by_id: float):
    return lambda req: {k: NoulAnswer(by_id.get(k, 0.0)) for k in req.questions}


def provider() -> ScriptedProvider:
    return ScriptedProvider([completion("ok") for _ in range(10)])


async def test_specialist_fit_opens_a_room_and_hands_over(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    o = orch(MockScorer(scores(**{"bot:writer": 0.9})), svc, ledger)
    out = await o.route("How do I become a beekeeper in Ohio?")
    assert out.outcome == "room" and out.created
    room = svc.get(out.room_id)
    assert room.lead == "lead" and room.members == ["lead", "writer"] and room.name == "Ohio beekeeping"
    assert room.topic == "Becoming a beekeeper in Ohio"
    assert [p.author for p in room.posts][:2] == ["user", "lead"] and "@writer" in room.posts[1].text
    await svc.settle(room.id)
    routed = [e.data for e in ledger.tail(kind="room") if e.data.get("event") == "routed"]
    assert routed and routed[-1]["outcome"] == "room" and routed[-1]["created"] is True


async def test_context_is_copied_into_a_new_room(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    o = orch(MockScorer(scores(**{"bot:writer": 0.9})), svc, ledger)
    out = await o.route("draft it", context="User: x\nLead: y")
    room = svc.get(out.room_id)
    assert [p.author for p in room.posts][:3] == ["lead", "user", "lead"]
    assert room.posts[0].text == "From our chat just now:\n\nUser: x\nLead: y"
    assert room.posts[1].text == "draft it" and "@writer" in room.posts[2].text


async def test_a_follow_up_continues_the_room(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider([completion("Lead: sure.")]), ledger, tmp_path)
    room = svc.create("Bees", ["lead", "writer"], lead="lead", topic="Beekeeping")
    o = orch(MockScorer(scores(**{f"room:{room.id}": 0.8})), svc, ledger)
    out = await o.route("and the hive?")
    assert out.outcome == "room" and out.room_id == room.id and not out.created
    await svc.settle(room.id)
    assert [p.author for p in room.posts] == ["user", "lead"]


async def test_room_question_asks_about_the_same_subject(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    room = svc.create("Bees", ["lead", "writer"], lead="lead", topic="Beekeeping in Ohio")
    scorer = MockScorer(scores())
    await orch(scorer, svc, ledger).route("and the hive?")
    question = str(scorer.requests[0].questions[f"room:{room.id}"].instructions)
    assert "same subject" in question and "Beekeeping in Ohio" in question


async def test_new_topic_skips_existing_rooms(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    svc.create("Bees", ["lead", "writer"], lead="lead")
    scorer = MockScorer(scores())
    out = await orch(scorer, svc, ledger).route("something else", new_topic=True)
    assert out.outcome == "task"
    assert all(not q.startswith("room:") for q in scorer.requests[0].questions)


async def test_archived_and_old_rooms_are_not_candidates(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    rooms = [svc.create(f"r{i}", ["lead", "writer"], lead="lead") for i in range(12)]
    gone = svc.create("gone", ["lead", "writer"], lead="lead")
    gone.archived = True
    svc.create("not mine", ["lead", "writer"])  # no lead
    scorer = MockScorer(scores())
    await orch(scorer, svc, ledger).route("hi")
    qs = [q for q in scorer.requests[0].questions if q.startswith("room:")]
    assert len(qs) == 10 and f"room:{gone.id}" not in qs
    assert len(rooms) == 12


async def test_low_scores_answer_directly(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    o = orch(MockScorer(scores(**{"bot:writer": 0.3, "bot:critic": 0.3})), svc, ledger)
    out = await o.route("what's 2+2?")
    assert out.outcome == "task" and out.room_id is None and svc.list() == []
    assert out.scores["bot:writer"] == 0.3


async def test_scorer_error_fails_open(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    o = orch(MockScorer(lambda r: ProviderUnavailable("down")), svc, ledger)
    out = await o.route("hi")
    assert out.outcome == "task" and out.fallback and "down" in out.fallback
    assert [e for e in ledger.tail(kind="error") if e.data.get("where") == "orchestrator"]


async def test_only_the_lead_and_no_rooms_skips_the_scorer(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path, names=("lead",))
    scorer = MockScorer(scores())
    out = await orch(scorer, svc, ledger).route("hi")
    assert out.outcome == "task" and scorer.requests == []
    assert (await Orchestrator(None, svc, svc.bots, ledger, topic_writer, "lead").route("hi")).fallback == "no scorer"


async def test_refresh_topic_updates_and_survives_errors(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    room = svc.create("Bees", ["lead", "writer"], lead="lead", topic="Old")
    post = await svc.post(room.id, "writer", "hives", schedule=False)
    await orch(None, svc, ledger).refresh_topic(room, post)
    assert room.topic == "Becoming a beekeeper in Ohio"
    assert room.name == "Bees"

    async def boom(text):
        raise RuntimeError("no")

    bad = Orchestrator(None, svc, svc.bots, ledger, boom, "lead")
    await bad.refresh_topic(room, post)
    assert room.topic == "Becoming a beekeeper in Ohio"
    assert [e for e in ledger.tail(kind="error") if e.data.get("where") == "refresh_topic"]


async def test_refresh_topic_is_built_from_the_users_posts_not_the_lead_post(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    room = svc.create("Beekeeping in Ohio", ["lead", "writer"], lead="lead", topic="Becoming a beekeeper in Ohio")
    await svc.post(room.id, "user", "How do I become a beekeeper in the state of Ohio?", schedule=False)
    await svc.post(room.id, "writer", "SECRET-WRITER-POST about woodenware", schedule=False)
    lead_post = await svc.post(room.id, "lead", "SECRET-LEAD-POST define nuc and varroa", schedule=False)
    await svc.post(room.id, "user", "u" * 500, schedule=False)
    seen: list[str] = []

    async def spy(text: str) -> tuple[str, str]:
        seen.append(text)
        return ("ignored", "Overall subject")

    o = Orchestrator(None, svc, svc.bots, ledger, spy, "lead")
    await o.refresh_topic(room, lead_post)
    text = seen[0]
    assert "Beekeeping in Ohio" in text and "Becoming a beekeeper in Ohio" in text
    assert "How do I become a beekeeper in the state of Ohio?" in text
    assert "SECRET-WRITER-POST" not in text and "SECRET-LEAD-POST" not in text
    assert "u" * 301 not in text and "u" * 300 in text
    assert room.topic == "Overall subject" and room.name == "Beekeeping in Ohio"


async def test_refresh_topic_uses_only_the_last_ten_user_posts(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    room = svc.create("Bees", ["lead", "writer"], lead="lead", topic="t")
    for i in range(12):
        await svc.post(room.id, "user", f"msg-{i:02d}", schedule=False)
    seen: list[str] = []

    async def spy(text: str) -> tuple[str, str]:
        seen.append(text)
        return ("n", "t2")

    await Orchestrator(None, svc, svc.bots, ledger, spy, "lead").refresh_topic(room, room.posts[-1])
    assert "msg-00" not in seen[0] and "msg-01" not in seen[0]
    assert seen[0].index("msg-02") < seen[0].index("msg-11")


async def test_room_topic_parses_json_and_falls_back():
    g = Generator(ScriptedProvider([completion('{"name": "Ohio bees", "topic": "Keeping bees in Ohio."}'), completion("nonsense")]))
    assert await g.room_topic("How do I keep bees?") == ("Ohio bees", "Keeping bees in Ohio.")
    text = "x" * 300
    assert await g.room_topic(text) == ("x" * 60, "x" * 200)


REFUSED = "my wifi password: hunter2222"  # the exclusion filter refuses it at ingest


async def test_refused_user_text_answers_directly_and_opens_no_room(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    o = orch(MockScorer(scores(**{"bot:writer": 0.9})), svc, ledger)
    out = await o.route(REFUSED)
    assert out.outcome == "task" and out.fallback.startswith("refused in room: ") and not out.room_id
    assert svc.list() == []


async def test_refused_text_on_the_continue_path_posts_nothing(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    room = svc.create("Bees", ["lead", "writer"], lead="lead", topic="Beekeeping")
    o = orch(MockScorer(scores(**{f"room:{room.id}": 0.9})), svc, ledger)
    out = await o.route(REFUSED)
    assert out.outcome == "task" and out.fallback.startswith("refused in room: ")
    assert room.posts == []


async def test_refused_context_is_dropped_and_not_scored(ledger: Ledger, tmp_path: Path):
    svc = make(provider(), ledger, tmp_path)
    scorer = MockScorer(scores(**{"bot:writer": 0.9}))
    o = orch(scorer, svc, ledger)
    out = await o.route("draft it", context="User: x\nLead: Put api_key=YOUR_API_KEY in the query string.")
    assert out.outcome == "room" and out.created
    room = svc.get(out.room_id)
    assert [p.author for p in room.posts][:2] == ["user", "lead"] and room.posts[0].text == "draft it"
    assert not any("From our chat" in p.text for p in room.posts)
    assert scorer.requests[0].state["recent"] == ""
    await svc.settle(room.id)


async def test_refresh_topic_does_not_resurrect_a_deleted_room(ledger: Ledger, tmp_path: Path):
    from src.rooms.store import RoomStore

    svc = make(provider(), ledger, tmp_path)
    svc.store = RoomStore(tmp_path / "rooms")
    room = svc.create("Bees", ["lead", "writer"], lead="lead", topic="t")
    post = await svc.post(room.id, "lead", "noted", schedule=False)
    svc.delete(room.id)
    await orch(None, svc, ledger).refresh_topic(room, post)
    assert not (tmp_path / "rooms" / f"{room.id}.json").exists()


async def test_a_routed_message_lands_in_the_rooms_current_conversation(ledger: Ledger, tmp_path: Path):
    svc = make(ScriptedProvider([completion("Lead: sure.")]), ledger, tmp_path)
    room = svc.create("Bees", ["lead", "writer"], lead="lead", topic="Beekeeping")
    await svc.post(room.id, "user", "earlier", schedule=False)
    conv = svc.new_conversation(room.id)
    room.topic = "Hives"
    out = await orch(MockScorer(scores(**{f"room:{room.id}": 0.8})), svc, ledger).route("and the hive?")
    assert out.room_id == room.id
    await svc.settle(room.id)
    assert [p.text for p in room.current_posts()][:1] == ["and the hive?"]
    assert all(p.conversation == conv.id for p in room.current_posts())

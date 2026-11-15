"""skill-fit: role first, past work breaks ties, double tie suggests
both, nothing attaches without confirm."""

from __future__ import annotations

import pytest

from src.decider.fit import FitConfig, FitEngine, past_work_from_ledger
from src.decider.interface import ChoiceAnswer, ConfirmState
from src.harness.bot import Bot
from src.memory.ledger import Kind, Ledger
from src.skills.loader import parse_skill

from .conftest import MockScorer

SKILL = parse_skill("---\nname: Weekly update\nuse: draft the weekly team update\nsteps: [summarize, draft]\n---\n")


@pytest.fixture
def bots() -> list[Bot]:
    return [
        Bot(name="Research partner", role="Research things", id="research"),
        Bot(name="Writing companion", role="Draft and edit content", id="writer"),
        Bot(name="Code teammate", role="Build software", id="builder"),
    ]


def choice(probs: dict[str, float]) -> dict:
    top = max(probs, key=probs.get)
    return {k: ChoiceAnswer(top, probs, probs[top]) for k in ("role_fit", "past_work")}


async def test_role_wins_outright(bots, ledger: Ledger):
    scorer = MockScorer(script=[{"role_fit": ChoiceAnswer("writer", {"research": 0.2, "writer": 0.7, "builder": 0.1}, 0.7)}])
    s = await FitEngine(scorer, ledger).suggest(SKILL, bots)
    assert s.result.bot_ids == ["writer"] and s.state == ConfirmState.pending and s.attached_to is None
    assert s.result.role_scores["writer"] == 0.7 and s.result.past_work_scores == {}
    assert len(scorer.requests) == 1 and scorer.requests[0].purpose == "fit"
    assert set(scorer.requests[0].questions["role_fit"].criteria) == {"research", "writer", "builder"}
    assert "Draft and edit content" in scorer.requests[0].questions["role_fit"].criteria["writer"]
    assert not any(b.skills for b in bots), "suggest never attaches"


async def test_past_work_breaks_a_role_tie(bots, ledger: Ledger):
    ledger.append(Kind.session, "writer", "s1", event="start", task="draft the Friday team update")
    ledger.append(Kind.session, "writer", "s2", event="start", task="edit the launch post")
    ledger.append(Kind.session, "research", "s3", event="start", task="compare vector databases")
    scorer = MockScorer(script=[
        {"role_fit": ChoiceAnswer("writer", {"research": 0.45, "writer": 0.5, "builder": 0.05}, 0.5)},
        {"past_work": ChoiceAnswer("writer", {"research": 0.1, "writer": 0.9}, 0.9)},
    ])
    s = await FitEngine(scorer, ledger, FitConfig(tie_width=0.1)).suggest(SKILL, bots)
    assert s.result.bot_ids == ["writer"] and s.result.past_work_scores["writer"] == 0.9
    pw = scorer.requests[1].questions["past_work"]
    assert set(pw.criteria) == {"research", "writer"}, "only the tied bots go to the tiebreaker"
    assert "Friday team update" in pw.criteria["writer"] and "vector databases" in pw.criteria["research"]


async def test_double_tie_suggests_both(bots, ledger: Ledger):
    ledger.append(Kind.session, "writer", "s1", event="start", task="x")
    ledger.append(Kind.session, "research", "s2", event="start", task="y")
    scorer = MockScorer(script=[
        {"role_fit": ChoiceAnswer("writer", {"research": 0.48, "writer": 0.5, "builder": 0.02}, 0.5)},
        {"past_work": ChoiceAnswer("writer", {"research": 0.5, "writer": 0.5}, 0.5)},
    ])
    s = await FitEngine(scorer, ledger).suggest(SKILL, bots)
    assert sorted(s.result.bot_ids) == ["research", "writer"]


async def test_tie_with_no_past_work_suggests_both_without_a_second_call(bots, ledger: Ledger):
    scorer = MockScorer(script=[{"role_fit": ChoiceAnswer("writer", {"research": 0.5, "writer": 0.5, "builder": 0.0}, 0.5)}])
    s = await FitEngine(scorer, ledger).suggest(SKILL, bots)
    assert sorted(s.result.bot_ids) == ["research", "writer"] and len(scorer.requests) == 1


async def test_single_bot_needs_no_scorer(ledger: Ledger):
    scorer = MockScorer()
    s = await FitEngine(scorer, ledger).suggest(SKILL, [Bot(name="only", role="r", id="only")])
    assert s.result.bot_ids == ["only"] and scorer.requests == []


async def test_confirm_attaches_and_denies_do_not(bots, ledger: Ledger):
    scorer = MockScorer(script=[{"role_fit": ChoiceAnswer("writer", {"research": 0.5, "writer": 0.5, "builder": 0.0}, 0.5)}] * 2)
    fit = FitEngine(scorer, ledger)
    s = await fit.suggest(SKILL, bots)
    writer = bots[1]
    builder = bots[2]

    with pytest.raises(ValueError, match="not suggested"):
        fit.confirm(s.id, builder)
    assert writer.skills == []

    done = fit.confirm(s.id, writer)
    assert done.state == ConfirmState.confirmed and done.attached_to == "writer"
    assert [k.slug for k in writer.skills] == ["weekly-update"]
    assert ledger.tail(kind="promotion")[0].data == {"what": "skill_attach", "skill": "weekly-update", "fit": s.id, "by": "user confirm"}
    with pytest.raises(ValueError, match="already"):
        fit.confirm(s.id, writer)

    s2 = await fit.suggest(SKILL, bots)
    assert fit.deny(s2.id).state == ConfirmState.denied
    assert [x.id for x in fit.list(ConfirmState.denied)] == [s2.id] and len(fit.list()) == 2


def test_past_work_reads_only_session_starts(ledger: Ledger):
    ledger.append(Kind.session, "a", "s1", event="start", task="one")
    ledger.append(Kind.session, "a", "s1", event="done", steps=3)
    ledger.append(Kind.turn, "a", "s1", content="not a task")
    ledger.append(Kind.session, "b", "s2", event="start", task="two")
    assert past_work_from_ledger(ledger, ["a", "b", "c"]) == {"a": ["one"], "b": ["two"], "c": []}

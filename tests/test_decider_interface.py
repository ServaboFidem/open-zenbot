"""decider-interface: shapes, band selection with top-k, supervisor
thresholds, scorer registry slot, no-prose rule, score logging."""

from __future__ import annotations

import re
from dataclasses import fields

import pytest

from src.decider import registry
from src.decider.interface import (
    ANSWER_TYPES,
    SUPERVISOR_QUESTIONS,
    BandConfig,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionRefused,
    DecisionRequest,
    DecisionResult,
    DecisionUsage,
    NoulAnswer,
    NoulQuestion,
    Outcome,
    RefusalAnswer,
    RunState,
    Scorer,
    ScoreAnswer,
    ScoreQuestion,
    SupervisorAction,
    SupervisorCheck,
    SupervisorThresholds,
    assert_no_prose_fields,
    decide_action,
    decide_and_log,
    question_to_dict,
    select_band,
)
from src.memory.ledger import Ledger

from .conftest import REPO


class MockScorer:
    """A second scorer registered in the test. Proves the slot without
    shipping a second adapter."""

    kind = "mock-scorer"

    def __init__(self, answers=None, **kw):
        self.answers = answers or {}
        self.requests: list[DecisionRequest] = []

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        self.requests.append(request)
        return DecisionResult(answers=self.answers, model="mock-1", kind=self.kind, usage=DecisionUsage(5, 1, 0.000001), id="dec-1")

    async def probe(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _isolate_registry():
    saved, kinds = dict(registry._factories), dict(registry._provider_kinds)
    registry.clear()
    yield
    registry.clear()
    registry._factories.update(saved)
    registry._provider_kinds.update(kinds)


# -- shapes -----------------------------------------------------------------


def test_questions_serialize_to_api_shape():
    assert question_to_dict(ChoiceQuestion("which?", {"a": "A", "b": "B"})) == {"type": "choice", "instructions": "which?", "criteria": {"a": "A", "b": "B"}}
    assert question_to_dict(NoulQuestion("yes?")) == {"type": "noul", "instructions": "yes?"}
    assert question_to_dict(NoulQuestion("yes?", {"true": "t", "false": "f"}))["criteria"] == {"true": "t", "false": "f"}
    assert question_to_dict(ScoreQuestion("how far?", ["none", "some", "done"])) == {"type": "score", "instructions": "how far?", "criteria": ["none", "some", "done"]}


def test_supervisor_questions_cover_the_three_checks():
    assert {k: q.type for k, q in SUPERVISOR_QUESTIONS.items()} == {"run_state": "choice", "is_repeating": "noul", "completion": "score"}
    assert set(SUPERVISOR_QUESTIONS["run_state"].criteria) == {str(s) for s in RunState}


def test_answers_carry_no_free_text():
    assert_no_prose_fields()
    for cls in ANSWER_TYPES:
        for f in fields(cls):
            assert f.type not in ("str", str) or f.name in {"type", "choice"}, f"{cls.__name__}.{f.name}"


def test_typed_accessors_and_refusal():
    r = DecisionResult(
        answers={"c": ChoiceAnswer("x", {"x": 0.9, "y": 0.1}, 0.9), "n": NoulAnswer(0.3), "s": ScoreAnswer(1.2, {"0": 0.1, "1": 0.6, "2": 0.3}), "ref": RefusalAnswer()},
        model="m", kind="k",
    )
    assert r.choice("c").choice == "x" and r.noul("n").noul == 0.3 and r.score("s").max_score == 2
    with pytest.raises(TypeError):
        r.choice("n")
    with pytest.raises(DecisionRefused):
        r.noul("ref")
    with pytest.raises(KeyError):
        r.score("missing")


# -- band selection ---------------------------------------------------------


def test_band_then_cap():
    scores = {"a": 0.80, "b": 0.75, "c": 0.72, "d": 0.71, "e": 0.30}
    d = select_band(scores, BandConfig(width=0.1, k=3))
    assert d.winners == ["a", "b", "c"] and d.capped is True
    assert d.top == 0.80 and d.floor == pytest.approx(0.70) and d.outcome == Outcome.act
    d = select_band(scores, BandConfig(width=0.1, k=10))
    assert d.winners == ["a", "b", "c", "d"] and d.capped is False


def test_same_band_inject_all_on_tie():
    d = select_band({"a": 0.5, "b": 0.5, "c": 0.1}, BandConfig(width=0.05, k=5))
    assert d.winners == ["a", "b"]


def test_all_in_one_band_still_capped():
    d = select_band({f"s{i}": 0.5 for i in range(10)}, BandConfig(width=1.0, k=3))
    assert len(d.winners) == 3 and d.capped


def test_per_entry_width_overrides_config():
    scores = {"a": 0.6, "b": 0.45, "c": 0.2}
    assert select_band(scores, BandConfig(width=0.1), widths={"a": 0.2}).winners == ["a", "b"]
    assert select_band(scores, BandConfig(width=0.1)).winners == ["a"]


def test_low_confidence_defers_and_empty_defers():
    assert select_band({"a": 0.1, "b": 0.05}, BandConfig(low_confidence=0.2)).outcome == Outcome.defer
    assert select_band({}).outcome == Outcome.defer and select_band({}).winners == []


# -- supervisor thresholds --------------------------------------------------


def check(looping=0.0, stuck=0.0, repeating=0.0, done=0.0) -> SupervisorCheck:
    prog = max(0.0, 1 - looping - stuck)
    return SupervisorCheck(
        ChoiceAnswer(max({"progressing": prog, "stuck": stuck, "looping": looping}.items(), key=lambda kv: kv[1])[0], {"progressing": prog, "stuck": stuck, "looping": looping}),
        NoulAnswer(repeating),
        ScoreAnswer(done, {"0": 1 - done / 2, "1": 0, "2": done / 2}),
    )


def test_halt_needs_strong_double_signal():
    assert decide_action(check(looping=0.85, repeating=0.85)) == SupervisorAction.halt
    assert decide_action(check(looping=0.85, repeating=0.5)) == SupervisorAction.nudge  # looping alone only nudges
    assert decide_action(check(looping=0.3, repeating=0.9)) == SupervisorAction.continue_  # repeating alone is not enough


def test_nudge_on_moderate_signal_and_thresholds_are_config():
    assert decide_action(check(looping=0.55)) == SupervisorAction.nudge
    assert decide_action(check(stuck=0.45)) == SupervisorAction.nudge
    assert decide_action(check(stuck=0.3)) == SupervisorAction.continue_
    assert decide_action(check(stuck=0.3), SupervisorThresholds(nudge_stuck=0.25)) == SupervisorAction.nudge


def test_check_from_result_uses_live_shape():
    r = DecisionResult(
        answers={
            "run_state": ChoiceAnswer("looping", {"looping": 0.9998, "progressing": 0.0, "stuck": 0.0001}, 0.9997),
            "is_repeating": NoulAnswer(0.9959),
            "completion": ScoreAnswer(0.0766, {"0": 0.92, "1": 0.07, "2": 0.0}, {"0": "no", "1": "some", "2": "done"}, 0.88),
        },
        model="microsoft/microsoft-decision-1-20261009", kind="openrouter-decision-1",
    )
    c = SupervisorCheck.from_result(r)
    assert c.p(RunState.looping) > 0.99 and decide_action(c) == SupervisorAction.halt


# -- registry slot and logging ----------------------------------------------


async def test_mock_scorer_registers_resolves_and_drives_a_band_decision(ledger: Ledger):
    answers = {"fit": ChoiceAnswer("brief", {"brief": 0.7, "update": 0.65, "plan": 0.1}, 0.7)}
    registry.register("mock-scorer", lambda **kw: MockScorer(answers))
    assert registry.kinds() == ["mock-scorer"]
    scorer = registry.get("mock-scorer")
    assert isinstance(scorer, Scorer)

    req = DecisionRequest(state={"task": "write the weekly brief"}, questions={"fit": ChoiceQuestion("which skill?", {"brief": "b", "update": "u", "plan": "p"})}, purpose="routing")
    result = await decide_and_log(scorer, req, ledger, "bot1", "s1")
    band = select_band(result.choice("fit").probabilities, BandConfig(width=0.1, k=3))
    assert band.winners == ["brief", "update"]

    e = ledger.tail(kind="decider_score")[0]
    assert e.data["scorer"] == "mock-scorer" and e.data["purpose"] == "routing" and e.data["model"] == "mock-1"
    assert e.data["answers"]["fit"]["probabilities"]["brief"] == 0.7 and e.data["cost"] == 0.000001
    assert "state" not in e.data and "weekly brief" not in str(e.data), "ledger carries scores, not the scored text"


def test_registry_errors():
    with pytest.raises(KeyError, match="no scorer registered"):
        registry.get("nope")

    class Wrong:
        kind = "other"
        async def decide(self, r): ...
        async def probe(self): return True

    registry.register("mock-scorer", lambda **kw: Wrong())
    with pytest.raises(TypeError, match="kind"):
        registry.get("mock-scorer")


# -- boundaries -------------------------------------------------------------


def test_decider_never_imports_the_generator():
    pat = re.compile(r"^\s*(from|import)\s+src\.generation\b", re.M)
    offenders = [str(p.relative_to(REPO)) for p in (REPO / "src" / "decider").rglob("*.py") if pat.search(p.read_text())]
    assert offenders == []


def test_decider_adapters_import_no_http_library():
    pat = re.compile(r"^\s*(import|from)\s+(httpx|requests|aiohttp)\b", re.M)
    offenders = [str(p.relative_to(REPO)) for p in (REPO / "src" / "decider").rglob("*.py") if pat.search(p.read_text())]
    assert offenders == []

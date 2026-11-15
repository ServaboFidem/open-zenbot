"""Decider interface.

The decider scores fixed options with calibrated probabilities. It never
writes prose. Every routing, banding, fit, scope, halt, and promotion
decision in the harness is typed here, and every scorer that fills the
interface registers in `registry.py` by kind.

Shapes follow the OpenRouter Decisions API (verified live against
`microsoft/microsoft-decision-1` on 2026-11-05):

  choice  pick one label from `criteria`; a probability per label
  noul    probability that the answer is yes
  score   position on an ordered rubric; a probability per level plus
          the expected score

Day one has one scorer adapter. The registry is the slot for a second.
Pure decision logic (band selection, supervisor thresholds) lives here
as functions so the router and supervisor share one definition and
tests can run it with no scorer at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# questions

Guidance = str | dict[str, Any] | list[Any] | None


@dataclass
class ChoiceQuestion:
    instructions: Guidance
    criteria: dict[str, Guidance]  # label -> what it means
    type: str = "choice"


@dataclass
class NoulQuestion:
    instructions: Guidance
    criteria: dict[str, Guidance] | None = None  # optional {"true": ..., "false": ...}
    type: str = "noul"


@dataclass
class ScoreQuestion:
    instructions: Guidance
    criteria: list[Guidance]  # ordered levels; score runs 0 .. len-1
    type: str = "score"


Question = ChoiceQuestion | NoulQuestion | ScoreQuestion


def question_to_dict(q: Question) -> dict[str, Any]:
    d: dict[str, Any] = {"type": q.type, "instructions": q.instructions}
    if isinstance(q, (ChoiceQuestion, ScoreQuestion)) or (isinstance(q, NoulQuestion) and q.criteria):
        d["criteria"] = q.criteria
    return d


# ---------------------------------------------------------------------------
# answers (no free text: a test asserts it)


@dataclass
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None
    type: str = "choice"


@dataclass
class NoulAnswer:
    noul: float
    type: str = "noul"


@dataclass
class ScoreAnswer:
    score: float
    probabilities: dict[str, float] = field(default_factory=dict)
    legend: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    type: str = "score"

    @property
    def max_score(self) -> int:
        return max((int(k) for k in self.probabilities), default=0)


@dataclass
class RefusalAnswer:
    type: str = "refusal"


Answer = ChoiceAnswer | NoulAnswer | ScoreAnswer | RefusalAnswer

ANSWER_TYPES = (ChoiceAnswer, NoulAnswer, ScoreAnswer, RefusalAnswer)
# The only string fields an answer may carry. `choice` is a label from
# the caller's own criteria, not generated text.
ALLOWED_TEXT_FIELDS = {"type", "choice"}


def assert_no_prose_fields() -> None:
    for cls in ANSWER_TYPES:
        for f in fields(cls):
            if f.type in ("str", str) and f.name not in ALLOWED_TEXT_FIELDS:
                raise TypeError(f"{cls.__name__}.{f.name} is a free-text field; the decider must not write prose")


def answer_to_dict(a: Answer) -> dict[str, Any]:
    if isinstance(a, ChoiceAnswer):
        return {"type": "choice", "choice": a.choice, "probabilities": a.probabilities, "confidence": a.confidence}
    if isinstance(a, NoulAnswer):
        return {"type": "noul", "noul": a.noul}
    if isinstance(a, ScoreAnswer):
        return {"type": "score", "score": a.score, "probabilities": a.probabilities, "confidence": a.confidence}
    return {"type": "refusal"}


# ---------------------------------------------------------------------------
# request / result


@dataclass
class DecisionRequest:
    state: str | dict[str, Any] | list[Any]
    questions: dict[str, Question]
    purpose: str = ""  # routing | fit | scope | supervise | promotion | recall ...


@dataclass
class DecisionUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None


@dataclass
class DecisionResult:
    answers: dict[str, Answer]
    model: str
    kind: str  # scorer kind; flows into the ledger
    usage: DecisionUsage = field(default_factory=DecisionUsage)
    id: str = ""
    provider: str = ""

    def choice(self, key: str) -> ChoiceAnswer:
        return _typed(self.answers, key, ChoiceAnswer)

    def noul(self, key: str) -> NoulAnswer:
        return _typed(self.answers, key, NoulAnswer)

    def score(self, key: str) -> ScoreAnswer:
        return _typed(self.answers, key, ScoreAnswer)


class DecisionRefused(ValueError):
    pass


def _typed(answers: dict[str, Answer], key: str, cls: type) -> Any:
    a = answers.get(key)
    if a is None:
        raise KeyError(f"no answer for {key!r}")
    if isinstance(a, RefusalAnswer):
        raise DecisionRefused(f"scorer refused question {key!r}")
    if not isinstance(a, cls):
        raise TypeError(f"answer {key!r} is {type(a).__name__}, expected {cls.__name__}")
    return a


@runtime_checkable
class Scorer(Protocol):
    kind: str

    async def decide(self, request: DecisionRequest) -> DecisionResult: ...

    async def probe(self) -> bool: ...


# ---------------------------------------------------------------------------
# outcomes and gates


class Outcome(StrEnum):
    act = "act"
    defer = "defer"
    review = "review"


class ConfirmState(StrEnum):
    not_needed = "not_needed"
    pending = "pending"
    confirmed = "confirmed"
    denied = "denied"


@dataclass
class BandConfig:
    width: float = 0.1  # per catalog entry type; a skill's band_width overrides
    k: int = 3  # top-k cap under the band
    low_confidence: float = 0.2  # top score below this -> defer


@dataclass
class BandDecision:
    scores: dict[str, float]
    winners: list[str]
    top: float
    floor: float
    capped: bool
    outcome: Outcome

    def to_dict(self) -> dict[str, Any]:
        return {"scores": self.scores, "winners": self.winners, "top": self.top, "floor": self.floor, "capped": self.capped, "outcome": str(self.outcome)}


def select_band(scores: dict[str, float], config: BandConfig | None = None, widths: dict[str, float] | None = None) -> BandDecision:
    """Band first, then cap.

    Winners are every option whose score is within the band width of the
    top score (same band inject all). Then the top k remain. The width
    is the top option's own width when `widths` gives one, else the
    config width. A top score under `low_confidence` defers.
    """
    config = config or BandConfig()
    if not scores:
        return BandDecision({}, [], 0.0, 0.0, False, Outcome.defer)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top_key, top = ranked[0]
    width = (widths or {}).get(top_key, config.width)
    floor = top - width
    winners = [k for k, v in ranked if v >= floor]
    capped = len(winners) > config.k
    winners = winners[: config.k]
    outcome = Outcome.defer if top < config.low_confidence else Outcome.act
    return BandDecision(dict(scores), winners, top, floor, capped, outcome)


def select_floor(scores: dict[str, float], floor: float, k: int) -> BandDecision:
    """Every option at or above an absolute floor, best first, top k kept.

    For options that do not compete, such as recalled facts: one fact
    scoring near 1.0 says nothing about whether another at 0.8 is useful,
    so a band relative to the top would drop it. Nothing above the floor
    defers."""
    if not scores:
        return BandDecision({}, [], 0.0, floor, False, Outcome.defer)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    winners = [key for key, v in ranked if v >= floor]
    capped = len(winners) > k
    winners = winners[:k]
    return BandDecision(dict(scores), winners, ranked[0][1], floor, capped, Outcome.act if winners else Outcome.defer)


@dataclass
class FitResult:
    skill: str
    bot_ids: list[str]  # one bot, or two on a double tie
    role_scores: dict[str, float]
    past_work_scores: dict[str, float]
    confirm: ConfirmState = ConfirmState.pending  # never auto attach


@dataclass
class ScopeChange:
    changed: bool
    probability: float
    new_goal: bool = False
    new_constraints: bool = False
    confirm: ConfirmState = ConfirmState.not_needed  # pending when changed


@dataclass
class SummaryGate:
    allowed: bool
    reason: str = ""


@dataclass
class PromotionGate:
    allowed: bool
    probability: float
    from_tier: str = ""
    to_tier: str = ""
    confirm: ConfirmState = ConfirmState.not_needed


# ---------------------------------------------------------------------------
# loop supervision


class RunState(StrEnum):
    progressing = "progressing"
    stuck = "stuck"
    looping = "looping"


class SupervisorAction(StrEnum):
    continue_ = "continue"
    nudge = "nudge"
    halt = "halt"


SUPERVISOR_QUESTIONS: dict[str, Question] = {
    "run_state": ChoiceQuestion(
        instructions="Which single state best describes this agent run?",
        criteria={
            "progressing": "The agent is making new progress: trying different actions, producing new results, or moving closer to the goal.",
            "stuck": "The agent is blocked by an error or missing information it cannot resolve, even when it tries different actions.",
            "looping": "The agent is repeating the same tool calls or reasoning with the same outcome.",
        },
    ),
    "is_repeating": NoulQuestion(
        instructions="In the last third of the transcript, does the agent repeat a tool call it already made with the same arguments and get the same result?",
    ),
    "completion": ScoreQuestion(
        instructions="How close is the agent to completing the user's task?",
        criteria=["Has not started or is far from done", "Partway there, with meaningful work remaining", "Essentially done, only wrap-up left"],
    ),
}


@dataclass
class SupervisorThresholds:
    halt_looping: float = 0.8
    halt_repeating: float = 0.8
    nudge_looping: float = 0.5
    nudge_stuck: float = 0.4


@dataclass
class SupervisorCheck:
    run_state: ChoiceAnswer
    is_repeating: NoulAnswer
    completion: ScoreAnswer

    @classmethod
    def from_result(cls, r: DecisionResult) -> "SupervisorCheck":
        return cls(r.choice("run_state"), r.noul("is_repeating"), r.score("completion"))

    def p(self, state: RunState) -> float:
        return float(self.run_state.probabilities.get(str(state), 0.0))


def decide_action(check: SupervisorCheck, t: SupervisorThresholds | None = None) -> SupervisorAction:
    """Halt needs a strong double signal. Nudge needs one moderate signal.
    Halt pauses for confirm; nudge auto-injects. Both are decided here,
    applied by the supervisor."""
    t = t or SupervisorThresholds()
    if check.p(RunState.looping) >= t.halt_looping and check.is_repeating.noul >= t.halt_repeating:
        return SupervisorAction.halt
    if check.p(RunState.looping) >= t.nudge_looping or check.p(RunState.stuck) >= t.nudge_stuck:
        return SupervisorAction.nudge
    return SupervisorAction.continue_


@dataclass
class NudgeOutput:
    message: str  # injected as a user turn, no confirm
    cause: str


@dataclass
class HaltOutput:
    cause: str
    confirm: ConfirmState = ConfirmState.pending  # pause and wait; never stop outright


# ---------------------------------------------------------------------------
# score logging


def score_log(result: DecisionResult, request: DecisionRequest) -> dict[str, Any]:
    """The ledger payload for one decider call. Probabilities and cost,
    never the state text."""
    return {
        "purpose": request.purpose,
        "scorer": result.kind,
        "model": result.model,
        "provider": result.provider,
        "questions": list(request.questions),
        "answers": {k: answer_to_dict(a) for k, a in result.answers.items()},
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "cost": result.usage.cost,
        "decision_id": result.id,
    }


async def decide_and_log(scorer: Scorer, request: DecisionRequest, ledger: Any, bot_id: str, session_id: str) -> DecisionResult:
    """Call the scorer and write a `decider_score` ledger entry. Errors
    propagate: the caller decides whether to fail open."""
    from src.memory.ledger import Kind  # local import keeps this module store-free
    from src.providers import tracing

    name = f"decide-{request.purpose}" if request.purpose else "make-decision"
    traced_in = {"state": request.state, "questions": {k: question_to_dict(q) for k, q in request.questions.items()}}
    with tracing.observe(name, as_type="generation", input=traced_in,
                         metadata={"purpose": request.purpose, "scorer": getattr(scorer, "kind", "")}) as gen:
        result = await scorer.decide(request)
        u = result.usage
        gen.update(model=result.model, output={k: answer_to_dict(a) for k, a in result.answers.items()},
                   usage_details={"input": u.input_tokens, "output": u.output_tokens},
                   cost_details={"total": float(u.cost)} if isinstance(u.cost, (int, float)) else None,
                   metadata={"purpose": request.purpose, "scorer": result.kind, "provider": result.provider, "decision_id": result.id})
    ledger.append(Kind.decider_score, bot_id, session_id, **score_log(result, request))
    return result

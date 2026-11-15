"""Skill to bot fit, with a confirm gate.

When a skill is added, the decider suggests which bot should hold it.
Role first: one choice question over the bots' roles. Past work breaks
a tie: a second choice question over what each tied bot has actually
done, read from the ledger. A double tie suggests both bots.

Nothing attaches until the user confirms. The suggestion is a pending
record the API exposes; `confirm` performs the attach, `deny` drops it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.memory.ledger import Kind, Ledger
from src.skills.loader import Skill, SkillState

from .interface import ChoiceQuestion, ConfirmState, DecisionRequest, FitResult, Scorer, decide_and_log

if TYPE_CHECKING:
    from src.harness.bot import Bot


@dataclass
class FitConfig:
    tie_width: float = 0.1  # top two within this are a tie
    past_work_items: int = 10  # recent tasks per bot shown to the tiebreaker
    max_per_bot: int = 250


@dataclass
class FitSuggestion:
    skill: Skill
    result: FitResult
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    state: ConfirmState = ConfirmState.pending
    attached_to: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "skill": self.skill.slug,
            "bot_ids": self.result.bot_ids,
            "role_scores": self.result.role_scores,
            "past_work_scores": self.result.past_work_scores,
            "state": str(self.state),
            "attached_to": self.attached_to,
            "created_at": self.created_at,
        }


def past_work_from_ledger(ledger: Ledger, bot_ids: list[str], n: int = 10) -> dict[str, list[str]]:
    """Recent task texts per bot, from session-start entries."""
    out: dict[str, list[str]] = {b: [] for b in bot_ids}
    for e in ledger.tail(0, kind=str(Kind.session)):
        if e.bot_id in out and e.data.get("event") == "start" and e.data.get("task"):
            out[e.bot_id].append(str(e.data["task"]))
    return {b: v[-n:] for b, v in out.items()}


class FitEngine:
    def __init__(self, scorer: Scorer, ledger: Ledger, config: FitConfig | None = None) -> None:
        self.scorer = scorer
        self.ledger = ledger
        self.config = config or FitConfig()
        self._suggestions: dict[str, FitSuggestion] = {}

    # -- suggest ----------------------------------------------------------

    async def suggest(self, skill: Skill, bots: list["Bot"], session_id: str = "") -> FitSuggestion:
        if not bots:
            raise ValueError("fit needs at least one bot")
        state = {"skill": skill.name, "use": skill.use, "steps": skill.steps[:6], "return": skill.returns}
        if len(bots) == 1:
            result = FitResult(skill.slug, [bots[0].id], {bots[0].id: 1.0}, {})
        else:
            role_q = ChoiceQuestion(
                instructions="Which bot's role is the best home for this skill? Judge by role alone.",
                criteria={b.id: f"{b.name}: {b.role} {b.instructions}".strip() for b in bots},
            )
            role = await decide_and_log(self.scorer, DecisionRequest(state, {"role_fit": role_q}, purpose="fit"), self.ledger, "", session_id)
            role_scores = role.choice("role_fit").probabilities
            tied = _tied(role_scores, self.config.tie_width)
            past_scores: dict[str, float] = {}
            if len(tied) == 1:
                winners = tied
            else:
                work = past_work_from_ledger(self.ledger, tied, self.config.past_work_items)
                if any(work.values()):
                    pw_q = ChoiceQuestion(
                        instructions="These bots' roles fit equally. Which bot's past work is closest to this skill?",
                        criteria={b: ("; ".join(work[b]) or "no past work recorded") for b in tied},
                    )
                    pw = await decide_and_log(self.scorer, DecisionRequest(state, {"past_work": pw_q}, purpose="fit"), self.ledger, "", session_id)
                    past_scores = pw.choice("past_work").probabilities
                    winners = _tied(past_scores, self.config.tie_width)
                else:
                    winners = tied
            if len(winners) > 2:
                winners = winners[:2]
            result = FitResult(skill.slug, winners, role_scores, past_scores)
        s = FitSuggestion(skill, result)
        self._suggestions[s.id] = s
        return s

    # -- confirm gate -----------------------------------------------------

    def get(self, id: str) -> FitSuggestion | None:
        return self._suggestions.get(id)

    def list(self, state: ConfirmState | str | None = None) -> list[FitSuggestion]:
        return [s for s in self._suggestions.values() if state is None or s.state == state]

    def confirm(self, id: str, bot: "Bot") -> FitSuggestion:
        s = self._suggestions[id]
        if s.state != ConfirmState.pending:
            raise ValueError(f"fit {id} already {s.state}")
        if bot.id not in s.result.bot_ids:
            raise ValueError(f"bot {bot.id} was not suggested for fit {id}")
        state, reason = bot.attach_skill(s.skill, self.config.max_per_bot)
        if state != SkillState.added:
            raise ValueError(f"could not attach: {reason}")
        s.state, s.attached_to = ConfirmState.confirmed, bot.id
        self.ledger.append(Kind.promotion, bot.id, "", what="skill_attach", skill=s.skill.slug, fit=s.id, by="user confirm")
        return s

    def deny(self, id: str) -> FitSuggestion:
        s = self._suggestions[id]
        if s.state != ConfirmState.pending:
            raise ValueError(f"fit {id} already {s.state}")
        s.state = ConfirmState.denied
        return s


def _tied(scores: dict[str, float], width: float) -> list[str]:
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top = ranked[0][1]
    return [k for k, v in ranked if v >= top - width]

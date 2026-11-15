"""The protected task.

The task plus its constraints live in a pinned system block that
compaction never summarizes. The decider spots a scope change (a new
goal or new constraints) in incoming text; a change waits for the user
to confirm before the block is rewritten. On confirm the router's
score cache is bumped so skills are re-scored against the new scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

from src.decider.interface import ConfirmState, DecisionRequest, NoulQuestion, Question, Scorer, ScopeChange, decide_and_log

from .render import PINNED_HEADER  # noqa: F401 - re-exported


@dataclass
class ProtectedBlock:
    task: str
    constraints: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [PINNED_HEADER, "Task: " + self.task]
        if self.constraints:
            lines.append("Constraints:")
            lines += [f"- {c}" for c in self.constraints]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task, "constraints": list(self.constraints)}


SCOPE_QUESTIONS: dict[str, Question] = {
    "new_goal": NoulQuestion(
        instructions="Does the new message ask for a different outcome than the protected task, replacing or redirecting the goal rather than refining how it is done?",
    ),
    "new_constraints": NoulQuestion(
        instructions="Does the new message add, remove, or change a rule the agent must follow, beyond what the protected constraints already say?",
    ),
}


class ScopeGuard:
    """Holds the pinned block for one session and the one pending change."""

    def __init__(
        self,
        scorer: Scorer,
        ledger: Any,
        block: ProtectedBlock,
        *,
        on_confirm: Callable[[], None],
        threshold: float = 0.7,
    ) -> None:
        self.scorer = scorer
        self.ledger = ledger
        self.block = block
        self.on_confirm = on_confirm
        self.threshold = threshold
        self.pending: ScopeChange | None = None
        self._bot_id = ""
        self._session_id = ""

    async def check(self, text: str, *, bot_id: str, session_id: str) -> ScopeChange:
        """Ask the decider whether `text` changes scope. Fails open: on a
        scorer error nothing changes and nothing waits."""
        self._bot_id, self._session_id = bot_id, session_id
        request = DecisionRequest(
            state={"protected": self.block.to_dict(), "message": text},
            questions=SCOPE_QUESTIONS,
            purpose="scope",
        )
        try:
            result = await decide_and_log(self.scorer, request, self.ledger, bot_id, session_id)
        except Exception as e:  # noqa: BLE001 - fail open, the loop goes on
            self.ledger.append("scope_change", bot_id, session_id, ok=False, error=f"{type(e).__name__}: {e}")
            return ScopeChange(changed=False, probability=0.0)
        p_goal = result.noul("new_goal").noul
        p_cons = result.noul("new_constraints").noul
        new_goal, new_cons = p_goal >= self.threshold, p_cons >= self.threshold
        changed = new_goal or new_cons
        change = ScopeChange(
            changed=changed,
            probability=max(p_goal, p_cons),
            new_goal=new_goal,
            new_constraints=new_cons,
            confirm=ConfirmState.pending if changed else ConfirmState.not_needed,
        )
        if changed:
            self.pending = change
        self.ledger.append(
            "scope_change", bot_id, session_id, ok=True, changed=changed, new_goal=p_goal, new_constraints=p_cons,
            confirm=str(change.confirm),
        )
        return change

    def confirm(self, *, task: str | None = None, constraints: list[str] | None = None) -> ProtectedBlock:
        """Rewrite the block. Only after a pending change; otherwise a no-op."""
        if self.pending is None:
            return self.block
        self.block = replace(
            self.block,
            task=task if task is not None else self.block.task,
            constraints=list(constraints) if constraints is not None else list(self.block.constraints),
        )
        self.pending.confirm = ConfirmState.confirmed
        self.pending = None
        self.ledger.append("scope_change", self._bot_id, self._session_id, event="confirmed", block=self.block.to_dict())
        self.on_confirm()
        return self.block

    def reject(self) -> None:
        if self.pending is None:
            return
        self.pending.confirm = ConfirmState.denied
        self.pending = None
        self.ledger.append("scope_change", self._bot_id, self._session_id, event="rejected")

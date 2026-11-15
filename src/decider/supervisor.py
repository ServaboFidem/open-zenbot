"""Loop supervision.

Every CHECK_EVERY steps the supervisor renders the transcript, tool
calls included, and asks the decider three fixed questions: run_state,
is_repeating, completion. `decide_action` turns the probabilities into
continue, nudge, or halt. A nudge injects one user message and needs
no confirm. A halt pauses the session through the PauseController and
waits for the user to resume; it never stops the session outright. A
failed check fails open: the loop continues and the ledger has the
error. The supervisor never touches the protected task.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.context.render import render_transcript
from src.providers.interface import Message

from .interface import (
    SUPERVISOR_QUESTIONS,
    DecisionRequest,
    RunState,
    Scorer,
    SupervisorAction,
    SupervisorCheck,
    SupervisorThresholds,
    decide_action,
    decide_and_log,
)

if TYPE_CHECKING:
    from src.harness.bot import Bot
    from src.harness.loop import Session
    from src.harness.pause import PauseController

NUDGE_TEXT = (
    "Supervisor note: you appear to be {cause}. Stop repeating the same action. "
    "State in one line what you have learned so far, then try a different approach or ask for what you are missing."
)


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def thresholds_from_env() -> SupervisorThresholds:
    d = SupervisorThresholds()
    return SupervisorThresholds(
        halt_looping=_env_float("ZEN_HALT_LOOPING", d.halt_looping),
        halt_repeating=_env_float("ZEN_HALT_REPEATING", d.halt_repeating),
        nudge_looping=_env_float("ZEN_NUDGE_LOOPING", d.nudge_looping),
        nudge_stuck=_env_float("ZEN_NUDGE_STUCK", d.nudge_stuck),
    )


@dataclass
class SupervisorConfig:
    check_every: int = field(default_factory=lambda: int(os.environ.get("ZEN_SUPERVISE_EVERY", "5")))
    thresholds: SupervisorThresholds = field(default_factory=thresholds_from_env)


class LoopSupervisor:
    kind = "loop-supervisor"

    def __init__(self, scorer: Scorer, ledger: Any, pause: "PauseController", config: SupervisorConfig | None = None) -> None:
        self.scorer = scorer
        self.ledger = ledger
        self.pause = pause
        self.config = config or SupervisorConfig()

    def due(self, session: "Session") -> bool:
        n = self.config.check_every
        return n > 0 and session.steps > 0 and session.steps % n == 0

    async def after_step(self, bot: "Bot", session: "Session") -> SupervisorAction | None:
        """Called by the loop after each step. Returns the action taken,
        or None when no check was due. Halt returns only after the user
        resumes, or after a stop while paused (then session.state is stopped)."""
        if not self.due(session):
            return None
        request = DecisionRequest(
            state={"task": session.task, "step": session.steps, "transcript": render_transcript(session.messages)},
            questions=SUPERVISOR_QUESTIONS,
            purpose="supervise",
        )
        try:
            result = await decide_and_log(self.scorer, request, self.ledger, bot.id, session.id)
            check = SupervisorCheck.from_result(result)
        except Exception as e:  # noqa: BLE001 - fail open
            self.ledger.append(
                "supervisor", bot.id, session.id, ok=False, step=session.steps, action=str(SupervisorAction.continue_),
                error=f"{type(e).__name__}: {e}",
            )
            return SupervisorAction.continue_

        action = decide_action(check, self.config.thresholds)
        if session.stop_requested:  # the stop won the race with the check; act on nothing
            return SupervisorAction.continue_
        looping, stuck = check.p(RunState.looping), check.p(RunState.stuck)
        self.ledger.append(
            "supervisor", bot.id, session.id, ok=True, step=session.steps, action=str(action),
            run_state=check.run_state.choice, looping=looping, stuck=stuck, progressing=check.p(RunState.progressing),
            repeating=check.is_repeating.noul, completion=check.completion.score, cost=result.usage.cost, scorer=result.kind,
        )
        if action == SupervisorAction.nudge:
            cause = "looping" if looping >= stuck else "stuck"
            session.messages.append(Message("user", NUDGE_TEXT.format(cause=cause)))
            session.nudges += 1
        elif action == SupervisorAction.halt:
            session.halts += 1
            reason = f"supervisor halt: looping {looping:.2f}, repeating {check.is_repeating.noul:.2f}; resume to confirm continuing"
            await self.pause.hold_reason(session, reason)
        return action

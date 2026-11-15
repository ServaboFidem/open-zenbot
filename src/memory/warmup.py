"""Warm the memory store at start, so the first message isn't the one that pays.

Hindsight is slow cold: the first call boots its server (a child process) and
waits for it. The harness runs `Warmup.run()` as a task when it starts and the
GUI shows a loading overlay over `GET /warmup` until the state leaves `warming`.

A store without a cold start (the local JSON graph) skips straight to
`ready`; so does a harness with no key bound, since Hindsight needs the key to
start: the harness runs the warm-up again once a key is bound. A failed step ends the
warm-up as `failed` with the error, and the app opens anyway.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .graph import SHARED

PROBE = "warm up"  # any text: the searches load the indexes whatever they find


@dataclass
class Step:
    key: str
    label: str
    expected_s: float  # a typical warm-start duration (models already downloaded), so the GUI can move its bar


STEPS = [  # the first call boots the server (a child process) and waits for it; the probe then costs little
    Step("engine", "Starting the memory server", 35.0),  # measured warm start: 34.2-34.7 s (live check)
    Step("store", "Opening your knowledge store", 2.0),
]


class Warmup:
    kind = "warmup"

    def __init__(self, memory: Any, ledger: Any, *, bound: Callable[[], bool] = lambda: True) -> None:
        self.memory = memory
        self.ledger = ledger
        self.bound = bound
        self.state = "pending"  # pending | warming | ready | skipped | failed
        self.reason = ""
        self.error = ""
        self.steps = STEPS
        self.index = 0  # the step running, or len(self.steps) once done
        self.started = 0.0
        self.step_started = 0.0
        self.finished = 0.0

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        step = self.steps[self.index] if self.state == "warming" and self.index < len(self.steps) else None
        return {
            "state": self.state, "reason": self.reason, "error": self.error,
            "done": self.index, "total": len(self.steps),
            "step": step.label if step else "", "step_key": step.key if step else "",
            "step_elapsed_s": round(now - self.step_started, 2) if step else 0.0,
            "step_expected_s": step.expected_s if step else 0.0,
            "elapsed_s": round((self.finished or now) - self.started, 2) if self.started else 0.0,
        }

    def _actions(self) -> dict[str, Callable[[], Any]]:
        store = self.memory.store
        return {"engine": store.start, "store": lambda: store.search(PROBE, [SHARED], 10)}

    async def run(self) -> None:
        self.started, self.finished = time.monotonic(), 0.0  # run() may be repeated (a key bound after start)
        if not hasattr(self.memory.store, "start"):
            return self._end("skipped", reason="the memory store has no cold start")
        if not self.bound():
            return self._end("skipped", reason="no key bound yet; memory starts once a key is bound")  # the harness re-runs this once one is
        self.state = "warming"
        actions = self._actions()
        for i, step in enumerate(self.steps):
            self.index, self.step_started = i, time.monotonic()
            try:
                await asyncio.to_thread(actions[step.key])  # each blocks on the store; the API stays responsive
            except Exception as e:  # noqa: BLE001 - a cold store is slow, not broken: the app opens either way
                return self._end("failed", error=f"{step.key}: {type(e).__name__}: {e}"[:300])
        self.index = len(self.steps)
        self._end("ready")

    def _end(self, state: str, *, reason: str = "", error: str = "") -> None:
        self.state, self.reason, self.error = state, reason, error
        self.finished = time.monotonic()
        self.ledger.append(self.kind, "", "", event=state, elapsed_s=round(self.finished - self.started, 2),
                           reason=reason or None, error=error or None)

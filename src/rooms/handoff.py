"""Direct async handoffs.

One bot hands work to one other bot with a request id and an
instruction. The receiver owns that stage until it completes or fails
it; nobody else may act on it. Inputs and results travel by reference
(`scheme:identifier`, no whitespace), never by value, so a handoff can
never carry a document body, a draft, or a secret. A next stage links
back with `parent`. Every transition is in the ledger."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from src.memory.exclusion import exclusion

REF = re.compile(r"^[a-z][a-z0-9_-]{0,15}:\S{1,200}$")


class HandoffError(ValueError):
    pass


class HandoffState(StrEnum):
    pending = "pending"
    accepted = "accepted"
    done = "done"
    failed = "failed"


@dataclass
class Handoff:
    from_bot: str
    to_bot: str
    instruction: str
    inputs: list[str] = field(default_factory=list)
    parent: str | None = None
    state: HandoffState = HandoffState.pending
    result_ref: str | None = None
    reason: str = ""
    request_id: str = field(default_factory=lambda: uuid4().hex[:12])
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def owner(self) -> str:
        return self.to_bot

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id, "from_bot": self.from_bot, "to_bot": self.to_bot, "owner": self.owner,
            "instruction": self.instruction, "inputs": list(self.inputs), "parent": self.parent, "state": str(self.state),
            "result_ref": self.result_ref, "reason": self.reason, "created_at": self.created_at,
        }


def check_ref(value: str, what: str) -> str:
    if not isinstance(value, str) or not REF.match(value):
        raise HandoffError(f"{what} must be a reference like post:ID or file:PATH, not a value: {str(value)[:40]!r}")
    return value


class HandoffStore:
    def __init__(self, ledger: Any) -> None:
        self.ledger = ledger
        self.handoffs: dict[str, Handoff] = {}

    def _log(self, h: Handoff, event: str, **extra: Any) -> None:
        self.ledger.append("handoff", h.from_bot, h.request_id, event=event, to_bot=h.to_bot, owner=h.owner,
                           inputs=list(h.inputs), parent=h.parent, state=str(h.state), result_ref=h.result_ref, **extra)

    def create(self, from_bot: str, to_bot: str, instruction: str, inputs: list[str] | None = None, parent: str | None = None) -> Handoff:
        verdict = exclusion.check_ingest({"kind": "handoff", "text": instruction})
        if not verdict:
            raise HandoffError("instruction refused: " + ", ".join(verdict.reasons))
        if parent is not None and parent not in self.handoffs:
            raise HandoffError(f"parent {parent!r} is not a handoff")
        h = Handoff(from_bot=from_bot, to_bot=to_bot, instruction=instruction,
                    inputs=[check_ref(i, "input") for i in (inputs or [])], parent=parent)
        self.handoffs[h.request_id] = h
        self._log(h, "created")
        return h

    def get(self, request_id: str) -> Handoff:
        try:
            return self.handoffs[request_id]
        except KeyError:
            raise HandoffError(f"no handoff {request_id!r}") from None

    def _owned(self, request_id: str, by: str) -> Handoff:
        h = self.get(request_id)
        if by != h.owner:
            raise HandoffError(f"{by!r} is not the owner of this stage ({h.owner!r})")
        if h.state in (HandoffState.done, HandoffState.failed):
            raise HandoffError(f"handoff is already {h.state}")
        return h

    def accept(self, request_id: str, by: str) -> Handoff:
        h = self._owned(request_id, by)
        h.state = HandoffState.accepted
        self._log(h, "accepted")
        return h

    def complete(self, request_id: str, by: str, result_ref: str) -> Handoff:
        h = self._owned(request_id, by)
        h.result_ref = check_ref(result_ref, "result")
        h.state = HandoffState.done
        self._log(h, "done")
        return h

    def fail(self, request_id: str, by: str, reason: str) -> Handoff:
        h = self._owned(request_id, by)
        h.state, h.reason = HandoffState.failed, reason
        self._log(h, "failed", reason=reason)
        return h

    def list(self, owner: str | None = None, state: HandoffState | str | None = None) -> list[Handoff]:
        out = list(self.handoffs.values())
        if owner:
            out = [h for h in out if h.owner == owner]
        if state:
            out = [h for h in out if h.state == state]
        return out

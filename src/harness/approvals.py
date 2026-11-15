"""Approvals.

A skill's frontmatter lists what needs approval. The gate matches a tool
call against those rules. No regex over the prompt text.

Rule forms:
  "shell:<glob>"   the shell command matches the glob (fnmatch)
  "<tool>"         any call to that tool
  "<tool>:<glob>"  the tool's first string argument matches the glob
  "*"              every tool call

A pending request waits until the user decides. There is no timeout and
no auto deny: on silence the session stays paused.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Any
from uuid import uuid4

from src.providers.interface import ToolCall
from src.skills.loader import Skill


class ApprovalState(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


@dataclass
class ApprovalRequest:
    bot_id: str
    session_id: str
    title: str
    detail: str
    rule: str
    tool: str
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    state: ApprovalState = ApprovalState.pending
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    decided_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "session_id": self.session_id,
            "title": self.title,
            "detail": self.detail,
            "rule": self.rule,
            "tool": self.tool,
            "state": str(self.state),
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }


def _first_string_arg(call: ToolCall) -> str:
    for v in call.arguments.values():
        if isinstance(v, str):
            return v
    return ""


def needs_approval(skills: list[Skill], call: ToolCall) -> str | None:
    """The first matching rule, or None."""
    subject = _first_string_arg(call)
    for skill in skills:
        for rule in skill.approvals:
            if rule == "*":
                return rule
            tool, sep, pattern = rule.partition(":")
            if tool != call.name:
                continue
            if not sep:
                return rule
            if fnmatchcase(subject, pattern.strip()):
                return rule
    return None


class ApprovalStore:
    def __init__(self) -> None:
        self._items: dict[str, ApprovalRequest] = {}
        self._events: dict[str, asyncio.Event] = {}

    def create(self, bot_id: str, session_id: str, title: str, detail: str, rule: str, tool: str) -> ApprovalRequest:
        req = ApprovalRequest(bot_id, session_id, title, detail, rule, tool)
        self._items[req.id] = req
        self._events[req.id] = asyncio.Event()
        return req

    def restore(self, req: ApprovalRequest) -> ApprovalRequest:
        """Register a request rebuilt from disk (a learn hold after a restart) with its id, state, and
        timestamps as they were. An id already present keeps the stored request."""
        if req.id in self._items:
            return self._items[req.id]
        self._items[req.id] = req
        self._events[req.id] = asyncio.Event()
        if req.state != ApprovalState.pending:
            self._events[req.id].set()
        return req

    def get(self, id: str) -> ApprovalRequest | None:
        return self._items.get(id)

    def list(self, state: ApprovalState | str | None = None, session_id: str | None = None) -> list[ApprovalRequest]:
        return [
            r
            for r in self._items.values()
            if (state is None or r.state == state) and (session_id is None or r.session_id == session_id)
        ]

    def decide(self, id: str, approved: bool) -> ApprovalRequest:
        req = self._items[id]
        if req.state != ApprovalState.pending:
            raise ValueError(f"approval {id} already {req.state}")
        req.state = ApprovalState.approved if approved else ApprovalState.rejected
        req.decided_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._events[id].set()
        return req

    async def wait(self, id: str) -> ApprovalRequest:
        """Block until decided. No timeout by design."""
        await self._events[id].wait()
        return self._items[id]

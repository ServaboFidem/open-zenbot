"""The recording format.

A recording is a list of browser actions plus a trace: navigation,
clicks, typed input by field name, selections. No screen pixels. The
`source` field says where the actions came from. v1 writes
`browser-actions`. `screen-capture` is reserved for a later source and
is refused on read so nothing downstream builds on it by accident.
Values that match a secret are stored as vault handle names
(`vault:NAME`), never as the value; the recorder does that before an
action reaches this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

ACTION_KINDS = ("navigate", "click", "type", "select", "wait")


class UnsupportedSource(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Action:
    kind: str
    target: str  # url for navigate, selector or field name otherwise
    value: str | None = None  # typed text or selected option; a vault handle when secret
    intent: str = ""  # what the person meant: "open the invoices page"
    url: str = ""  # page url at the time
    at: float = 0.0  # seconds since the recording started

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "target": self.target, "value": self.value, "intent": self.intent, "url": self.url, "at": self.at}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Action":
        return cls(kind=str(d["kind"]), target=str(d["target"]), value=d.get("value"), intent=str(d.get("intent") or ""),
                   url=str(d.get("url") or ""), at=float(d.get("at") or 0.0))


@dataclass
class Recording:
    SOURCES = ("browser-actions", "screen-capture")
    RESERVED = ("screen-capture",)

    name: str
    source: str = "browser-actions"
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    started_at: str = field(default_factory=_now)
    stopped_at: str | None = None
    max_s: float = 600.0
    duration_s: float = 0.0
    redactions: int = 0
    truncated: bool = False
    actions: list[Action] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "source": self.source, "started_at": self.started_at, "stopped_at": self.stopped_at,
            "max_s": self.max_s, "duration_s": self.duration_s, "redactions": self.redactions, "truncated": self.truncated,
            "actions": [a.to_dict() for a in self.actions], "trace": list(self.trace),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Recording":
        source = str(d.get("source") or "")
        if source in cls.RESERVED:
            raise UnsupportedSource(f"source {source!r} is reserved and not supported yet")
        if source not in cls.SOURCES:
            raise UnsupportedSource(f"unknown recording source {source!r}")
        return cls(
            name=str(d.get("name") or ""), source=source, id=str(d.get("id") or uuid4().hex[:12]),
            started_at=str(d.get("started_at") or _now()), stopped_at=d.get("stopped_at"),
            max_s=float(d.get("max_s") or 600.0), duration_s=float(d.get("duration_s") or 0.0),
            redactions=int(d.get("redactions") or 0), truncated=bool(d.get("truncated")),
            actions=[Action.from_dict(a) for a in d.get("actions") or []], trace=list(d.get("trace") or []),
        )

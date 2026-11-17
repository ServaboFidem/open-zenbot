"""The ledger.

The user's window into what the harness did. Append-only JSONL in a
directory adjacent to the Obsidian vault, so the user finds it next to
their notes. Every entry carries a timestamp, bot id, and session id.

Secrets in free text are redacted and the entry is marked. Approval
values (the grant, the approved draft) are refused: the ledger records
that an approval happened, never what was approved.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .exclusion import APPROVAL_FIELDS, APPROVAL_KINDS, ExclusionError, ExclusionFilter, _walk_keys, exclusion as default_filter

LEDGER_SUFFIX = "-ledger"
LEDGER_FILE = "ledger.jsonl"


class Kind(StrEnum):
    session = "session"
    turn = "turn"
    tool_start = "tool_start"
    tool_call = "tool_call"
    decider_score = "decider_score"
    promotion = "promotion"
    pause = "pause"
    resume = "resume"
    approval_request = "approval_request"
    approval_decision = "approval_decision"
    error = "error"
    compaction = "compaction"
    scope_change = "scope_change"
    supervisor = "supervisor"
    room = "room"
    room_post = "room_post"
    room_turn = "room_turn"
    turn_note = "turn_note"
    handoff = "handoff"
    helper = "helper"
    recording = "recording"
    memory = "memory"
    ingest = "ingest"
    learn = "learn"
    settings = "settings"
    mcp = "mcp"
    onboarding = "onboarding"
    import_dir = "import"
    rebuild = "rebuild"
    image = "image"
    routine = "routine"
    delegate = "delegate"
    design_part = "design_part"


def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def ledger_path_for(vault_root: Path) -> Path:
    """The one function that owns the vault-to-ledger mapping."""
    vault_root = Path(vault_root)
    return vault_root.parent / f"{vault_root.name}{LEDGER_SUFFIX}"


@dataclass
class Entry:
    id: str
    ts: str
    kind: str
    bot_id: str
    session_id: str
    data: dict[str, Any] = field(default_factory=dict)
    redacted: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Ledger:
    def __init__(self, vault_root: Path, filter: ExclusionFilter | None = None) -> None:
        self.vault_root = Path(vault_root)
        self.dir = ledger_path_for(self.vault_root)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / LEDGER_FILE
        self.filter = filter or default_filter
        self._subscribers: list[tuple[asyncio.Queue[Entry], asyncio.AbstractEventLoop | None]] = []
        self._write_lock = threading.Lock()

    # -- write ------------------------------------------------------------

    def append(self, kind: Kind | str, bot_id: str, session_id: str, **data: Any) -> Entry:
        kind = str(kind)
        if kind in APPROVAL_KINDS:
            raise ExclusionError(f"ledger refuses kind {kind!r}")
        offending = APPROVAL_FIELDS & set(_walk_keys(data))
        if offending:
            raise ExclusionError(f"ledger refuses approval fields {sorted(offending)}")
        data, reasons = self.filter.redact_obj(data)
        entry = Entry(
            id=uuid4().hex,
            ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            kind=kind,
            bot_id=bot_id,
            session_id=session_id,
            data=data,
            redacted=sorted(set(reasons)),
        )
        with self._write_lock, self.file.open("a", encoding="utf-8") as f:  # appends from a worker thread must not tear a line
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        for q, loop in list(self._subscribers):
            if loop is not None and not _on_loop(loop):
                loop.call_soon_threadsafe(q.put_nowait, entry)  # an import runs ingest off the loop
            else:
                q.put_nowait(entry)
        return entry

    # -- read -------------------------------------------------------------

    def iter(self) -> Iterator[Entry]:
        if not self.file.exists():
            return
        with self.file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield Entry(**json.loads(line))

    def tail(
        self,
        n: int = 50,
        *,
        kind: str | None = None,
        bot_id: str | None = None,
        session_id: str | None = None,
    ) -> list[Entry]:
        rows = [
            e
            for e in self.iter()
            if (kind is None or e.kind == kind)
            and (bot_id is None or e.bot_id == bot_id)
            and (session_id is None or e.session_id == session_id)
        ]
        return rows[-n:] if n else rows

    # -- live -------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[Entry]:
        q: asyncio.Queue[Entry] = asyncio.Queue()
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        self._subscribers.append((q, loop))
        return q

    def unsubscribe(self, q: asyncio.Queue[Entry]) -> None:
        self._subscribers = [(s, loop) for s, loop in self._subscribers if s is not q]

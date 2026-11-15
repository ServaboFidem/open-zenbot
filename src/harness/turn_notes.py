"""Turn notes: a bot's private note to its next turn in the same conversation.

Each turn is a fresh `Session`; what a bot learned by working (the folder it found, the files it made,
the recipe that worked, what is left) would end with it. When a turn ends, however it ends, `TurnNotes`
writes a short note from the turn's tool calls and their results: the summary model folds it into the
previous note, and a mechanical note built from the tool results stands in when that call fails or is
slow. The note is redacted and capped, stored with the conversation (one per bot per conversation, under
the ledger folder, out of the bots' reach), and read back into that bot's next turn: its context message
and the task the router scores. Another bot never sees it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from src.memory.exclusion import exclusion

if TYPE_CHECKING:
    from src.generation.generator import Generator
    from src.harness.bot import Bot
    from src.harness.loop import Session

__all__ = ["NOTE_HEADING", "TurnNoteConfig", "TurnNotes", "for_another_bot", "mechanical_note", "outcome_of", "render_turn"]

NOTE_HEADING = (
    "Your notes from your last turn in this conversation (your own record, built from your tool results; "
    "files under Made exist unless a tool shows otherwise; check anything under Failed before relying on it):"
)

RESULT_CHARS = 600  # of each tool result: its head and tail
RENDER_CHARS = 12_000  # of the whole rendering: the latest work matters most, so the tail is kept
FALLBACK_CALLS = 8  # tool calls the mechanical note lists


@dataclass
class TurnNoteConfig:
    enabled: bool = field(default_factory=lambda: os.environ.get("ZEN_TURN_NOTES", "1") != "0")
    max_chars: int = field(default_factory=lambda: int(os.environ.get("ZEN_TURN_NOTE_CHARS", "1500")))
    timeout_s: float = field(default_factory=lambda: float(os.environ.get("ZEN_TURN_NOTE_TIMEOUT", "20")))


def outcome_of(session: "Session", deadline_s: float | None = None) -> str:
    """How a turn ended, in the words a note uses. `deadline_s` set: the room deadline cut it."""
    if deadline_s is not None:
        return f"deadline after {deadline_s:.0f} s"
    state = str(session.state)
    if state == "failed":
        return f"failed: {session.error}" if session.error else "failed"
    return state


def _clip(text: str, n: int) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    half = n // 2
    return f"{text[:half]}\n…\n{text[-half:]}"


def _args(arguments: Any) -> str:
    """Arguments as `key=value` with strings verbatim: JSON would double every backslash in a Windows path,
    and the note must carry paths exactly."""
    if isinstance(arguments, dict):
        return "; ".join(f"{k}={v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}"
                         for k, v in arguments.items())
    return arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)


def _calls(session: "Session") -> list[tuple[str, str, str]]:
    """(tool, arguments, result) for each tool call the turn made, in order."""
    results = {m.tool_call_id: m.content for m in session.messages if m.role == "tool"}
    return [(c.name, _args(c.arguments), results.get(c.id, "(no result: the turn ended first)"))
            for m in session.messages if m.role == "assistant" for c in m.tool_calls]


def render_turn(session: "Session") -> str:
    """The turn's work as the summary model reads it: each tool call and its (clipped) result, after the
    compaction summary of any earlier part. Empty when the turn called no tools."""
    lines = [f"- {name} {args}\n  result: {_clip(result, RESULT_CHARS) or '(empty: ok)'}"
             for name, args, result in _calls(session)]
    if not lines:
        return ""
    head = f"Earlier in this turn (summary): {session.summary}\n" if session.summary else ""
    text = head + "\n".join(lines)
    return text if len(text) <= RENDER_CHARS else "…\n" + text[-RENDER_CHARS:]


def _status(result: str) -> str:
    first = result.strip().splitlines()[0] if result.strip() else ""
    exit_code = re.search(r"\[exit (-?\d+)\]", result)
    if exit_code and exit_code.group(1) != "0":
        return f"exit {exit_code.group(1)}"
    if re.match(r"(?i)^(\w*error|refused|failed)\b", first) or first.startswith("(no result"):
        return f"failed: {first[:200]}"
    return "ok"


def mechanical_note(session: "Session", outcome: str, max_chars: int) -> str:
    """A note without a model: the last tool calls with their outcomes, and how the turn ended. Built only
    from tool results, never from the bot's own words."""
    calls = _calls(session)[-FALLBACK_CALLS:]
    lines = [f"Ended: {outcome}."]
    if calls:
        lines.append("Last tool calls (newest last):")
        lines += [f"- {name} {_clip(args, 200)} -> {_status(result)}" for name, args, result in calls]
    return "\n".join(lines)[:max_chars]


_ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/)[^\s;,()'\"]*")


def for_another_bot(note: str) -> str:
    """The note's Made / Failed / Left lines for the room's lead, with every absolute path cut to its last
    part: another bot gets file names, never directories (as `_thread_line` does for attachments)."""
    keep = [ln for ln in note.splitlines() if re.match(r"\s*(Made|Failed|Left)\s*:", ln)]

    def name(m: re.Match[str]) -> str:
        parts = [p for p in re.split(r"[\\/]", m.group(0)) if p]
        return parts[-1] if parts else ""

    return "\n".join(_ABS_PATH.sub(name, ln).strip() for ln in keep)


class TurnNotes:
    def __init__(self, generator: "Generator", ledger: Any, config: TurnNoteConfig | None = None) -> None:
        self.generator = generator
        self.ledger = ledger
        self.config = config or TurnNoteConfig()

    async def write(self, bot: "Bot", session: "Session", *, outcome: str, previous: str = "",
                    save: Callable[[str], None]) -> str:
        """Write this turn's note, `save` it, and return it. A turn that called no tools keeps `previous`
        (nothing is saved). On a cancel during the summary call, the mechanical note is saved first."""
        if not self.config.enabled:
            return ""
        rendered = render_turn(session)
        if not rendered:
            return previous
        source = "model"
        try:
            note = await asyncio.wait_for(
                self.generator.turn_note(previous, rendered, outcome, self.config.max_chars), self.config.timeout_s)
        except asyncio.CancelledError:
            self._keep(bot, session, outcome, mechanical_note(session, outcome, self.config.max_chars), "fallback", save)
            raise
        except Exception:  # noqa: BLE001 - a bare note beats none
            note = ""
        if not note.strip():
            note, source = mechanical_note(session, outcome, self.config.max_chars), "fallback"
        return self._keep(bot, session, outcome, note, source, save)

    def _keep(self, bot: "Bot", session: "Session", outcome: str, note: str, source: str,
              save: Callable[[str], None]) -> str:
        note, _ = exclusion.redact(note.strip())
        note = note[: self.config.max_chars]
        save(note)
        self.ledger.append("turn_note", bot.id, session.id, note=note, outcome=outcome, source=source,
                           conversation=session.conversation_id, room=session.room_id)
        return note

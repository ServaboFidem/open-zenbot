"""Compaction with a protected task.

Under budget the session is returned unchanged. Over budget the context
becomes: the system prompt, the pinned protected block, one summary,
and the most recent whole turns. A turn is an assistant message plus
the tool results that answer it, so a cut never orphans a tool result.
Old turns render to text with their tool calls and go through the
generator (chunked, summarized in parallel, joined as Part N of M), each
request sized to the summary model's context. The summary folds forward
on the next round. On a generator error the full history is kept and the
failure is in the ledger; a context too small is told to `on_overrun`.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.generation.generator import Fitted, Generator
from src.providers.interface import Message

from .fit import ContextOverrun, Overrun
from .protected import ProtectedBlock
from .render import is_pinned, render_transcript  # noqa: F401 - re-exported

log = logging.getLogger(__name__)

SUMMARY_HEADER = "## Summary of earlier work"
CONTINUE_TEXT = "Continue the protected task. The summary above covers the earlier turns; the recent turns follow."


@dataclass
class CompactionConfig:
    budget_chars: int = field(default_factory=lambda: int(os.environ.get("ZEN_CONTEXT_BUDGET_CHARS", "120000")))
    keep_recent_turns: int = field(default_factory=lambda: int(os.environ.get("ZEN_KEEP_RECENT_TURNS", "6")))
    # a conversation's history (summary plus exchanges) past this folds into its summary, all but the last `keep` exchanges
    conversation_budget_chars: int = field(default_factory=lambda: int(os.environ.get("ZEN_CONVERSATION_BUDGET_CHARS", "60000")))
    conversation_keep: int = field(default_factory=lambda: int(os.environ.get("ZEN_CONVERSATION_KEEP", "6")))


@dataclass
class CompactResult:
    messages: list[Message]
    summary: str
    compacted: bool
    error: str | None = None
    overrun: Overrun | None = None  # the length refusal, or the context too small, that made it fail


def is_summary(m: Message) -> bool:
    return m.role == "system" and m.content.startswith(SUMMARY_HEADER)


def message_chars(m: Message) -> int:
    n = len(m.content)
    for c in m.tool_calls:
        n += len(c.name) + len(json.dumps(c.arguments))
    return n


def context_chars(messages: list[Message]) -> int:
    return sum(message_chars(m) for m in messages)


def cut_index(messages: list[Message], keep: int, start: int) -> int:
    """Index of the first message to keep: the start of the `keep`-th
    whole turn from the end. Turns start on any non-tool message, so a
    tool result always travels with the call before it."""
    starts = [i for i in range(start, len(messages)) if messages[i].role != "tool"]
    if keep <= 0 or not starts:
        return len(messages)
    return starts[-keep] if len(starts) >= keep else start


class Compactor:
    def __init__(
        self,
        generator: Generator,
        ledger: Any,
        config: CompactionConfig | None = None,
        *,
        on_overrun: Callable[[str, str, Overrun], None] | None = None,
        on_success: Callable[[], None] | None = None,
    ) -> None:
        self.generator = generator
        self.ledger = ledger
        self.config = config or CompactionConfig()
        self.on_overrun = on_overrun  # (bot_id, session_id, overrun) when the summary model's context was too small
        self.on_success = on_success  # a compaction's summary was written

    async def maybe_compact(
        self,
        messages: list[Message],
        block: ProtectedBlock,
        *,
        bot_id: str,
        session_id: str,
        summary: str = "",
    ) -> CompactResult:
        size = context_chars(messages)
        if size <= self.config.budget_chars:
            return CompactResult(messages, summary, compacted=False)

        head = messages[:1] if messages and messages[0].role == "system" and not is_pinned(messages[0]) else []
        body_start = len(head)
        while body_start < len(messages) and (is_pinned(messages[body_start]) or is_summary(messages[body_start])):
            body_start += 1
        fixed = head + [Message("system", block.render()), Message("system", SUMMARY_HEADER + "\n" + summary), Message("user", CONTINUE_TEXT)]
        keep = max(1, self.config.keep_recent_turns)
        cut = cut_index(messages, keep, body_start)
        # shrink the kept window until the result fits; one whole turn is the floor
        while keep > 1 and context_chars(fixed) + context_chars(messages[cut:]) > self.config.budget_chars:
            keep -= 1
            cut = cut_index(messages, keep, body_start)
        old = [m for m in messages[body_start:cut] if not is_pinned(m) and not is_summary(m)]
        if not any(m.role == "assistant" for m in old):
            # nothing worth summarizing: the kept window or the system prompt alone is over budget
            self.ledger.append("compaction", bot_id, session_id, ok=True, skipped=True, before=len(messages), chars=size, kept_turns=keep)
            return CompactResult(messages, summary, compacted=False)

        try:
            fitted = await self.generator.compact_and_fold(render_transcript(old), summary, goal=block.task)
        except ContextOverrun as e:  # keep the full history, and say the context was the problem
            err, o = f"{type(e).__name__}: {e}", e.overrun
            self.ledger.append("compaction", bot_id, session_id, ok=False, error=err, before=len(messages), chars=size,
                               overrun=True, n_ctx=o.n_ctx, n_prompt_tokens=o.n_prompt_tokens)
            notify(self.on_overrun, bot_id, session_id, o)
            return CompactResult(messages, summary, compacted=False, error=err, overrun=o)
        except Exception as e:  # noqa: BLE001 - keep the full history and say so
            err = f"{type(e).__name__}: {e}"
            self.ledger.append("compaction", bot_id, session_id, ok=False, error=err, before=len(messages), chars=size)
            return CompactResult(messages, summary, compacted=False, error=err)
        new_summary = fitted.summary

        out = head + [Message("system", block.render()), Message("system", SUMMARY_HEADER + "\n" + new_summary)]
        if messages[cut].role != "user":
            out.append(Message("user", CONTINUE_TEXT))  # chat backends expect a user turn before the first assistant turn
        out += messages[cut:]
        self.ledger.append(
            "compaction", bot_id, session_id, ok=True, before=len(messages), after=len(out), chars=size,
            chars_after=context_chars(out), summary_chars=len(new_summary), folded=fitted.folded, kept_turns=keep,
            **fit_fields(fitted),
        )
        notify(self.on_success)
        return CompactResult(out, new_summary, compacted=True)


def fit_fields(fitted: Fitted) -> dict[str, Any]:
    """The ledger's account of how a summary was fitted to the model: numbers only."""
    fields: dict[str, Any] = {"pieces": fitted.pieces, "context": fitted.context}
    if fitted.retried:
        fields["retried"] = True
    return fields


def notify(hook: Callable[..., None] | None, *args: Any) -> None:
    """Tell a hook; a hook that fails never fails the compaction (logged by class name only)."""
    if hook is None:
        return
    try:
        hook(*args)
    except Exception as e:  # noqa: BLE001
        log.warning("summary context hook failed: %s", type(e).__name__)

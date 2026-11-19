"""What a dream reads from a conversation: the user's own messages, and only those.

Nothing but `Exchange.user` is ever returned here: not the reply, the addendum, comments, attachments,
the summary or the note."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the harness imports this package, so the import is for types only
    from ..harness.conversations import Conversation

MESSAGE_MAX = 4000
BATCH_MAX = 12000
QUIET_S = 1800
FORM_PREFIX = "Form answers ("


def unread_marked(conv: Conversation, read: int, clean: Callable[[str], bool]) -> tuple[list[tuple[str, int]], int]:
    """The messages to read past the mark `read`, each with the mark that holds once it has been read
    (its exchange index + 1), and the new mark: the index of the first pending exchange, else the end.
    Skipped but counted as read: a routed or set-aside exchange, a form's answers, a repeat of an earlier
    message (those before `read` count), an empty one, and one `clean` refuses."""
    kept: list[tuple[str, int]] = []
    exchanges = conv.exchanges
    seen = {e.user.strip() for e in exchanges[:read]}
    mark = read
    for i in range(read, len(exchanges)):
        e = exchanges[i]
        if e.outcome == "pending":
            return kept, i
        text = e.user.strip()
        repeat = text in seen
        seen.add(text)
        mark = i + 1
        if (e.outcome == "routed" or e.set_aside or e.answers_to or text.startswith(FORM_PREFIX)
                or repeat or not text or not clean(text)):
            continue
        kept.append((text[:MESSAGE_MAX], mark))
    return kept, mark


def unread(conv: Conversation, read: int, clean: Callable[[str], bool]) -> tuple[list[str], int]:
    """`unread_marked` without the per-message marks: the texts and the new mark."""
    kept, mark = unread_marked(conv, read, clean)
    return [text for text, _ in kept], mark


def batches(messages: list[str], limit: int = BATCH_MAX) -> list[list[str]]:
    """Split messages, in order, into groups of at most `limit` characters (a lone longer one stands alone)."""
    out: list[list[str]] = []
    size = 0
    for m in messages:
        if out and size + len(m) <= limit:
            out[-1].append(m)
            size += len(m)
        else:
            out.append([m])
            size = len(m)
    return out

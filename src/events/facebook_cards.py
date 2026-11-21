"""Split Facebook event pool rows into approval cards and feed cards.

A pool row waits as an approval card when the user tapped want-to-go on
Facebook (``Interested`` or ``Going``) or when the event is personal: the
row names a friend in memory (tier ``close``, through
:func:`src.memory.friend_check.is_friend`) as the inviter, a host, an
organizer, or someone going. Every other event row lands in the feed.
A ``Like`` tap creates nothing: likes never count as want-to-go.

Only the Facebook tap counts. A reaction, a comment, a share, or a view
is not want-to-go, whatever value a caller puts under the signal keys:
anything but ``Interested``/``Going`` is ``other`` (feed), and an
explicit like drops.

Cards only, never the calendar. Nothing here imports or calls the
calendar store: approval cards hold until the owner says yes, and the
lifecycle slice turns an approved card into an event through
:func:`src.events.calendar_add.make_event` under
:data:`src.events.calendar_add.add_lock`. Birthdays are not split here
either (source ``"facebook-birthday"`` drops as ``not_event_source``):
:mod:`src.events.birthdays` owns them.

Shapes (``row`` is always the pool row itself, untouched):

- approval: ``{"id", "reason", "row"}`` with ``reason`` one of ``rsvp``,
  ``personal``, ``rsvp_personal``;
- feed: ``{"id", "reason", "row"}`` with ``reason`` ``"other"``;
- dropped: ``{"id", "reason"}`` with ``reason`` one of ``like``,
  ``not_event_source``, ``bad_row``. Ids and counts only, never
  listing text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.events.facebook_pool import SOURCE
from src.memory.friend_check import is_friend
from src.memory.registry import PeopleRegistry

__all__ = [
    "LIKE_VALUES",
    "RSVP_VALUES",
    "REASONS",
    "SplitResult",
    "approval_card",
    "classify",
    "feed_card",
    "is_like",
    "is_personal",
    "is_rsvp",
    "person_names",
    "rsvp_of",
    "split",
]

#: The Facebook taps that count as want-to-go (normalized: stripped, casefolded).
RSVP_VALUES = frozenset({"interested", "going"})

#: The Facebook taps that create nothing (normalized the same way).
LIKE_VALUES = frozenset({"like", "likes", "liked"})

#: Every reason a row can carry, by bucket.
REASONS = {
    "approval": ("rsvp", "personal", "rsvp_personal"),
    "feed": ("other",),
    "dropped": ("like", "not_event_source", "bad_row"),
}

#: Row keys holding one person's name (the inviter, a host, an organizer).
_SINGLE_PERSON_KEYS = ("inviter", "host", "organizer")

#: Row keys holding several people's names (hosts, organizers, friends going).
_LIST_PERSON_KEYS = ("hosts", "organizers", "friends_going", "attending", "attendees")

#: Row keys holding the Facebook tap (the first present wins).
_SIGNAL_KEYS = ("rsvp", "response")


def rsvp_of(row: dict[str, Any]) -> str:
    """The row's normalized want-to-go signal, or ``""`` when it has none.

    Only a string under a signal key counts, stripped and casefolded;
    anything else (missing, empty, not a string) answers ``""``.
    """
    for key in _SIGNAL_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return ""


def is_rsvp(row: dict[str, Any]) -> bool:
    """Whether the row's Facebook tap is want-to-go (Interested or Going)."""
    return rsvp_of(row) in RSVP_VALUES


def is_like(row: dict[str, Any]) -> bool:
    """Whether the row's Facebook tap is a like, which creates nothing."""
    return rsvp_of(row) in LIKE_VALUES


def person_names(row: dict[str, Any]) -> list[str]:
    """The people's names the row attaches (inviter, hosts, organizers, friends going).

    Only the person keys count: a venue or a city named like a friend is
    a place, not the friend. Non-string and blank values are ignored.
    """
    names: list[str] = []
    for key in _SINGLE_PERSON_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            names.append(value.strip())
    for key in _LIST_PERSON_KEYS:
        value = row.get(key)
        if isinstance(value, (list, tuple)):
            names.extend(v.strip() for v in value if isinstance(v, str) and v.strip())
    return names


def is_personal(row: dict[str, Any], people: PeopleRegistry | None) -> bool:
    """Whether anyone the row names is a friend in memory (tier ``close``).

    Read-only: answers False when ``people`` is None or the row names
    nobody, and never adds, promotes, or edits.
    """
    if people is None:
        return False
    return any(is_friend(people, name) for name in person_names(row))


def classify(row: Any, people: PeopleRegistry | None = None) -> tuple[str, str]:
    """The ``(bucket, reason)`` for one pool row.

    ``bucket`` is ``approval``, ``feed``, or ``dropped``. An explicit
    like drops first: a like tap creates nothing even on a friend's
    event. Then an RSVP tap or a friend holds for approval, and
    everything else lands in the feed.
    """
    if not isinstance(row, dict) or not row.get("id"):
        return "dropped", "bad_row"
    if row.get("source") != SOURCE:
        return "dropped", "not_event_source"
    if is_like(row):
        return "dropped", "like"
    rsvp = is_rsvp(row)
    personal = is_personal(row, people)
    if rsvp and personal:
        return "approval", "rsvp_personal"
    if rsvp:
        return "approval", "rsvp"
    if personal:
        return "approval", "personal"
    return "feed", "other"


def approval_card(row: dict[str, Any], reason: str) -> dict[str, Any]:
    """An approval hold for ``row``: the row itself plus why it holds."""
    return {"id": row["id"], "reason": reason, "row": row}


def feed_card(row: dict[str, Any]) -> dict[str, Any]:
    """A feed card for ``row``: the row itself, held for nothing."""
    return {"id": row["id"], "reason": "other", "row": row}


@dataclass
class SplitResult:
    """What :func:`split` kept and why the rest dropped (ids and reasons only)."""

    approval: list[dict[str, Any]] = field(default_factory=list)
    feed: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)  # {"id", "reason"}


def split(rows: list[dict[str, Any]], people: PeopleRegistry | None = None
          ) -> SplitResult:
    """Split pool rows into approval cards, feed cards, and drops.

    RSVP or personal rows wait as approval cards; other event rows land
    in the feed; likes and non-event rows drop. Read-only on ``people``
    and writes nothing anywhere: cards only, never the calendar.
    """
    if not isinstance(rows, list):
        raise ValueError("not a batch")
    out = SplitResult()
    for row in rows:
        bucket, reason = classify(row, people)
        if bucket == "approval":
            out.approval.append(approval_card(row, reason))
        elif bucket == "feed":
            out.feed.append(feed_card(row))
        else:
            rid = row.get("id") if isinstance(row, dict) else ""
            out.dropped.append({"id": rid if isinstance(rid, str) else "",
                                "reason": reason})
    return out

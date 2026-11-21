"""Close-friend birthday holds and the single week-before nudge.

Source rows are the helper's birthday corner of the pool
(:mod:`src.events.facebook_pool`, source ``"facebook-birthday"``): each row
holds a name and the next occurrence as an all-day ``start_date``. Each
close-friend birthday holds the calendar, through the friend check
(:func:`src.memory.friend_check.is_friend`, tier ``close``). No other
birthday holds.

A hold is an ordinary made-here event (:meth:`CalendarStore.add`): an
all-day event on the birthday, so push sends it like any other once the
caller kicks (``CalendarService.kick``; locked and kick rules unchanged).
The nudge fires once, seven days out, on both surfaces (``chat`` and
``feed``). It expects nothing back and never blocks: every delivery is
best effort and every failure is counted, never raised.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from uuid import uuid4

from src.events.facebook_pool import BIRTHDAY_SOURCE
from src.harness.scheduler import Routine, RoutineStore, Schedule
from src.memory.friend_check import is_friend

log = logging.getLogger(__name__)

__all__ = [
    "BIRTHDAY_BOT_ID",
    "BIRTHDAY_MANAGED_BY",
    "BIRTHDAY_ROUTINE_NAME",
    "BIRTHDAY_ROUTINE_SKILL",
    "NUDGE_DAYS_OUT",
    "SURFACES",
    "NudgeSink",
    "birthday_title",
    "ensure_routine",
    "hold_birthday",
    "hold_birthdays",
    "hold_fields",
    "is_due_for_nudge",
    "nudge_text",
    "nudges_due",
    "send_nudges",
]

BIRTHDAY_MANAGED_BY = "birthdays"
BIRTHDAY_ROUTINE_NAME = "Birthday holds and nudges"
BIRTHDAY_ROUTINE_SKILL = "birthdays"
BIRTHDAY_BOT_ID = "birthday-bot"

#: The single nudge goes out this many days before the birthday.
NUDGE_DAYS_OUT = 7
#: The two surfaces the nudge fires on: chat and feed.
SURFACES: tuple[str, str] = ("chat", "feed")

#: A nudge delivery: ``sink(surface, nudge)`` with ``surface`` one of
#: :data:`SURFACES` and ``nudge`` a ``{"name", "start_date", "text"}`` dict.
NudgeSink = Callable[[str, dict], None]


def birthday_title(name: str) -> str:
    """The calendar title for ``name``'s birthday hold."""
    return f"{name.strip()}'s birthday"


def _next_day(day: str) -> str | None:
    """The day after the ``YYYY-MM-DD`` day key ``day``, or None when malformed."""
    try:
        return (
            date.fromisoformat(day)
            .fromordinal(date.fromisoformat(day).toordinal() + 1)
            .isoformat()
        )
    except ValueError:
        return None


def hold_fields(row: dict) -> dict | None:
    """The :meth:`CalendarStore.add` fields for a pool birthday ``row``.

    An all-day hold on the row's ``start_date`` (end exclusive, the next
    day). None when the row has no usable name or date.
    """
    name = row.get("name") if isinstance(row.get("name"), str) else ""
    name = name.strip()
    start = row.get("start_date") if isinstance(row.get("start_date"), str) else ""
    if not name or not start:
        return None
    try:
        date.fromisoformat(start)
    except ValueError:
        return None
    end = _next_day(start)
    if end is None:
        return None
    return {
        "title": birthday_title(name),
        "location": "",
        "description": f"Birthday of {name}",
        "all_day": True,
        "start": start,
        "end": end,
        "tz": "",
    }


def _row_is_birthday(row: object) -> bool:
    return isinstance(row, dict) and row.get("source") == BIRTHDAY_SOURCE


def hold_birthday(row: dict, people: object, store: object) -> tuple[dict | None, str]:
    """Hold one pool birthday row on the calendar.

    Returns ``(event, "")`` when a hold was created, else ``(None, reason)``:
    ``not_birthday_source`` (only the helper birthdays corner holds),
    ``non_friend`` (close friends only, through the friend check),
    ``bad_row`` (no usable name or date), or ``existing`` (a live hold with
    the same title already covers the day).
    """
    if not _row_is_birthday(row):
        return None, "not_birthday_source"
    name = row.get("name") if isinstance(row.get("name"), str) else ""
    if not name or not name.strip():
        return None, "bad_row"
    if not is_friend(people, name):  # type: ignore[arg-type]
        return None, "non_friend"
    fields = hold_fields(row)
    if fields is None:
        return None, "bad_row"
    for have in store.events(fields["start"], fields["end"]):  # type: ignore[union-attr]
        if have.get("title", "").casefold() == fields["title"].casefold():
            return None, "existing"
    return store.add(fields), ""  # type: ignore[union-attr]


def hold_birthdays(
    rows: list[dict],
    people: object,
    store: object,
    *,
    kick: Callable[[], None] | None = None,
) -> dict:
    """Hold every close-friend birthday in ``rows``; nothing else holds.

    Answers ``{"held", "skipped"}``: ``held`` is the created events,
    ``skipped`` counts by reason (``not_birthday_source``, ``non_friend``,
    ``bad_row``, ``existing``). When holds were made and ``kick`` is given,
    it is called once so push sends them (locked and kick rules unchanged).
    """
    held: list[dict] = []
    skipped: dict[str, int] = {}
    for row in rows:
        event, reason = hold_birthday(row, people, store)
        if event is not None:
            held.append(event)
        else:
            skipped[reason] = skipped.get(reason, 0) + 1
    if held and kick is not None:
        kick()
    return {"held": held, "skipped": skipped}


def _days_until(start_date: str, today: str) -> int | None:
    """Whole days from ``today`` to ``start_date`` (both day keys), or None."""
    try:
        return (date.fromisoformat(start_date) - date.fromisoformat(today)).days
    except ValueError:
        return None


def is_due_for_nudge(row: dict, today: str) -> bool:
    """Whether ``row``'s birthday is exactly :data:`NUDGE_DAYS_OUT` out from ``today``."""
    if not _row_is_birthday(row):
        return False
    start = row.get("start_date")
    if not isinstance(start, str):
        return False
    return _days_until(start, today) == NUDGE_DAYS_OUT


def nudge_text(name: str, start_date: str) -> str:
    """The nudge line for ``name``'s birthday on ``start_date`` (a week out)."""
    return f"{name.strip()}'s birthday is in a week ({start_date})."


def nudges_due(rows: list[dict], today: str) -> list[dict]:
    """The ``{"name", "start_date", "text"}`` nudges due ``today`` (seven days out)."""
    out: list[dict] = []
    for row in rows:
        if not is_due_for_nudge(row, today):
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        out.append(
            {
                "name": name.strip(),
                "start_date": row["start_date"],
                "text": nudge_text(name, row["start_date"]),
            }
        )
    return out


def send_nudges(rows: list[dict], today: str, sink: NudgeSink) -> dict:
    """Fire each due nudge once on both surfaces; the nudge never blocks.

    Every ``sink(surface, nudge)`` call is best effort: a failure is
    counted and the next surface still fires. Answers ``{"nudged",
    "delivered", "errors"}``: nudges due, deliveries made, deliveries
    refused by the sink.
    """
    nudged = 0
    delivered = 0
    errors = 0
    for nudge in nudges_due(rows, today):
        nudged += 1
        for surface in SURFACES:
            try:
                sink(surface, nudge)
            except Exception as e:  # noqa: BLE001 - a nudge never blocks the run
                errors += 1
                log.warning("birthday nudge not delivered: %s", type(e).__name__)
            else:
                delivered += 1
    return {"nudged": nudged, "delivered": delivered, "errors": errors}


def ensure_routine(
    routines: RoutineStore,
    *,
    bot: str = BIRTHDAY_BOT_ID,
    skill: str = BIRTHDAY_ROUTINE_SKILL,
) -> Routine | None:
    """Register the daily birthday rhythm through the RoutineStore pattern.

    Makes the managed routine when missing (daily 09:00: holds plus the
    week-before nudge) and switches it back on; answers it, or None when
    ``routines`` is None.
    """
    if routines is None:
        return None
    found = next(
        (r for r in routines.all() if r.managed_by == BIRTHDAY_MANAGED_BY), None
    )
    if found is None:
        found = Routine(
            uuid4().hex[:10],
            BIRTHDAY_ROUTINE_NAME,
            bot,
            skill,
            True,
            Schedule("daily", time="09:00"),
            managed_by=BIRTHDAY_MANAGED_BY,
        )
    else:
        found.enabled = True
    routines.upsert(found)
    routines.save()
    return found

"""Shared calendar-add helper for discovery and browser-helper intake.

One lock serializes every add, and one builder writes every calendar event,
so the card route, the pool route, and the later split/lifecycle paths cannot
make two live calendar events for one pool event. No path writes except
through :func:`make_event`.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from fastapi import HTTPException

from src.events.store import EventError

PAST = "This event has already happened."
BAD_TIMES = "Those times are not the listing's. Reload the page and try again."
NO_ZONE = "This listing has no usable time zone."
MAX_HOURS = 14
_INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

add_lock = asyncio.Lock()  # the whole add runs under it: two requests for one pool event cannot both create


class AddTimes(Protocol):
    """The page's instants for one listing: ``start`` and ``end``."""

    start: str
    end: str


def _instant(value: str) -> datetime | None:
    if not _INSTANT.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _day(value: str) -> date | None:
    if not _DAY.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _wall(day: str, clock: str) -> datetime | None:
    try:
        return datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def listing_times(card: dict[str, Any], start: str, end: str) -> dict[str, Any]:
    """The calendar event's timing fields for ``card`` from the page's instants, or ``EventError``."""
    if not card["start_time"]:  # an all-day event: exactly the listing's dates, the end exclusive
        last = _day(card["end_date"] or card["start_date"])
        if _day(card["start_date"]) is None or last is None or start != card["start_date"] \
                or end != (last + timedelta(days=1)).isoformat():
            raise EventError(BAD_TIMES)
        return {"all_day": True, "start": start, "end": end, "tz": ""}
    if not card["timezone"]:  # the listing's zone failed the check when it was stored: no instant can be trusted
        raise EventError(NO_ZONE)
    a, b = _instant(start), _instant(end)
    wall = _wall(card["start_date"], card["start_time"])
    if a is None or b is None or wall is None or b <= a:
        raise EventError(BAD_TIMES)
    limit = timedelta(hours=MAX_HOURS)
    if abs(a - wall) > limit:
        raise EventError(BAD_TIMES)
    if card["end_date"] and card["end_time"]:
        listed_end = _wall(card["end_date"], card["end_time"])
        if listed_end is None or abs(b - listed_end) > limit:
            raise EventError(BAD_TIMES)
    elif b - a != timedelta(hours=1):  # no end listed: one hour, nothing else
        raise EventError(BAD_TIMES)
    return {"all_day": False, "start": start, "end": end, "tz": card["timezone"]}


def make_event(discovery: Any, listing: dict[str, Any], body: AddTimes) -> dict[str, Any]:
    """The calendar event for a card or a pool row, at the listing's own time.

    409 when it has passed, 422 for times that are not the listing's or fields
    the calendar refuses. The only way discovery makes a calendar event.
    """
    if (listing["end_date"] or listing["start_date"]) < discovery.today():
        raise HTTPException(409, PAST)
    try:
        timing = listing_times(listing, body.start, body.end)
        # a timed listing that ended earlier today: its end instant (checked against the listing just above)
        # has passed. An all-day listing keeps the date rule.
        if not timing["all_day"] and timing["end"] <= discovery.now():
            raise HTTPException(409, PAST)
        place = ", ".join(p for p in (listing["venue"], listing["address"]) if p)
        text = f"{listing['summary']}\n\n{listing['url']}" if listing["summary"] else listing["url"]
        return discovery.calendar.store.add(
            {"title": listing["name"], "location": place, "description": text, **timing})
    except EventError as e:
        raise HTTPException(422, str(e)) from None

"""Which texts are due now, read from the built-in calendar (the same rows the Today card reads).

A pulled Google event is known by its Google event id, not its local id: pull makes the local id again when it
re-inserts a row, and an invitation that shows on two calendars is one event. So it gets one reminder, and appears
once in the rundown. An event made here is known by its own id. Hidden calendars are skipped, as on the Today card."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from src.events.morning_agenda import _bounds, _clock
from src.telnyx.texts import clean, reminder
from src.telnyx.texts import rundown as rundown_text

ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Due:
    key: str
    kind: str  # "before" or "rundown"
    text: str  # "" for a rundown with nothing tomorrow


def event_id(row: dict) -> str:
    if row.get("origin") == "google" and row.get("google_id"):
        return "g:" + row["google_id"]
    return row["id"]


def _rows(store, first, last) -> list[dict]:
    """Visible events overlapping the days ``first`` to ``last`` with a day either side, one per event id. The store
    compares dates and instants as text, so the margin is fetched and the caller compares in each one's own form."""
    hidden = {c["id"] for c in store.calendars() if not c["visible"]}
    seen, out = set(), []
    for row in store.events((first - ONE_DAY).isoformat(), (last + 2 * ONE_DAY).isoformat()):
        key = (event_id(row), row["start"])
        if row["calendar_id"] in hidden or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def reminders(store, now: datetime, lead_minutes: int) -> list[Due]:
    """Timed events whose reminder time has come and that have not started: ``start - lead <= now < start``."""
    lead = timedelta(minutes=lead_minutes)
    due = []
    for row in _rows(store, now.date(), (now + lead).date()):
        if row["all_day"]:
            continue
        start = datetime.fromisoformat(row["start"])
        if start - lead <= now < start:
            due.append((start, Due(f"before:{event_id(row)}:{row['start']}", "before",
                                   reminder(row["title"], _clock(start, now), row["location"]))))
    due.sort(key=lambda pair: pair[0])
    return [d for _, d in due]


def rundown(store, now: datetime, evening: str) -> Due | None:
    """Tomorrow's events, due from ``evening`` until midnight. None outside that window."""
    if now.strftime("%H:%M") < evening:
        return None
    tomorrow = now.date() + ONE_DAY
    begin, end = _bounds(datetime.combine(tomorrow, time(12), now.tzinfo))
    days, timed = [], []
    for row in _rows(store, tomorrow, tomorrow):
        title = clean(row["title"])
        if row["all_day"]:
            if row["start"] <= tomorrow.isoformat() < row["end"]:
                days.append(f"All day {title}")
            continue
        start = datetime.fromisoformat(row["start"])
        if begin <= start < end:
            timed.append((start, title.casefold(), f"{_clock(start, now)} {title}"))
    items = days + [item for *_, item in sorted(timed)]
    return Due(f"rundown:{now.date().isoformat()}", "rundown", rundown_text(items) if items else "")

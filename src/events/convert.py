"""Google event items to local events and back.

A timed event is kept as UTC instants plus an IANA ``tz``; an all-day event as dates, end exclusive.
Nothing here converts a wall-clock time: Google sends instants with an offset, and the page owns zones."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

NO_TITLE = "(No title)"  # shown here for a Google event with no summary; never sent to Google
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _day(value: Any) -> str:
    """``value`` when it is a real date as YYYY-MM-DD; ``ValueError`` otherwise."""
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        raise ValueError("not a date")
    return date.fromisoformat(value).isoformat()


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_local(item: dict) -> dict | None:
    """The local fields of a Google event, or None for one this calendar cannot show (cancelled, no start, a
    malformed date or time). Text is kept whole, however long: an edit here is checked by the store, so
    nothing is ever sent back to Google shortened."""
    start = item.get("start")
    if item.get("status") == "cancelled" or not isinstance(start, dict):
        return None
    end = item.get("end") if isinstance(item.get("end"), dict) else {}
    unspecified = bool(item.get("endTimeUnspecified"))
    try:
        if start.get("date"):
            first = _day(start["date"])
            if end.get("date") and not unspecified:
                last = _day(end["date"])
            else:
                last = (date.fromisoformat(first) + timedelta(days=1)).isoformat()
            all_day, tz = True, ""
        else:
            begin = _instant(str(start["dateTime"]))
            finish = _instant(str(end["dateTime"])) if end.get("dateTime") and not unspecified else None
            first = _z(begin)
            last = _z(finish if finish is not None else begin + timedelta(hours=1))
            all_day, tz = False, str(start.get("timeZone") or "")
    except (KeyError, ValueError):
        return None
    return {
        "title": _text(item.get("summary")) or NO_TITLE,
        "location": _text(item.get("location")),
        "description": _text(item.get("description")),
        "all_day": all_day, "start": first, "end": last, "tz": tz,
    }


def to_google(event: dict, *, patch: bool = False) -> dict:
    """The body sent to Google. ``patch``: for a PATCH, which merges ``start`` and ``end`` into what Google
    holds, so the other form (date against dateTime) is nulled; an insert carries no nulls. A pulled event
    still showing the no-title placeholder goes back with an empty summary."""
    def when(value: str) -> dict:
        if event["all_day"]:
            return {"date": value, **({"dateTime": None, "timeZone": None} if patch else {})}
        return {"dateTime": value, **({"timeZone": event["tz"]} if event.get("tz") else {}),
                **({"date": None} if patch else {})}

    title = event["title"]
    if event.get("origin") == "google" and title == NO_TITLE:
        title = ""
    return {"summary": title, "location": event.get("location", ""),
            "description": event.get("description", ""), "start": when(event["start"]), "end": when(event["end"])}


def calendar_row(item: dict) -> dict:
    return {
        "id": item["id"],
        "name": item.get("summaryOverride") or item.get("summary") or "",
        "color": item.get("backgroundColor") or "",
        "tz": item.get("timeZone") or "",
        "writable": item.get("accessRole") in {"owner", "writer"},
        "primary": bool(item.get("primary")),
    }

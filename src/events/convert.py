"""Google event items to local events and back.

A timed event is kept as UTC instants plus an IANA ``tz``; an all-day event as dates, end exclusive.
Nothing here converts a wall-clock time: Google sends instants with an offset, and the page owns zones."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.events.store import MAX_DESCRIPTION, MAX_LOCATION, MAX_TITLE

NO_TITLE = "(No title)"


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_local(item: dict) -> dict | None:
    start = item.get("start")
    if item.get("status") == "cancelled" or not isinstance(start, dict):
        return None
    end = item.get("end") if isinstance(item.get("end"), dict) else {}
    unspecified = bool(item.get("endTimeUnspecified"))
    try:
        if start.get("date"):
            first = str(start["date"])
            last = str(end.get("date") or "") if not unspecified else ""
            if not last:
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
        "title": _text(item.get("summary"), MAX_TITLE) or NO_TITLE,
        "location": _text(item.get("location"), MAX_LOCATION),
        "description": _text(item.get("description"), MAX_DESCRIPTION),
        "all_day": all_day, "start": first, "end": last, "tz": tz,
    }


def to_google(event: dict) -> dict:
    def when(value: str) -> dict:
        if event["all_day"]:
            return {"date": value}
        return {"dateTime": value, **({"timeZone": event["tz"]} if event.get("tz") else {})}

    return {"summary": event["title"], "location": event.get("location", ""),
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

"""The FindLocal connector: reads a city's events from FindLocal's keyed REST API.

It knows FindLocal and nothing else. Everything in an answer is untrusted text, so three rules hold:

- one endpoint: requests go only to https://findlocal.community/api/events, with a city checked against
  FindLocal's own list;
- nothing read from an answer is ever fetched (a link in a row is stored, never followed);
- no redirect is followed: any 3xx is `source_changed`.

Errors carry a kind (`SourceError.kind`): `network`, `key` (401), `refused` (429), `source_changed`, `provider`.
Nothing here logs; an exception's text is never kept, and the key is only ever put in a request header."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from urllib.parse import urlsplit

import httpx

from src.events.discover_settings import CATEGORIES, FINDLOCAL_CITIES
from src.events.eventbrite import (
    NAME_MAX, PLACE_MAX, SUMMARY_MAX, TIMEOUT_S, VENUE_ADDRESS_MAX, GAP_S, PAGE_LIMIT, ReadResult, SourceError,
    _body, _clean, _day, _time,
)

API_URL = "https://findlocal.community/api/events"
MAX_CALLS = 10
LIMIT = 100
DAYS_AHEAD = 60
LINK_MAX = 500

CATEGORY_TAGS: dict[str, str] = {
    "music": "EventbriteCategory/103",
    "comedy": "EventbriteCategory/105", "theater": "EventbriteCategory/105", "dance": "EventbriteCategory/105",
    "art": "EventbriteCategory/105", "film": "EventbriteCategory/105",
    "fitness": "EventbriteCategory/108", "sports": "EventbriteCategory/108",
    "food_drink": "EventbriteCategory/110",
    "community": "EventbriteCategory/113",
}
SLUGS = ("music", "comedy", "theater", "dance", "literary", "film", "talks", "art", "food_drink", "family",
         "market", "workshop", "fitness", "sports", "nightlife", "community", "festival", "parks")

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_EVENTBRITE_PATH = re.compile(r"/e/[^/]*?([0-9]{1,30})/?")


def category_names(slug: str) -> list[str]:
    """The slug's own name, then the Eventbrite category's name when it maps to one and differs."""
    if slug not in SLUGS:
        return []
    own = "Food & Drink" if slug == "food_drink" else slug.replace("_", " ").title()
    names = [own]
    mapped = CATEGORIES.get(CATEGORY_TAGS.get(slug, ""), "")
    if mapped and mapped != own:
        names.append(mapped)
    return names


def _link(value: object) -> str:
    """`value` when it is an https link with a host, no user info, no space or control character, short enough."""
    if not isinstance(value, str) or not value.startswith("https://") or len(value) > LINK_MAX:
        return ""
    if any(ord(c) <= 32 or ord(c) == 127 or c == "\\" or not c.isascii() for c in value):
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
            return ""
    except ValueError:
        return ""
    return value


def _eventbrite_id(*links: object) -> str:
    """The digits ending an https://www.eventbrite.com/e/... path in the first link that has them, else ''."""
    for value in links:
        if not isinstance(value, str) or len(value) > 2000:
            continue
        try:
            parts = urlsplit(value)
            if parts.scheme != "https" or parts.netloc != "www.eventbrite.com":
                continue
        except ValueError:
            continue
        found = _EVENTBRITE_PATH.fullmatch(parts.path)
        if found:
            return found.group(1)
    return ""


def _event(row: object, zone: str) -> dict | None:
    """One kept event from an API row, or None when the row is not one we keep."""
    if not isinstance(row, dict):
        return None
    event_id = row.get("id")
    if not isinstance(event_id, str) or not _UUID.fullmatch(event_id):
        return None
    if row.get("status") in ("cancelled", "postponed") or row.get("attendance") == "online":
        return None
    if row.get("is_deleted") == 1 or row.get("duplicate_of") or row.get("is_real_event") == 0:
        return None
    name = _clean(row.get("title"), NAME_MAX)
    start_date = _day(row.get("event_date"))
    url = _link(row.get("detail_page_url")) or _link(row.get("ticket_page_url"))
    if not name or not start_date or not url:
        return None
    start = row.get("start_time")
    start_time = _time(start[:5] if isinstance(start, str) else None)
    end = row.get("end_time")
    end_time = _time(end[:5] if isinstance(end, str) else None)
    if start_time:
        # a timed event ends that day, and only when the end is after the start
        end_date = start_date if end_time > start_time else ""
        end_time = end_time if end_date else ""
    else:
        end_time = ""
        end_date = _day(row.get("end_date"))
        if not end_date or end_date < start_date:
            end_date = start_date
    venue = _clean(row.get("venue_name"), VENUE_ADDRESS_MAX)
    address = "" if row.get("venue_tba") == 1 else _clean(row.get("venue_address"), VENUE_ADDRESS_MAX)
    address = address[:max(VENUE_ADDRESS_MAX - len(venue) - (2 if venue and address else 0), 0)].strip()
    category = row.get("category") if isinstance(row.get("category"), str) else ""
    tag = CATEGORY_TAGS.get(category)
    return {
        "id": f"fl-{event_id}", "source": "findlocal", "name": name,
        "summary": _clean(row.get("description"), SUMMARY_MAX),
        "start_date": start_date, "start_time": start_time, "end_date": end_date, "end_time": end_time,
        "timezone": zone, "venue": venue, "address": address,
        "city": _clean(row.get("city"), PLACE_MAX), "region": _clean(row.get("region"), PLACE_MAX),
        "image_url": "", "url": url,
        "categories": [tag] if tag else [], "category_names": category_names(category), "format": "",
        "eventbrite_id": _eventbrite_id(row.get("detail_page_url"), row.get("ticket_page_url")),
    }


class FindLocal:
    def __init__(self, http: Callable[[], httpx.AsyncClient],
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._http = http
        self._sleep = sleep

    async def _page(self, params: dict[str, str], key: str) -> tuple[list, int | None]:
        """The `data` list of one answer and its `meta.total` (None when unusable), or SourceError."""
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        try:
            async with self._http().stream("GET", API_URL, params=params, headers=headers, follow_redirects=False,
                                           timeout=TIMEOUT_S) as response:
                status = response.status_code
                if status == 401:
                    raise SourceError("key")
                if status == 429:
                    raise SourceError("refused")
                if 300 <= status < 400:
                    raise SourceError("source_changed")  # a redirect is never followed
                if status != 200:
                    raise SourceError("provider")
                body = await _body(response, PAGE_LIMIT)
        except httpx.HTTPError:
            raise SourceError("network") from None
        if body is None:
            raise SourceError("provider")
        try:
            answer = json.loads(body.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            raise SourceError("source_changed") from None
        if not isinstance(answer, dict) or not isinstance(answer.get("data"), list):
            raise SourceError("source_changed")
        meta = answer.get("meta")
        total = meta.get("total") if isinstance(meta, dict) else None
        usable = total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None
        return answer["data"], usable

    async def read(self, city: str, key: str, zone: str, today: str) -> ReadResult:
        """The city's events from `today` for `DAYS_AHEAD` days, a page of `LIMIT` at a time, at most `MAX_CALLS`
        calls, `GAP_S` apart. Raises SourceError only when the first call fails; a later failure ends the reading
        and is named in `error`. `complete` is true only when the end was reached, never when the cap stopped it."""
        if city not in FINDLOCAL_CITIES:
            raise ValueError("not a FindLocal city")
        if not isinstance(key, str) or not key or not all(c.isascii() and c.isprintable() and c != " " for c in key):
            raise SourceError("key")  # a key that cannot go in a header is refused before any request
        start = date.fromisoformat(today)
        base = {"city": city, "date_from": start.isoformat(), "date_to": (start + timedelta(days=DAYS_AHEAD)).isoformat(),
                "sort": "date", "limit": str(LIMIT)}
        events: list[dict] = []
        seen: set[str] = set()
        calls = 0
        complete = False  # true only when the reader saw the end: an empty page, or as many rows as `meta.total`
        while calls < MAX_CALLS:
            if calls:
                await self._sleep(GAP_S)
            try:
                data, total = await self._page({**base, "page": str(calls + 1)}, key)
                if data and not any(isinstance(r, dict) and isinstance(r.get("id"), str) for r in data):
                    raise SourceError("source_changed")
            except SourceError as e:
                if not calls:
                    raise
                return ReadResult(events, calls, e.kind, complete=False)
            calls += 1
            for r in data:
                event = _event(r, zone)
                if event is not None and event["id"] not in seen:
                    seen.add(event["id"])
                    events.append(event)
            if not data or (total is not None and calls * LIMIT >= total):
                complete = True
                break
        # stopped at MAX_CALLS with more left: no error, but the rows not seen are not gone from FindLocal
        return ReadResult(events, calls, "", complete=complete)

"""The Eventbrite connector: reads a city's events from Eventbrite's public listing pages.

It knows Eventbrite and nothing else. Everything on a page is untrusted text written by strangers, and this is
the only code in the events package that makes outside requests, so two things are boundaries:

- requests go only to https://www.eventbrite.com/d/<place>/... (the place is checked) and, for a thumbnail,
  https://img.evbuc.com/...; nothing read from a page is ever fetched except a picture on that image host;
- `parse_page` keeps a row only when it has the expected shape, strips control characters from every text and
  caps its length, and keeps a link only when it is on the expected host.

Errors carry a kind (`SourceError.kind`): `network`, `refused` (403 or 429), `source_changed`, `provider`.
Nothing here logs; an exception's text is never kept."""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

import httpx

from src.events.discover_settings import PLACE

MAX_PAGES = 40
PER_MONTH = 20
GAP_S = 1.0
PAGE_LIMIT = 5_000_000
IMAGE_LIMIT = 1_500_000
IMAGE_HOST = "img.evbuc.com"
SITE_HOST = "www.eventbrite.com"
TIMEOUT_S = 30.0
MONTHS = ("this", "next")

NAME_MAX = 300
VENUE_ADDRESS_MAX = 500
SUMMARY_MAX = 1000
PLACE_MAX = 100  # city, region, category and format names
URL_MAX = 1000
MAX_ROWS = 200  # rows looked at on one page (a page holds 20)
MAX_PAGE_COUNT = 10_000
MAX_TAGS = 60

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US",
}

_ASSIGN = re.compile(r"window\.__SERVER_DATA__\s*=\s*\{")
_ID = re.compile(r"[0-9]{1,30}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
_ZONE = re.compile(r"[A-Za-z0-9_+\-/]{1,64}")
_CATEGORY_TAG = re.compile(r"EventbriteCategory/[0-9]{1,6}")
_SPACES = {"\t", "\n", "\r", " ", " "}


class SourceError(Exception):
    """A failed read; `kind` is one of network, refused, source_changed, provider."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass
class ReadResult:
    events: list[dict]
    pages: int
    error: str = ""  # "" or the kind that cut the reading short


def page_url(place: str, month: str, page: int) -> str:
    if not isinstance(place, str) or not PLACE.fullmatch(place):
        raise ValueError("not a place")
    if month not in MONTHS:
        raise ValueError("month must be this or next")
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= MAX_PAGE_COUNT:
        raise ValueError("not a page")
    return f"https://{SITE_HOST}/d/{place}/events--{month}-month/?page={page}"


# ---- parsing -------------------------------------------------------------------------------------------

def _clean(value: object, limit: int) -> str:
    """Text with control characters taken out (line breaks and tabs become a space), trimmed, cut to `limit`."""
    if not isinstance(value, str):
        return ""
    out = []
    for ch in value:
        if ch in _SPACES:
            out.append(" ")
        elif unicodedata.category(ch) != "Cc":
            out.append(ch)
    return "".join(out).strip()[:limit].strip()


def _day(value: object) -> str:
    if isinstance(value, str) and _DATE.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return ""
        return value
    return ""


def _time(value: object) -> str:
    return value if isinstance(value, str) and _TIME.fullmatch(value) else ""


def _link(value: object, host: str, prefix: str) -> str:
    """`value` when it is an https link on exactly `host` whose path starts `prefix`, with no stray characters."""
    if not isinstance(value, str) or not value.startswith(f"https://{host}{prefix}") or len(value) > URL_MAX:
        return ""
    if any(ord(c) <= 32 or ord(c) == 127 or c == "\\" or not c.isascii() for c in value):
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.hostname != host or parts.netloc != host or not parts.path.startswith(prefix):
            return ""
    except ValueError:
        return ""
    return value


def _image(row: dict) -> str:
    image = row.get("image")
    if not isinstance(image, dict):
        return ""
    original = image.get("original")
    for candidate in (original.get("url") if isinstance(original, dict) else None, image.get("url")):
        link = _link(candidate, IMAGE_HOST, "/")
        if link:
            return link
    return ""


def _tags(row: dict) -> tuple[list[str], list[str], str]:
    tags = row.get("tags")
    categories: list[str] = []
    names: list[str] = []
    fmt = ""
    if not isinstance(tags, list):
        return categories, names, fmt
    for tag in tags[:MAX_TAGS]:
        if not isinstance(tag, dict):
            continue
        key = tag.get("tag")
        if tag.get("prefix") == "EventbriteCategory" and isinstance(key, str) and _CATEGORY_TAG.fullmatch(key):
            if key not in categories:
                categories.append(key)
                names.append(_clean(tag.get("display_name"), PLACE_MAX))
        elif tag.get("prefix") == "EventbriteFormat" and not fmt:
            fmt = _clean(tag.get("display_name"), PLACE_MAX)
    return categories, names, fmt


def _event(row: object) -> dict | None:
    """One kept event from a listing row, or None when the row is not one we keep."""
    if not isinstance(row, dict):
        return None
    event_id = row.get("id")
    if not isinstance(event_id, str) or not _ID.fullmatch(event_id):
        return None
    if row.get("is_cancelled") or row.get("is_online_event"):
        return None
    name = _clean(row.get("name"), NAME_MAX)
    start_date = _day(row.get("start_date"))
    url = _link(row.get("url"), SITE_HOST, "/e/")
    if not name or not start_date or not url:
        return None
    start_time = _time(row.get("start_time"))
    end_date = _day(row.get("end_date"))
    end_time = _time(row.get("end_time"))
    if start_time:
        # a timed event keeps its end only when it is after the start
        if not (end_date and end_time and (end_date, end_time) > (start_date, start_time)):
            end_date = end_time = ""
    else:
        end_time = ""
        if not end_date or end_date < start_date:
            end_date = start_date
    venue_row = row.get("primary_venue")
    venue_row = venue_row if isinstance(venue_row, dict) else {}
    address_row = venue_row.get("address")
    address_row = address_row if isinstance(address_row, dict) else {}
    venue = _clean(venue_row.get("name"), VENUE_ADDRESS_MAX)
    address = _clean(address_row.get("localized_address_display"), VENUE_ADDRESS_MAX)
    room = VENUE_ADDRESS_MAX - len(venue) - (2 if venue and address else 0)
    address = address[:max(room, 0)].strip()
    zone = row.get("timezone")
    categories, category_names, fmt = _tags(row)
    return {
        "id": event_id, "name": name, "summary": _clean(row.get("summary"), SUMMARY_MAX),
        "start_date": start_date, "start_time": start_time, "end_date": end_date, "end_time": end_time,
        "timezone": zone if isinstance(zone, str) and _ZONE.fullmatch(zone) else "",
        "venue": venue, "address": address,
        "city": _clean(address_row.get("city"), PLACE_MAX), "region": _clean(address_row.get("region"), PLACE_MAX),
        "image_url": _image(row), "url": url,
        "categories": categories, "category_names": category_names, "format": fmt,
    }


def parse_page(html: str) -> tuple[list[dict], int]:
    """(events, page_count) of one listing page, or SourceError("source_changed") when it lacks the expected shape."""
    if not isinstance(html, str):
        raise SourceError("source_changed")
    found = _ASSIGN.search(html)
    if found is None:
        raise SourceError("source_changed")
    try:
        data, _ = json.JSONDecoder().raw_decode(html, found.end() - 1)
        events = data["search_data"]["events"]
        results = events["results"]
        count = events["pagination"]["page_count"]
    except (ValueError, KeyError, TypeError, RecursionError):
        raise SourceError("source_changed") from None
    if not isinstance(results, list) or not isinstance(count, int) or isinstance(count, bool):
        raise SourceError("source_changed")
    rows = results[:MAX_ROWS]
    kept = [e for e in map(_event, rows) if e is not None]
    # rows but none with the shape: the page was reshaped. A row dropped only for being cancelled or online is
    # not a shape failure.
    if rows and not kept and not all(isinstance(r, dict) and (r.get("is_cancelled") or r.get("is_online_event"))
                                     for r in rows):
        raise SourceError("source_changed")
    return kept, max(1, min(count, MAX_PAGE_COUNT))


# ---- fetching ------------------------------------------------------------------------------------------

def _sniff(data: bytes) -> bool:
    """Whether the first bytes are a PNG, JPEG, GIF or WebP."""
    return (data.startswith(bytes.fromhex("89504e470d0a1a0a")) or data.startswith(b"\xff\xd8\xff")
            or data.startswith((b"GIF87a", b"GIF89a")) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


async def _body(response: httpx.Response, limit: int) -> bytes | None:
    """The body, or None when it passes `limit` bytes (read no further than that)."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


class Eventbrite:
    def __init__(self, http: Callable[[], httpx.AsyncClient],
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._http = http
        self._sleep = sleep

    async def _page(self, url: str) -> str:
        try:
            async with self._http().stream("GET", url, headers=HEADERS, follow_redirects=False,
                                           timeout=TIMEOUT_S) as response:
                status = response.status_code
                if status in (403, 429):
                    raise SourceError("refused")
                if 300 <= status < 400:
                    raise SourceError("source_changed")  # a redirect off the listing is never followed
                if status != 200:
                    raise SourceError("provider")
                body = await _body(response, PAGE_LIMIT)
        except httpx.HTTPError:
            raise SourceError("network") from None
        if body is None:
            raise SourceError("provider")
        return body.decode("utf-8", errors="replace")

    async def read(self, place: str, max_pages: int = MAX_PAGES) -> ReadResult:
        """This month's pages, then next month's, one at a time and `GAP_S` apart, at most `PER_MONTH` of each
        and `max_pages` in all. Raises SourceError only when the first page of this month fails; a later failure
        ends the reading and is named in `error`."""
        events: list[dict] = []
        seen: set[str] = set()
        pages = 0
        for month in MONTHS:
            total = 1
            page = 1
            while page <= min(total, PER_MONTH) and pages < max_pages:
                if pages:
                    await self._sleep(GAP_S)
                try:
                    found, total = parse_page(await self._page(page_url(place, month, page)))
                except SourceError as e:
                    if not pages:
                        raise
                    return ReadResult(events, pages, e.kind)
                pages += 1
                page += 1
                for event in found:
                    if event["id"] not in seen:
                        seen.add(event["id"])
                        events.append(event)
        return ReadResult(events, pages, "")

    async def image(self, url: str) -> bytes | None:
        """A thumbnail's bytes, or None. Only https://img.evbuc.com/ is asked, and only a PNG, JPEG, WebP or GIF
        (by its first bytes) of at most `IMAGE_LIMIT` bytes is returned."""
        if not _link(url, IMAGE_HOST, "/"):
            return None
        try:
            async with self._http().stream("GET", url, headers=HEADERS, follow_redirects=False,
                                           timeout=TIMEOUT_S) as response:
                if response.status_code != 200:
                    return None
                body = await _body(response, IMAGE_LIMIT)
        except httpx.HTTPError:
            return None
        return body if body is not None and _sniff(body) else None

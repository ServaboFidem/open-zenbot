"""Map the Chrome helper's Facebook observations to DiscoverStore pool rows.

The helper watches Facebook events and birthdays and reports plain JSON. Everything it
reports is untrusted text written by strangers, so this module holds four boundaries:

- a row is kept only when it has the expected shape: a stable id, a non-empty name and
  a real date. Anything else drops with a named kind, never a guess;
- every text is stripped of control characters and capped exactly as Eventbrite's are
  (name 300, venue and address 500 together, summary 1000);
- no URL is ever built from page data: a link is kept only when the helper gave one on
  an expected Facebook host, and pictures are never kept (no Facebook image host is
  fetched by the pool picture routes, which only ask ``img.evbuc.com``);
- dropped rows are reported as ``{"index", "kind", "id"}``: ids and counts only, never
  listing text.

Pool work stays scoped by source: events use ``"facebook"``, birthdays use
``"facebook-birthday"``, so ``clear_pool``/``drop_stale``/``keep_pool`` never touch
``eventbrite`` or ``findlocal`` rows. Rows fit the pool columns, so ``upsert_pool``
takes them as mapped. There is no grocery mapping: any other helper kind drops with
``"bad_type"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

from src.events.discover_store import name_key
from src.events.eventbrite import (
    NAME_MAX,
    PLACE_MAX,
    SUMMARY_MAX,
    URL_MAX,
    VENUE_ADDRESS_MAX,
    _clean,
    _day,
    _time,
)
from src.events.discover_settings import ZONE as _ZONE

SOURCE = "facebook"
BIRTHDAY_SOURCE = "facebook-birthday"
SOURCES = (SOURCE, BIRTHDAY_SOURCE)

_FB_HOSTS = ("www.facebook.com", "facebook.com", "m.facebook.com")
_ID = re.compile(r"[0-9]{1,30}")
_EVENT_PATH = re.compile(r"/events/([0-9]{1,30})(?:[/?#]|$)")
_SLUG = re.compile(r"[A-Za-z0-9._-]{1,64}")
_MONTHDAY = re.compile(r"([0-9]{1,2})-([0-9]{1,2})")
_DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def is_facebook_source(source: object) -> bool:
    """Whether ``source`` is one this module maps (events or birthdays)."""
    return source in SOURCES


def _fb_link(value: object, prefix: str) -> str:
    """``value`` when it is an https link on a Facebook host whose path starts ``prefix``."""
    if not isinstance(value, str) or not value.startswith("https://") or len(value) > URL_MAX:
        return ""
    if any(ord(c) <= 32 or ord(c) == 127 or c == "\\" or not c.isascii() for c in value):
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.hostname not in _FB_HOSTS or parts.netloc not in _FB_HOSTS:
            return ""
        if not parts.path.startswith(prefix):
            return ""
    except ValueError:
        return ""
    return value


def _event_digits(*values: object) -> str:
    """The digits of a helper id field or of a ``/events/<digits>`` link, else ``""``."""
    for value in values:
        if isinstance(value, str) and _ID.fullmatch(value):
            return value
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 10**30:
            return str(value)
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            parts = urlsplit(value)
        except ValueError:
            continue
        if parts.hostname not in _FB_HOSTS:
            continue
        found = _EVENT_PATH.search(parts.path)
        if found:
            return found.group(1)
    return ""


def _cancelled(raw: dict) -> bool:
    return bool(raw.get("is_cancelled") or raw.get("cancelled")) or raw.get("status") == "cancelled"


def _venue_address(raw: dict) -> tuple[str, str]:
    venue = _clean(raw.get("venue") or raw.get("location") or raw.get("place"), VENUE_ADDRESS_MAX)
    address = _clean(raw.get("address") or raw.get("localized_address"), VENUE_ADDRESS_MAX)
    room = VENUE_ADDRESS_MAX - len(venue) - (2 if venue and address else 0)
    return venue, address[:max(room, 0)].strip()


def _zone(raw: dict) -> str:
    zone = raw.get("timezone")
    return zone if isinstance(zone, str) and _ZONE.fullmatch(zone) else ""


def _end(start_date: str, start_time: str, raw: dict) -> tuple[str, str]:
    end_date = _day(raw.get("end_date"))
    end_time = _time(raw.get("end_time"))
    if start_time:
        # a timed event keeps its end only when it is after the start
        if not (end_date and end_time and (end_date, end_time) > (start_date, start_time)):
            return "", ""
        return end_date, end_time
    if not end_date or end_date < start_date:
        end_date = start_date
    return end_date, ""


def map_event(raw: object) -> tuple[dict | None, str]:
    """One pool row from a helper event observation, or ``(None, kind)`` with the drop reason.

    Kinds: ``not_a_dict``, ``bad_type``, ``cancelled``, ``missing_id``, ``missing_name``,
    ``missing_date``, ``bad_date``.
    """
    if not isinstance(raw, dict):
        return None, "not_a_dict"
    if raw.get("type") not in (None, "event"):
        return None, "bad_type"
    if _cancelled(raw):
        return None, "cancelled"
    digits = _event_digits(raw.get("id"), raw.get("event_id"),
                           raw.get("url"), raw.get("link"), raw.get("facebook_url"))
    if not digits:
        return None, "missing_id"
    name = _clean(raw.get("name") if raw.get("name") is not None else raw.get("title"), NAME_MAX)
    if not name:
        return None, "missing_name"
    start = raw.get("start_date")
    start_date = _day(start)
    if not start_date:
        return None, "missing_date" if start in (None, "") else "bad_date"
    start_time = _time(raw.get("start_time"))
    end_date, end_time = _end(start_date, start_time, raw)
    venue, address = _venue_address(raw)
    url = (_fb_link(raw.get("url"), "/events/") or _fb_link(raw.get("link"), "/events/")
           or _fb_link(raw.get("facebook_url"), "/events/"))
    return {
        "id": f"fb-{digits}", "source": SOURCE, "name": name,
        "summary": _clean(raw.get("summary") if raw.get("summary") is not None
                          else raw.get("description"), SUMMARY_MAX),
        "start_date": start_date, "start_time": start_time, "end_date": end_date, "end_time": end_time,
        "timezone": _zone(raw), "venue": venue, "address": address,
        "city": _clean(raw.get("city"), PLACE_MAX), "region": _clean(raw.get("region"), PLACE_MAX),
        "image_url": "", "url": url,
        "categories": [], "category_names": [], "format": "",
    }, ""


def _monthday(raw: dict) -> tuple[int, int] | None:
    """The birthday's (month, day), from ``birthday``/``date`` (``MM-DD`` or ``YYYY-MM-DD``) or a
    ``{"month", "day"}`` mapping, or None when absent or malformed. No year is kept: a birthday
    recurs, and the pool row holds its next occurrence."""
    value = raw.get("birthday") if raw.get("birthday") is not None else raw.get("date")
    if isinstance(value, dict):
        month, day = value.get("month"), value.get("day")
    elif isinstance(value, str):
        text = value.strip()
        full = _day(text)
        if full:
            return int(full[5:7]), int(full[8:10])
        found = _MONTHDAY.fullmatch(text)
        if not found:
            return None
        month, day = int(found.group(1)), int(found.group(2))
    else:
        return None
    if not (isinstance(month, int) and isinstance(day, int)) or isinstance(month, bool):
        return None
    if not 1 <= month <= 12 or not 1 <= day <= _DAYS_IN_MONTH[month - 1]:
        return None
    return month, day


def _next_occurrence(month: int, day: int, today: str) -> str:
    """The next ``MM-DD`` on or after ``today`` as a day key (Feb 29 lands on Feb 28 off leap years)."""
    year = int(today[0:4])
    for candidate in range(year, year + 9):
        try:
            at = date(candidate, month, day)
        except ValueError:
            at = date(candidate, 2, 28)  # Feb 29 in a common year
        if at.isoformat() >= today:
            return at.isoformat()
    raise AssertionError("unreachable")  # a leap day always lands within nine years


def _person_key(raw: dict, name: str, month: int, day: int) -> str:
    """The birthday row's stable id: helper person digits first, then a profile slug, then name+day."""
    digits = _event_digits(raw.get("id"), raw.get("person_id"), raw.get("profile_id"))
    if digits:
        return f"fb-bday-{digits}"
    for value in (raw.get("profile_url"), raw.get("url")):
        if not isinstance(value, str):
            continue
        try:
            parts = urlsplit(value)
        except ValueError:
            continue
        if parts.hostname not in _FB_HOSTS:
            continue
        if parts.path == "/profile.php":
            query = dict(part.split("=", 1) for part in parts.query.split("&") if "=" in part)
            if _ID.fullmatch(query.get("id", "")):
                return f"fb-bday-{query['id']}"
            continue
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) == 1 and _SLUG.fullmatch(segments[0]) and segments[0].lower() != "events":
            return f"fb-bday-u-{segments[0].lower()}"
    return f"fb-bday-{name_key(name)}-{month:02d}{day:02d}"


def map_birthday(raw: object, *, today: str) -> tuple[dict | None, str]:
    """One pool row from a helper birthday observation, or ``(None, kind)`` with the drop reason.

    ``today`` is the day key the next occurrence is counted from. Kinds: ``not_a_dict``,
    ``bad_type``, ``missing_name``, ``missing_birthday``, ``bad_birthday``, ``bad_today``.
    """
    if not isinstance(raw, dict):
        return None, "not_a_dict"
    if raw.get("type") not in (None, "birthday"):
        return None, "bad_type"
    name = _clean(raw.get("name"), NAME_MAX)
    if not name:
        return None, "missing_name"
    monthday = _monthday(raw)
    if monthday is None:
        value = raw.get("birthday") if raw.get("birthday") is not None else raw.get("date")
        return None, "missing_birthday" if value in (None, "") else "bad_birthday"
    if not _day(today):
        return None, "bad_today"
    month, day = monthday
    start_date = _next_occurrence(month, day, today)
    url = _fb_link(raw.get("profile_url"), "/") or _fb_link(raw.get("url"), "/")
    return {
        "id": _person_key(raw, name, month, day), "source": BIRTHDAY_SOURCE, "name": name,
        "summary": _clean(raw.get("summary") if raw.get("summary") is not None
                          else raw.get("note"), SUMMARY_MAX),
        "start_date": start_date, "start_time": "", "end_date": start_date, "end_time": "",
        "timezone": "", "venue": "", "address": "",
        "city": _clean(raw.get("city"), PLACE_MAX), "region": _clean(raw.get("region"), PLACE_MAX),
        "image_url": "", "url": url,
        "categories": [], "category_names": [], "format": "birthday",
    }, ""


def map_raw(raw: object, *, today: str = "") -> tuple[dict | None, str]:
    """Dispatch one helper observation by its ``type``: ``birthday`` rows go to ``map_birthday``,
    missing or ``event`` types to ``map_event``; anything else (groceries included) drops with
    ``bad_type``."""
    kind = raw.get("type") if isinstance(raw, dict) else None
    if kind == "birthday":
        return map_birthday(raw, today=today)
    if kind in (None, "event"):
        return map_event(raw)
    return None, "bad_type"


def row_key(row: dict) -> tuple[str, str]:
    """A row's dedupe key: its day and its name folded to letters and digits."""
    return (row.get("start_date", ""), name_key(row.get("name", "")))


def keys_of(rows: list[dict]) -> tuple[set[str], set[tuple[str, str]]]:
    """The ids and dedupe keys of pool-shaped rows (built from a listing), for ``dedupe``."""
    ids = {r["id"] for r in rows if isinstance(r.get("id"), str) and r["id"]}
    keys = {row_key(r) for r in rows if row_key(r)[0] and row_key(r)[1]}
    return ids, keys


def dedupe(rows: list[dict], *, existing_ids: set[str] | frozenset[str] = frozenset(),
           existing_keys: set[tuple[str, str]] | frozenset[tuple[str, str]] = frozenset()
           ) -> tuple[list[dict], list[dict]]:
    """Split mapped rows into ``(kept, dropped)``: a row drops as ``duplicate`` when its id or its
    day+name key was already seen in this batch or is in ``existing_*`` (built from the pool
    listing, so dedupe stays scoped to these sources). Dropped entries carry ``id`` and
    ``reason`` only, never listing text."""
    kept: list[dict] = []
    dropped: list[dict] = []
    seen_ids = set(existing_ids)
    seen_keys = set(existing_keys)
    for row in rows:
        key = row_key(row)
        if row["id"] in seen_ids or (key[0] and key[1] and key in seen_keys):
            dropped.append({"id": row["id"], "reason": "duplicate"})
            continue
        seen_ids.add(row["id"])
        if key[0] and key[1]:
            seen_keys.add(key)
        kept.append(row)
    return kept, dropped


@dataclass
class BatchResult:
    """What ``map_batch`` kept and why the rest dropped (``kind`` per row, never its text)."""

    kept: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)  # {"index", "kind", "id"}


def map_batch(raws: list, *, today: str = "", existing_ids: set[str] | frozenset[str] = frozenset(),
              existing_keys: set[tuple[str, str]] | frozenset[tuple[str, str]] = frozenset()
              ) -> BatchResult:
    """Map helper observations in order, dropping bad rows and duplicates (within the batch and
    against ``existing_*``). ``today`` counts birthday occurrences; without one, birthday rows
    drop with ``bad_today``."""
    if not isinstance(raws, list):
        raise ValueError("not a batch")
    out = BatchResult()
    seen_ids = set(existing_ids)
    seen_keys = set(existing_keys)
    for i, raw in enumerate(raws):
        row, kind = map_raw(raw, today=today)
        if row is None:
            out.dropped.append({"index": i, "kind": kind, "id": ""})
            continue
        key = row_key(row)
        if row["id"] in seen_ids or (key[0] and key[1] and key in seen_keys):
            out.dropped.append({"index": i, "kind": "duplicate", "id": row["id"]})
            continue
        seen_ids.add(row["id"])
        if key[0] and key[1]:
            seen_keys.add(key)
        out.kept.append(row)
    return out

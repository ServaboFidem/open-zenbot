"""The Facebook helper intake routes: validated reports become pool rows.

The Chrome helper watches three named corners (the events dashboard, the
invites page, the birthdays page) and posts cleaned reports to
``POST /intake/facebook``. Every report is validated here against the helper
payload schema (``src/helper/schema.js``: exact helper id, exact corners,
exact per-kind keys, trimmed text within caps, real dates), then each item is
translated to a :mod:`src.events.facebook_pool` raw and mapped to a pool row.
Only mapped rows reach the store, through :meth:`FacebookIntake.intake`:
nothing writes direct to the store, and the pool rows are the handoff the
split and birthday slices read.

- ``POST /intake/facebook`` takes the report as a JSON object. A bad shape
  is refused with a reason (422); a good report with unmappable rows still
  answers 200, with those rows listed as dropped (``{"index", "kind",
  "id"}``: ids and counts only, never listing text).
- ``GET /intake/facebook/status`` answers the last read, the last error and
  whether the intake is ready, plus the pool sizes per Facebook source.
- A report may carry ``timezone``, the browser's own zone (an IANA name by
  shape). The helper reads no zone from a page, so every timed Facebook
  event is taken to be in the machine's zone: the report's zone goes on its
  rows and on every Facebook event row already in the pool
  (``DiscoverStore.set_zone``, scoped to source ``facebook``). A report
  without one changes no zone, and a timed row with none cannot be added.
- An invite's ``inviter`` is kept beside its pool row
  (``DiscoverStore.fb_set_inviter``), never in the pool, a route answer, a
  ledger entry or a log line: it is what the friend check reads.
- ``GET /intake/facebook/lists`` answers what the Facebook tab shows, read
  from the pool and written nowhere: ``invites`` (Facebook event rows from
  today on that are not held for approval), ``approvals`` (the rows
  :func:`src.events.facebook_cards.split` holds, today those a close friend
  invited the user to, each with a fixed ``why`` sentence, never a person's
  name), ``birthdays`` (birthday rows at most
  :data:`src.events.birthdays.NUDGE_DAYS_OUT` days out, with ``daysUntil``)
  and ``status``. Rows carry the listing fields the grid's routes answer
  (``ROW``) plus ``state``; never a disk path.
- ``POST /intake/facebook/refresh`` (``NothingIn``) prunes pool rows whose
  start date has passed and answers the status: the on-demand refresh beside
  the helper's own rhythm (about four sweeps through the day).
- ``POST /intake/facebook/events/{id}/dismiss`` (``NothingIn``) hides one
  Facebook event row from the tab's lists (``DiscoverStore.fb_dismiss``). It
  changes no pool row, card or calendar event. A Facebook row has no card,
  so the card dismiss route cannot do this; approving one is the existing
  pool add route (``POST /discover/events/{id}/add``).

Every POST takes a required JSON body (``NothingIn`` where it needs
nothing), so a cross-site page cannot send one without a preflight. Ledger
entries (kind ``calendar``, event ``facebook_intake``) carry counts and an
error kind only. Log lines carry an exception's class name only.

Residual risk, shared with the rest of the harness: the harness API has no
authentication, so any local process (a bot with ``shell`` or ``browser``
included) can post a report here or read the status. Do not describe these
watches as out of bots' reach.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.events import facebook_pool as pool
from src.events.birthdays import NUDGE_DAYS_OUT
from src.events.discover_settings import ZONE
from src.events.eventbrite import _clean
from src.events.facebook_cards import split
from src.events.routes import NothingIn
from src.memory.ledger import Kind

log = logging.getLogger(__name__)

HELPER_ID = "fb-events-v1"
MAX_ITEMS = 200
TITLE_MAX = 300
NAME_MAX = 200
LABEL_MAX = 64
URL_MAX = 1000
HOST = "www.facebook.com"

FB_LAST_READ = "facebook_last_read"
FB_LAST_ERROR = "facebook_last_error"

REPORT_REFUSED = "The helper report was refused."
NOT_SAVED = "The report could not be saved."
NOT_READ = "The Facebook lists could not be read."
NO_EVENT = "No such event."

# what a list answers of a pool row (the grid's slim fields plus what an add needs), beside ``state``
ROW = ("id", "name", "start_date", "start_time", "end_date", "end_time", "timezone", "venue", "city",
       "region", "url", "source")
# why a row waits for approval, by the split's reason: fixed sentences, never listing text or a name
WHY = {"rsvp": "You marked this event Interested or Going on Facebook.",
       "personal": "A close friend is part of this event.",
       "rsvp_personal": "You marked this event Interested or Going, and a close friend is part of it."}

CORNERS = (
    {"id": "events_dashboard", "paths": ("/events", "/events/")},
    {"id": "invites", "paths": ("/events/invites", "/events/invites/",
                                "/events/invitation_box", "/events/invitation_box/")},
    {"id": "birthdays", "paths": ("/birthdays", "/birthdays/")},
)
CORNER_IDS = tuple(c["id"] for c in CORNERS)
_KINDS = {"events_dashboard": ("event",), "invites": ("invite",), "birthdays": ("birthday",)}

_EVENT_KEYS = frozenset({"kind", "id", "title", "start_date", "start_time", "url", "venue"})
_INVITE_KEYS = frozenset({"kind", "id", "title", "start_date", "start_time", "url", "inviter"})
_BIRTHDAY_KEYS = frozenset({"kind", "name", "profile_url", "date_text"})
_TOP_KEYS = frozenset({"helper", "observed_at", "corner", "url", "items", "timezone"})
_DIGITS = re.compile(r"[0-9]{1,30}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")

_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
           "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
_ABBR = {name[:3]: n for name, n in _MONTHS.items()}
_MONTH_FIRST = re.compile(r"^([A-Za-z]+)\s+(\d{1,2})(?:\s*,?\s*\d{2,4})?$")
_DAY_FIRST = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)(?:\s*,?\s*\d{2,4})?$")


class ReportRefused(Exception):
    """A helper report whose shape is not the payload schema. The message
    names the shape only, never any listing text."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ReportIn(BaseModel):
    """The intake body stays loose on purpose: the schema check below owns
    the shape and answers each refusal with its own reason."""

    model_config = {"extra": "allow"}

    helper: Any = None
    observed_at: Any = None
    corner: Any = None
    url: Any = None
    items: Any = None


def corner_for_url(value: object) -> str:
    """The named corner ``value`` is a page of, or ``""`` when it is not one."""
    if not isinstance(value, str):
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if (parts.scheme != "https" or parts.hostname != HOST or parts.username
            or parts.password or parts.port):
        return ""
    for corner in CORNERS:
        if parts.path in corner["paths"]:
            return corner["id"]
    return ""


def _is_facebook_link(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= URL_MAX:
        return False
    if value != value.strip() or not value.startswith(f"https://{HOST}/"):
        return False
    if any(ord(c) <= 32 or ord(c) == 127 or c == "\\" or not c.isascii() for c in value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (parts.scheme == "https" and parts.hostname == HOST
            and not parts.username and not parts.password and not parts.port)


def _is_real_date(value: object) -> bool:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _checked_text(value: object, name: str, limit: int, *, allow_empty: bool) -> None:
    """Raise ``ReportRefused`` unless ``value`` is trimmed text within ``limit``."""
    if not isinstance(value, str):
        raise ReportRefused(f"{name} is not a string")
    if not allow_empty and not value:
        raise ReportRefused(f"{name} is empty")
    if len(value) > limit:
        raise ReportRefused(f"{name} is too long")
    if value != value.strip():
        raise ReportRefused(f"{name} is not trimmed")


def _check_event(item: dict) -> None:
    for key in item:
        if key not in _EVENT_KEYS:
            raise ReportRefused("unknown event key")
    if not isinstance(item.get("id"), str) or not _DIGITS.fullmatch(item["id"]):
        raise ReportRefused("event id is not digits")
    _checked_text(item.get("title"), "event title", TITLE_MAX, allow_empty=False)
    if not _is_real_date(item.get("start_date")):
        raise ReportRefused("event start_date is not a date")
    start_time = item.get("start_time")
    if not isinstance(start_time, str) or (start_time != "" and not _CLOCK.fullmatch(start_time)):
        raise ReportRefused("event start_time is not a clock time")
    if not _is_facebook_link(item.get("url")):
        raise ReportRefused("event url is not a facebook link")
    _checked_text(item.get("venue"), "event venue", TITLE_MAX, allow_empty=True)


def _check_invite(item: dict) -> None:
    for key in item:
        if key not in _INVITE_KEYS:
            raise ReportRefused("unknown invite key")
    if not isinstance(item.get("id"), str) or not _DIGITS.fullmatch(item["id"]):
        raise ReportRefused("invite id is not digits")
    _checked_text(item.get("title"), "invite title", TITLE_MAX, allow_empty=False)
    if not _is_real_date(item.get("start_date")):
        raise ReportRefused("invite start_date is not a date")
    start_time = item.get("start_time")
    if not isinstance(start_time, str) or (start_time != "" and not _CLOCK.fullmatch(start_time)):
        raise ReportRefused("invite start_time is not a clock time")
    if not _is_facebook_link(item.get("url")):
        raise ReportRefused("invite url is not a facebook link")
    _checked_text(item.get("inviter"), "invite inviter", NAME_MAX, allow_empty=True)


def _check_birthday(item: dict) -> None:
    for key in item:
        if key not in _BIRTHDAY_KEYS:
            raise ReportRefused("unknown birthday key")
    _checked_text(item.get("name"), "birthday name", NAME_MAX, allow_empty=False)
    profile_url = item.get("profile_url")
    if not isinstance(profile_url, str) or (profile_url != "" and not _is_facebook_link(profile_url)):
        raise ReportRefused("birthday link is not a facebook link")
    _checked_text(item.get("date_text"), "birthday date_text", LABEL_MAX, allow_empty=False)


_CHECKERS = {"events_dashboard": _check_event, "invites": _check_invite, "birthdays": _check_birthday}


def validate_report(report: object) -> dict:
    """The report when it matches the helper payload schema, else
    ``ReportRefused`` naming the shape only (never any listing text)."""
    if not isinstance(report, dict):
        raise ReportRefused("not an object")
    for key in report:
        if key not in _TOP_KEYS:
            raise ReportRefused("unknown key")
    if report.get("helper") != HELPER_ID:
        raise ReportRefused("unknown helper")
    observed_at = report.get("observed_at")
    if not isinstance(observed_at, str):
        raise ReportRefused("observed_at is not a string")
    try:
        datetime.fromisoformat(observed_at)
    except ValueError:
        raise ReportRefused("observed_at is not a time") from None
    zone = report.get("timezone")
    if zone is not None and not (isinstance(zone, str) and ZONE.fullmatch(zone)):
        raise ReportRefused("timezone is not a zone name")
    corner = report.get("corner")
    if corner not in CORNER_IDS:
        raise ReportRefused("unknown corner")
    url = report.get("url")
    if not isinstance(url, str):
        raise ReportRefused("url is not a string")
    if corner_for_url(url) != corner:
        raise ReportRefused("url is not that corner")
    items = report.get("items")
    if not isinstance(items, list):
        raise ReportRefused("items is not a list")
    if len(items) > MAX_ITEMS:
        raise ReportRefused("too many items")
    check = _CHECKERS[corner]
    for item in items:
        if not isinstance(item, dict):
            raise ReportRefused("item is not an object")
        if item.get("kind") not in _KINDS[corner]:
            raise ReportRefused(f"wrong kind for {corner}")
        check(item)
    return report


def parse_date_text(label: object) -> tuple[int, int] | None:
    """A ``(month, day)`` from a birthday label like ``"November 21"`` or
    ``"21 Nov"`` (an optional year is ignored), or None when it has no
    usable month and day. Month and day validity against the calendar is
    left to the pool mapper, which drops what it cannot use."""
    if not isinstance(label, str):
        return None
    text = label.strip()
    month_first = _MONTH_FIRST.match(text)
    if month_first is not None:
        word, day = month_first.group(1), int(month_first.group(2))
    else:
        day_first = _DAY_FIRST.match(text)
        if day_first is None:
            return None
        day, word = int(day_first.group(1)), day_first.group(2)
    key = word.casefold()
    month = _MONTHS.get(key, _ABBR.get(key[:3], 0))
    if not month or not 1 <= day <= 31:
        return None
    return month, day


def to_raw(item: dict) -> dict:
    """A helper item as the pool mapper's raw: events and invites map as
    event rows (the inviter travels along, the mapper keeps the pool
    fields); birthdays map with their next occurrence counted from the
    label's month and day, or keep the label when it has none so the
    mapper drops the row with its kind instead of a guess."""
    kind = item.get("kind")
    if kind in ("event", "invite"):
        raw: dict[str, Any] = {"type": "event", "id": item["id"], "name": item["title"],
                               "title": item["title"], "start_date": item["start_date"],
                               "start_time": item["start_time"], "url": item["url"],
                               "link": item["url"], "facebook_url": item["url"],
                               "venue": item["venue"] if kind == "event" else "",
                               "location": item["venue"] if kind == "event" else ""}
        if kind == "invite":
            raw["inviter"] = item["inviter"]
        return raw
    monthday = parse_date_text(item.get("date_text"))
    stamp = f"{monthday[0]:02d}-{monthday[1]:02d}" if monthday else item["date_text"]
    return {"type": "birthday", "name": item["name"], "birthday": stamp, "date": stamp,
            "profile_url": item["profile_url"], "url": item["profile_url"]}


class FacebookIntake:
    """The single writer of Facebook helper reports: validation guards
    every write, and the pool rows it keeps are the handoff the split and
    birthday slices read."""

    def __init__(self, store, ledger, *, today=None, now=None):
        self.store = store
        self.ledger = ledger
        self._today = today or (lambda: date.today().isoformat())
        self._now = now or (lambda: datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%SZ"))

    def _pool_counts(self) -> dict[str, int]:
        return {source: self.store.pool_size(source) for source in pool.SOURCES}

    def _existing(self) -> tuple[set[str], set[tuple[str, str]]]:
        """The ids and day+name keys already in the pool for these sources,
        so the batch dedupes against them and never touches other sources."""
        ids: set[str] = set()
        keys: set[tuple[str, str]] = set()
        for source in pool.SOURCES:
            for event_id in self.store.pool_ids(source):
                ids.add(event_id)
                row = self.store.pool_row(event_id)
                if row is not None:
                    key = pool.row_key(row)
                    if key[0] and key[1]:
                        keys.add(key)
        return ids, keys

    def intake(self, report: object) -> dict[str, Any]:
        """Validate ``report``, map its items through the pool mapper and
        keep the good rows. Raises ``ReportRefused`` for a bad shape (the
        error meta then names it); a good report with unmappable rows
        still keeps the rest, with each drop listed by kind and id."""
        body = validate_report(report)
        today = self._today()
        existing_ids, existing_keys = self._existing()
        zone = body.get("timezone") or ""
        raws = [to_raw(item) for item in body["items"]]
        if zone:  # the machine's zone, for the timed rows: the helper reads none from a page
            for raw in raws:
                if raw["type"] == "event":
                    raw["timezone"] = zone
        batch = pool.map_batch(raws, today=today, existing_ids=existing_ids,
                               existing_keys=existing_keys)
        try:
            kept = self.store.upsert_pool(batch.kept, self._now())
            if zone:  # rows read before, which a repeat report never rewrites, take it too
                self.store.set_zone(pool.SOURCE, zone)
            self._keep_inviters(body["items"])
        except Exception as e:
            log.warning("facebook intake not saved: %s", type(e).__name__)
            self.store.set_meta(FB_LAST_ERROR, "provider")
            raise
        self.store.set_meta(FB_LAST_READ, self._now())
        self.store.set_meta(FB_LAST_ERROR, "")
        self.ledger.append(Kind.calendar, "", "", event="facebook_intake",
                           kept=kept, dropped=len(batch.dropped), error="")
        return {"kept": kept, "dropped": batch.dropped, "pool": self._pool_counts()}

    def _keep_inviters(self, items: list[dict]) -> None:
        """Keep each invite's inviter beside its pool row (a repeat report's too: the row dropped as a
        duplicate is still that event). Only for a Facebook event row that is in the pool."""
        ids = self.store.pool_ids(pool.SOURCE)
        for item in items:
            event_id = f"fb-{item.get('id')}"
            if item.get("kind") == "invite" and event_id in ids:
                self.store.fb_set_inviter(event_id, _clean(item.get("inviter"), NAME_MAX))

    def dismiss(self, event_id: str) -> bool:
        """Hide one Facebook event row from the tab's lists; False when it is not one."""
        if event_id not in self.store.pool_ids(pool.SOURCE):
            return False
        self.store.fb_dismiss(event_id)
        return True

    def status(self) -> dict[str, Any]:
        """The intake status: the last read, the last error and readiness."""
        return {"last_read": self.store.meta(FB_LAST_READ),
                "error": self.store.meta(FB_LAST_ERROR),
                "ready": True,
                "pool": self._pool_counts()}

    def refresh(self) -> dict[str, Any]:
        """The on-demand refresh beside the helper's own rhythm: prune pool
        rows whose start date has passed (birthdays re-arrive with their
        next occurrence) and answer the status."""
        self.store.drop_past(self._today())
        self.store.fb_prune()
        return self.status()


def facebook_router(discovery, harness=None) -> APIRouter:
    """The ``/intake`` routes over ``discovery``'s store and ledger. The
    intake never needs more than the store, the ledger and the clocks; the
    lists read the harness's people registry (``harness.profile.people``,
    read-only) for the friend check, and without one nothing is personal."""
    r = APIRouter(prefix="/intake")
    intake = FacebookIntake(discovery.store, discovery.ledger,
                            today=getattr(discovery, "today", None),
                            now=getattr(discovery, "now", None))
    store = discovery.store

    def live(calendar_event_id: str) -> bool:
        """Whether a calendar event made from a pool row still exists, not deleted."""
        calendar = getattr(discovery, "calendar", None)
        if calendar is None or not calendar_event_id:
            return False
        event = calendar.store.event(calendar_event_id)
        return event is not None and not event["deleted"]

    def rows_of(source: str, today: str) -> list[dict]:
        """One Facebook source's pool rows that have not passed, in date order. Read by source, so no
        other source's row is ever listed here."""
        rows = [row for row in map(store.pool_row, store.pool_ids(source))
                if row is not None and (row["end_date"] or row["start_date"]) >= today]
        return sorted(rows, key=lambda r: (r["start_date"], r["start_time"], r["id"]))

    def view(row: dict) -> dict:
        """A row as the tab draws it: the ``/discover/events`` state rule over the same marks."""
        marks = store.marks(row["id"])
        state = ("added" if any(map(live, marks["calendar_ids"]))
                 else "recommended" if marks["recommended"] else "")
        return {**{k: row[k] for k in ROW}, "state": state}

    def days_until(day: str, today: str) -> int | None:
        try:
            return (date.fromisoformat(day) - date.fromisoformat(today)).days
        except ValueError:
            return None

    def lists() -> dict[str, Any]:
        today = intake._today()
        people = getattr(getattr(harness, "profile", None), "people", None)
        marks = store.fb_marks()
        # the inviter rides along for the friend check only: ``view`` answers the ``ROW`` fields, never it
        shown = [{**row, "inviter": marks.get(row["id"], {}).get("inviter", "")}
                 for row in rows_of(pool.SOURCE, today)
                 if not marks.get(row["id"], {}).get("dismissed")]
        held = split(shown, people)
        birthdays = []
        for row in rows_of(pool.BIRTHDAY_SOURCE, today):
            days = days_until(row["start_date"], today)
            if days is not None and 0 <= days <= NUDGE_DAYS_OUT:
                birthdays.append({"id": row["id"], "name": row["name"], "date": row["start_date"],
                                  "start_date": row["start_date"], "daysUntil": days})
        return {"invites": [view(card["row"]) for card in held.feed],
                "approvals": [{**view(card["row"]), "why": WHY[card["reason"]]} for card in held.approval],
                "birthdays": birthdays,
                "status": intake.status()}

    @r.post("/facebook")
    async def report(body: ReportIn) -> dict[str, Any]:
        try:
            return intake.intake(body.model_dump())
        except ReportRefused as e:
            intake.store.set_meta(FB_LAST_ERROR, e.reason)
            raise HTTPException(422, REPORT_REFUSED + f" {e.reason}") from None
        except Exception as e:
            log.warning("facebook intake failed: %s", type(e).__name__)
            raise HTTPException(500, NOT_SAVED) from None

    @r.get("/facebook/status")
    async def get_status() -> dict[str, Any]:
        return intake.status()

    @r.get("/facebook/lists")
    async def get_lists() -> dict[str, Any]:
        """What the Facebook tab shows. Reads the pool only: no source is read and nothing is written."""
        try:
            return lists()
        except Exception as e:
            log.warning("facebook lists failed: %s", type(e).__name__)
            raise HTTPException(500, NOT_READ) from None

    @r.post("/facebook/events/{event_id}/dismiss")
    async def dismiss(event_id: str, body: NothingIn) -> dict[str, Any]:
        try:
            done = intake.dismiss(event_id)
        except Exception as e:
            log.warning("facebook dismiss failed: %s", type(e).__name__)
            raise HTTPException(500, NOT_SAVED) from None
        if not done:
            raise HTTPException(404, NO_EVENT)
        return {"id": event_id, "state": "dismissed"}

    @r.post("/facebook/refresh")
    async def refresh(body: NothingIn) -> dict[str, Any]:
        try:
            return intake.refresh()
        except Exception as e:
            log.warning("facebook refresh failed: %s", type(e).__name__)
            raise HTTPException(500, NOT_SAVED) from None

    return r

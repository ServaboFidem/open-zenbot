"""Event discovery: refresh the pool from Eventbrite and FindLocal, check every digest card, fetch thumbnails.

The gate (``Discovery.save``) is the single authority on what reaches a digest. A bot supplies an event id and
a reason for each card; every other fact comes from the pool. A digest saves whole or not at all. Ledger
entries (kind ``calendar``, ``event`` ``discover_refresh`` or ``discover_digest``) carry counts, a source name and
error kinds only, never listing text, a note, a key or a path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.events.discover_settings import DiscoverKeys, DiscoverSettings, check_key
from src.events.discover_store import LAST_ERROR, LAST_REFRESH, DiscoverStore
from src.events.eventbrite import Eventbrite, SourceError
from src.events.findlocal import FindLocal
from src.events.settings import calendar_dir
from src.harness.loop import SessionState
from src.harness.scheduler import Routine, Schedule
from src.memory.ledger import Kind

log = logging.getLogger(__name__)

REFRESH_AFTER_S = 12 * 3600
RETRY_AFTER_S = 10 * 60  # a failed refresh is not tried again this soon, unless forced
LAST_FAILURE = "last_failure"  # meta: when the last refresh failed; cleared by a good one
FL_LAST_REFRESH = "findlocal_last_refresh"  # FindLocal's own three, as Eventbrite's
FL_LAST_ERROR = "findlocal_last_error"
FL_LAST_FAILURE = "findlocal_last_failure"
FINDLOCAL_AFTER_S = 20 * 3600  # the weekly run reads FindLocal again once its last read is this old
FINDLOCAL_LIFE_S = 24 * 3600  # a FindLocal row not seen again for this long is no candidate, then dropped
BOT_ID = "event-scout"
ROUTINE_SKILL = "events-digest"
ROUTINE_NAME = "Weekly events digest"
MANAGED_BY = "events"
MAX_CARDS = 8
MAX_WHY = 300
MAX_NOTE = 500
KINDS = ("weekly", "on_demand")
INSTANT = "%Y-%m-%dT%H:%M:%SZ"
CARD_ID = re.compile(r"[0-9a-f]{32}")


class DigestRefused(Exception):
    """A digest the gate refused. ``failures`` is a list of ``{"id", "reasons"}``; reasons are
    ``unknown_event``, ``not_a_candidate``, ``duplicate_in_batch`` and ``bad_why``."""

    def __init__(self, failures: list[dict]):
        super().__init__("The digest was refused.")
        self.failures = failures


def _local_today() -> str:
    return date.today().isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime(INSTANT)


def _plain(text: str) -> bool:
    """No control character at all (newline, tab, DEL, U+2028 and U+2029 included)."""
    return not any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in text)


class Discovery:
    def __init__(self, config, ledger, calendar, *, today: Callable[[], str] = _local_today,
                 now: Callable[[], str] = _utc_now):
        self.config = config
        self.ledger = ledger
        self.calendar = calendar
        self.today = today
        self.now = now
        self.vault = Path(config.vault_root)
        folder = calendar_dir(self.vault)
        self.images_dir = folder / "discover-images"
        self.settings = DiscoverSettings.load(self.vault)
        self.store = DiscoverStore(folder / "discover.db")
        self.source = Eventbrite(calendar.http)
        self.findlocal = FindLocal(calendar.http)
        self.keys = DiscoverKeys.load(self.vault)
        self._lock = asyncio.Lock()
        # attached by the harness, as MusicService's are: the tool runner and catalog the scout's tools go into, the
        # routine store and the scheduler that holds each routine's last session
        self.tools: Any = None
        self.catalog: Any = None
        self.routines: Any = None
        self.scheduler: Any = None
        self.bot_lookup: Callable[[str], Any] | None = None  # bot id -> the bot, set by the harness; none means refuse
        self.on_demand_sessions: set[str] = set()  # sessions the discover route started: they save on_demand only

    @property
    def active(self) -> bool:
        return bool(self.settings.enabled and self.settings.place)

    @property
    def findlocal_ready(self) -> bool:
        return bool(self.active and self.settings.findlocal_city and self.settings.timezone and self.keys.findlocal)

    def scout_is_closed(self) -> bool:
        """The event scout exists and is a closed bot (`closed: true`). A bot file of the same id without it, or no
        way to look the bot up, is not: the events tools are then offered to nobody and refused."""
        bot = self.bot_lookup(BOT_ID) if self.bot_lookup is not None else None
        return bot is not None and getattr(bot, "closed", False) is True

    # refresh ----------------------------------------------------------------

    def _age(self, key: str) -> float | None:
        """Seconds since the instant in meta ``key``, by the ``now`` clock; None when it is empty or unreadable."""
        try:
            then = datetime.strptime(self.store.meta(key), INSTANT)
            current = datetime.strptime(self.now(), INSTANT)
        except ValueError:
            return None
        return (current - then).total_seconds()

    def _fresh(self) -> bool:
        """The last good refresh is under REFRESH_AFTER_S old, by the ``now`` clock (stored, so it holds across
        a restart)."""
        age = self._age(LAST_REFRESH)
        return age is not None and 0 <= age < REFRESH_AFTER_S

    def _recently_failed(self, key: str = LAST_FAILURE) -> bool:
        """The last refresh (of the source whose failure meta is ``key``) failed under RETRY_AFTER_S ago."""
        age = self._age(key)
        return age is not None and 0 <= age < RETRY_AFTER_S

    def _before(self, seconds: int) -> str:
        """The instant ``seconds`` before now, by the ``now`` clock."""
        return (datetime.strptime(self.now(), INSTANT) - timedelta(seconds=seconds)).strftime(INSTANT)

    async def refresh(self, force: bool = False, weekly: bool = False) -> dict:
        """Read both sources into the pool, one refresh at a time. Answers ``{"pages", "events", "error"}`` for
        Eventbrite and ``"findlocal": {"calls", "events", "error"}``.

        Eventbrite is skipped while its last refresh is under 12 hours old, unless forced; a refresh that failed is
        not tried again for 10 minutes (an unforced call then answers the recorded error kind with no request).
        FindLocal is read when it is set up and has never been read, or on the weekly run once its last read is 20
        hours old; never sooner than 10 minutes after a failure, and ``force`` does not apply to it."""
        none = {"pages": 0, "events": 0, "error": ""}
        fl_none = {"calls": 0, "events": 0, "error": ""}
        if not self.active:
            return {**none, "findlocal": fl_none}
        async with self._lock:
            out = await self._refresh_eventbrite(force)
            self.store.drop_stale("findlocal", self._before(FINDLOCAL_LIFE_S))
            return {**out, "findlocal": await self._refresh_findlocal(weekly)}

    async def _refresh_eventbrite(self, force: bool) -> dict:
        none = {"pages": 0, "events": 0, "error": ""}
        if not force:
            if self._recently_failed():
                return {**none, "error": self.store.meta(LAST_ERROR)}
            if self._fresh():
                return none
        try:
            result = await self.source.read(self.settings.place)
        except SourceError as e:
            self.store.set_meta(LAST_ERROR, e.kind)
            self.store.set_meta(LAST_FAILURE, self.now())
            out = {"pages": 0, "events": 0, "error": e.kind}
        else:
            self.store.upsert_pool(result.events, self.now())
            self.store.drop_past(self.today())
            self.store.set_meta(LAST_REFRESH, self.now())
            self.store.set_meta(LAST_ERROR, result.error)
            self.store.set_meta(LAST_FAILURE, "")
            out = {"pages": result.pages, "events": len(result.events), "error": result.error}
        self.ledger.append(Kind.calendar, "", "", event="discover_refresh", source="eventbrite", **out)
        return out

    async def _refresh_findlocal(self, weekly: bool) -> dict:
        none = {"calls": 0, "events": 0, "error": ""}
        if not self.findlocal_ready:
            return none
        if self._recently_failed(FL_LAST_FAILURE):
            return {**none, "error": self.store.meta(FL_LAST_ERROR)}
        age = self._age(FL_LAST_REFRESH)
        if self.store.meta(FL_LAST_REFRESH) and not (weekly and (age is None or age > FINDLOCAL_AFTER_S)):
            return none
        s = self.settings
        try:
            result = await self.findlocal.read(s.findlocal_city, self.keys.findlocal, s.timezone, self.today())
        except (SourceError, ValueError) as e:  # ValueError: a city not in the list, which settings never hold
            kind = e.kind if isinstance(e, SourceError) else "provider"
            self.store.set_meta(FL_LAST_ERROR, kind)
            self.store.set_meta(FL_LAST_FAILURE, self.now())
            out = {"calls": 0, "events": 0, "error": kind}
        else:
            fresh = [e for e in result.events if not self.store.known_duplicate(e)]
            self.store.upsert_pool(fresh, self.now())
            self.store.set_meta(FL_LAST_REFRESH, self.now())
            self.store.set_meta(FL_LAST_ERROR, result.error)
            self.store.set_meta(FL_LAST_FAILURE, "")
            out = {"calls": result.pages, "events": len(fresh), "error": result.error}
        self.ledger.append(Kind.calendar, "", "", event="discover_refresh", source="findlocal",
                           pages=out["calls"], events=out["events"], error=out["error"])
        return out

    async def set_key(self, value: str | None) -> None:
        """Keep a new FindLocal key (checked; None forgets it) and clear FindLocal's last error and failure, so the
        next refresh may read at once."""
        self.keys = DiscoverKeys(findlocal=check_key(value) if value is not None else "")
        self.keys.save(self.vault)
        async with self._lock:
            self.store.set_meta(FL_LAST_ERROR, "")
            self.store.set_meta(FL_LAST_FAILURE, "")

    # the pool ---------------------------------------------------------------

    def _fresh_after(self) -> str:
        """FindLocal rows last seen before this instant are no candidates."""
        return self._before(FINDLOCAL_LIFE_S)

    def candidates(self, **filters) -> list[dict]:
        return self.store.candidates(self.today(), self.settings.categories, fresh_after=self._fresh_after(),
                                     **filters)

    def candidate_count(self, **filters) -> int:
        return self.store.candidate_count(self.today(), self.settings.categories, fresh_after=self._fresh_after(),
                                          **filters)

    # the gate ---------------------------------------------------------------

    def _check(self, cards: list) -> list[tuple[dict, str]]:
        failures: list[dict] = []
        good: list[tuple[dict, str]] = []
        seen: set[str] = set()
        today, ticked, fresh_after = self.today(), self.settings.categories, self._fresh_after()
        for card in cards:
            card = card if isinstance(card, dict) else {}
            event_id, why = card.get("id"), card.get("why")
            reasons: list[str] = []
            row = self.store.pool_row(event_id) if isinstance(event_id, str) and event_id else None
            if row is None:
                reasons.append("unknown_event")
            elif event_id in seen:
                reasons.append("duplicate_in_batch")
            elif not self.store.is_candidate(event_id, today, ticked, fresh_after=fresh_after):
                reasons.append("not_a_candidate")
            if isinstance(event_id, str):
                seen.add(event_id)
            if not isinstance(why, str) or not why.strip() or len(why.strip()) > MAX_WHY or not _plain(why):
                reasons.append("bad_why")
            if reasons:
                failures.append({"id": event_id if isinstance(event_id, str) else "", "reasons": reasons})
            else:
                good.append((row, why.strip()))  # type: ignore[union-attr, arg-type]
        if failures:
            raise DigestRefused(failures)
        return good

    async def save(self, cards: list, *, kind: str, note: str, session_id: str) -> dict:
        """Check every card against the pool and save the digest whole, then fetch the thumbnails. Raises
        ``ValueError`` for a wrong count, kind or note (or a weekly save from an on-demand run), and
        ``DigestRefused`` for any card that fails."""
        if not isinstance(cards, list) or not 1 <= len(cards) <= MAX_CARDS:
            raise ValueError(f"A digest holds 1 to {MAX_CARDS} cards.")
        if kind not in KINDS:
            raise ValueError("A digest is weekly or on_demand.")
        if not isinstance(note, str) or len(note.strip()) > MAX_NOTE or not _plain(note.strip()):
            raise ValueError(f"The note must be plain text of at most {MAX_NOTE} characters.")
        if kind == "weekly" and session_id and session_id in self.on_demand_sessions:
            raise ValueError("this is an on-demand run; save with kind on_demand")
        # No await between the candidate check and the store write: two saves at once for one event cannot both
        # pass the check, so one event never lands on two cards. Keep it that way.
        good = self._check(cards)
        digest = self.store.save_digest(good, kind=kind, note=note.strip(), session_id=str(session_id),
                                        created_at=self.now())
        pictures = 0
        for card in digest["cards"]:
            pictures += await self._thumbnail(card)
        self.ledger.append(Kind.calendar, "", "", event="discover_digest", digest_kind=kind,
                           cards=len(digest["cards"]), images=pictures)
        return self.store.digest(digest["id"]) or digest

    # thumbnails -------------------------------------------------------------

    async def _thumbnail(self, card: dict) -> int:
        """Fetch one card's picture (Eventbrite's connector asks only img.evbuc.com). 1 when kept, else 0."""
        url = card.get("image_url") or ""
        if not url:
            return 0
        try:
            data = await self.source.image(url)
        except Exception as e:  # a picture never fails a save
            log.warning("discover thumbnail failed: %s", type(e).__name__)
            return 0
        if not data:
            return 0
        tmp = self.images_dir / f"{card['id']}.part"
        try:
            self.images_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            tmp.replace(self.images_dir / card["id"])
        except OSError as e:
            log.warning("discover thumbnail not written: %s", type(e).__name__)
            try:
                tmp.unlink(missing_ok=True)  # a rename that failed leaves no part file behind
            except OSError:
                pass
            return 0
        self.store.set_image(card["id"], True)
        return 1

    def image_path(self, card_id: str) -> Path | None:
        """The saved picture of a card, or None. Inside ``discover-images/``, named by the card id."""
        if not isinstance(card_id, str) or not CARD_ID.fullmatch(card_id):
            return None
        card = self.store.card(card_id)
        if card is None or not card["has_image"]:
            return None
        path = self.images_dir / card_id
        return path if path.is_file() and not path.is_symlink() else None

    # the scout: tools, routine, status ----------------------------------------

    def register(self) -> bool:
        """Offer the scout's tools while discovery is on with a place; else take them away."""
        if self.tools is None or self.catalog is None:
            return False
        from src.events.discover_tools import DiscoverTools  # it imports this module

        return DiscoverTools(self).register(self.tools, self.catalog)

    def managed_routine(self) -> Routine | None:
        return next((r for r in self.routines.all() if r.managed_by == MANAGED_BY), None) if self.routines else None

    def ensure_routine(self) -> Routine | None:
        """The weekly routine, made if missing (Thursday 09:00, before the weekend) and switched on."""
        if self.routines is None:
            return None
        r = self.managed_routine()
        if r is None:
            r = Routine(uuid4().hex[:10], ROUTINE_NAME, BOT_ID, ROUTINE_SKILL, True,
                        Schedule("weekly", [3], "09:00", 0), managed_by=MANAGED_BY)
        else:
            r.enabled = True
        self.routines.upsert(r)
        self.routines.save()
        return r

    def _pause_routine(self) -> None:
        r = self.managed_routine()
        if r is not None and r.enabled:
            r.enabled = False
            self.routines.save()

    def running_run(self, sessions: dict) -> str:
        """The id of a scout run still going (an on-demand one or the routine's), or ""."""
        ended = (SessionState.done, SessionState.failed, SessionState.stopped)
        r = self.managed_routine()
        routine_session = self.scheduler.sessions.get(r.id) if r and self.scheduler else None
        candidates = [sessions.get(sid) for sid in self.on_demand_sessions] + [routine_session]
        return next((s.id for s in candidates if s is not None and s.state not in ended), "")

    def is_weekly_run(self, session_id: str) -> bool:
        """Whether ``session_id`` is the managed routine's current session (the weekly run)."""
        r = self.managed_routine()
        if not session_id or r is None or self.scheduler is None:
            return False
        session = self.scheduler.sessions.get(r.id)
        return session is not None and session.id == session_id

    def run_failure(self, session) -> str:
        """Why a routine run failed although its session ended: it saved no digest. "" when it did."""
        return "" if self.store.has_digest_for(session.id) else "no digest saved"

    async def apply(self, settings: DiscoverSettings) -> dict:
        """Use new settings: keep them (in memory and saved), make or pause the routine, register the tools."""
        before = self.settings
        self.settings = settings
        settings.save(self.vault)
        # the pool has no place column: the old city's events must not be offered for the new one
        for changed, source, keys in (
                (settings.place != before.place, "eventbrite", (LAST_REFRESH, LAST_ERROR, LAST_FAILURE)),
                (settings.findlocal_city != before.findlocal_city, "findlocal",
                 (FL_LAST_REFRESH, FL_LAST_ERROR, FL_LAST_FAILURE))):
            if changed:
                async with self._lock:   # wait for a refresh under way, so it cannot refill the pool afterwards
                    self.store.clear_pool(source)
                    for key in keys:
                        self.store.set_meta(key, "")
        if self.active and not (before.enabled and before.place):
            self.ensure_routine()
        elif not self.active:
            self._pause_routine()
        self.register()
        return self.status({})

    def status(self, sessions: dict) -> dict:
        r = self.managed_routine()
        return {"settings": self.settings.public(), "active": self.active,
                "routine_id": r.id if r else None, "routine_on": bool(r and r.enabled),
                "last_refresh": self.store.meta(LAST_REFRESH), "last_error": self.store.meta(LAST_ERROR),
                "running": self.running_run(sessions),
                "findlocal": {"city": self.settings.findlocal_city, "timezone": self.settings.timezone,
                              **self.keys.public(), "last_refresh": self.store.meta(FL_LAST_REFRESH),
                              "last_error": self.store.meta(FL_LAST_ERROR), "ready": self.findlocal_ready}}

    async def start(self) -> None:
        """At harness start: the tools, the routine when discovery is on and it is missing, and the routine paused
        when discovery is not active."""
        self.register()
        if self.active and self.managed_routine() is None:
            self.ensure_routine()
        elif not self.active:
            self._pause_routine()   # a routine left on from before (events.json gone) must not fire

    async def close(self) -> None:
        self.store.close()

"""Event discovery: refresh the pool from Eventbrite, check every digest card, fetch thumbnails.

The gate (``Discovery.save``) is the single authority on what reaches a digest. A bot supplies an event id and
a reason for each card; every other fact comes from the pool. A digest saves whole or not at all. Ledger
entries (kind ``calendar``, ``event`` ``discover_refresh`` or ``discover_digest``) carry counts and error
kinds only, never listing text, a note or a path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

from src.events.discover_settings import DiscoverSettings
from src.events.discover_store import LAST_ERROR, LAST_REFRESH, DiscoverStore
from src.events.eventbrite import Eventbrite, SourceError
from src.events.settings import calendar_dir
from src.memory.ledger import Kind

log = logging.getLogger(__name__)

REFRESH_AFTER_S = 12 * 3600
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
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return bool(self.settings.enabled and self.settings.place)

    # refresh ----------------------------------------------------------------

    def _fresh(self) -> bool:
        """The last good refresh is under REFRESH_AFTER_S old, by the ``now`` clock (stored, so it holds across
        a restart)."""
        try:
            last = datetime.strptime(self.store.meta(LAST_REFRESH), INSTANT)
            current = datetime.strptime(self.now(), INSTANT)
        except ValueError:
            return False
        return 0 <= (current - last).total_seconds() < REFRESH_AFTER_S

    async def refresh(self, force: bool = False) -> dict:
        """Read the place's pages into the pool. Skipped while the last refresh is under 12 hours old, unless
        forced; one at a time. Answers ``{"pages", "events", "error"}``."""
        none = {"pages": 0, "events": 0, "error": ""}
        if not self.active:
            return none
        async with self._lock:
            if not force and self._fresh():
                return none
            try:
                result = await self.source.read(self.settings.place)
            except SourceError as e:
                self.store.set_meta(LAST_ERROR, e.kind)
                out = {"pages": 0, "events": 0, "error": e.kind}
            else:
                self.store.upsert_pool(result.events, self.now())
                self.store.drop_past(self.today())
                self.store.set_meta(LAST_REFRESH, self.now())
                self.store.set_meta(LAST_ERROR, result.error)
                out = {"pages": result.pages, "events": len(result.events), "error": result.error}
            self.ledger.append(Kind.calendar, "", "", event="discover_refresh", **out)
            return out

    # the pool ---------------------------------------------------------------

    def candidates(self, **filters) -> list[dict]:
        return self.store.candidates(self.today(), self.settings.categories, **filters)

    # the gate ---------------------------------------------------------------

    def _check(self, cards: list) -> list[tuple[dict, str]]:
        failures: list[dict] = []
        good: list[tuple[dict, str]] = []
        seen: set[str] = set()
        today, ticked = self.today(), self.settings.categories
        for card in cards:
            card = card if isinstance(card, dict) else {}
            event_id, why = card.get("id"), card.get("why")
            reasons: list[str] = []
            row = self.store.pool_row(event_id) if isinstance(event_id, str) and event_id else None
            if row is None:
                reasons.append("unknown_event")
            elif event_id in seen:
                reasons.append("duplicate_in_batch")
            elif not self.store.is_candidate(event_id, today, ticked):
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
        ``ValueError`` for a wrong count, kind or note, and ``DigestRefused`` for any card that fails."""
        if not isinstance(cards, list) or not 1 <= len(cards) <= MAX_CARDS:
            raise ValueError(f"A digest holds 1 to {MAX_CARDS} cards.")
        if kind not in KINDS:
            raise ValueError("A digest is weekly or on_demand.")
        if not isinstance(note, str) or len(note.strip()) > MAX_NOTE:
            raise ValueError(f"The note can be at most {MAX_NOTE} characters.")
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

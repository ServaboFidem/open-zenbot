"""The /discover HTTP routes. No response carries a disk path.

Adding a card makes one calendar event, at the listing's own time. The harness cannot turn a wall-clock time into an
instant (no zone database), so the page sends the instants; each is read as a naive date and time and must lie within
14 hours of the listing's (no zone is further from UTC), and an all-day card takes exactly its own dates."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.events.discover_settings import CATEGORIES, FINDLOCAL_CITIES, DiscoverSettings, check_key
from src.events.discover_store import LAST_REFRESH
from src.events.discovery import BOT_ID, ROUTINE_SKILL
from src.events.routes import NothingIn
from src.events.store import EventError
from src.harness.scheduler import next_run

SET_UP_FIRST = "Set up event discovery first."
ALREADY_LOOKING = "The event scout is already looking."
PAST = "This event has already happened."
BAD_TIMES = "Those times are not the listing's. Reload the page and try again."
NO_ZONE = "This listing has no usable time zone."
NO_EVENT = "No such event."
RUN_TASK = ("On-demand picks: find local events for the user. "
            "Save them with events_save_digest using kind \"on_demand\".")
MAX_HOURS = 14
_INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# what the pool's routes answer of a row: the list's slim fields, and the detail's (plus ``state`` and ``image``)
SLIM = ("id", "name", "start_date", "start_time", "end_date", "end_time", "venue", "city", "category_names", "source")
DETAIL = SLIM + ("summary", "address", "region", "timezone", "url", "categories")


class KeyIn(BaseModel):
    key: str


class AddIn(BaseModel):
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


def _sniff(head: bytes) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def listing_times(card: dict, start: str, end: str) -> dict[str, Any]:
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


def discover_router(discovery, harness) -> APIRouter:
    r = APIRouter(prefix="/discover")
    store = discovery.store
    add_lock = asyncio.Lock()  # the whole add runs under it: two requests for one card cannot both create

    def card_view(card: dict) -> dict:
        out = {k: v for k, v in card.items() if k != "has_image"}
        out["image"] = f"/discover/images/{card['id']}" if card.get("has_image") else None
        return out

    def digest_view(digest: dict) -> dict:
        return {**digest, "cards": [card_view(c) for c in digest["cards"]]}

    def status() -> dict[str, Any]:
        s = discovery.status(harness.sessions)
        routine = discovery.managed_routine()
        nxt = None
        if routine is not None and routine.enabled:
            try:
                nxt = next_run(routine.schedule, datetime.now().astimezone()).isoformat()
            except ValueError:
                nxt = None
        return {"settings": s["settings"],
                "categories": [{"tag": t, "name": n} for t, n in CATEGORIES.items()],
                "set_up": discovery.active,
                "routine": None if routine is None else {"enabled": routine.enabled, "next_run": nxt},
                "refresh": {"last": s["last_refresh"], "error": s["last_error"], "pool": store.pool_size()},
                "running": s["running"],
                "refreshing": discovery.refreshing,
                "findlocal": {**s["findlocal"], "cities": list(FINDLOCAL_CITIES)}}

    def event_view(row: dict) -> dict:
        return {k: v for k, v in row.items() if k != "etag"}

    def live(calendar_event_id: str) -> dict | None:
        ev = discovery.calendar.store.event(calendar_event_id) if calendar_event_id else None
        return ev if ev is not None and not ev["deleted"] else None

    def live_event(card: dict) -> dict | None:
        return live(card["calendar_event_id"])

    def pool_live(row: dict) -> dict | None:
        """The first calendar event made from a pool row (``calendar_ids``) that still exists, not deleted."""
        return next((ev for ev in map(live, row["calendar_ids"]) if ev is not None), None)

    def state(row: dict) -> str:
        """A pool row's state, from its ``recommended`` and ``calendar_ids`` (``DiscoverStore.listing``, ``marks``)."""
        return "added" if pool_live(row) is not None else "recommended" if row["recommended"] else ""

    def slim_view(row: dict) -> dict:
        return {**{k: row[k] for k in SLIM}, "state": state(row)}

    def full_view(row: dict) -> dict:
        image = f"/discover/events/{row['id']}/image" if row["source"] == "eventbrite" and row["image_url"] else None
        return {**{k: row[k] for k in DETAIL}, "state": state(row), "image": image}

    def marked_row(event_id: str) -> dict | None:
        row = store.pool_row(event_id)
        return None if row is None else {**row, **store.marks(event_id)}

    def make_event(listing: dict, body: AddIn) -> dict:
        """The calendar event for a card or a pool row, at the listing's own time: 409 when it has passed, 422 for
        times that are not the listing's or fields the calendar refuses."""
        if (listing["end_date"] or listing["start_date"]) < discovery.today():
            raise HTTPException(409, PAST)
        try:
            timing = listing_times(listing, body.start, body.end)
            place = ", ".join(p for p in (listing["venue"], listing["address"]) if p)
            text = f"{listing['summary']}\n\n{listing['url']}" if listing["summary"] else listing["url"]
            return discovery.calendar.store.add(
                {"title": listing["name"], "location": place, "description": text, **timing})
        except EventError as e:
            raise HTTPException(422, str(e)) from None

    @r.get("/status")
    async def get_status() -> dict[str, Any]:
        return status()

    @r.put("/settings")
    async def put_settings(body: dict[str, Any]) -> dict[str, Any]:
        try:
            settings = DiscoverSettings.validated(body, discovery.settings)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        await discovery.apply(settings)
        return status()

    @r.put("/findlocal/key")
    async def put_key(body: KeyIn) -> dict[str, Any]:
        try:
            key = check_key(body.key)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        await discovery.set_key(key)
        return status()

    @r.delete("/findlocal/key")
    async def delete_key() -> dict[str, Any]:
        await discovery.set_key(None)
        return status()

    @r.get("/digests")
    async def list_digests(before: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [digest_view(d) for d in store.digests(before, max(1, min(limit, 100)))]

    @r.get("/digests/{digest_id}")
    async def get_digest(digest_id: str) -> dict[str, Any]:
        d = store.digest(digest_id)
        if d is None:
            raise HTTPException(404, "No such digest.")
        return digest_view(d)

    @r.get("/images/{card_id}")
    async def image(card_id: str) -> FileResponse:
        path = discovery.image_path(card_id)
        if path is None:
            raise HTTPException(404, "No picture.")
        with path.open("rb") as f:
            kind = _sniff(f.read(12))
        return FileResponse(path, media_type=kind, headers={"X-Content-Type-Options": "nosniff"})

    @r.post("/run")
    async def run(body: NothingIn) -> dict[str, Any]:
        if not discovery.active:
            raise HTTPException(409, SET_UP_FIRST)
        bot = harness.bots.get(BOT_ID)
        if bot is None:
            raise HTTPException(409, f"the {BOT_ID} bot does not exist")
        if not bot.closed:
            raise HTTPException(409, "The event scout must be a closed bot.")
        if not harness.bound:
            raise HTTPException(503, "no provider is bound")
        if discovery.running_run(harness.sessions):
            raise HTTPException(409, ALREADY_LOOKING)
        session = harness.start_task(bot, RUN_TASK, in_conversation=False, pinned_skills=[ROUTINE_SKILL],
                                     recall_query=discovery.settings.interests)
        discovery.on_demand_sessions.add(session.id)
        return {"session_id": session.id}

    @r.post("/cards/{card_id}/dismiss")
    async def dismiss(card_id: str, body: NothingIn) -> dict[str, Any]:
        card = store.set_state(card_id, "dismissed") if store.card(card_id) else None
        if card is None:
            raise HTTPException(404, "No such card.")
        return card_view(card)

    @r.post("/cards/{card_id}/add")
    async def add(card_id: str, body: AddIn) -> dict[str, Any]:
        calendar = discovery.calendar
        async with add_lock:
            card = store.card(card_id)
            if card is None:
                raise HTTPException(404, "No such card.")
            existing = live_event(card)
            if existing is not None:
                card = store.mark_added(card_id, existing["id"]) or card
                return {"card": card_view(card), "event": event_view(existing)}
            row = make_event(card, body)
            card = store.mark_added(card_id, row["id"], replace=True) or card
        calendar.kick()
        return {"card": card_view(card), "event": event_view(row)}

    @r.get("/events")
    async def list_events() -> dict[str, Any]:
        """The pool from today on, slim. Never reads a source: the list is what the last refresh left."""
        if not discovery.active:
            return {"events": [], "total": 0, "read_at": ""}
        events = [slim_view(row) for row in store.listing(discovery.today())]
        return {"events": events, "total": len(events), "read_at": store.meta(LAST_REFRESH)}

    @r.get("/events/{event_id}")
    async def get_event(event_id: str) -> dict[str, Any]:
        row = marked_row(event_id)
        if row is None:
            raise HTTPException(404, NO_EVENT)
        return full_view(row)

    @r.get("/events/{event_id}/image")
    async def event_image(event_id: str) -> FileResponse:
        """A pool event's picture, fetched from Eventbrite's image host on the first ask and kept. 404 when the row
        has no picture, is not Eventbrite's, or the fetch failed."""
        path = await discovery.pool_image(event_id)
        if path is None:
            raise HTTPException(404, "No picture.")
        try:
            with path.open("rb") as f:
                kind = _sniff(f.read(12))
        except OSError:
            raise HTTPException(404, "No picture.") from None
        return FileResponse(path, media_type=kind, headers={"X-Content-Type-Options": "nosniff"})

    @r.post("/events/{event_id}/add")
    async def add_event(event_id: str, body: AddIn) -> dict[str, Any]:
        """Add a pool event to the calendar, from the pool row (what the list showed), never a card's snapshot. On a
        card that is not dismissed, that card is marked added; otherwise an ``added`` row is kept."""
        calendar = discovery.calendar
        async with add_lock:
            row = marked_row(event_id)
            if row is None:
                raise HTTPException(404, NO_EVENT)
            existing = pool_live(row)
            if existing is not None:
                return {"event": full_view(row), "calendar_event": event_view(existing)}
            made = make_event(row, body)
            card = store.card_for_event(event_id)
            if card is not None:
                store.mark_added(card["id"], made["id"], replace=True)
            else:
                store.mark_event_added(event_id, made["id"], discovery.now())
            row = marked_row(event_id) or row
        calendar.kick()
        return {"event": full_view(row), "calendar_event": event_view(made)}

    @r.post("/refresh")
    async def refresh(body: NothingIn) -> dict[str, Any]:
        """Read the sources again in the background (unforced: the usual waits hold) and answer the status."""
        if not discovery.active:
            raise HTTPException(409, SET_UP_FIRST)
        discovery.start_refresh()
        return status()

    return r

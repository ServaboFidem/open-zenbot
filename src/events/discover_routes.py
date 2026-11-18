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
from src.events.discovery import BOT_ID, ROUTINE_SKILL
from src.events.routes import NothingIn
from src.events.store import EventError
from src.harness.scheduler import next_run

SET_UP_FIRST = "Set up event discovery first."
ALREADY_LOOKING = "The event scout is already looking."
PAST = "This event has already happened."
BAD_TIMES = "Those times are not the listing's. Reload the page and try again."
NO_ZONE = "This listing has no usable time zone."
RUN_TASK = ("On-demand picks: find local events for the user. "
            "Save them with events_save_digest using kind \"on_demand\".")
MAX_HOURS = 14
_INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


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
                "findlocal": {**s["findlocal"], "cities": list(FINDLOCAL_CITIES)}}

    def event_view(row: dict) -> dict:
        return {k: v for k, v in row.items() if k != "etag"}

    def live_event(card: dict) -> dict | None:
        ev = discovery.calendar.store.event(card["calendar_event_id"]) if card["calendar_event_id"] else None
        return ev if ev is not None and not ev["deleted"] else None

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
            if (card["end_date"] or card["start_date"]) < discovery.today():
                raise HTTPException(409, PAST)
            try:
                timing = listing_times(card, body.start, body.end)
                place = ", ".join(p for p in (card["venue"], card["address"]) if p)
                text = f"{card['summary']}\n\n{card['url']}" if card["summary"] else card["url"]
                row = calendar.store.add({"title": card["name"], "location": place, "description": text, **timing})
            except EventError as e:
                raise HTTPException(422, str(e)) from None
            card = store.mark_added(card_id, row["id"], replace=True) or card
        calendar.kick()
        return {"card": card_view(card), "event": event_view(row)}

    return r

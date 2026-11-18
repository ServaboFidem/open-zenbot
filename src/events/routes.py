"""The /calendar HTTP routes. No response carries the client secret, an access token or a refresh token."""

from __future__ import annotations

import html
import logging
import re
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.events.connect import FAILED
from src.events.service import CalendarLocked
from src.events.store import EventError

log = logging.getLogger(__name__)

MAX_RANGE_DAYS = 400
READ_ONLY = "This event is read-only here."
LOCKED = "This calendar holds events pushed from here; it stays selected while push is on."
NEEDS_WRITE = "Allow OpenZenBot to add events to Google first."
NO_DESTINATION = "Pick a Google calendar OpenZenBot may add events to."
CALLBACK_PAGE = '<!doctype html><meta charset="utf-8"><title>OpenZenBot</title><p>{message}</p>'
CALLBACK_HEADERS = {"Content-Security-Policy": "default-src 'none'", "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-store"}
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class EventIn(BaseModel):
    # loose on purpose: the store's validate answers a bad value with a sentence for the user
    title: Any = None
    location: Any = ""
    description: Any = ""
    all_day: Any = False
    start: Any = None
    end: Any = None
    tz: Any = ""


class CalendarIn(BaseModel):
    visible: bool | None = None
    selected: bool | None = None


class CredentialsIn(BaseModel):
    client_id: str = ""
    client_secret: str = ""


class ConnectIn(BaseModel):
    write: bool = False


class PushIn(BaseModel):
    on: bool
    destination: str | None = None


class NothingIn(BaseModel):
    """The body of a POST that needs no value. It is still required, on every POST here: a request another
    site's page can send without a preflight has no body, a form or ``text/plain``, and none of those passes
    for a JSON body, so such a request is refused (422) before the route runs. Keep it on a new POST route."""


class AckIn(BaseModel):
    count: int = Field(ge=0)


def _day(value: str) -> date:
    if not _DAY.fullmatch(value):
        raise HTTPException(422, "start and end are dates as YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "start and end are dates as YYYY-MM-DD.") from None


def calendar_router(service) -> APIRouter:
    r = APIRouter(prefix="/calendar")
    store, conn = service.store, service.conn

    def calendars() -> dict[str, dict]:
        return {c["id"]: c for c in store.calendars()}

    def editable(row: dict, cals: dict[str, dict]) -> bool:
        if row["origin"] == "local":
            return True
        cal = cals.get(row["calendar_id"])
        return service.syncer.can_push() and cal is not None and cal["writable"] and row["series_id"] == ""

    def event_view(row: dict, cals: dict[str, dict]) -> dict:
        out = {k: v for k, v in row.items() if k != "etag"}
        out["editable"] = editable(row, cals)
        out["calendar_name"] = cals[row["calendar_id"]]["name"] if row["calendar_id"] in cals else ""
        return out

    def calendar_view(cal: dict, pushed: set[str]) -> dict:
        s = conn.settings
        out = {k: v for k, v in cal.items() if k not in ("sync_token", "full_read_at")}
        out["locked"] = bool(s.push and (cal["id"] == s.destination or cal["id"] in pushed))
        return out

    def writable_google(calendar_id: str | None) -> bool:
        cal = calendars().get(calendar_id or "")
        return cal is not None and cal["source"] == "google" and cal["writable"]

    def changeable(id: str) -> dict:
        """The live event behind ``id`` when the user may change it: 404 when missing or deleted, 403 when not editable."""
        row = store.event(id)
        if row is None or row["deleted"]:
            raise HTTPException(404, "No such event.")
        if not editable(row, calendars()):
            raise HTTPException(403, READ_ONLY)
        return row

    # events -------------------------------------------------------------------

    @r.get("/status")
    async def status() -> dict[str, Any]:
        return service.status()

    @r.get("/events")
    async def list_events(start: str, end: str) -> list[dict]:
        a, b = _day(start), _day(end)
        if not 0 <= (b - a).days <= MAX_RANGE_DAYS:
            raise HTTPException(422, f"The end must be on or after the start and at most {MAX_RANGE_DAYS} days later.")
        cals = calendars()
        return [event_view(row, cals) for row in store.events(start, end)]

    @r.post("/events")
    async def create_event(body: EventIn) -> dict:
        try:
            row = store.add(body.model_dump())
        except EventError as e:
            raise HTTPException(422, str(e)) from None
        service.kick()
        return event_view(row, calendars())

    @r.put("/events/{id}")
    async def update_event(id: str, body: EventIn) -> dict:
        changeable(id)
        try:
            row = store.update(id, body.model_dump())
        except EventError as e:
            raise HTTPException(422, str(e)) from None
        if row is None:
            raise HTTPException(404, "No such event.")
        service.kick()
        return event_view(row, calendars())

    @r.delete("/events/{id}")
    async def delete_event(id: str) -> dict:
        changeable(id)
        if not store.delete(id):
            raise HTTPException(404, "No such event.")
        service.kick()
        return {"ok": True}

    # calendars ----------------------------------------------------------------

    @r.get("/calendars")
    async def list_calendars() -> list[dict]:
        pushed = store.pushed_calendars()
        return [calendar_view(c, pushed) for c in store.calendars()]

    @r.put("/calendars/{calendar_id:path}")
    async def put_calendar(calendar_id: str, body: CalendarIn) -> dict:
        cal = calendars().get(calendar_id)
        if cal is None:
            raise HTTPException(404, "No such calendar.")
        if body.selected is False and calendar_view(cal, store.pushed_calendars())["locked"]:
            raise HTTPException(409, LOCKED)
        if body.visible is not None:
            cal = store.set_calendar(calendar_id, visible=body.visible)
        if body.selected is not None and body.selected != cal["selected"]:
            try:
                cal = (store.set_calendar(calendar_id, selected=True) if body.selected
                       else await service.unselect(calendar_id))
            except CalendarLocked:  # it became locked while the unselect waited for the run in flight
                raise HTTPException(409, LOCKED) from None
            if cal is None:
                raise HTTPException(404, "No such calendar.")
            service.request()
        return calendar_view(cal, store.pushed_calendars())

    # Google -------------------------------------------------------------------

    @r.put("/google/credentials")
    async def put_credentials(body: CredentialsIn) -> dict[str, Any]:
        try:
            conn.set_credentials(body.client_id, body.client_secret)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return service.status()

    @r.delete("/google/credentials")
    async def delete_credentials() -> dict[str, Any]:
        try:
            conn.clear_credentials()
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return service.status()

    @r.post("/google/connect")
    async def connect(body: ConnectIn) -> dict[str, str]:
        try:
            return {"url": conn.begin(write=body.write)}
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @r.get("/google/callback")
    async def callback(state: str | None = None, code: str | None = None, error: str | None = None) -> HTMLResponse:
        try:
            ok, message = await service.finish(state, code, error)
        except Exception as e:
            # Never an unhandled error here: its traceback would hold the code, the tokens and the client secret.
            # A warning, not an error: an error record is sent on with this request's query string.
            log.warning("calendar sign-in failed: %s", type(e).__name__)
            ok, message = False, FAILED
        return HTMLResponse(CALLBACK_PAGE.format(message=html.escape(message)), status_code=200 if ok else 400,
                            headers=CALLBACK_HEADERS)

    @r.post("/google/disconnect")
    async def disconnect(body: NothingIn) -> dict[str, Any]:
        await service.disconnect()
        return service.status()

    # push and sync --------------------------------------------------------------

    @r.get("/push/preview")
    async def push_preview(destination: str) -> dict[str, int]:
        if not writable_google(destination):
            raise HTTPException(422, NO_DESTINATION)
        return service.syncer.preview(destination)

    @r.put("/push")
    async def put_push(body: PushIn) -> dict[str, Any]:
        s = conn.settings
        if body.on:
            if not conn.can_write():
                raise HTTPException(409, NEEDS_WRITE)
            destination = body.destination or s.destination
            if not writable_google(destination):
                raise HTTPException(422, NO_DESTINATION)
            s.push, s.destination = True, destination
            s.save(service.vault)
            # the destination and every calendar already holding an event pushed from here are pulled, so a
            # change or delete made on Google there is seen (set_calendar skips one no longer listed)
            for calendar_id in {destination, *store.pushed_calendars()}:
                store.set_calendar(calendar_id, selected=True)
            service.request()
        else:
            s.push = False
            s.save(service.vault)
        return service.status()

    @r.post("/sync")
    async def sync(body: NothingIn) -> dict[str, Any]:
        return await service.sync()

    @r.post("/lost-edits/ack")
    async def ack_lost_edits(body: AckIn) -> dict[str, Any]:
        service.ack_lost_edits(body.count)
        return service.status()

    return r

"""The /briefings HTTP routes: the status, create or replace, delete and Play now.

Every POST takes a required JSON body (`NothingIn`), so a cross-site page cannot send one without a preflight. The
PUT body is checked here, by hand, so a malformed one is a 422 that never echoes it. The settings are read from and
written to the one briefings file (`BriefingStore`), and every save or delete tells the service
(`settings_saved`), so a schedule edit or a switch-on never yields a false late skip or a second play that day.
Ledger entries hold `enabled` and the line count only."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from src.briefing.service import BUSY, Busy, BriefingService, NotReady
from src.briefing.settings import CEILING_ZERO, KINDS, Briefing, BriefingStore, Slot, StoreReadOnly, check
from src.events.routes import NothingIn
from src.memory.ledger import Kind

log = logging.getLogger(__name__)

NOT_READY = {
    "sonos_off": "Sonos is off. Switch it on in Settings > Connected apps.",
    "no_speakers": "No Sonos speaker was found.",
    "no_voice": "Set up a voice in Settings > Models > Jobs > Read aloud first.",
    "ceiling_zero": CEILING_ZERO,
}
NO_BRIEFING = "There is no such briefing."
BAD_BODY = "The briefing could not be read. Check the days, times, speakers and volume."
NOT_SAVED = "The briefing could not be saved."


def _parse(kind: str, body: Any, previous: Briefing | None) -> Briefing:
    """The body as a `Briefing` (not yet checked), or a 422 that echoes nothing."""
    try:
        slots = [Slot(days=s["days"], time=s["time"]) for s in body["slots"]]
        return Briefing(kind=kind, enabled=body["enabled"], slots=slots, speakers=body.get("speakers"),
                        volume=body["volume"], **({"id": previous.id} if previous else {}))
    except (TypeError, KeyError, AttributeError):
        raise HTTPException(422, BAD_BODY) from None


def briefing_router(service: BriefingService) -> APIRouter:
    r = APIRouter(prefix="/briefings")

    def note(**data: Any) -> None:
        try:
            service.ledger.append(Kind.briefing, "", "", event="settings", **data)
        except Exception as e:  # noqa: BLE001
            log.warning("briefing ledger entry not written: %s", type(e).__name__)

    def known(kind: str) -> None:
        if kind not in KINDS:
            raise HTTPException(404, NO_BRIEFING)

    def persist(store: BriefingStore, kind: str) -> None:
        try:
            store.save(service.vault)
        except StoreReadOnly as e:
            raise HTTPException(409, str(e)) from None
        except Exception as e:  # noqa: BLE001 - the frames may hold more than a sentence
            log.error("briefings not saved: %s", type(e).__name__)
            raise HTTPException(500, NOT_SAVED) from None
        service.settings_saved(kind)

    @r.get("")
    async def status() -> dict[str, Any]:
        return service.status()

    @r.put("/{kind}")
    async def save(kind: str, body: Any = Body(...)) -> dict[str, Any]:
        known(kind)
        if not isinstance(body, dict):
            raise HTTPException(422, BAD_BODY)
        store = BriefingStore.load(service.vault)
        b = _parse(kind, body, store.items.get(kind))
        try:
            check(b, service.sonos.settings.max_volume)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        store.put(b)
        persist(store, kind)
        note(enabled=b.enabled, lines=len(b.slots))
        return service.status()

    @r.delete("/{kind}")
    async def delete(kind: str) -> dict[str, Any]:
        known(kind)
        store = BriefingStore.load(service.vault)
        if not store.delete(kind):
            raise HTTPException(404, NO_BRIEFING)
        persist(store, kind)
        note(enabled=False, lines=0)
        return service.status()

    @r.post("/{kind}/play")
    async def play(kind: str, body: NothingIn) -> dict[str, Any]:
        known(kind)
        try:
            service.start_play_now(kind)  # in the background: a client that goes away must not stop the briefing
        except Busy:
            raise HTTPException(409, BUSY) from None
        except NotReady as e:
            if e.reason == "no_briefing":
                raise HTTPException(404, NO_BRIEFING) from None
            raise HTTPException(409, NOT_READY.get(e.reason, "The briefing is not ready.")) from None
        return service.status()

    return r

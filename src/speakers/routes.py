"""The /speakers HTTP routes: the status, the three switches, the default speaker, and Look again.

The only POST takes a required JSON body (`NothingIn`), so a cross-site page cannot send it without a preflight. No
answer carries a speaker's address."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.events.routes import NothingIn
from src.speakers.service import SpeakerService


class SwitchesIn(BaseModel):
    airplay: bool | None = None
    chromecast: bool | None = None
    dlna: bool | None = None


class DefaultIn(BaseModel):
    key: str | None = Field(..., max_length=300)  # required: only an explicit null clears the default


def speakers_router(service: SpeakerService) -> APIRouter:
    r = APIRouter(prefix="/speakers")

    @r.get("/status")
    async def status() -> dict[str, Any]:
        return service.status()

    @r.put("/settings")
    async def settings(body: SwitchesIn) -> dict[str, Any]:
        try:
            return await service.set_switches(airplay=body.airplay, chromecast=body.chromecast, dlna=body.dlna)
        except OSError:
            raise HTTPException(500, "The settings could not be saved.") from None

    @r.put("/default")
    async def default(body: DefaultIn) -> dict[str, Any]:
        try:
            return await service.set_default(body.key)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except OSError:
            raise HTTPException(500, "The settings could not be saved.") from None

    @r.post("/look")
    async def look(body: NothingIn) -> dict[str, Any]:
        refused = service.start_looks()
        if refused:
            raise HTTPException(409, refused)
        return service.status()

    return r

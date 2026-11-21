"""The /sonos HTTP routes: status, settings, and Look again (a look with the slower subnet scan).

Every POST takes a required JSON body, so a cross-site page cannot send one without a preflight."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.events.routes import NothingIn
from src.sonos.service import SonosService

OFF = "Sonos is off."
ALREADY = "Already looking for speakers."


class SettingsIn(BaseModel):
    enabled: bool
    max_volume: int


def sonos_router(service: SonosService) -> APIRouter:
    r = APIRouter(prefix="/sonos")

    @r.get("/status")
    async def status():
        return await service.status()

    @r.put("/settings")
    async def save_settings(body: SettingsIn):
        try:
            return await service.set_settings(body.enabled, body.max_volume)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @r.post("/discover")
    async def discover(_: NothingIn):
        if not service.settings.enabled:
            raise HTTPException(409, OFF)
        if not service.start_look(True):
            raise HTTPException(409, ALREADY)
        return await service.status()

    return r

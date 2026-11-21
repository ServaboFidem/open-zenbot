"""The /sonos HTTP routes: status, settings, Look again (a look with the slower subnet scan), and the Sonos page's
controls: the rooms with what plays and their volume, the Favorites, play on chosen speakers, transport and volume.

Every POST takes a required JSON body, so a cross-site page cannot send one without a preflight. A command the
speakers or the rules refuse (an unknown room or favorite, a speaker that did not answer, the volume ceiling) is a
409 whose detail is the sentence to show; a malformed body is FastAPI's 422."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.events.routes import NothingIn
from src.sonos.devices import ACTIONS
from src.sonos.service import SonosService
from src.sonos.tools import SonosTools

OFF = "Sonos is off."
ALREADY = "Already looking for speakers."


class SettingsIn(BaseModel):
    enabled: bool
    max_volume: int


class PlayIn(BaseModel):
    rooms: list[str] = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=300)


class ControlIn(BaseModel):
    room: str = Field(min_length=1, max_length=300)
    action: Literal[ACTIONS]  # type: ignore[valid-type]


class VolumeIn(BaseModel):
    room: str = Field(min_length=1, max_length=300)
    level: int = Field(ge=0, le=100)


def sonos_router(service: SonosService, tools: SonosTools) -> APIRouter:
    r = APIRouter(prefix="/sonos")

    def on() -> None:
        if not service.settings.enabled:
            raise HTTPException(409, OFF)

    async def command(work: Callable[[], Awaitable[Any]]) -> Any:
        on()
        try:
            return await work()
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

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
        on()
        if not service.start_look(True):
            raise HTTPException(409, ALREADY)
        return await service.status()

    @r.get("/rooms")
    async def rooms():
        rows = await command(tools.panel_rooms)
        return {"rooms": rows, "max_volume": service.settings.max_volume}

    @r.get("/favorites")
    async def favorites():
        return {"favorites": await command(tools.panel_favorites)}

    @r.post("/play")
    async def play(body: PlayIn):
        return {"message": await command(lambda: tools.panel_play(body.rooms, body.name))}

    @r.post("/control")
    async def control(body: ControlIn):
        return {"message": await command(lambda: tools.panel_control(body.room, body.action))}

    @r.post("/volume")
    async def volume(body: VolumeIn):
        return {"message": await command(lambda: tools.panel_volume(body.room, body.level))}

    return r

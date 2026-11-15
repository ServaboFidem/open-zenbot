"""The /music HTTP routes. The Discogs token is never in a response."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.music.digest import BOT_ID
from src.music.settings import MusicSettings

REACTIONS = {"passed", "owned", "listened"}


class SettingsIn(BaseModel):
    enabled: bool | None = None
    username: str | None = None
    focus: list[str] | None = None
    count: int | None = None
    era_mix: float | None = None
    obscurity: float | None = None
    notify: bool | None = None
    digest: bool | None = None  # true: (re)create the weekly routine, e.g. after it was deleted


class ConnectIn(BaseModel):
    username: str
    token: str | None = None


class TokenIn(BaseModel):
    token: str


class ReactionIn(BaseModel):
    reaction: Literal["passed", "owned", "listened", "wanted"] | None = None


class DiscoverIn(BaseModel):
    genre: str


def music_router(service, harness) -> APIRouter:
    r = APIRouter(prefix="/music")

    def unit(name: str, value: float | None, lo: float, hi: float) -> None:
        if value is not None and not lo <= value <= hi:
            raise HTTPException(422, f"{name} must be between {lo:g} and {hi:g}")

    @r.get("/status")
    async def status() -> dict[str, Any]:
        return service.status()

    @r.put("/settings")
    async def put_settings(body: SettingsIn) -> dict[str, Any]:
        cur = service.settings
        if body.count is not None and not 1 <= body.count <= 10:
            raise HTTPException(422, "count must be between 1 and 10")
        unit("era_mix", body.era_mix, 0, 1)
        unit("obscurity", body.obscurity, 0, 1)
        if body.focus:
            if not await service.prepare():
                raise HTTPException(503, "The genre atlas is not available yet.")
            unknown = [g for g in body.focus if service.atlas.genre(g) is None]
            if unknown:
                raise HTTPException(422, f"not Soundlore genres: {', '.join(unknown)}")
        new = MusicSettings(
            enabled=cur.enabled if body.enabled is None else body.enabled,
            username=cur.username if body.username is None else body.username.strip(),
            focus=cur.focus if body.focus is None else [g.strip() for g in body.focus if g.strip()],
            count=cur.count if body.count is None else body.count,
            era_mix=cur.era_mix if body.era_mix is None else body.era_mix,
            obscurity=cur.obscurity if body.obscurity is None else body.obscurity,
            notify=cur.notify if body.notify is None else body.notify)
        return await service.enable(new, digest=bool(body.digest))

    @r.post("/connect")
    async def connect(body: ConnectIn) -> dict[str, Any]:
        if not body.username.strip():
            raise HTTPException(422, "username is empty")
        return await service.connect(body.username, body.token)

    @r.put("/token")
    async def put_token(body: TokenIn) -> dict[str, Any]:
        if not body.token.strip():
            raise HTTPException(422, "token is empty")
        return service.set_token(body.token)

    @r.delete("/token")
    async def delete_token() -> dict[str, Any]:
        return service.remove_token()

    @r.post("/scan")
    async def scan() -> dict[str, Any]:
        if not service.settings.username:
            raise HTTPException(422, "set a Discogs username first")
        if not await service.prepare():
            raise HTTPException(503, "The genre atlas is not available yet.")
        return {"scan": service.start_scan()}

    @r.get("/map")
    async def get_map() -> dict[str, Any]:
        if not await service.prepare():
            raise HTTPException(503, "The genre atlas is not available yet.")
        return service.map()

    @r.get("/genres/{name}")
    async def get_genre(name: str) -> dict[str, Any]:
        if not await service.prepare():
            raise HTTPException(503, "The genre atlas is not available yet.")
        out = service.genre(name)
        if out is None:
            raise HTTPException(404, f"no genre {name!r}")
        return out

    @r.get("/digests")
    async def list_digests(before: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return service.store.digests(before, max(1, min(limit, 100)))

    @r.get("/digests/{digest_id}")
    async def get_digest(digest_id: str) -> dict[str, Any]:
        d = service.store.digest(digest_id)
        if d is None:
            raise HTTPException(404, f"no digest {digest_id}")
        return d

    @r.post("/digests/{digest_id}/read")
    async def read_digest(digest_id: str) -> dict[str, Any]:
        if not service.store.mark_read(digest_id):
            raise HTTPException(404, f"no digest {digest_id}")
        return {"unread": service.store.unread_count()}

    @r.put("/cards/{card_id}/reaction")
    async def put_reaction(card_id: str, body: ReactionIn) -> dict[str, Any]:
        try:
            return await service.digests.react(card_id, body.reaction)
        except KeyError:
            raise HTTPException(404, f"no card {card_id}") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @r.post("/cards/{card_id}/want")
    async def want(card_id: str) -> dict[str, Any]:
        try:
            return await service.digests.want(card_id)
        except KeyError:
            raise HTTPException(404, f"no card {card_id}") from None

    @r.delete("/cards/{card_id}/want")
    async def unwant(card_id: str) -> dict[str, Any]:
        try:
            return await service.digests.unwant(card_id)
        except KeyError:
            raise HTTPException(404, f"no card {card_id}") from None

    @r.get("/covers/{master_id}")
    async def cover(master_id: int) -> FileResponse:
        path = service.digests.cover_path(master_id)
        if path is None:
            raise HTTPException(404, "no cover")
        return FileResponse(path)

    @r.post("/discover")
    async def discover(body: DiscoverIn) -> dict[str, Any]:
        if not (service.settings.enabled and service.keys.token):
            raise HTTPException(409, "Turn Music on and add a Discogs token first.")
        if not await service.prepare():
            raise HTTPException(503, "The genre atlas is not available yet.")
        if service.atlas.genre(body.genre) is None:
            raise HTTPException(422, f"{body.genre!r} is not a Soundlore genre")
        bot = harness.bots.get(BOT_ID)
        if bot is None:
            raise HTTPException(409, f"the {BOT_ID} bot does not exist")
        if not harness.bound:
            raise HTTPException(503, "no provider is bound")
        if running := service.running_run(harness.sessions):
            raise HTTPException(409, f"The music librarian is already on a run (session {running}). "
                                     "Try again when it finishes.")
        task = (f"On-demand picks: find albums in the genre {body.genre} that the user does not own. "
                f"kind=on_demand genre={body.genre}: get candidates with music_candidates(genre=\"{body.genre}\") "
                f"and save them with music_save_digest using kind \"on_demand\".")
        session = harness.start_task(bot, task, in_conversation=False, pinned_skills=["weekly-digest"])
        service.digests.on_demand_sessions.add(session.id)
        return {"session_id": session.id}

    return r

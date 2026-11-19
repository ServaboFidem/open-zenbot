"""The `/dream` routes: dreaming's switch and idle time, its status, the idle report Electron posts, "Dream now"
and the list of what was dreamt. None returns message text; only `GET /dream/facts` returns fact text (the
dreamt facts, which the user may Forget through `DELETE /memory/{fact_id}`). Every POST takes a required JSON
body, as the calendar's do, so another site's page cannot send one without a preflight."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StrictBool, StrictInt

from src.events.routes import NothingIn
from src.memory.dream import DreamSettings
from src.memory.graph import SHARED
from src.memory.ledger import Kind

SOURCE = "dream:"  # the source a dreamt fact is written with, followed by the conversation id
FACTS_MAX = 200
NEED_MODEL = "Choose a dreaming model first."
NOT_SET_UP = "Dreaming is not set up."
UNDER_WAY = "A dream is already under way."


class DreamSettingsIn(BaseModel):
    enabled: StrictBool
    idle_minutes: StrictInt  # the range is DreamSettings.checked's, answered with its sentence


class IdleIn(BaseModel):
    idle_s: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)


def dream_router(h) -> APIRouter:
    """`h` is the Harness; it is read only inside the routes."""
    r = APIRouter(prefix="/dream")

    def status() -> dict[str, Any]:
        choice = h.model_roles().dream
        return {**h.dreamer.status(), "model": choice.to_dict() if choice else None}

    @r.get("/status")
    async def get_status() -> dict[str, Any]:
        return status()

    @r.put("/settings")
    async def put_settings(body: DreamSettingsIn) -> dict[str, Any]:
        try:
            s = DreamSettings.checked(body.enabled, body.idle_minutes)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        if s.enabled and h.model_roles().dream is None:  # never filled from another role
            raise HTTPException(409, NEED_MODEL)
        h.dreamer.set_settings(s)
        h.ledger.append(Kind.settings, "", "", event="dream", enabled=s.enabled, idle_minutes=s.idle_minutes)
        return status()

    @r.post("/idle")
    async def post_idle(body: IdleIn) -> dict[str, bool]:
        h.dreamer.report_idle(body.idle_s)
        return {"ok": True}

    @r.post("/run")
    async def post_run(_: NothingIn) -> dict[str, Any]:
        """One pass now, the idle rule skipped. Always through `start`: `close` waits only for a pass it began."""
        if not h.dreamer.ready():
            raise HTTPException(409, NOT_SET_UP)
        if not h.dreamer.start(manual=True):
            raise HTTPException(409, UNDER_WAY)
        return status()

    @r.get("/facts")
    async def get_facts() -> list[dict[str, Any]]:
        dreamt = [f for f in h.memory.facts(SHARED) if f.source.startswith(SOURCE)]
        # newest first; facts written in the same second keep the later-written one first
        newest = sorted(reversed(dreamt), key=lambda f: f.at, reverse=True)[:FACTS_MAX]
        out = []
        for f in newest:
            conv_id = f.source[len(SOURCE):]
            conv = h.conversations.get(conv_id) if h.conversations is not None else None
            out.append({"id": f.id, "text": f.text, "at": f.at, "conversation": conv_id,
                        "title": conv.title if conv is not None else ""})
        return out

    return r

"""`POST /speech` (a reply read aloud: the Narrator rewrites it, the speech model speaks it, the answer is WAV) and
`PUT /settings/tools/speech` (the speech model, voice and switch). The POST takes a required JSON body, so another
site's page cannot send one without a preflight. No route returns or logs the text."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from src.generation.speech import DEFAULT_MODEL, VOICE_RE, SpeechConfig, SpeechError
from src.memory.ledger import Kind

TEXT_MAX = 20_000
log = logging.getLogger(__name__)


class SpeechIn(BaseModel):
    text: str  # its length is checked in the route: a validation error would echo the whole reply back


class SpeechToolIn(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    model: str | None = None  # "" goes back to the default model
    voice: str | None = None


def speech_providers(h) -> list[str]:
    """The provider ids that can speak: today, the OpenRouter ones."""
    return [e.id for e in h.provider_store.entries.values() if e.kind == "openrouter"] if h.provider_store else []


def narrator(h):
    """The narrating bot: the one with id `narrator`, else the first that narrates, else None."""
    bots = [b for b in h.bots.values() if getattr(b, "narrates", False)]
    return next((b for b in bots if b.id == "narrator"), bots[0] if bots else None)


def speech_router(h) -> APIRouter:
    """`h` is the Harness; it is read only inside the routes."""
    r = APIRouter()

    @r.post("/speech")
    async def speak(body: SpeechIn) -> Response:
        if not body.text.strip():
            raise HTTPException(422, "There is nothing to read.")
        if len(body.text) > TEXT_MAX:
            raise HTTPException(422, "That reply is too long to read aloud.")
        if h.speech is None:
            raise HTTPException(503, "Reading aloud is not available on this harness.")
        try:
            wav = await h.speech.speak(body.text, narrator(h), h._model_for)
        except SpeechError as e:
            raise HTTPException(e.status, str(e)) from None
        except Exception as e:  # the reply is in this call's frames: no traceback leaves for the error tracker
            log.warning("reading aloud failed: %s", type(e).__name__)
            raise HTTPException(502, "Reading aloud failed. Try again in a moment.") from None
        return Response(wav, media_type="audio/wav", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @r.put("/settings/tools/speech")
    async def set_speech_tool(body: SpeechToolIn) -> dict[str, Any]:
        if h.speech is None:
            raise HTTPException(503, "Reading aloud is not available on this harness.")
        cur = h.speech.config
        provider = cur.provider if body.provider is None else body.provider.strip()
        model = cur.model if body.model is None else (body.model.strip() or DEFAULT_MODEL)
        voice = cur.voice if body.voice is None else body.voice.strip()
        if not VOICE_RE.match(voice):
            raise HTTPException(422, "A voice is a single name of letters, digits, - or _, such as Zephyr.")
        if body.provider is not None or body.model is not None:  # a new choice must be an enabled speech model
            known = speech_providers(h)
            if provider not in known:
                raise HTTPException(422, f"{provider!r} is not a provider that speaks; known: {known}")
            entry = h.provider_store.entries[provider]
            m = entry.model(model)
            if m is None or "tts" not in m.kinds:
                raise HTTPException(422, f"{model} is not an enabled speech model on {entry.name}; "
                                         "enable it on the Model providers page")
        new = SpeechConfig(provider=provider, model=model, voice=voice,
                           enabled=cur.enabled if body.enabled is None else body.enabled)
        new.save(h.config.vault_root)
        h.speech.reconfigure(new)
        h.ledger.append(Kind.settings, "", "", event="tool", tool="speech", enabled=new.enabled, provider=new.provider,
                        model=new.model, voice=new.voice)
        return h.speech.status(speech_providers(h))

    return r

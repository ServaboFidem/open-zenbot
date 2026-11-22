"""`POST /speech` (a reply read aloud: the Narrator rewrites it, the speech model or Kokoro speaks it; the answer is a
stream of length-prefixed WAV frames), `PUT /settings/tools/speech` (the speech model, voice and switch) and the routes
for the voice on this computer (download, remove, sample). Every POST takes a required JSON body, so another site's
page cannot send one without a preflight. No route returns or logs the text."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from src.events.routes import NothingIn
from src.generation.speech import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    LOCAL,
    PICK_A_VOICE,
    VOICE_RE,
    Reading,
    Speech,
    SpeechConfig,
    SpeechError,
)
from src.kokoro.engine import DEFAULT_VOICE as KOKORO_VOICE
from src.kokoro.engine import MODEL as KOKORO_MODEL
from src.kokoro.engine import voice as kokoro_voice
from src.memory.ledger import Kind
from src.providers.interface import failure_kind

TEXT_MAX = 20_000
MEDIA = "application/x-zenbot-speech"
FRAME_MAX = 64_000_000
END = b"\x00\x00\x00\x00"
READ_FAILED = "Reading aloud failed. Try again in a moment."
UNAVAILABLE = "Reading aloud is not available on this harness."
log = logging.getLogger(__name__)


class SpeechIn(BaseModel):
    text: str  # its length is checked in the route: a validation error would echo the whole reply back


class SampleIn(BaseModel):
    voice: str = Field(max_length=40)


class SpeechToolIn(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    model: str | None = None  # "" goes back to the default model
    voice: str | None = None


def frame(wav: bytes) -> bytes:
    """One WAV as a frame: a 4-byte big-endian length, then the bytes."""
    if not wav or len(wav) > FRAME_MAX:
        raise ValueError("a speech frame is 1 to FRAME_MAX bytes")
    return len(wav).to_bytes(4, "big") + wav


async def frames(speech: Speech, reading: Reading) -> AsyncIterator[bytes]:
    """The body of a reading: a frame per piece, then the end mark. The reading's one ledger entry is written here,
    once, whichever way it ends. A failure part-way ends the body without the end mark."""
    pieces = reading.pieces()
    try:
        async for wav in pieces:
            yield frame(wav)
        yield END
    except (asyncio.CancelledError, GeneratorExit):  # the app closed the connection
        speech.finish(reading, stopped=True)
        raise
    except Exception as e:  # the text is in this call's frames: the class name only
        log.warning("reading aloud failed part-way: %s", type(e).__name__)
        speech.finish(reading, error=failure_kind(e) or "other")
        return
    else:
        speech.finish(reading)
    finally:
        await pieces.aclose()


class SpeechResponse(StreamingResponse):
    """The framed body. Starlette never closes a body its client dropped, so the reading is finished here, whatever
    way the response ends: `finish` is idempotent, and closing the body runs its cleanup if it had started."""

    def __init__(self, speech: Speech, reading: Reading, **kwargs: Any) -> None:
        self._speech, self._reading = speech, reading
        super().__init__(frames(speech, reading), **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._speech.finish(self._reading, stopped=True)
            await self.body_iterator.aclose()  # a started body runs its own cleanup; an unstarted one has none


def speech_providers(h) -> list[str]:
    """The provider ids that can speak: Kokoro (on this computer), then the OpenRouter ones."""
    store = [e.id for e in h.provider_store.entries.values() if e.kind == "openrouter"] if h.provider_store else []
    return [LOCAL, *store]


def narrator(h):
    """The narrating bot: the one with id `narrator`, else the first that narrates, else None."""
    bots = [b for b in h.bots.values() if getattr(b, "narrates", False)]
    return next((b for b in bots if b.id == "narrator"), bots[0] if bots else None)


def speech_router(h) -> APIRouter:
    """`h` is the Harness; it is read only inside the routes."""
    r = APIRouter()

    def local_model():
        if h.speech is None or h.speech.local_model is None:
            raise HTTPException(503, UNAVAILABLE)
        return h.speech.local_model

    @r.post("/speech")
    async def speak(body: SpeechIn) -> Response:
        if not body.text.strip():
            raise HTTPException(422, "There is nothing to read.")
        if len(body.text) > TEXT_MAX:
            raise HTTPException(422, "That reply is too long to read aloud.")
        if h.speech is None:
            raise HTTPException(503, UNAVAILABLE)
        try:
            reading = await h.speech.read(body.text, narrator(h), h._model_for)
        except SpeechError as e:
            raise HTTPException(e.status, str(e)) from None
        except Exception as e:  # the reply is in this call's frames: no traceback leaves for the error tracker
            log.warning("reading aloud failed: %s", type(e).__name__)
            raise HTTPException(502, READ_FAILED) from None
        return SpeechResponse(h.speech, reading, media_type=MEDIA,
                              headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @r.put("/settings/tools/speech")
    async def set_speech_tool(body: SpeechToolIn) -> dict[str, Any]:
        if h.speech is None:
            raise HTTPException(503, UNAVAILABLE)
        cur = h.speech.config
        provider = cur.provider if body.provider is None else body.provider.strip()
        model = cur.model if body.model is None else (body.model.strip() or DEFAULT_MODEL)
        voice = cur.voice if body.voice is None else body.voice.strip()
        if provider == LOCAL:
            model = KOKORO_MODEL
            if body.voice is None and kokoro_voice(voice) is None:  # coming from a provider's voice
                voice = KOKORO_VOICE
            if kokoro_voice(voice) is None:
                raise HTTPException(422, PICK_A_VOICE)
        else:
            if cur.provider == LOCAL:  # leaving Kokoro: its model and voice mean nothing to a provider
                if body.model is None:
                    model = DEFAULT_MODEL
                if body.voice is None and kokoro_voice(voice) is not None:
                    voice = DEFAULT_VOICE
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

    @r.post("/speech/local/download")
    async def download_local(_: NothingIn) -> dict[str, Any]:
        model = local_model()
        if model.status()["state"] == "downloading":
            raise HTTPException(409, "Already downloading.")
        model.start_download()  # False only when the files are already in place
        return h.speech.status(speech_providers(h))

    @r.delete("/speech/local/model")
    async def remove_local() -> dict[str, Any]:
        model = local_model()
        if model.status()["state"] == "downloading":
            raise HTTPException(409, "Wait for the download to finish.")
        if h.speech.local is not None:
            await h.speech.local.unload()  # waits for a synthesis in flight; the files are then not in use
        if not await model.remove():
            raise HTTPException(409, "The voice could not be removed. Close anything using it and try again.")
        return h.speech.status(speech_providers(h))

    @r.post("/speech/local/sample")
    async def sample(body: SampleIn) -> Response:
        if h.speech is None:
            raise HTTPException(503, UNAVAILABLE)
        try:
            wav = await h.speech.sample(body.voice)
        except SpeechError as e:
            raise HTTPException(e.status, str(e)) from None
        except Exception as e:  # tracing may hold the sentence in these frames: the class name only
            log.warning("sample failed: %s", type(e).__name__)
            raise HTTPException(502, READ_FAILED) from None
        return Response(wav, media_type="audio/wav", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    return r

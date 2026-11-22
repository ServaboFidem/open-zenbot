"""The Kokoro engine: text to 16-bit mono PCM, on this computer, one synthesis at a time.

This is the only module that imports `kokoro_onnx` or `onnxruntime`, and only inside `real_load`, so the harness
starts without them. The model is loaded on first use and dropped after `IDLE_S` seconds without a call. A synthesis
runs in a thread under one lock; a thread cannot be cancelled, so a cancelled call waits for it to finish.
Errors from the library are raised as `EngineFailed` naming the exception's class only: the frames may hold text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from src.kokoro.model import FILES, KokoroModel
from src.providers.interface import ProviderError

log = logging.getLogger(__name__)

RATE = 24000
IDLE_S = 600.0
MODEL = "kokoro-v1.0"
DEFAULT_VOICE = "af_heart"


@dataclass(frozen=True)
class Voice:
    name: str
    label: str
    accent: str  # "American" | "British"
    gender: str  # "female" | "male"
    lang: str  # "en-us" | "en-gb"


def _voices() -> tuple[Voice, ...]:
    names = {
        ("American", "female"): "af_heart af_alloy af_aoede af_bella af_jessica af_kore af_nicole af_nova af_river "
                                "af_sarah af_sky",
        ("American", "male"): "am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck am_santa",
        ("British", "female"): "bf_alice bf_emma bf_isabella bf_lily",
        ("British", "male"): "bm_daniel bm_fable bm_george bm_lewis",
    }
    out = []
    for (accent, gender), group in names.items():
        lang = "en-us" if accent == "American" else "en-gb"
        out.extend(Voice(n, n.split("_", 1)[1].capitalize(), accent, gender, lang) for n in group.split())
    return tuple(out)


VOICES: tuple[Voice, ...] = _voices()
_BY_NAME = {v.name: v for v in VOICES}


def voice(name: str) -> Voice | None:
    return _BY_NAME.get(name)


def voices_view() -> list[dict[str, str]]:
    return [{"name": v.name, "label": v.label, "accent": v.accent, "gender": v.gender} for v in VOICES]


def clean(text: str) -> str:
    """Characters of Unicode category C and U+2028 and U+2029 out (a line break or tab counts as a space), space
    collapsed, ends stripped."""
    out = []
    for ch in text:
        if ch in "  ":
            continue
        if ch.isspace():
            out.append(" ")
        elif unicodedata.category(ch)[0] != "C":
            out.append(ch)
    return " ".join("".join(out).split())


def real_load(model_path: Path, voices_path: Path) -> Any:
    """The one place the voice library and onnxruntime are imported. CPU only."""
    import onnxruntime
    from kokoro_onnx import Kokoro

    session = onnxruntime.InferenceSession(
        str(model_path), sess_options=onnxruntime.SessionOptions(), providers=["CPUExecutionProvider"])
    return Kokoro.from_session(session, str(voices_path))


class NotDownloaded(ProviderError):
    """The model files are missing or failed their hash."""


class EngineFailed(ProviderError):
    """The library would not import or load, or a synthesis failed. The message is the exception's class name."""


class KokoroEngine:
    def __init__(self, model: KokoroModel, *, load: Callable[[Path, Path], Any] | None = None,
                 idle_s: float = IDLE_S) -> None:
        self.model = model
        # None means the module's real_load, looked up at load time (so a test can swap it)
        self._load = load
        self.idle_s = idle_s
        self._lib: Any = None
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task[None] | None = None

    @property
    def loaded(self) -> bool:
        return self._lib is not None

    async def ensure(self) -> None:
        async with self._lock:
            await self._ensure_locked()

    async def _ensure_locked(self) -> None:
        if not self.model.ready():
            self._drop()
            raise NotDownloaded("The voice for this computer is not downloaded.")
        if self._lib is not None:
            return
        try:
            verified = await self.model.verify()
        except Exception as e:  # noqa: BLE001 - an OSError's message holds a disk path
            log.warning("kokoro verify failed: %s", type(e).__name__)
            raise EngineFailed(type(e).__name__) from None
        if not verified:
            raise NotDownloaded("The voice for this computer is not downloaded.")
        load = self._load or real_load
        try:
            self._lib = await self._in_thread(load, self.model.path(FILES[0].name), self.model.path(FILES[1].name))
        except Exception as e:  # noqa: BLE001 - class name only, the frames may hold more
            log.warning("kokoro load failed: %s", type(e).__name__)
            raise EngineFailed(type(e).__name__) from None
        self._arm()

    async def speak(self, text: str, voice: str) -> bytes:
        v = _BY_NAME.get(voice)
        if v is None:
            raise ValueError("Unknown voice.")
        text = clean(text)
        if not text:
            return b""
        async with self._lock:
            await self._ensure_locked()
            lib = self._lib
            try:
                try:
                    samples, rate = await self._in_thread(self._create, lib, text, v)
                except Exception as e:  # noqa: BLE001
                    log.warning("kokoro synthesis failed: %s", type(e).__name__)
                    raise EngineFailed(type(e).__name__) from None
                if rate != RATE:
                    raise EngineFailed("RateMismatch")
            finally:
                if self._lib is not None:
                    self._arm()  # a call, even a failed one, starts the idle count again
        pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        return (pcm * 32767.0).astype("<i2").tobytes()

    @staticmethod
    def _create(lib: Any, text: str, v: Voice) -> Any:
        return lib.create(text, voice=v.name, speed=1.0, lang=v.lang)

    @staticmethod
    async def _in_thread(fn: Callable[..., Any], *args: Any) -> Any:
        """Run in a thread. A cancelled caller still waits for the thread to end: it cannot be stopped."""
        fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        try:
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            if not fut.done():
                await asyncio.wait([fut])
            if fut.done() and not fut.cancelled():
                fut.exception()  # retrieved: nobody else will read it
            raise

    def _arm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.create_task(self._idle())

    async def _idle(self) -> None:
        await asyncio.sleep(self.idle_s)
        await self.unload()

    def _drop(self) -> None:
        self._lib = None

    async def unload(self) -> None:
        async with self._lock:  # a synthesis in flight holds it
            self._drop()

    async def close(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer
        await self.unload()

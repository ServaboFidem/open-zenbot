"""The Kokoro engine: text to 16-bit mono PCM, on this computer, one synthesis at a time.

This is the only module that imports `kokoro_onnx` or `onnxruntime`, and only inside `real_load`, so the harness
starts without them (numpy too is imported only when speech is made). The model is loaded on first use and dropped
after `IDLE_S` seconds without a call. A load or a synthesis runs in a thread inside a job, a task of its own that
holds the one lock until the thread has ended; a cancelled caller stops waiting at once, but the thread cannot be
stopped, so the lock stays held until it ends and its result is thrown away. Errors from the library are raised as
`EngineFailed` naming the exception's class only: its message may hold the text.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import logging
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from src.kokoro.model import FILES, KokoroModel
from src.providers.interface import ProviderError

log = logging.getLogger(__name__)
T = TypeVar("T")

LIBRARY_LOGGER = "kokoro_onnx"
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


def _quiet_library_log() -> None:
    """kokoro_onnx sets its logger from the generic LOG_LEVEL variable, and at DEBUG it writes the phonemes of the
    spoken text to stderr: hold it at WARNING (its warnings carry no text). Never fails the load."""
    with contextlib.suppress(Exception):
        logging.getLogger(LIBRARY_LOGGER).setLevel(logging.WARNING)


def real_load(model_path: Path, voices_path: Path) -> Any:
    """The one place the voice library and onnxruntime are imported. CPU only."""
    import onnxruntime
    from kokoro_onnx import Kokoro

    _quiet_library_log()
    session = onnxruntime.InferenceSession(
        str(model_path), sess_options=onnxruntime.SessionOptions(), providers=["CPUExecutionProvider"])
    return Kokoro.from_session(session, str(voices_path))


class NotDownloaded(ProviderError):
    """The model files are missing or failed their hash."""


class EngineFailed(ProviderError):
    """The library would not import or load, or a synthesis failed. The message is the exception's class name."""


@dataclass
class _Job:
    """One call's work on the engine. `started`: it holds the lock, so it runs to its end whatever happens to its
    caller. `abandoned`: the caller went away, so nothing more is started for it."""
    started: bool = False
    abandoned: bool = False


class KokoroEngine:
    """Each call's work (a load, a synthesis) runs in a job: a task of its own that holds the lock from before its
    thread starts until after it ends. The caller only awaits the job through a shield, so a caller cancelled by the
    route (an anyio cancel scope cancels again on every loop turn) never releases the lock under a running thread."""

    def __init__(self, model: KokoroModel, *, load: Callable[[Path, Path], Any] | None = None,
                 idle_s: float = IDLE_S) -> None:
        self.model = model
        # None means the module's real_load, looked up at load time (so a test can swap it)
        self._load = load
        self.idle_s = idle_s
        self._lib: Any = None
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task[None] | None = None
        self._jobs: dict[asyncio.Task[Any], _Job] = {}  # strong references: the loop keeps only weak ones
        self._closed = False

    @property
    def loaded(self) -> bool:
        return self._lib is not None

    async def ensure(self) -> None:
        await self._run(lambda job: self._ensure_locked())

    async def speak(self, text: str, voice: str) -> bytes:
        v = _BY_NAME.get(voice)
        if v is None:
            raise ValueError("Unknown voice.")
        text = clean(text)
        if not text:
            return b""
        samples = await self._run(lambda job: self._speak_locked(job, text, v))
        import numpy as np  # here, not at module level: a harness that never speaks locally never imports it

        pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        return (pcm * 32767.0).astype("<i2").tobytes()

    async def _run(self, work: Callable[[_Job], Awaitable[T]]) -> T:
        job = _Job()
        task = asyncio.create_task(self._locked(job, work))
        self._jobs[task] = job
        task.add_done_callback(self._job_done)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # No await from here on: an anyio scope would cancel it at once. A job holding the lock finishes and
            # releases it itself; one still waiting for the lock is cancelled before it starts anything.
            job.abandoned = True
            if not job.started:
                task.cancel()
            raise

    async def _locked(self, job: _Job, work: Callable[[_Job], Awaitable[T]]) -> T:
        async with self._lock:
            job.started = True
            return await work(job)

    def _job_done(self, task: asyncio.Task[Any]) -> None:
        self._jobs.pop(task, None)
        if not task.cancelled():
            task.exception()  # read here, so a job whose caller went away leaves nothing for the loop's handler

    async def _ensure_locked(self) -> None:
        if not self.model.ready():
            self._drop()
            raise NotDownloaded("The voice for this computer is not downloaded.")
        if self._lib is not None:
            return
        failed = ""
        try:
            verified = await self.model.verify()
        except Exception as e:  # noqa: BLE001 - an OSError's message holds a disk path
            failed = type(e).__name__
        if failed:  # raised outside the handler, so the error carries no reference to the original
            log.warning("kokoro verify failed: %s", failed)
            raise EngineFailed(failed)
        if not verified:
            raise NotDownloaded("The voice for this computer is not downloaded.")
        load = self._load or real_load
        lib, failed = await self._thread(load, self.model.path(FILES[0].name), self.model.path(FILES[1].name))
        if failed:
            log.warning("kokoro load failed: %s", failed)
            raise EngineFailed(failed)
        self._lib = lib  # kept even when the caller went away during the load: the next call needs no second load
        self._arm()

    async def _speak_locked(self, job: _Job, text: str, v: Voice) -> Any:
        try:
            await self._ensure_locked()
            if job.abandoned:  # the caller went away during the load: no synthesis for nobody
                return None
            result, failed = await self._thread(self._create, self._lib, text, v)
            if failed:
                log.warning("kokoro synthesis failed: %s", failed)
                raise EngineFailed(failed)
            samples, rate = result
            if rate != RATE:
                raise EngineFailed("RateMismatch")
            return samples
        finally:
            if self._lib is not None:
                self._arm()  # a call, even a failed or abandoned one, starts the idle count again

    @staticmethod
    def _create(lib: Any, text: str, v: Voice) -> tuple[Any, Any]:
        samples, rate = lib.create(text, voice=v.name, speed=1.0, lang=v.lang)
        return samples, rate

    @staticmethod
    async def _thread(fn: Callable[..., Any], *args: Any) -> tuple[Any, str]:
        """Run `fn` in a thread and wait for it to end, through any cancel of this task: a thread cannot be stopped,
        and the lock must stay held while it runs. Answers (result, "") or (None, the exception's class name): the
        exception itself goes no further, its message may hold the text."""
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, functools.partial(contextvars.copy_context().run, fn, *args))
        cancelled = False
        while not fut.done():
            try:
                await asyncio.wait([fut])
            except asyncio.CancelledError:
                cancelled = True
        error = None if fut.cancelled() else fut.exception()  # read, so it is never reported as unretrieved
        if cancelled or fut.cancelled():
            raise asyncio.CancelledError
        if error is None:
            return fut.result(), ""
        if not isinstance(error, Exception):  # KeyboardInterrupt, a test's fail(): not the library's, passed on
            raise error
        return None, type(error).__name__

    def _arm(self) -> None:
        if self._closed:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.create_task(self._idle())

    async def _idle(self) -> None:
        await asyncio.sleep(self.idle_s)
        await self.unload()

    def _drop(self) -> None:
        self._lib = None

    async def unload(self) -> None:
        async with self._lock:  # a job in flight holds it until its thread has ended
            self._drop()

    async def close(self) -> None:
        """Waits for the one job holding the lock, if any; jobs still waiting for it are cancelled."""
        self._closed = True
        for task, job in list(self._jobs.items()):
            if not job.started:
                task.cancel()
        timer, self._timer = self._timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer
        await self.unload()

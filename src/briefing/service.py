"""The spoken briefing's runner: it decides when each slot is due, claims it, builds the words, speaks them and has the
Sonos speakers play them, and records what happened.

A slot is claimed (saved to `briefing_state.json`) before anything is built or played, so it never plays twice, across
a restart too. Building starts `PREP_LEAD` before the slot and the clip plays at the slot; a briefing that cannot start
within `LATE_LIMIT` of its slot is skipped. Ledger entries hold counts, outcomes and reason codes only: never the text,
the joke, a speaker's name or a title. Log lines carry class names only."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.briefing.settings import KINDS, Briefing, BriefingState, BriefingStore, ToldJokes, next_slot
from src.generation.speech import SpeechError
from src.memory.ledger import Kind
from src.sonos import tools as sonos_tools

log = logging.getLogger(__name__)

PREP_LEAD = timedelta(minutes=5)
LATE_LIMIT = timedelta(minutes=15)
INTERVAL_S = 30
WAIT_STEP_S = 2.0  # while waiting for the slot, the settings are read again this often

BUSY = "A briefing is already playing."
BUILD_FAILED = "The briefing could not be written."
SPEAK_FAILED = "The voice could not be made."
PLAY_FAILED = "The speakers could not play the briefing."

# the announcement's refusals, as reason codes for the ledger (the sentences may name a speaker)
_PLAY_CODES = {
    sonos_tools.NO_SPEAKER: "no_speaker",
    sonos_tools.CUT_OFF: "cut_off",
    sonos_tools.NOT_FETCHED: "not_fetched",
    sonos_tools.NOT_SERVED: "not_served",
    sonos_tools.UNCONTROLLED: "uncontrolled",
    sonos_tools.NOT_WAV: "not_wav",
    sonos_tools.OFF: "sonos_off",
}


class Busy(Exception):
    def __init__(self) -> None:
        super().__init__(BUSY)


class NotReady(Exception):
    """Why a briefing cannot play now: `sonos_off`, `no_speakers`, `no_voice`, `ceiling_zero` or `no_briefing`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now() -> datetime:
    return datetime.now().astimezone()


def _play_code(sentence: str) -> str:
    if sentence in _PLAY_CODES:
        return _PLAY_CODES[sentence]
    return "lead" if sentence.endswith(" did not answer.") else "other"


class BriefingService:
    def __init__(self, vault: Path, ledger: Any, *,
                 build: Callable[[datetime, ToldJokes], Awaitable[Any]],
                 say: Callable[[str], Awaitable[bytes]],
                 announce: Callable[[list[str] | None, bytes, int], Awaitable[Any]],
                 sonos: Any,
                 speech_ready: Callable[[], bool],
                 hosted: Callable[[], str | None] = lambda: None,
                 clock: Callable[[], datetime] = _now,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.vault = Path(vault)
        self.ledger = ledger
        self._build, self._say, self._announce = build, say, announce
        self.sonos = sonos
        self._speech_ready, self._hosted = speech_ready, hosted
        self._clock, self._sleep = clock, sleep
        self.state = BriefingState.load(self.vault)
        self._running: str | None = None  # the kind building or playing; one briefing at a time
        self._phase = "idle"  # building | playing while one runs
        self._token: object | None = None  # the run that holds the busy flag; only it may clear it
        self._tasks: set[asyncio.Task] = set()  # Play now runs started in the background

    # -- readiness and status ----------------------------------------------------------------------------------

    def _sonos_reason(self) -> str | None:
        s = self.sonos.settings
        if not s.enabled:
            return "sonos_off"
        if not self.sonos.rooms:
            return "no_speakers"
        if s.max_volume <= 0:
            return "ceiling_zero"
        return None

    def readiness(self) -> str | None:
        s = self.sonos.settings
        if not s.enabled:
            return "sonos_off"
        if not self.sonos.rooms:
            return "no_speakers"
        try:
            voice = bool(self._speech_ready())
        except Exception as e:  # noqa: BLE001
            log.warning("briefing voice check failed: %s", type(e).__name__)
            voice = False
        if not voice:
            return "no_voice"
        if s.max_volume <= 0:
            return "ceiling_zero"
        return None

    @staticmethod
    def _when(raw: str | None) -> datetime | None:
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(raw).astimezone()
        except ValueError:
            return None

    def _claimed(self, kind: str) -> datetime | None:
        return self._when(self.state.claimed(kind))

    def _plan(self, kind: str, b: Briefing, now: datetime) -> tuple[datetime | None, datetime]:
        """`(missed, slot)`: the latest slot already more than `LATE_LIMIT` old that the loop has yet to record
        (None when there is none), and the slot it takes next. The one rule for the loop and for the status.

        Slots count from the later of the claimed slot and the last settings save (`since`); with neither, from
        `now - LATE_LIMIT`. Only a save moves `since`, so a gap while the harness was off is still a late skip, but
        an edit, a switch-on or a switch-off is not. One briefing a day: never a slot on the claimed slot's date."""
        claimed = self._claimed(kind)
        marks = [m for m in (claimed, self._when(self.state.since(kind))) if m is not None]
        slot = next_slot(b, max(marks) if marks else now - LATE_LIMIT)
        if claimed is not None:
            while slot.date() == claimed.date():
                slot = next_slot(b, slot)
        missed = None
        while now > slot + LATE_LIMIT:
            missed, slot = slot, next_slot(b, slot)
        return missed, slot

    def settings_saved(self, kind: str) -> None:
        """Called after every save of a briefing's settings (an edit, switching it on or off): no slot before now
        is due any more. Records no run or skip."""
        now = self._clock()
        marks = [m for m in (self._claimed(kind), self._when(self.state.since(kind))) if m is not None]
        self.state.set_since(kind, max([now, *marks]).isoformat())
        try:
            self.state.save(self.vault)
        except Exception as e:  # noqa: BLE001
            log.error("briefing state not saved: %s", type(e).__name__)

    def status(self) -> dict[str, Any]:
        store = BriefingStore.load(self.vault)
        now = self._clock()
        reason = self.readiness()
        items = []
        for kind, b in store.items.items():
            d = asdict(b)
            d.update(next_run=self._plan(kind, b, now)[1].isoformat() if b.enabled else None,
                     last_run=self.state.last_run(kind), last_skip=self.state.last_skip(kind),
                     state=self._phase if self._running == kind else "idle",
                     ready=reason is None, reason=reason)
            items.append(d)
        try:
            hosted = self._hosted()
        except Exception as e:  # noqa: BLE001
            log.warning("briefing hosted check failed: %s", type(e).__name__)
            hosted = None
        return {"briefings": items, "ready": reason is None, "reason": reason, "ceiling": self.sonos.settings.max_volume,
                "rooms": [r.name for r in self.sonos.rooms], "hosted": hosted, "error": store.error}

    # -- the loop ----------------------------------------------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001 - the loop outlives any failure; the text may be in the frames
                log.warning("briefing pass failed: %s", type(e).__name__)
            await self._sleep(INTERVAL_S)

    async def tick(self) -> None:
        if self._running:  # Play now is under way; a slot waiting is taken on a later pass
            return
        store = BriefingStore.load(self.vault)
        for kind, b in list(store.items.items()):
            if b.enabled:
                await self._tick_one(kind, b)

    async def _tick_one(self, kind: str, b: Briefing) -> None:
        now = self._clock()
        missed, slot = self._plan(kind, b, now)
        if missed is not None:  # first seen too late (the harness was off, the machine asleep): only the latest
            if self._claim(kind, missed):
                self._skip(kind, missed, "skipped_late", "late")
            return
        if now < slot - PREP_LEAD:
            return
        reason = self.readiness()
        if reason is not None:
            if now < slot:  # a speaker look or the voice may still come in time
                return
            if self._claim(kind, slot):
                self._skip(kind, slot, "skipped", reason)
            return
        if not self._claim(kind, slot):
            return
        await self._run(kind, b, slot)

    def _claim(self, kind: str, slot: datetime) -> bool:
        """Saved before anything is built: a claim that cannot be saved plays nothing (it stays claimed in memory)."""
        self.state.claim(kind, slot.isoformat())
        try:
            self.state.save(self.vault)
            return True
        except Exception as e:  # noqa: BLE001
            log.error("briefing claim not saved: %s", type(e).__name__)
            return False

    # -- Play now ----------------------------------------------------------------------------------------------

    def start_play_now(self, kind: str) -> asyncio.Task:
        """Build and play at once, in the background. Claims no slot. Raises Busy while one builds or plays, else
        NotReady(reason), before anything starts; the service is busy from the moment this returns."""
        if self._running:
            raise Busy()
        b = BriefingStore.load(self.vault).items.get(kind) if kind in KINDS else None
        if b is None:
            raise NotReady("no_briefing")
        reason = self.readiness()
        if reason is not None:
            raise NotReady(reason)
        token = self._begin(kind)
        task = asyncio.get_running_loop().create_task(self._run(kind, b, None, token))
        self._tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            self._end(token)  # a task cancelled before it ever ran never reached _run's finally
            if not t.cancelled() and t.exception() is not None:
                log.warning("briefing play now failed: %s", type(t.exception()).__name__)

        task.add_done_callback(done)
        return task

    async def play_now(self, kind: str) -> dict[str, Any]:
        """`start_play_now`, awaited."""
        return await self.start_play_now(kind)

    async def close(self) -> None:
        """Harness shutdown: a Play now under way is cancelled (announce puts the speakers back)."""
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- one run -----------------------------------------------------------------------------------------------

    def _begin(self, kind: str) -> object:
        token = object()
        self._running, self._phase, self._token = kind, "building", token
        return token

    def _end(self, token: object) -> None:
        if self._token is token:
            self._running, self._phase, self._token = None, "idle", None

    async def _run(self, kind: str, b: Briefing, slot: datetime | None,
                   token: object | None = None) -> dict[str, Any] | None:
        token = token or self._begin(kind)
        try:
            return await self._attempt(kind, b, slot)
        except Exception as e:  # noqa: BLE001 - whatever failed, the run is recorded
            log.warning("briefing run failed: %s", type(e).__name__)
            return self._fail(kind, slot, "run", type(e).__name__, PLAY_FAILED)
        finally:
            self._end(token)

    async def _attempt(self, kind: str, b: Briefing, slot: datetime | None) -> dict[str, Any] | None:
        when = slot or self._clock()
        jokes = ToldJokes.load(self.vault)
        try:
            built = await self._build(when, jokes)
            if not isinstance(built.text, str) or not built.text.strip():
                raise ValueError("empty")
        except Exception as e:  # noqa: BLE001
            log.warning("briefing build failed: %s", type(e).__name__)
            return self._fail(kind, slot, "build", type(e).__name__, BUILD_FAILED)
        try:
            wav = await self._say(built.text)
        except SpeechError as e:
            return self._fail(kind, slot, "speak", f"speech_{e.status}", str(e))
        except Exception as e:  # noqa: BLE001
            log.warning("briefing speech failed: %s", type(e).__name__)
            return self._fail(kind, slot, "speak", type(e).__name__, SPEAK_FAILED)

        if slot is not None:
            fresh = await self._wait_for(kind, slot)
            if fresh is None:  # switched off, removed or its slot changed while it was made: nothing plays
                return None
            b = fresh
            if self._clock() > slot + LATE_LIMIT:
                return self._skip(kind, slot, "skipped_late", "late")
        reason = self._sonos_reason()
        if reason is not None:
            return self._skip(kind, slot, "skipped", reason)

        self._phase = "playing"
        volume = min(b.volume, self.sonos.settings.max_volume)  # never above the ceiling as it is now
        try:
            a = await self._announce(b.speakers, wav, volume)
        except ValueError as e:
            return self._fail(kind, slot, "play", _play_code(str(e)), str(e))
        except Exception as e:  # noqa: BLE001
            log.warning("briefing play failed: %s", type(e).__name__)
            return self._fail(kind, slot, "play", type(e).__name__, PLAY_FAILED)

        played = list(a.played)
        if played and built.joke:
            try:
                jokes.add(built.joke, when.date().isoformat())
                jokes.save(self.vault)
            except Exception as e:  # noqa: BLE001
                log.warning("briefing jokes not saved: %s", type(e).__name__)
        record = self._record(kind, slot, "played" if played else "failed", played=len(played),
                              failed=dict(a.failed), phone_paused=list(a.phone_paused),
                              reason="" if played else sonos_tools.NO_SPEAKER, sections=list(built.sections))
        self._note(event="played" if played else "failed", briefing=kind, trigger=self._trigger(slot),
                   played=len(played), failed=len(a.failed), phone_paused=len(a.phone_paused),
                   seconds=a.seconds, sections=list(built.sections), chars=len(built.text),
                   **({} if played else {"stage": "play", "error": "no_speaker"}))
        return record

    async def _wait_for(self, kind: str, slot: datetime) -> Briefing | None:
        """Wait until the slot, reading the settings again as it waits. None when the briefing was switched off,
        removed or no longer has this slot."""
        while True:
            b = BriefingStore.load(self.vault).items.get(kind)
            if b is None or not b.enabled or next_slot(b, slot - timedelta(seconds=1)) != slot:
                log.info("briefing dropped: its settings changed while it was made")
                return None
            left = (slot - self._clock()).total_seconds()
            if left <= 0:
                return b
            await self._sleep(min(WAIT_STEP_S, left))

    # -- records -----------------------------------------------------------------------------------------------

    @staticmethod
    def _trigger(slot: datetime | None) -> str:
        return "slot" if slot is not None else "now"

    def _record(self, kind: str, slot: datetime | None, outcome: str, *, played: int = 0,
                failed: dict[str, str] | None = None, phone_paused: list[str] | None = None, reason: str = "",
                **extra: Any) -> dict[str, Any]:
        at = self._clock().isoformat()
        run = {"at": at, "slot": slot.isoformat() if slot is not None else None, "outcome": outcome,
               "played": played, "failed": failed or {}, "phone_paused": phone_paused or [], "reason": reason,
               **extra}
        self.state.record_run(kind, run)
        if outcome.startswith("skipped"):
            self.state.record_skip(kind, {"at": at, "slot": run["slot"], "outcome": outcome, "reason": reason})
        try:
            self.state.save(self.vault)
        except Exception as e:  # noqa: BLE001
            log.error("briefing state not saved: %s", type(e).__name__)
        return run

    def _skip(self, kind: str, slot: datetime | None, outcome: str, reason: str) -> dict[str, Any]:
        record = self._record(kind, slot, outcome, reason=reason)
        self._note(event="skipped", briefing=kind, trigger=self._trigger(slot), outcome=outcome, reason=reason)
        return record

    def _fail(self, kind: str, slot: datetime | None, stage: str, code: str, sentence: str) -> dict[str, Any]:
        record = self._record(kind, slot, "failed", reason=sentence, stage=stage)
        self._note(event="failed", briefing=kind, trigger=self._trigger(slot), stage=stage, error=code)
        return record

    def _note(self, **data: Any) -> None:
        try:
            self.ledger.append(Kind.briefing, "", "", **data)
        except Exception as e:  # noqa: BLE001
            log.warning("briefing ledger entry not written: %s", type(e).__name__)

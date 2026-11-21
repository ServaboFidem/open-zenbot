"""The Sonos service: the speakers found, the look loop, settings and status.

A look tries the speakers last found first, then a multicast; the slower subnet scan runs only when the user asks
(Look again). While Sonos is on, a look runs at start and the loop looks every LOOK_EVERY_S seconds after. `on_change`
is called with whether any speaker is known after every look, switch-off and drop: the harness hangs the tools'
registration on it, so the `sonos` tools exist only while a speaker answers. Every SoCo call runs in a thread through
`call`."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.memory.ledger import Kind, Ledger
from src.sonos.devices import NowPlaying, Room, SocoSpeakers, Speakers, SpeakerError
from src.sonos.settings import SonosSettings, check_volume

log = logging.getLogger(__name__)

LOOK_EVERY_S = 600
DISCOVER_TIMEOUT_S = 5
CALL_TIMEOUT_S = 10
LOOK_SLACK_S = 30  # on top of the multicast wait: the speakers last found are asked directly first
SCAN_SLACK_S = 90  # a subnet scan asks every address on the network


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SonosService:
    def __init__(self, vault_root: Path, ledger: Ledger, speakers: Speakers | None = None) -> None:
        self.vault_root = vault_root
        self.ledger = ledger
        self.speakers: Speakers = speakers if speakers is not None else SocoSpeakers()
        self.settings = SonosSettings.load(vault_root)
        self.rooms: list[Room] = []
        self.last_look_at = ""
        self.error = ""
        self.on_change: Callable[[bool], None] | None = None
        self._looks = 0  # looks started and not yet ended
        self._gen = 0  # bumped when Sonos is switched on or off: a look from before drops its result
        self._lock = asyncio.Lock()
        self._loop_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def looking(self) -> bool:
        return self._looks > 0

    # calls into the speakers ------------------------------------------------

    async def call(self, fn: Callable[..., Any], *args: Any, timeout: float = CALL_TIMEOUT_S) -> Any:
        """Run a blocking speaker call in a thread; a call that outlasts `timeout` is a SpeakerError (network)."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout)
        except (asyncio.TimeoutError, TimeoutError):
            raise SpeakerError("network", "TimeoutError") from None

    # looking ------------------------------------------------------------------

    async def look(self, scan: bool = False) -> None:
        if not self.settings.enabled:
            return
        self._looks += 1
        await self._look(scan, self._gen)

    def start_look(self, scan: bool) -> bool:
        """Start a look in the background; False when Sonos is off or a look is under way."""
        if not self.settings.enabled or self.looking:
            return False
        self._looks += 1  # at once, so a second press before the task starts is refused
        self.spawn(self._look(scan, self._gen))
        return True

    async def _look(self, scan: bool, gen: int) -> None:
        """One look for switch generation `gen`; the caller has counted it in `_looks`."""
        try:
            async with self._lock:
                if gen != self._gen:
                    return  # switched off (or off and on) while it waited: a newer look is the one that counts
                try:
                    found = await self.call(self.speakers.discover, list(self.settings.known), scan,
                                            DISCOVER_TIMEOUT_S,
                                            timeout=DISCOVER_TIMEOUT_S + (SCAN_SLACK_S if scan else LOOK_SLACK_S))
                    error = "" if found else "none_found"
                except SpeakerError as e:
                    found, error = [], e.kind
                if gen != self._gen or not self.settings.enabled:
                    return  # switched off (or off and on) meanwhile: this result is stale
                self.rooms, self.error, self.last_look_at = list(found), error, _now()
                if found:
                    self.settings.known = [r.ip for r in found]
                    self.settings.save(self.vault_root)
                self.ledger.append(Kind.sonos, "", "", event="look", rooms=len(found), scan=scan, error=error)
                self._changed()
        finally:
            if gen == self._gen:  # a stale look was already uncounted when the switch changed
                self._looks -= 1

    def _new_generation(self) -> None:
        """Sonos was switched on or off: drop the looks under way, so none can bring back old speakers or hold up
        the next look (a cancelled look's thread may still finish its request, and its result is thrown away)."""
        self._gen += 1
        self._looks = 0
        for task in list(self._tasks):
            task.cancel()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(LOOK_EVERY_S)
            try:
                await self.look()
            except Exception as e:  # noqa: BLE001 - the loop outlives any one look
                log.warning("sonos look failed: %s", type(e).__name__)

    async def start(self) -> None:
        """A look now and the loop after it, while Sonos is on."""
        if not self.settings.enabled:
            return
        self.start_look(False)
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.ensure_future(self._loop())

    def _stop_loop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None

    # the speakers known ---------------------------------------------------------

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(bool(self.rooms))

    async def refresh(self) -> list[Room]:
        """The rooms as a live speaker reports them now. Speakers that do not answer are skipped; when none answers,
        every room is forgotten."""
        if not self.rooms:
            return []
        gen = self._gen
        for r in list(self.rooms):
            try:
                topology = await self.call(self.speakers.rooms, r.ip)
            except SpeakerError:
                continue
            if gen != self._gen or not self.settings.enabled:
                return []  # switched off (or off and on) while it asked: the answer must not bring speakers back
            if topology:
                self.rooms = list(topology)
                return list(self.rooms)
        if gen != self._gen or not self.settings.enabled:
            return []
        self.rooms = []
        self._changed()
        return []

    def drop(self, uid: str) -> None:
        """Forget a speaker that stopped answering."""
        self.rooms = [r for r in self.rooms if r.uid != uid]
        if not self.rooms:
            self._changed()

    # settings and status ---------------------------------------------------------

    async def set_settings(self, enabled: bool, max_volume: int) -> dict[str, Any]:
        max_volume = check_volume(max_volume)
        was = self.settings.enabled
        candidate = replace(self.settings, enabled=bool(enabled), max_volume=max_volume, known=list(self.settings.known))
        candidate.save(self.vault_root)  # a failed save changes nothing, so the same request can simply be sent again
        self.settings = candidate
        self.ledger.append(Kind.sonos, "", "", event="settings", enabled=self.settings.enabled, max_volume=max_volume)
        if was and not self.settings.enabled:
            self._new_generation()
            self._stop_loop()
            self.rooms, self.error = [], ""
            self._changed()
        elif not was and self.settings.enabled:
            self._new_generation()
            await self.start()
        return await self.status()

    async def status(self) -> dict[str, Any]:
        by_uid = {r.uid: r for r in self.rooms}
        playing: dict[str, NowPlaying] = {}
        for uid in dict.fromkeys(r.coordinator_uid for r in self.rooms):
            lead = by_uid.get(uid)
            if lead is None:
                continue
            try:
                playing[uid] = await self.call(self.speakers.now_playing, lead.ip)
            except SpeakerError:
                pass
        rooms = []
        for r in self.rooms:
            lead = by_uid.get(r.coordinator_uid, r)
            now = playing.get(r.coordinator_uid, NowPlaying("", "", ""))
            rooms.append({"name": r.name, "group": lead.name, "model": r.model, "state": now.state,
                          "title": now.title})
        return {**self.settings.public(), "rooms": rooms, "looking": self.looking,
                "last_look_at": self.last_look_at, "error": self.error}

    # background work ---------------------------------------------------------------

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled() or task.exception() is None:
            return
        name = type(task.exception()).__name__
        log.warning("sonos background task failed: %s", name)
        self.ledger.append(Kind.error, "", "", error=f"sonos background task: {name}")

    async def settle(self) -> None:
        """Wait for background looks to finish (the loop is not waited for: it never ends)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await asyncio.sleep(0)

    async def close(self) -> None:
        self._stop_loop()
        for t in list(self._tasks):
            t.cancel()
        await self.settle()

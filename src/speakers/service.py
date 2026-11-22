"""The Wi-Fi speakers service: a switch per speaker type, a look per type, the speakers found, and the one default.

Each protocol is looked for on its own: once at start for the types that are on, every LOOK_EVERY_S while on, on
Look again, and at once when its switch goes on. Switching a type off bumps that type's generation, so a look of it
still under way is thrown away, and drops its speakers from `known`, except the default's, which is never lost to a
missed look or a switch. A finder that hangs is cut off at FIND_TIMEOUT_S + FIND_SLACK_S as a `network` failure.
No address leaves this service: the status carries names, models and keys, and `also_sonos`, worked out when asked.
The ledger carries counts, protocols and error kinds, never a name or an address."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.memory.ledger import Kind, Ledger
from src.speakers.finders import Finder, FindError, real_finders
from src.speakers.settings import KNOWN_CAP, PROTOCOLS, Speaker, SpeakerSettings

log = logging.getLogger(__name__)

LOOK_EVERY_S = 600
FIND_TIMEOUT_S = 5
FIND_SLACK_S = 10
ALL_OFF = "No speaker type is switched on."
ALREADY = "Already looking for speakers."
UNKNOWN_DEFAULT = "Pick a speaker from the lists."


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _protocol_of(key: str) -> str:
    return key.split(":", 1)[0]


class SpeakerService:
    def __init__(self, vault_root: Path, ledger: Ledger, finders: dict[str, Finder] | None = None,
                 sonos_addresses: Callable[[], set[str]] = set) -> None:
        self.vault_root = vault_root
        self.ledger = ledger
        # nothing listens until a finder's look: each makes and closes what it needs
        self.finders: dict[str, Finder] = finders if finders is not None else real_finders()
        self.sonos_addresses = sonos_addresses
        self.settings = SpeakerSettings.load(vault_root)
        # what each type that is on shows: the last look's speakers, or, before its first look, the ones remembered
        self.found: dict[str, list[Speaker]] = {p: list(self.settings.known.get(p, [])) for p in PROTOCOLS if self.settings.on(p)}
        self.errors: dict[str, str] = {p: "" for p in PROTOCOLS}
        self.last_look_at: dict[str, str] = {p: "" for p in PROTOCOLS}
        self._gen: dict[str, int] = {p: 0 for p in PROTOCOLS}
        self._looks: dict[str, asyncio.Task] = {}
        self._loop_task: asyncio.Task | None = None

    # looking --------------------------------------------------------------------------

    def looking(self, protocol: str) -> bool:
        task = self._looks.get(protocol)
        return task is not None and not task.done()

    def _start_look(self, protocol: str) -> None:
        self._looks[protocol] = asyncio.ensure_future(self._look(protocol, self._gen[protocol]))

    def start_looks(self) -> str:
        """Start a look for every type that is on and not already looking: "" when one started, else the reason."""
        on = [p for p in PROTOCOLS if self.settings.on(p)]
        if not on:
            return ALL_OFF
        idle = [p for p in on if not self.looking(p)]
        if not idle:
            return ALREADY
        for p in idle:
            self._start_look(p)
        return ""

    async def _look(self, protocol: str, gen: int) -> None:
        try:
            found: list[Speaker] | None
            try:
                found = await asyncio.wait_for(self.finders[protocol].find(FIND_TIMEOUT_S), FIND_TIMEOUT_S + FIND_SLACK_S)
                error = ""
            except FindError as e:
                found, error = None, e.kind
            except (asyncio.TimeoutError, TimeoutError):
                found, error = None, "network"
            if gen != self._gen[protocol] or not self.settings.on(protocol):
                return  # switched off (or off and on) meanwhile: this result is stale
            self.errors[protocol], self.last_look_at[protocol] = error, _now()
            if found is not None:
                shown = self._default_first(protocol, found)[:KNOWN_CAP]
                self.found[protocol] = shown
                self.settings.known[protocol] = self._with_default(protocol, shown)
                try:
                    self.settings.save(self.vault_root)
                except OSError as e:
                    log.warning("wifi speakers: could not save what was found: %s", type(e).__name__)
            self.ledger.append(Kind.speakers, "", "", event="look", protocol=protocol, found=len(found or []), error=error)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - one failed look never takes the service down
            log.warning("wifi speakers look failed: %s", type(e).__name__)
            self.ledger.append(Kind.error, "", "", error=f"wifi speakers look: {type(e).__name__}")

    def _default_first(self, protocol: str, speakers: list[Speaker]) -> list[Speaker]:
        """`speakers` with the default moved to the front when it is among them, so no cap ever cuts it off."""
        key = self.settings.default
        if not key or _protocol_of(key) != protocol:
            return list(speakers)
        return [s for s in speakers if s.key == key][:1] + [s for s in speakers if s.key != key]

    def _with_default(self, protocol: str, speakers: list[Speaker]) -> list[Speaker]:
        """`speakers` (at most KNOWN_CAP) with the default first: found now, or its remembered entry when missed."""
        kept = self._default_first(protocol, speakers)
        key = self.settings.default
        if key and _protocol_of(key) == protocol and all(s.key != key for s in kept):
            entry = self._entry(key)
            if entry is not None:
                kept.insert(0, entry)
        return kept[:KNOWN_CAP]

    def _entry(self, key: str) -> Speaker | None:
        p = _protocol_of(key)
        for s in [*self.found.get(p, []), *self.settings.known.get(p, [])]:
            if s.key == key:
                return s
        return None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(LOOK_EVERY_S)
            try:
                self.start_looks()
            except Exception as e:  # noqa: BLE001 - the loop outlives any one look
                log.warning("wifi speakers loop: %s", type(e).__name__)

    def _keep_loop(self) -> None:
        if any(self.settings.on(p) for p in PROTOCOLS):
            if self._loop_task is None or self._loop_task.done():
                self._loop_task = asyncio.ensure_future(self._loop())
        elif self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None

    async def start(self) -> None:
        """A look now for each type that is on, and the loop after it."""
        if any(self.settings.on(p) for p in PROTOCOLS):
            self.start_looks()
        self._keep_loop()

    # settings ---------------------------------------------------------------------------

    async def set_switches(self, airplay: bool | None = None, chromecast: bool | None = None,
                           dlna: bool | None = None) -> dict[str, Any]:
        asked = {"airplay": airplay, "chromecast": chromecast, "dlna": dlna}
        turned_on = [p for p, v in asked.items() if v is True and not self.settings.on(p)]
        turned_off = [p for p, v in asked.items() if v is False and self.settings.on(p)]
        known = {p: list(v) for p, v in self.settings.known.items()}
        for p in turned_off:
            keep = self._with_default(p, [])
            if keep:
                known[p] = keep
            else:
                known.pop(p, None)
        candidate = replace(self.settings, **{p: v for p, v in asked.items() if v is not None}, known=known)
        candidate.save(self.vault_root)  # a failed save changes nothing, so the same request can be sent again
        self.settings = candidate
        for p in turned_off:
            self._gen[p] += 1
            task = self._looks.pop(p, None)
            if task is not None:
                task.cancel()
            self.found.pop(p, None)
            self.errors[p], self.last_look_at[p] = "", ""
        for p in turned_on:
            self._gen[p] += 1
            self.found[p] = list(self.settings.known.get(p, []))
            self._start_look(p)
        if turned_on or turned_off:
            self.ledger.append(Kind.speakers, "", "", event="settings", **self.settings.switches())
        self._keep_loop()
        return self.status()

    async def set_default(self, key: str | None) -> dict[str, Any]:
        known = {p: list(v) for p, v in self.settings.known.items()}
        if key is not None:
            p = _protocol_of(key)
            entry = self._entry(key) if self.settings.on(p) else None
            if entry is None:
                raise ValueError(UNKNOWN_DEFAULT)
            if all(s.key != key for s in known.get(p, [])):
                known.setdefault(p, []).append(entry)  # remembered from now on, even if a look misses it
        candidate = replace(self.settings, default=key, known=known)
        candidate.save(self.vault_root)
        self.settings = candidate
        self.ledger.append(Kind.speakers, "", "", event="default", protocol=_protocol_of(key) if key else "")
        return self.status()

    # status -------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        try:
            sonos = set(self.sonos_addresses())
        except Exception:  # noqa: BLE001 - the Sonos side is a nicety; never fail the status over it
            sonos = set()
        protocols = {}
        for p in PROTOCOLS:
            on = self.settings.on(p)
            protocols[p] = {"on": on, "looking": self.looking(p), "last_look_at": self.last_look_at[p], "error": self.errors[p],
                            "speakers": [{"key": s.key, "name": s.name, "model": s.model, "also_sonos": bool(s.address) and s.address in sonos}
                                         for s in (self.found.get(p, []) if on else [])]}
        default = None
        key = self.settings.default
        if key:
            p = _protocol_of(key)
            entry = self._entry(key)
            on = self.settings.on(p)
            default = {"key": key, "name": entry.name if entry else "", "protocol": p,
                       "found": on and any(s.key == key for s in self.found.get(p, [])), "protocol_on": on}
        return {"default": default, "protocols": protocols}

    # background ---------------------------------------------------------------------------

    async def settle(self) -> None:
        """Wait for the looks under way (not the loop: it never ends)."""
        while any(not t.done() for t in self._looks.values()):
            await asyncio.gather(*[t for t in self._looks.values() if not t.done()], return_exceptions=True)
            await asyncio.sleep(0)

    async def close(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None
        for t in self._looks.values():
            t.cancel()
        await asyncio.gather(*self._looks.values(), return_exceptions=True)
        self._looks.clear()

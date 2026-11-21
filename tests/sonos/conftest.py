"""A stand-in for the speakers (the `Speakers` boundary). Every room, address and title is made up; no network."""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from src.sonos.devices import Favorite, NowPlaying, Room, SpeakerError


def room(name: str, ip: str, coord: str | None = None) -> Room:
    """A room whose uid is RINCON_<ip>; `coord` is the coordinator's ip (itself when None)."""
    return Room(uid=f"RINCON_{ip}", name=name, ip=ip, model="Sonos One", coordinator_uid=f"RINCON_{coord or ip}")


class FakeSpeakers:
    def __init__(self) -> None:
        self.found: list[Room] = []
        self.favs: list[Favorite] = []
        self.volumes: dict[str, int] = {}
        self.states: dict[str, NowPlaying] = {}
        self.down: set[str] = set()
        self.calls: list[tuple] = []
        self.discovers: list[tuple[list[str], bool]] = []
        self.gate: threading.Event | None = None
        self.discover_error: SpeakerError | None = None
        self.rooms_gate: threading.Event | None = None  # blocks rooms() until set

    def _check(self, ip: str) -> None:
        if ip in self.down:
            raise SpeakerError("network", "ConnectionError")

    def discover(self, known: list[str], scan: bool, timeout: float) -> list[Room]:
        self.discovers.append((list(known), scan))
        if self.gate is not None:
            self.gate.wait(5)
        if self.discover_error is not None:
            raise self.discover_error
        return [r for r in self.found if r.ip not in self.down]

    def rooms(self, ip: str) -> list[Room]:
        if self.rooms_gate is not None:
            self.rooms_gate.wait(5)
        self._check(ip)
        return list(self.found)

    def favorites(self, ip: str) -> list[Favorite]:
        self._check(ip)
        return list(self.favs)

    def play(self, coordinator_ip: str, fav: Favorite) -> None:
        self._check(coordinator_ip)
        self.calls.append(("play", coordinator_ip, fav.title, fav.kind))

    def transport(self, coordinator_ip: str, action: str) -> None:
        self._check(coordinator_ip)
        self.calls.append(("transport", coordinator_ip, action))

    def now_playing(self, coordinator_ip: str) -> NowPlaying:
        self._check(coordinator_ip)
        return self.states.get(coordinator_ip, NowPlaying("stopped", "", ""))

    def get_volume(self, ip: str) -> int:
        self._check(ip)
        return self.volumes.get(ip, 20)

    def set_volume(self, ip: str, level: int) -> None:
        self._check(ip)
        self.volumes[ip] = level
        self.calls.append(("set_volume", ip, level))

    def join(self, ip: str, coordinator_ip: str) -> None:
        self._check(ip)
        self.calls.append(("join", ip, coordinator_ip))
        self.found = [replace(r, coordinator_uid=f"RINCON_{coordinator_ip}") if r.ip == ip else r for r in self.found]

    def unjoin(self, ip: str) -> None:
        self._check(ip)
        self.calls.append(("unjoin", ip))
        self.found = [replace(r, coordinator_uid=r.uid) if r.ip == ip else r for r in self.found]


@pytest.fixture
def fake_speakers() -> FakeSpeakers:
    return FakeSpeakers()

"""The boundary to the speakers: the only module that imports SoCo.

Every method blocks (SoCo speaks UPnP over `requests`), so the service runs them in threads. Every failure leaves as
a SpeakerError: kind `network` when the speaker could not be reached, `other` for anything else. Nothing here logs."""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, TypeVar

import requests
import soco

T = TypeVar("T")

TRANSPORT_STATES = {"PLAYING": "playing", "PAUSED_PLAYBACK": "paused", "STOPPED": "stopped"}
ACTIONS = ("pause", "resume", "next", "previous", "stop")


@dataclass(frozen=True)
class Room:
    """One visible speaker (a Sonos room) and the room that leads its group."""

    uid: str
    name: str
    ip: str
    model: str
    coordinator_uid: str


@dataclass(frozen=True)
class Favorite:
    """A Sonos Favorite or Sonos playlist; `item` is SoCo's object, opaque outside this module."""

    title: str
    kind: Literal["favorite", "playlist"]
    item: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class NowPlaying:
    state: str  # playing | paused | stopped | ""
    title: str
    artist: str


class SpeakerError(Exception):
    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind  # network | other


class Speakers(Protocol):
    def discover(self, known: list[str], scan: bool, timeout: float) -> list[Room]: ...
    def rooms(self, ip: str) -> list[Room]: ...
    def favorites(self, ip: str) -> list[Favorite]: ...
    def play(self, coordinator_ip: str, fav: Favorite) -> None: ...
    def transport(self, coordinator_ip: str, action: str) -> None: ...
    def now_playing(self, coordinator_ip: str) -> NowPlaying: ...
    def get_volume(self, ip: str) -> int: ...
    def set_volume(self, ip: str, level: int) -> None: ...
    def join(self, ip: str, coordinator_ip: str) -> None: ...
    def unjoin(self, ip: str) -> None: ...


def _kind(exc: BaseException) -> str:
    return "network" if isinstance(exc, (requests.exceptions.RequestException, OSError)) else "other"


def _guarded(fn: Callable[..., T]) -> Callable[..., T]:
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SpeakerError:
            raise
        except Exception as e:  # noqa: BLE001 - every failure leaves as a SpeakerError
            raise SpeakerError(_kind(e), type(e).__name__) from e
    return wrapper


def _room(zone: Any) -> Room:
    group = zone.group
    coordinator = group.coordinator if group is not None and group.coordinator is not None else zone
    try:
        model = str(zone.get_speaker_info().get("model_name") or "")
    except Exception:  # noqa: BLE001 - a model name is nice to have, not worth failing a look over
        model = ""
    return Room(uid=zone.uid, name=str(zone.player_name), ip=str(zone.ip_address), model=model,
                coordinator_uid=coordinator.uid)


def _is_container(obj: Any) -> bool:
    return str(getattr(obj, "item_class", "")).startswith("object.container")


class SocoSpeakers:
    """The real speakers, through SoCo."""

    @staticmethod
    def _zone(ip: str) -> Any:
        return soco.SoCo(ip)

    @_guarded
    def discover(self, known: list[str], scan: bool, timeout: float) -> list[Room]:
        found: dict[str, Room] = {}
        for ip in known:  # a direct request first: it gets through where a multicast may not
            try:
                for r in self.rooms(ip):
                    found.setdefault(r.uid, r)
                break  # one answering speaker gives its whole household
            except SpeakerError:
                continue
        for zone in soco.discover(timeout=timeout, allow_network_scan=scan) or ():
            if zone.uid not in found and zone.is_visible:
                found[zone.uid] = _room(zone)
        return sorted(found.values(), key=lambda r: r.name.casefold())

    @_guarded
    def rooms(self, ip: str) -> list[Room]:
        return sorted((_room(z) for z in self._zone(ip).visible_zones), key=lambda r: r.name.casefold())

    @_guarded
    def favorites(self, ip: str) -> list[Favorite]:
        library = self._zone(ip).music_library
        favs = [Favorite(str(f.title), "favorite", f) for f in library.get_sonos_favorites(complete_result=True)]
        lists = [Favorite(str(p.title), "playlist", p) for p in library.get_sonos_playlists(complete_result=True)]
        return favs + lists

    @_guarded
    def play(self, coordinator_ip: str, fav: Favorite) -> None:
        zone = self._zone(coordinator_ip)
        target = fav.item if fav.kind == "playlist" else fav.item.reference
        if _is_container(target):  # an album, a playlist, a service station: replace the queue and play it
            zone.clear_queue()
            zone.add_to_queue(target)
            zone.play_from_queue(0)
        else:  # a stream or a single track: play the favorite's own address with its metadata
            zone.play_uri(fav.item.get_uri(), meta=fav.item.resource_meta_data)

    @_guarded
    def transport(self, coordinator_ip: str, action: str) -> None:
        if action not in ACTIONS:
            raise SpeakerError("other", f"unknown action {action!r}")
        zone = self._zone(coordinator_ip)
        {"pause": zone.pause, "resume": zone.play, "next": zone.next, "previous": zone.previous,
         "stop": zone.stop}[action]()

    @_guarded
    def now_playing(self, coordinator_ip: str) -> NowPlaying:
        zone = self._zone(coordinator_ip)
        state = TRANSPORT_STATES.get(zone.get_current_transport_info().get("current_transport_state", ""), "")
        track = zone.get_current_track_info()
        return NowPlaying(state, str(track.get("title") or ""), str(track.get("artist") or ""))

    @_guarded
    def get_volume(self, ip: str) -> int:
        return int(self._zone(ip).volume)

    @_guarded
    def set_volume(self, ip: str, level: int) -> None:
        self._zone(ip).volume = level

    @_guarded
    def join(self, ip: str, coordinator_ip: str) -> None:
        self._zone(ip).join(self._zone(coordinator_ip))

    @_guarded
    def unjoin(self, ip: str) -> None:
        self._zone(ip).unjoin()

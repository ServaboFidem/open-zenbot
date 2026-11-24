"""The boundary to the speakers: the only module that imports SoCo.

Every method blocks (SoCo speaks UPnP over `requests`), so the service runs them in threads. Every failure leaves as
a SpeakerError: kind `network` when the speaker could not be reached, `other` for anything else. Nothing here logs."""

from __future__ import annotations

import contextlib
import functools
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, TypeVar

import requests
import soco
from soco.snapshot import Snapshot

T = TypeVar("T")

# SoCo waits 20 s on a speaker by default; on a home network 5 s is plenty, and a look that first asks the speakers
# last found must not spend its whole time on one that is switched off
REQUEST_TIMEOUT_S = 5.0
soco.config.REQUEST_TIMEOUT = REQUEST_TIMEOUT_S
QUEUE_TIMEOUT_S = 30.0  # adding a playlist or album to the queue: the one request that may take the speaker long

TRANSPORT_STATES = {"PLAYING": "playing", "PAUSED_PLAYBACK": "paused", "STOPPED": "stopped"}
# What a speaker was playing, from its current URI, for putting it back after an announcement
PHONE_SCHEMES = ("x-sonos-vli:", "x-sonos-spotify:", "x-sonosapi-rtrecent:")  # a phone or app cast to the speaker
TV_SCHEMES = ("x-sonos-htastream:", "x-rincon-stream:")  # the TV or a line-in
QUEUE_SCHEME = "x-rincon-queue:"
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


@dataclass
class Snap:
    """What a speaker was doing before an announcement. `source` is queue | stream | phone | tv | none; `raw` is
    SoCo's Snapshot (None in fakes), opaque outside this module."""

    ip: str
    coordinator_ip: str
    volume: int
    had_track: bool
    source: str
    state: str  # SoCo's current_transport_state: PLAYING, PAUSED_PLAYBACK, STOPPED, ...
    raw: Any = field(default=None, repr=False, compare=False)


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
    def snapshot(self, ip: str) -> Snap: ...
    def restore(self, snap: Snap) -> None: ...
    def play_uri(self, coordinator_ip: str, url: str, title: str) -> None: ...
    def transport_state(self, ip: str) -> str: ...
    def clear_clip(self, ip: str) -> None: ...


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
        # SoCo sends this request with no timeout of its own unless given one
        model = str(zone.get_speaker_info(timeout=REQUEST_TIMEOUT_S).get("model_name") or "")
    except Exception:  # noqa: BLE001 - a model name is nice to have, not worth failing a look over
        model = ""
    return Room(uid=zone.uid, name=str(zone.player_name), ip=str(zone.ip_address), model=model,
                coordinator_uid=coordinator.uid)


def _answering(zones: Any) -> list[Room]:
    """The rooms of these speakers, leaving out any that does not answer. Reading another speaker's group or name
    can send it a request (SoCo asks it for its household and topology), so one speaker still listed in the
    topology but unplugged must not take the whole list down with it."""
    rooms = []
    for zone in zones:
        try:
            if zone.is_visible:
                rooms.append(_room(zone))
        except Exception:  # noqa: BLE001 - a speaker that cannot be read is one that is not there
            continue
    return sorted(rooms, key=lambda r: r.name.casefold())


def _source(track_uri: str, media_uri: str) -> str:
    """The kind of what plays: the media address says queue (the track address then names a queue item), the track
    address says phone or tv, an address of either that is none of these is a stream, no address is none."""
    track, media = track_uri.strip(), media_uri.strip()
    if media.startswith(QUEUE_SCHEME) or track.startswith(QUEUE_SCHEME):
        return "queue"
    uri = track or media
    if not uri:
        return "none"
    if uri.startswith(PHONE_SCHEMES) or media.startswith(PHONE_SCHEMES):
        return "phone"
    if uri.startswith(TV_SCHEMES) or media.startswith(TV_SCHEMES):
        return "tv"
    return "stream"


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
        try:
            multicast = soco.discover(timeout=timeout, allow_network_scan=scan) or ()
        except Exception:  # noqa: BLE001 - the responder can fail as SoCo reads its topology
            if not found:
                raise
            multicast = ()  # what a saved address found still stands
        for r in _answering(multicast):
            found.setdefault(r.uid, r)
        return sorted(found.values(), key=lambda r: r.name.casefold())

    @_guarded
    def rooms(self, ip: str) -> list[Room]:
        return _answering(self._zone(ip).visible_zones)

    @_guarded
    def favorites(self, ip: str) -> list[Favorite]:
        zone = self._zone(ip)
        # SoCo 0.32: Sonos Favorites are on the music library, Sonos playlists on the speaker itself
        favs = [Favorite(str(f.title), "favorite", f) for f in zone.music_library.get_sonos_favorites(complete_result=True)]
        lists = [Favorite(str(p.title), "playlist", p) for p in zone.get_sonos_playlists(complete_result=True)]
        return favs + lists

    @_guarded
    def play(self, coordinator_ip: str, fav: Favorite) -> None:
        zone = self._zone(coordinator_ip)
        target = fav.item if fav.kind == "playlist" else fav.item.reference
        if _is_container(target):  # an album, a playlist, a service station: replace the queue and play it
            zone.clear_queue()
            zone.add_to_queue(target, timeout=QUEUE_TIMEOUT_S)  # a long playlist takes the speaker a while
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

    @_guarded
    def snapshot(self, ip: str) -> Snap:
        zone = self._zone(ip)
        group = zone.group
        coordinator = group.coordinator if group is not None and group.coordinator is not None else zone
        track = str(coordinator.get_current_track_info().get("uri") or "")
        media = ""
        with contextlib.suppress(Exception):  # the media address only refines the kind; the track address suffices
            media = str(coordinator.avTransport.GetMediaInfo([("InstanceID", 0)]).get("CurrentURI") or "")
        state = str(coordinator.get_current_transport_info().get("current_transport_state") or "")
        raw = Snapshot(zone)
        raw.snapshot()
        return Snap(ip=str(zone.ip_address), coordinator_ip=str(coordinator.ip_address), volume=int(zone.volume),
                    had_track=bool(track or media), source=_source(track, media), state=state, raw=raw)

    @_guarded
    def restore(self, snap: Snap) -> None:
        snap.raw.restore(fade=False)

    @_guarded
    def play_uri(self, coordinator_ip: str, url: str, title: str) -> None:
        self._zone(coordinator_ip).play_uri(url, title=title)

    @_guarded
    def transport_state(self, ip: str) -> str:
        return str(self._zone(ip).get_current_transport_info().get("current_transport_state") or "")

    @_guarded
    def clear_clip(self, ip: str) -> None:
        """Drop a finished clip as the current track by pointing the transport at nothing. A speaker that refuses
        leaves it as it was: this is best effort and never raises."""
        with contextlib.suppress(Exception):
            self._zone(ip).avTransport.SetAVTransportURI([("InstanceID", 0), ("CurrentURI", ""), ("CurrentURIMetaData", "")])

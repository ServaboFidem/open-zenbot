"""SocoSpeakers' own logic against stand-in SoCo objects: no network, no real discovery."""

from types import SimpleNamespace

import pytest
import requests

from src.sonos import devices
from src.sonos.devices import Favorite, Room, SocoSpeakers, SpeakerError


class StubZone:
    def __init__(self, uid, name, ip, visible=True, coordinator=None, peers=None):
        self.uid, self.player_name, self.ip_address, self.is_visible = uid, name, ip, visible
        self._coordinator = coordinator
        self.peers = peers if peers is not None else [self]
        self.calls = []

    @property
    def group(self):
        return SimpleNamespace(coordinator=self._coordinator or self)

    @property
    def visible_zones(self):
        return {z for z in self.peers if z.is_visible}

    def get_speaker_info(self, refresh=False, timeout=None):
        return {"model_name": "Sonos One"}

    def play_uri(self, uri="", meta="", title="", start=True, force_radio=False):
        self.calls.append(("play_uri", uri, meta))

    def clear_queue(self):
        self.calls.append(("clear_queue",))

    def add_to_queue(self, item, position=0, as_next=False, **kw):
        self.calls.append(("add_to_queue", item))
        return 1

    def play_from_queue(self, index, start=True):
        self.calls.append(("play_from_queue", index))


def stub_soco(monkeypatch, zones, discovered=None, calls=None):
    by_ip = {z.ip_address: z for z in zones}

    def make(ip):
        if ip not in by_ip:
            raise requests.exceptions.ConnectionError("no route")
        return by_ip[ip]

    def discover(timeout=5, include_invisible=False, allow_network_scan=False, **kw):
        if calls is not None:
            calls.append({"timeout": timeout, "allow_network_scan": allow_network_scan})
        return set(discovered or []) or None

    monkeypatch.setattr(devices, "soco", SimpleNamespace(SoCo=make, discover=discover))
    return by_ip


class Ref:
    def __init__(self, item_class):
        self.item_class = item_class


class StubFav:
    def __init__(self, ref):
        self.reference = ref
        self.resource_meta_data = "<DIDL-Lite>meta</DIDL-Lite>"

    def get_uri(self):
        return "x-sonosapi-stream:s123"


def test_play_stream_favorite_uses_uri_and_meta(monkeypatch):
    zone = StubZone("RINCON_1", "Kitchen", "192.0.2.10")
    stub_soco(monkeypatch, [zone])
    fav = StubFav(Ref("object.item.audioItem.audioBroadcast"))
    SocoSpeakers().play("192.0.2.10", Favorite("Radio", "favorite", fav))
    assert zone.calls == [("play_uri", "x-sonosapi-stream:s123", "<DIDL-Lite>meta</DIDL-Lite>")]


def test_play_container_favorite_replaces_queue(monkeypatch):
    zone = StubZone("RINCON_1", "Kitchen", "192.0.2.10")
    stub_soco(monkeypatch, [zone])
    ref = Ref("object.container.album.musicAlbum")
    SocoSpeakers().play("192.0.2.10", Favorite("Album", "favorite", StubFav(ref)))
    assert zone.calls == [("clear_queue",), ("add_to_queue", ref), ("play_from_queue", 0)]


def test_play_sonos_playlist_replaces_queue(monkeypatch):
    zone = StubZone("RINCON_1", "Kitchen", "192.0.2.10")
    stub_soco(monkeypatch, [zone])
    playlist = Ref("object.container.playlistContainer")
    SocoSpeakers().play("192.0.2.10", Favorite("Dinner", "playlist", playlist))
    assert zone.calls == [("clear_queue",), ("add_to_queue", playlist), ("play_from_queue", 0)]


def test_discover_unions_known_and_multicast(monkeypatch):
    kitchen = StubZone("RINCON_1", "Kitchen", "192.0.2.10")
    sub = StubZone("RINCON_9", "Kitchen", "192.0.2.19", visible=False)
    kitchen.peers = [kitchen, sub]
    office = StubZone("RINCON_2", "Office", "192.0.2.20")
    calls = []
    stub_soco(monkeypatch, [kitchen, sub, office], discovered=[kitchen, office], calls=calls)
    found = SocoSpeakers().discover(["192.0.2.10", "192.0.2.99"], scan=False, timeout=3)
    assert sorted(r.name for r in found) == ["Kitchen", "Office"]
    assert Room("RINCON_1", "Kitchen", "192.0.2.10", "Sonos One", "RINCON_1") in found
    assert calls == [{"timeout": 3, "allow_network_scan": False}]
    SocoSpeakers().discover([], scan=True, timeout=3)
    assert calls[-1]["allow_network_scan"] is True


def test_rooms_reads_topology_with_coordinators(monkeypatch):
    living = StubZone("RINCON_2", "Living Room", "192.0.2.11")
    patio = StubZone("RINCON_3", "Patio", "192.0.2.12", coordinator=living)
    living.peers = patio.peers = [living, patio]
    stub_soco(monkeypatch, [living, patio])
    rooms = {r.name: r for r in SocoSpeakers().rooms("192.0.2.12")}
    assert rooms["Patio"].coordinator_uid == "RINCON_2"
    assert rooms["Living Room"].coordinator_uid == "RINCON_2"


@pytest.mark.parametrize("exc", [requests.exceptions.ConnectionError("x"), requests.exceptions.Timeout("x"),
                                 OSError("x")])
def test_network_errors_map_to_speaker_error(monkeypatch, exc):
    zone = StubZone("RINCON_1", "Kitchen", "192.0.2.10")

    def boom(*a, **kw):
        raise exc

    zone.clear_queue = boom
    stub_soco(monkeypatch, [zone])
    with pytest.raises(SpeakerError) as e:
        SocoSpeakers().play("192.0.2.10", Favorite("Dinner", "playlist", Ref("object.container.playlistContainer")))
    assert e.value.kind == "network"


def test_other_errors_map_to_other(monkeypatch):
    zone = StubZone("RINCON_1", "Kitchen", "192.0.2.10")

    def boom(*a, **kw):
        raise RuntimeError("odd")

    zone.clear_queue = boom
    stub_soco(monkeypatch, [zone])
    with pytest.raises(SpeakerError) as e:
        SocoSpeakers().play("192.0.2.10", Favorite("Dinner", "playlist", Ref("object.container.playlistContainer")))
    assert e.value.kind == "other"

"""SonosService: looking for speakers, the loop, settings and status. The speakers are FakeSpeakers; no network."""

import asyncio
import json
import threading
import time

import pytest

from src.memory.ledger import Ledger
from src.sonos import service as service_mod
from src.sonos.devices import NowPlaying, SpeakerError
from src.sonos.service import SonosService
from src.sonos.settings import SonosSettings
from tests.sonos.conftest import room


@pytest.fixture
def speakers(fake_speakers):
    fake_speakers.found = [room("Kitchen", "192.0.2.10"), room("Living Room", "192.0.2.11"),
                           room("Patio", "192.0.2.12", coord="192.0.2.11")]
    return fake_speakers


@pytest.fixture
async def svc(vault, speakers):
    s = SonosService(vault, Ledger(vault), speakers=speakers)
    changes = []
    s.on_change = changes.append
    s.changes = changes
    yield s
    await s.close()


def entries(vault, event=None):
    rows = [e for e in Ledger(vault).tail(0, kind="sonos")]
    return [e for e in rows if event is None or e.data.get("event") == event]


async def test_look_finds_rooms_and_saves_known(svc, vault):
    await svc.look()
    assert [r.name for r in svc.rooms] == ["Kitchen", "Living Room", "Patio"]
    assert SonosSettings.load(vault).known == ["192.0.2.10", "192.0.2.11", "192.0.2.12"]
    assert svc.changes == [True]
    assert svc.error == ""
    assert svc.last_look_at.endswith("Z")
    (look,) = entries(vault, "look")
    assert look.data["rooms"] == 3 and look.data["scan"] is False
    assert "Kitchen" not in json.dumps(look.data)


async def test_look_passes_known_first_and_scan_only_when_asked(svc, speakers):
    await svc.look()
    assert speakers.discovers[-1] == ([], False)
    await svc.look(scan=True)
    assert speakers.discovers[-1] == (["192.0.2.10", "192.0.2.11", "192.0.2.12"], True)


async def test_none_found(svc, speakers):
    speakers.found = []
    await svc.look()
    assert svc.error == "none_found" and svc.rooms == []
    assert svc.changes == [False]


async def test_network_failure_keeps_error_kind(svc, speakers):
    speakers.discover_error = SpeakerError("network")
    await svc.look()
    assert svc.error == "network" and svc.rooms == []
    assert svc.changes == [False]


async def test_loop_looks_at_start_then_every_interval(svc, speakers, monkeypatch):
    monkeypatch.setattr(service_mod, "LOOK_EVERY_S", 0.01)
    await svc.start()
    for _ in range(100):
        if len(speakers.discovers) >= 2:
            break
        await asyncio.sleep(0.01)
    assert len(speakers.discovers) >= 2
    await svc.close()
    count = len(speakers.discovers)
    await asyncio.sleep(0.05)
    assert len(speakers.discovers) == count


async def test_disabled_does_not_look(vault, speakers):
    SonosSettings(enabled=False).save(vault)
    s = SonosService(vault, Ledger(vault), speakers=speakers)
    await s.start()
    await asyncio.sleep(0.02)
    await s.settle()
    assert speakers.discovers == []
    await s.close()


async def test_switch_off_forgets_and_switch_on_looks(svc, vault):
    await svc.look()
    status = await svc.set_settings(False, 60)
    assert status["enabled"] is False and status["rooms"] == []
    assert svc.rooms == [] and svc.changes[-1] is False
    assert SonosSettings.load(vault).enabled is False
    assert entries(vault, "settings")[-1].data["enabled"] is False
    await svc.set_settings(True, 60)
    await svc.settle()
    assert len(svc.rooms) == 3 and svc.changes[-1] is True


async def test_switch_off_during_look_discards_result(svc, speakers):
    speakers.gate = threading.Event()
    assert svc.start_look(False) is True
    await asyncio.sleep(0.02)
    await svc.set_settings(False, 60)
    speakers.gate.set()
    await svc.settle()
    assert svc.rooms == []
    assert svc.changes[-1] is False


async def test_start_look_refuses_while_looking_or_disabled(svc, speakers):
    speakers.gate = threading.Event()
    assert svc.start_look(False) is True
    await asyncio.sleep(0.02)
    assert svc.looking is True
    assert svc.start_look(True) is False
    speakers.gate.set()
    await svc.settle()
    await svc.set_settings(False, 60)
    assert svc.start_look(True) is False


async def test_set_settings_validates(svc):
    with pytest.raises(ValueError):
        await svc.set_settings(True, 101)


async def test_max_volume_change_does_not_look_again(svc, speakers):
    await svc.look()
    count = len(speakers.discovers)
    await svc.set_settings(True, 40)
    await svc.settle()
    assert svc.settings.max_volume == 40 and len(speakers.discovers) == count


async def test_call_times_out(svc):
    with pytest.raises(SpeakerError) as e:
        await svc.call(time.sleep, 1, timeout=0.05)
    assert e.value.kind == "network"


async def test_refresh_reads_topology_and_drops_dead(svc, speakers):
    await svc.look()
    speakers.down.add("192.0.2.10")
    speakers.found = [r for r in speakers.found if r.ip != "192.0.2.10"]
    rooms = await svc.refresh()
    assert [r.name for r in rooms] == ["Living Room", "Patio"]
    svc.drop("RINCON_192.0.2.11")
    svc.drop("RINCON_192.0.2.12")
    assert svc.rooms == [] and svc.changes[-1] is False


async def test_refresh_with_none_answering_empties(svc, speakers):
    await svc.look()
    speakers.down.update({"192.0.2.10", "192.0.2.11", "192.0.2.12"})
    assert await svc.refresh() == []
    assert svc.changes[-1] is False


async def test_status_shape(svc, speakers):
    await svc.look()
    speakers.states["192.0.2.11"] = NowPlaying("playing", "So What", "Miles Davis")
    st = await svc.status()
    assert st["enabled"] is True and st["max_volume"] == 60 and st["looking"] is False and st["error"] == ""
    patio = next(r for r in st["rooms"] if r["name"] == "Patio")
    assert patio == {"name": "Patio", "group": "Living Room", "model": "Sonos One", "state": "playing",
                     "title": "So What"}
    kitchen = next(r for r in st["rooms"] if r["name"] == "Kitchen")
    assert kitchen["group"] == "Kitchen" and kitchen["state"] == "stopped"


async def test_status_survives_a_dead_coordinator(svc, speakers):
    await svc.look()
    speakers.down.add("192.0.2.11")
    st = await svc.status()
    patio = next(r for r in st["rooms"] if r["name"] == "Patio")
    assert (patio["state"], patio["title"]) == ("", "")


async def test_switch_off_and_on_during_a_look_looks_again(svc, speakers):
    speakers.gate = threading.Event()
    assert svc.start_look(False) is True
    await asyncio.sleep(0.02)
    await svc.set_settings(False, 60)
    await svc.set_settings(True, 60)
    assert svc.looking is True  # a look for the new switch-on is under way, not refused by the stale one
    speakers.gate.set()
    await svc.settle()
    assert len(speakers.discovers) == 2
    assert [r.name for r in svc.rooms] == sorted(r.name for r in speakers.found)


async def test_a_failed_save_changes_nothing(svc, monkeypatch):
    await svc.look()
    from src.sonos import settings as sonos_settings
    real, broken = sonos_settings.SonosSettings.save, [True]

    def save(self, root):
        if broken[0]:
            raise OSError("disk full")
        return real(self, root)

    monkeypatch.setattr(sonos_settings.SonosSettings, "save", save)
    with pytest.raises(OSError):
        await svc.set_settings(False, 40)
    assert (svc.settings.enabled, svc.settings.max_volume) == (True, 60) and svc.rooms
    broken[0] = False
    await svc.set_settings(False, 40)  # the same request again, once the disk is back, does switch off
    assert svc.settings.enabled is False and svc.rooms == [] and svc.changes[-1] is False


async def test_switch_off_during_refresh_keeps_rooms_empty(svc, speakers):
    await svc.look()
    assert svc.rooms
    speakers.rooms_gate = threading.Event()
    task = asyncio.ensure_future(svc.refresh())
    await asyncio.sleep(0.02)
    await svc.set_settings(False, 60)
    speakers.rooms_gate.set()
    assert await task == []
    assert svc.rooms == []

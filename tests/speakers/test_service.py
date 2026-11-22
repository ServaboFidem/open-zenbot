"""The speaker service with fake finders: a look per protocol, the switches, `known`, the one default, the status."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.memory.ledger import Ledger
from src.speakers import service as service_mod
from src.speakers.finders import FindError
from src.speakers.service import ALL_OFF, ALREADY, SpeakerService
from src.speakers.settings import Speaker, SpeakerSettings


def sp(protocol, ident, name="Speaker", address="10.0.0.5"):
    return Speaker(f"{protocol}:{ident}", protocol, name, "Model", address)


class FakeFinder:
    def __init__(self, answer=(), error=None, hold=False):
        self.answer, self.error, self.calls = list(answer), error, 0
        self.gate = asyncio.Event()
        if not hold:
            self.gate.set()

    async def find(self, timeout):
        self.calls += 1
        await self.gate.wait()
        if self.error:
            raise self.error
        return list(self.answer)


def make(tmp_path, sonos=lambda: set(), **finders):
    fs = {"airplay": FakeFinder(), "chromecast": FakeFinder(), "dlna": FakeFinder()}
    fs.update(finders)
    vault = tmp_path / "vault"
    return SpeakerService(vault, Ledger(vault), finders=fs, sonos_addresses=sonos), fs


async def test_nothing_looks_while_all_switches_are_off(tmp_path):
    s, fs = make(tmp_path)
    await s.start()
    await s.settle()
    assert all(f.calls == 0 for f in fs.values())
    st = s.status()
    assert st["default"] is None and all(not v["on"] and v["speakers"] == [] for v in st["protocols"].values())
    await s.close()


async def test_switching_one_on_looks_for_that_one_only(tmp_path):
    s, fs = make(tmp_path, airplay=FakeFinder([sp("airplay", "A", "Kitchen")]))
    st = await s.set_switches(airplay=True)
    await s.settle()
    st = s.status()
    assert fs["airplay"].calls == 1 and fs["chromecast"].calls == 0 and fs["dlna"].calls == 0
    assert st["protocols"]["airplay"]["speakers"] == [{"key": "airplay:A", "name": "Kitchen", "model": "Model", "also_sonos": False}]
    assert SpeakerSettings.load(tmp_path / "vault").airplay is True
    await s.close()


async def test_a_failing_finder_fails_only_its_own_protocol(tmp_path):
    s, _ = make(tmp_path, airplay=FakeFinder(error=FindError("network")), dlna=FakeFinder([sp("dlna", "D")]))
    await s.set_switches(airplay=True, dlna=True)
    await s.settle()
    st = s.status()["protocols"]
    assert st["airplay"]["error"] == "network" and st["airplay"]["speakers"] == []
    assert st["dlna"]["error"] == "" and [x["key"] for x in st["dlna"]["speakers"]] == ["dlna:D"]
    await s.close()


async def test_a_hanging_finder_times_out_as_network(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "FIND_TIMEOUT_S", 0.01)
    monkeypatch.setattr(service_mod, "FIND_SLACK_S", 0.05)
    s, _ = make(tmp_path, chromecast=FakeFinder(hold=True))
    await s.set_switches(chromecast=True)
    assert s.status()["protocols"]["chromecast"]["looking"] is True
    await s.settle()
    st = s.status()["protocols"]["chromecast"]
    assert st["error"] == "network" and st["looking"] is False
    await s.close()


async def test_switching_off_mid_look_drops_its_result_and_known(tmp_path):
    held = FakeFinder([sp("dlna", "D")], hold=True)
    s, _ = make(tmp_path, dlna=held)
    await s.set_switches(dlna=True)
    await asyncio.sleep(0)
    await s.set_switches(dlna=False)
    held.gate.set()
    await s.settle()
    assert s.status()["protocols"]["dlna"]["speakers"] == []
    assert SpeakerSettings.load(tmp_path / "vault").known.get("dlna", []) == []
    await s.close()


async def test_the_default_survives_a_missed_look_and_its_protocol_off(tmp_path):
    finder = FakeFinder([sp("airplay", "A", "Kitchen"), sp("airplay", "B", "Den")])
    s, _ = make(tmp_path, airplay=finder)
    await s.set_switches(airplay=True)
    await s.settle()
    await s.set_default("airplay:A")
    finder.answer = [sp("airplay", "B", "Den")]  # Kitchen is unplugged
    assert s.start_looks() == ""
    await s.settle()
    d = s.status()["default"]
    assert d == {"key": "airplay:A", "name": "Kitchen", "protocol": "airplay", "found": False, "protocol_on": True}
    assert [x.key for x in SpeakerSettings.load(tmp_path / "vault").known["airplay"]] == ["airplay:A", "airplay:B"]  # the default first
    await s.set_switches(airplay=False)
    d = s.status()["default"]
    assert d["key"] == "airplay:A" and d["name"] == "Kitchen" and d["protocol_on"] is False and d["found"] is False
    saved = SpeakerSettings.load(tmp_path / "vault")
    assert saved.default == "airplay:A" and [x.key for x in saved.known["airplay"]] == ["airplay:A"]
    await s.close()


async def test_set_default_refuses_a_speaker_not_listed(tmp_path):
    s, _ = make(tmp_path, dlna=FakeFinder([sp("dlna", "D")]))
    await s.set_switches(dlna=True)
    await s.settle()
    with pytest.raises(ValueError, match="Pick a speaker from the lists."):
        await s.set_default("airplay:nope")
    with pytest.raises(ValueError):
        await s.set_default("dlna:other")
    assert (await s.set_default("dlna:D"))["default"]["key"] == "dlna:D"
    assert (await s.set_default(None))["default"] is None
    await s.close()


async def test_start_looks_refusals(tmp_path):
    held = FakeFinder(hold=True)
    s, _ = make(tmp_path, chromecast=held)
    assert s.start_looks() == ALL_OFF
    await s.set_switches(chromecast=True)
    assert s.start_looks() == ALREADY
    held.gate.set()
    await s.settle()
    assert s.start_looks() == ""
    await s.settle()
    await s.close()


async def test_also_sonos_is_worked_out_at_status_time(tmp_path):
    sonos: set[str] = set()
    s, _ = make(tmp_path, sonos=lambda: sonos, dlna=FakeFinder([sp("dlna", "D", address="10.0.0.9")]))
    await s.set_switches(dlna=True)
    await s.settle()
    assert s.status()["protocols"]["dlna"]["speakers"][0]["also_sonos"] is False
    sonos.add("10.0.0.9")
    assert s.status()["protocols"]["dlna"]["speakers"][0]["also_sonos"] is True
    await s.close()


async def test_known_shows_at_start_before_the_first_look(tmp_path):
    vault = tmp_path / "vault"
    SpeakerSettings(dlna=True, known={"dlna": [sp("dlna", "D", "Hall")]}).save(vault)
    held = FakeFinder(hold=True)
    s = SpeakerService(vault, Ledger(vault), finders={"airplay": FakeFinder(), "chromecast": FakeFinder(), "dlna": held})
    assert [x["name"] for x in s.status()["protocols"]["dlna"]["speakers"]] == ["Hall"]
    held.gate.set()
    await s.close()


async def test_ledger_and_status_carry_no_address_or_name(tmp_path):
    s, _ = make(tmp_path, dlna=FakeFinder([sp("dlna", "D", "Secret Den", address="10.0.0.77")]))
    await s.set_switches(dlna=True)
    await s.settle()
    await s.set_default("dlna:D")
    assert "10.0.0.77" not in json.dumps(s.status())
    entries = [e for e in s.ledger.iter() if e.kind == "speakers"]
    assert {e.data["event"] for e in entries} == {"settings", "look", "default"}
    assert "Secret Den" not in json.dumps([e.data for e in entries]) and "10.0.0.77" not in json.dumps([e.data for e in entries])
    await s.close()


async def test_the_default_survives_more_than_64_others_and_a_restart(tmp_path):
    finder = FakeFinder([sp("dlna", "K", "Kitchen")])
    s, _ = make(tmp_path, dlna=finder)
    await s.set_switches(dlna=True)
    await s.settle()
    await s.set_default("dlna:K")
    finder.answer = [sp("dlna", f"X{i}", f"Other {i}") for i in range(70)] + [sp("dlna", "K", "Kitchen")]
    s.start_looks()
    await s.settle()
    assert s.status()["default"]["found"] is True
    assert len(s.status()["protocols"]["dlna"]["speakers"]) == 64
    await s.close()
    again = SpeakerService(tmp_path / "vault", Ledger(tmp_path / "vault"), finders={"airplay": FakeFinder(), "chromecast": FakeFinder(), "dlna": FakeFinder(hold=True)})
    assert again.status()["default"]["name"] == "Kitchen"
    await again.close()

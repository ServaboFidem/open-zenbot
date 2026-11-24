"""SonosTools.announce against FakeSpeakers: snapshot, pause, group under one lead, set the volume, play the clip from
a stand-in listener, and always put every speaker back. No network: the listener and the local address are swapped."""

import asyncio
import io
import wave

import pytest

from src.memory.ledger import Ledger
from src.sonos import tools as tools_mod
from src.sonos.devices import Favorite, SpeakerError
from src.sonos.service import SonosService
from src.sonos.tools import OFF, SonosTools
from tests.sonos.conftest import room

DEN, GARAGE, KITCHEN, OFFICE, PATIO = "192.0.2.20", "192.0.2.21", "192.0.2.22", "192.0.2.23", "192.0.2.24"
ALL = [DEN, GARAGE, KITCHEN, OFFICE, PATIO]
NAMES = ["Den", "Garage", "Kitchen", "Office", "Patio"]
URL = "http://10.0.0.9:5000/clip.wav"
TITLE = "ZenBot morning briefing"


def make_wav(seconds: float = 0.1, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


WAV = make_wav()


class StandInServer:
    def __init__(self, wav: bytes, allowed: str, bind: str, life_s: float) -> None:
        self.wav, self.allowed, self.bind, self.life_s = wav, allowed, bind, life_s
        self.url = URL
        self.hits = 1
        self.open = False
        self.closed = False

    def __enter__(self) -> "StandInServer":
        self.open = True
        return self

    def __exit__(self, *exc) -> None:
        self.closed = True


@pytest.fixture
def servers(monkeypatch):
    made: list[StandInServer] = []

    def factory(*args, **kwargs):
        s = StandInServer(*args, **kwargs)
        made.append(s)
        return s
    monkeypatch.setattr(SonosTools, "server_factory", factory)
    monkeypatch.setattr(SonosTools, "local_ip", lambda ip: "10.0.0.9")
    monkeypatch.setattr(tools_mod, "CLIP_POLL_S", 0.01)
    return made


@pytest.fixture
def speakers(fake_speakers):
    # Office leads a group with Den; Garage, Kitchen and Patio play alone
    fake_speakers.found = [room("Den", DEN, coord=OFFICE), room("Garage", GARAGE), room("Kitchen", KITCHEN),
                           room("Office", OFFICE), room("Patio", PATIO)]
    fake_speakers.volumes = {DEN: 30, GARAGE: 56, KITCHEN: 12, OFFICE: 40, PATIO: 20}
    fake_speakers.favs = [Favorite("Morning Jazz", "favorite")]
    # the clip plays: the lead reads PLAYING, then STOPPED once it ends
    fake_speakers.after_play = lambda ip: fake_speakers.state_script.__setitem__(ip, ["PLAYING", "STOPPED"])
    return fake_speakers


@pytest.fixture
async def svc(vault, speakers):
    s = SonosService(vault, Ledger(vault), speakers=speakers)
    await s.look()
    yield s
    await s.close()


@pytest.fixture
def sonos(svc, servers):
    return SonosTools(svc, in_room=lambda sid: False)


def first(calls, entry):
    return calls.index(entry)


def last(calls, entry):
    return len(calls) - 1 - calls[::-1].index(entry)


def topology(speakers):
    return {r.ip: r.coordinator_uid for r in speakers.found}


async def test_announce_groups_plays_and_restores(sonos, speakers, servers):
    before = topology(speakers)
    result = await sonos.announce(None, WAV, 25)
    calls = speakers.calls
    snaps = [first(calls, ("snapshot", ip)) for ip in ALL]
    pauses = [first(calls, ("transport", ip, "pause")) for ip in (GARAGE, KITCHEN, OFFICE, PATIO)]
    assert ("transport", DEN, "pause") not in calls  # a member is not a lead: its lead's pause stops it
    joins = [first(calls, ("join", ip, OFFICE)) for ip in (GARAGE, KITCHEN, PATIO)]
    volumes = [first(calls, ("set_volume", ip, 25)) for ip in ALL]
    play = [c for c in calls if c[0] == "play_uri"]
    assert play == [("play_uri", OFFICE, URL, TITLE)]
    played_at = calls.index(play[0])
    unjoins = [last(calls, ("unjoin", ip)) for ip in ALL]
    rejoin = last(calls, ("join", DEN, OFFICE))
    restores = [first(calls, ("restore", ip)) for ip in ALL]
    assert max(snaps) < min(pauses) <= max(pauses) < min(joins)
    assert max(joins) < min(volumes) and max(volumes) < played_at < min(unjoins)
    assert max(unjoins) < rejoin < min(restores)
    assert calls.index(("restore", OFFICE)) < calls.index(("restore", DEN))  # leads first, then members
    assert ("join", DEN, OFFICE) not in calls[:played_at]  # Den was already in Office's group
    assert result.played == NAMES
    assert result.failed == {} and result.phone_paused == []
    assert result.seconds >= 0
    assert topology(speakers) == before  # the groups are as they were
    assert servers[0].closed


async def test_only_the_lead_may_fetch(sonos, servers):
    await sonos.announce(None, WAV, 25)
    assert len(servers) == 1
    assert servers[0].allowed == OFFICE
    assert servers[0].bind == "10.0.0.9"
    assert servers[0].life_s == pytest.approx(0.1 + 60)


async def test_a_join_failure_skips_that_speaker_and_restores_all(sonos, speakers):
    real = speakers.join

    def join(ip, coordinator_ip):
        if ip == PATIO:
            raise SpeakerError("network", "ConnectionError")
        real(ip, coordinator_ip)
    speakers.join = join
    result = await sonos.announce(None, WAV, 25)
    assert result.failed == {"Patio": "could not join"}
    assert result.played == ["Den", "Garage", "Kitchen", "Office"]
    assert ("set_volume", PATIO, 25) not in speakers.calls
    assert {c[1] for c in speakers.calls if c[0] == "restore"} == set(ALL)


async def test_volume_is_capped_by_the_ceiling_read_at_play_time(sonos, svc, speakers):
    real = speakers.snapshot

    def snapshot(ip):
        svc.settings.max_volume = 10  # the owner lowers the ceiling after the briefing started
        return real(ip)
    speakers.snapshot = snapshot
    await sonos.announce(None, WAV, 25)
    sets = [c for c in speakers.calls if c[0] == "set_volume"]
    assert sets and all(level <= 10 for _, _, level in sets)


async def test_named_speakers_only(sonos, speakers):
    result = await sonos.announce(["office", "Attic"], WAV, 25)
    assert result.played == ["Office"]
    assert result.failed == {"Attic": "did not answer"}
    touched = {c[1] for c in speakers.calls}
    assert touched == {OFFICE, DEN}
    calls = speakers.calls
    # Den is in Office's group but was not named: it leaves before the clip plays, and comes back after
    assert calls.index(("unjoin", DEN)) < calls.index(("play_uri", OFFICE, URL, TITLE))
    assert ("set_volume", DEN, 25) not in calls
    assert last(calls, ("join", DEN, OFFICE)) > calls.index(("play_uri", OFFICE, URL, TITLE))
    assert ("restore", OFFICE) in calls and ("restore", DEN) in calls


async def test_a_named_group_member_leads_on_its_own_and_goes_back(sonos, speakers):
    before = topology(speakers)
    result = await sonos.announce(["Den"], WAV, 25)
    calls = speakers.calls
    assert result.played == ["Den"]
    play = calls.index(("play_uri", DEN, URL, TITLE))
    assert calls.index(("unjoin", DEN)) < play  # out of Office's group before it plays
    assert ("transport", OFFICE, "pause") in calls[:play]  # its group is taken apart, so Office's music pauses
    assert last(calls, ("join", DEN, OFFICE)) > play
    assert calls.index(("restore", OFFICE)) < calls.index(("restore", DEN))
    assert topology(speakers) == before


async def test_phone_source_not_resumed_is_reported(sonos, speakers):
    speakers.sources = {KITCHEN: "phone", GARAGE: "phone"}
    speakers.transport_states[GARAGE] = "PLAYING"  # Garage's phone session resumes; Kitchen's does not
    result = await sonos.announce(None, WAV, 25)
    assert result.phone_paused == ["Kitchen"]
    assert "Kitchen" in result.played


async def test_tools_wait_for_the_announcement(sonos, speakers):
    loop = asyncio.get_running_loop()
    playing = asyncio.Event()
    speakers.after_play = lambda ip: loop.call_soon_threadsafe(playing.set)
    task = asyncio.create_task(sonos.announce(None, WAV, 25))
    await asyncio.wait_for(playing.wait(), 5)
    tool = asyncio.create_task(sonos.play("Kitchen", "Morning Jazz"))
    await asyncio.sleep(0.1)
    assert not tool.done()
    speakers.transport_states[OFFICE] = "STOPPED"  # the clip ends
    result = await asyncio.wait_for(task, 5)
    answer = await asyncio.wait_for(tool, 5)
    assert result.played == NAMES
    assert answer == "Playing the favorite Morning Jazz in Kitchen."
    calls = speakers.calls
    assert calls.index(("play", KITCHEN, "Morning Jazz", "favorite")) > last(calls, ("restore", PATIO))


async def test_idle_speaker_clip_is_cleared(sonos, speakers):
    speakers.sources = {GARAGE: "none"}
    await sonos.announce(None, WAV, 25)
    calls = speakers.calls
    assert [c for c in calls if c[0] == "clear_clip"] == [("clear_clip", GARAGE)]
    assert calls.index(("clear_clip", GARAGE)) > calls.index(("restore", GARAGE))


async def test_playback_that_never_stops_is_cut_off(sonos, speakers, monkeypatch):
    monkeypatch.setattr(tools_mod, "CLIP_WAIT_SLACK_S", 0.05)
    speakers.after_play = None  # the transport stays PLAYING
    with pytest.raises(ValueError, match="did not finish"):
        await asyncio.wait_for(sonos.announce(None, WAV, 25), 5)
    calls = speakers.calls
    stop = calls.index(("transport", OFFICE, "stop"))
    assert stop > calls.index(("play_uri", OFFICE, URL, TITLE))
    assert stop < min(last(calls, ("restore", ip)) for ip in ALL)
    assert {c[1] for c in calls if c[0] == "restore"} == set(ALL)


async def test_a_lead_that_fails_to_play_still_puts_everything_back(sonos, speakers):
    before = topology(speakers)

    def play_uri(ip, url, title):
        raise SpeakerError("network", "ConnectionError")
    speakers.play_uri = play_uri
    with pytest.raises(ValueError, match="Office did not answer"):
        await sonos.announce(None, WAV, 25)
    assert {c[1] for c in speakers.calls if c[0] == "restore"} == set(ALL)
    assert topology(speakers) == before


async def test_tv_speaker_is_left_alone(sonos, speakers):
    speakers.sources = {PATIO: "tv"}
    result = await sonos.announce(None, WAV, 25)
    assert result.played == ["Den", "Garage", "Kitchen", "Office"]
    assert result.failed == {}
    assert [c for c in speakers.calls if c[1] == PATIO and c[0] != "snapshot"] == []


async def test_off_and_nothing_to_play_on(sonos, svc, speakers):
    with pytest.raises(ValueError, match="No Sonos speaker answered"):
        await sonos.announce(["Attic"], WAV, 25)
    speakers.down.update(ALL)
    with pytest.raises(ValueError, match="No Sonos speaker answered"):
        await sonos.announce(None, WAV, 25)
    svc.settings.enabled = False
    with pytest.raises(ValueError) as e:
        await sonos.announce(None, WAV, 25)
    assert str(e.value) == OFF


async def test_a_snapshot_failure_drops_that_speaker(sonos, speakers):
    real = speakers.snapshot

    def snapshot(ip):
        if ip == KITCHEN:
            raise SpeakerError("network", "ConnectionError")
        return real(ip)
    speakers.snapshot = snapshot
    result = await sonos.announce(None, WAV, 25)
    assert result.failed == {"Kitchen": "did not answer"}
    assert "Kitchen" not in result.played
    assert [c for c in speakers.calls if c[1] == KITCHEN] == []


async def test_not_a_wav_is_refused(sonos, speakers):
    with pytest.raises(ValueError):
        await sonos.announce(None, b"not a wav", 25)
    assert speakers.calls == []


async def test_ledger_holds_counts_only(sonos, vault):
    await sonos.announce(None, WAV, 25)
    entries = [e.data for e in Ledger(vault).tail(0, kind="sonos") if e.data.get("event") == "announce"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["played"] == 5 and entry["failed"] == 0 and entry["error"] == ""
    text = repr(entry)
    for secret in ALL + NAMES + [URL, "10.0.0.9"]:
        assert secret not in text


async def test_an_early_stopped_reading_does_not_end_the_wait(sonos, speakers):
    # SoCo sets the address (STOPPED) before Play, and the lead was paused in step 3: neither is the clip ending
    speakers.after_play = lambda ip: speakers.state_script.__setitem__(
        ip, ["STOPPED", "PAUSED_PLAYBACK", "PLAYING", "STOPPED"])
    result = await sonos.announce(None, WAV, 25)
    assert speakers.state_script[OFFICE] == []  # every reading was taken: the wait went on past the early ones
    assert ("transport", OFFICE, "stop") not in speakers.calls
    assert result.played == NAMES


async def test_playing_never_seen_is_cut_off_as_a_failure(sonos, speakers, monkeypatch):
    monkeypatch.setattr(tools_mod, "CLIP_WAIT_SLACK_S", 0.05)
    speakers.after_play = lambda ip: speakers.state_script.__setitem__(ip, ["STOPPED"])
    with pytest.raises(ValueError, match="did not finish"):
        await asyncio.wait_for(sonos.announce(None, WAV, 25), 5)
    assert ("transport", OFFICE, "stop") in speakers.calls
    assert {c[1] for c in speakers.calls if c[0] == "restore"} == set(ALL)


def failing(speakers, method, bad_ip):
    real = getattr(speakers, method)

    def call(ip, *args):
        if ip == bad_ip:
            raise SpeakerError("network", "ConnectionError")
        return real(ip, *args)
    setattr(speakers, method, call)


async def test_a_member_that_will_not_leave_is_turned_down_before_the_clip(sonos, speakers):
    failing(speakers, "unjoin", DEN)  # Den was not named, but cannot leave Office's group
    speakers.volumes[DEN] = 90
    before = topology(speakers)
    result = await sonos.announce(["Office"], WAV, 25)
    calls = speakers.calls
    play = calls.index(("play_uri", OFFICE, URL, TITLE))
    assert ("set_volume", DEN, 25) in calls[:play]
    assert result.played == ["Office"]
    assert ("restore", DEN) in calls  # its own volume comes back
    assert topology(speakers) == before


async def test_a_member_that_cannot_be_quieted_stops_the_briefing(sonos, speakers):
    failing(speakers, "unjoin", DEN)
    failing(speakers, "set_volume", DEN)
    with pytest.raises(ValueError, match="did not play"):
        await sonos.announce(["Office"], WAV, 25)
    assert not [c for c in speakers.calls if c[0] == "play_uri"]
    assert {c[1] for c in speakers.calls if c[0] == "restore"} == {OFFICE, DEN}


async def test_a_target_whose_volume_and_leaving_fail_stops_the_briefing(sonos, speakers):
    failing(speakers, "set_volume", PATIO)
    failing(speakers, "unjoin", PATIO)
    with pytest.raises(ValueError, match="did not play"):
        await sonos.announce(None, WAV, 25)
    assert not [c for c in speakers.calls if c[0] == "play_uri"]
    assert {c[1] for c in speakers.calls if c[0] == "restore"} == set(ALL)


async def test_a_group_with_a_snapshot_failure_is_left_alone(sonos, speakers):
    failing(speakers, "snapshot", DEN)
    before = topology(speakers)
    result = await sonos.announce(None, WAV, 25)
    assert result.failed == {"Den": "did not answer", "Office": "did not answer"}
    assert result.played == ["Garage", "Kitchen", "Patio"]
    assert [c for c in speakers.calls if c[1] in (DEN, OFFICE) and c[0] != "snapshot"] == []
    assert topology(speakers) == before


async def test_unjoining_a_coordinator_hands_its_group_on(speakers):
    speakers.unjoin(OFFICE)
    assert topology(speakers)[DEN] == f"RINCON_{DEN}"  # Den, Office's only member, leads what is left
    assert topology(speakers)[OFFICE] == f"RINCON_{OFFICE}"

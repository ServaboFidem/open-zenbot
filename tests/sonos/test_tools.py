"""The sonos_* tools against FakeSpeakers: matching, coordinators, the volume ceiling, the bot-room rule."""

import pytest

from src.harness.tools import ToolRunner, current_call
from src.memory.ledger import Ledger
from src.providers.interface import ToolCall
from src.skills.catalog import Catalog
from src.sonos.devices import Favorite, NowPlaying, SpeakerError
from src.sonos.service import SonosService
from src.sonos.tools import CONTENT_NOTE, GROUP, NAMES, SonosTools, PLAY_TIMEOUT_S
from tests.sonos.conftest import room

KITCHEN, LIVING, PATIO = "192.0.2.10", "192.0.2.11", "192.0.2.12"


@pytest.fixture
def speakers(fake_speakers):
    fake_speakers.found = [room("Kitchen", KITCHEN), room("Living Room", LIVING), room("Patio", PATIO, coord=LIVING)]
    fake_speakers.favs = [Favorite("Morning Jazz", "favorite"), Favorite("Dinner", "playlist"),
                          Favorite("Jazz", "playlist"), Favorite("Jazz", "favorite")]
    fake_speakers.volumes = {KITCHEN: 30, LIVING: 30, PATIO: 30}
    return fake_speakers


@pytest.fixture
async def svc(vault, speakers):
    s = SonosService(vault, Ledger(vault), speakers=speakers)
    await s.look()
    yield s
    await s.close()


@pytest.fixture
def rooms_in():
    return set()


@pytest.fixture
def sonos(svc, rooms_in):
    return SonosTools(svc, in_room=lambda sid: sid in rooms_in)


@pytest.fixture
def runner(tmp_path):
    return ToolRunner(tmp_path)


def sonos_names(runner):
    return sorted(n for n in runner.names() if n.startswith("sonos_"))


def test_register_follows_rooms(sonos, svc, runner):
    catalog = Catalog()
    assert sonos.register(runner, catalog) is True
    sonos.register(runner, catalog)
    assert sonos_names(runner) == sorted(NAMES)
    assert [e.ref for e in catalog.mcps()] == [GROUP]
    assert all(runner.group_of(n) == GROUP for n in NAMES)
    svc.rooms = []
    assert sonos.register(runner, catalog) is False
    assert sonos_names(runner) == [] and catalog.mcps() == []


async def test_not_offered_in_a_bot_room(sonos, runner, rooms_in):
    sonos.register(runner, Catalog())
    rooms_in.add("room-session")
    assert "sonos_play" not in {s.name for s in runner.specs(caller=("zenbot", "room-session"))}
    assert "sonos_play" in {s.name for s in runner.specs(caller=("zenbot", "chat-session"))}
    token = current_call.set(("zenbot", "room-session"))
    try:
        with pytest.raises(ValueError, match="not used in a bot room"):
            await sonos.play("Kitchen", "Morning Jazz")
    finally:
        current_call.reset(token)


async def test_room_name_matching(sonos, speakers):
    await sonos.play(" kitchen ", "Morning Jazz")
    assert ("play", KITCHEN, "Morning Jazz", "favorite") in speakers.calls
    with pytest.raises(ValueError, match="Sonos rooms: Kitchen, Living Room, Patio"):
        await sonos.play("Garage", "Morning Jazz")


async def test_play_goes_to_coordinator(sonos, speakers):
    await sonos.play("Patio", "Dinner")
    assert speakers.calls[-1] == ("play", LIVING, "Dinner", "playlist")


async def test_same_name_prefers_favorite(sonos, speakers):
    answer = await sonos.play("Kitchen", "jazz")
    assert speakers.calls[-1] == ("play", KITCHEN, "Jazz", "favorite")
    assert "the favorite" in answer


async def test_unknown_favorite_lists_closest(sonos):
    with pytest.raises(ValueError, match="Morning Jazz"):
        await sonos.play("Kitchen", "Morning Jaz")


async def test_play_lowers_loud_speakers_first(sonos, speakers):
    speakers.volumes.update({LIVING: 80, PATIO: 30})
    answer = await sonos.play("Patio", "Dinner")
    assert speakers.calls == [("set_volume", LIVING, 60), ("play", LIVING, "Dinner", "playlist")]
    assert "Turned Living Room down to 60 first." in answer


async def test_resume_lowers_but_pause_does_not(sonos, speakers):
    speakers.volumes[KITCHEN] = 90
    await sonos.control("Kitchen", "pause")
    assert speakers.calls == [("transport", KITCHEN, "pause")]
    await sonos.control("Kitchen", "resume")
    assert speakers.calls[1:] == [("set_volume", KITCHEN, 60), ("transport", KITCHEN, "resume")]
    with pytest.raises(ValueError):
        await sonos.control("Kitchen", "shuffle")


async def test_volume_ceiling(sonos, speakers):
    speakers.volumes[KITCHEN] = 50
    with pytest.raises(ValueError, match="The highest volume ZenBot may set is 60."):
        await sonos.volume("Kitchen", level=70)
    await sonos.volume("Kitchen", change=5)
    assert speakers.volumes[KITCHEN] == 55
    speakers.volumes[KITCHEN] = 80
    await sonos.volume("Kitchen", change=-10)
    assert speakers.volumes[KITCHEN] == 70
    await sonos.volume("Kitchen", level=0)
    assert speakers.volumes[KITCHEN] == 0
    with pytest.raises(ValueError):
        await sonos.volume("Kitchen", level=10, change=5)
    with pytest.raises(ValueError):
        await sonos.volume("Kitchen")
    with pytest.raises(ValueError):
        await sonos.volume("Kitchen", level=101)


async def test_volume_goes_to_the_room_not_the_group(sonos, speakers):
    await sonos.volume("Patio", level=40)
    assert speakers.calls[-1] == ("set_volume", PATIO, 40)


async def test_group_and_ungroup(sonos, speakers):
    speakers.volumes[PATIO] = 75
    await sonos.group(["Kitchen", "Patio"])
    assert speakers.calls == [("set_volume", PATIO, 60), ("join", PATIO, KITCHEN)]
    with pytest.raises(ValueError):
        await sonos.group(["Kitchen"])
    await sonos.ungroup("Patio")
    assert speakers.calls[-1] == ("unjoin", PATIO)


async def test_dead_speaker_is_dropped_and_tools_go(vault, speakers, runner):
    speakers.found = [room("Kitchen", KITCHEN)]
    svc = SonosService(vault, Ledger(vault), speakers=speakers)
    catalog = Catalog()
    sonos = SonosTools(svc, in_room=lambda sid: False)
    svc.on_change = lambda _found: sonos.register(runner, catalog)
    await svc.look()
    assert "sonos_play" in runner.names()
    speakers.down.add(KITCHEN)
    with pytest.raises(ValueError, match="Kitchen did not answer."):
        await sonos.volume("Kitchen", level=10)
    assert svc.rooms == []
    assert sonos_names(runner) == []
    await svc.close()


async def test_command_failure_drops_the_room(sonos, svc, speakers):
    def failing(ip, fav):
        raise SpeakerError("network")

    speakers.play = failing
    with pytest.raises(ValueError, match="Kitchen did not answer."):
        await sonos.play("Kitchen", "Morning Jazz")
    assert "Kitchen" not in [r.name for r in svc.rooms]


async def test_ledger_has_no_titles(sonos, vault):
    await sonos.play("Kitchen", "Morning Jazz")
    ledger = Ledger(vault)
    (cmd,) = [e for e in ledger.tail(0, kind="sonos") if e.data.get("event") == "command"]
    assert cmd.data["tool"] == "sonos_play" and cmd.data["room"] == "Kitchen" and cmd.data["ok"] is True
    assert "Morning Jazz" not in ledger.file.read_text(encoding="utf-8")


async def test_listings_start_with_content_note_and_cap(sonos, speakers):
    assert (await sonos.rooms()).startswith(CONTENT_NOTE)
    speakers.favs = [Favorite(f"Fav {i:03}", "favorite") for i in range(250)]
    listing = await sonos.favorites()
    assert listing.startswith(CONTENT_NOTE)
    assert "Fav 199" in listing and "Fav 200" not in listing


async def test_rooms_lists_groups_and_playing(sonos, speakers):
    speakers.states[LIVING] = NowPlaying("playing", "So What", "Miles Davis")
    text = await sonos.rooms()
    assert "Patio" in text and "So What" in text and "Living Room" in text


async def test_runs_through_the_tool_runner(sonos, runner):
    sonos.register(runner, Catalog())
    result = await runner.run(ToolCall("1", "sonos_play", {"room": "Garage", "name": "Dinner"}))
    assert result.ok is False and "No Sonos room named 'Garage'" in result.error
    result = await runner.run(ToolCall("2", "sonos_play", {"room": "Kitchen", "name": "Dinner"}))
    assert result.ok is True


async def test_a_failing_group_member_is_named_and_dropped_not_the_target(sonos, svc, speakers):
    # Patio still listed in the topology but not answering: playing in Living Room must not blame Living Room
    speakers.down.add(PATIO)
    with pytest.raises(ValueError, match="Patio did not answer"):
        await sonos.play("Living Room", "Dinner")
    names = {r.name for r in svc.rooms}
    assert "Living Room" in names and "Patio" not in names
    assert not any(c[0] == "play" for c in speakers.calls)  # its volume could not be checked, so nothing played


async def test_play_gives_queueing_a_long_timeout(sonos, svc, monkeypatch):
    seen = []
    real = svc.call

    async def call(fn, *args, timeout=10):
        seen.append((getattr(fn, "__name__", ""), timeout))
        return await real(fn, *args, timeout=timeout)

    monkeypatch.setattr(svc, "call", call)
    await sonos.play("Kitchen", "Dinner")
    assert ("play", PLAY_TIMEOUT_S) in seen and PLAY_TIMEOUT_S > 30

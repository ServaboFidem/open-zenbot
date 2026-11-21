"""The Sonos page's commands (SonosTools.panel_*) against FakeSpeakers: playing on a chosen set of speakers,
transport, volume, the ceiling, and what reaches the ledger."""

import pytest

from src.memory.ledger import Ledger
from src.sonos.devices import Favorite, NowPlaying
from src.sonos.service import SonosService
from src.sonos.tools import OFF, SonosTools
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
def sonos(svc):
    return SonosTools(svc, in_room=lambda sid: False)


def commands(vault):
    return [e.data for e in Ledger(vault).tail(0, kind="sonos") if e.data.get("event") == "command"]


async def test_rooms_rows(sonos, speakers):
    speakers.states = {LIVING: NowPlaying("playing", "So What", "Miles Davis")}
    speakers.volumes[KITCHEN] = 12
    rows = await sonos.panel_rooms()
    assert [r["name"] for r in rows] == ["Kitchen", "Living Room", "Patio"]
    assert rows[2] == {"name": "Patio", "group": "Living Room", "lead": False, "model": "Sonos One",
                       "state": "playing", "title": "So What", "artist": "Miles Davis", "volume": 30}
    assert rows[0]["lead"] is True and rows[0]["volume"] == 12 and rows[0]["state"] == "stopped"


async def test_rooms_volume_unknown_when_unreadable(sonos, speakers):
    speakers.down.add(PATIO)  # listed by the topology read, but its own volume read fails
    rows = await sonos.panel_rooms()
    assert next(r for r in rows if r["name"] == "Patio")["volume"] is None


async def test_favorites_sorted_with_kind(sonos):
    assert await sonos.panel_favorites() == [
        {"title": "Dinner", "kind": "playlist"}, {"title": "Jazz", "kind": "favorite"},
        {"title": "Jazz", "kind": "playlist"}, {"title": "Morning Jazz", "kind": "favorite"}]


async def test_play_on_one_lone_speaker(sonos, speakers, vault):
    msg = await sonos.panel_play(["Kitchen"], "Morning Jazz")
    assert speakers.calls == [("play", KITCHEN, "Morning Jazz", "favorite")]
    assert msg == "Playing Morning Jazz in Kitchen."
    assert commands(vault)[-1] == {"event": "command", "tool": "panel_play", "room": "Kitchen", "ok": True, "error": ""}


async def test_play_on_two_speakers_groups_them(sonos, speakers):
    msg = await sonos.panel_play(["Kitchen", "Living Room"], "Dinner")
    assert ("join", LIVING, KITCHEN) in speakers.calls
    assert speakers.calls[-1] == ("play", KITCHEN, "Dinner", "playlist")
    assert msg == "Playing Dinner in Kitchen and Living Room."


async def test_play_drops_unchosen_members_of_the_leads_group(sonos, speakers):
    await sonos.panel_play(["Living Room"], "Dinner")
    assert ("unjoin", PATIO) in speakers.calls
    assert speakers.calls[-1] == ("play", LIVING, "Dinner", "playlist")


async def test_play_on_a_member_takes_it_out_of_its_group(sonos, speakers):
    await sonos.panel_play(["Patio"], "Dinner")
    assert speakers.calls == [("unjoin", PATIO), ("play", PATIO, "Dinner", "playlist")]


async def test_play_lowers_every_chosen_speaker_before_joining(sonos, speakers):
    speakers.volumes = {KITCHEN: 90, LIVING: 80, PATIO: 30}
    msg = await sonos.panel_play(["Kitchen", "Living Room"], "Dinner")
    calls = speakers.calls
    assert calls.index(("set_volume", KITCHEN, 60)) < calls.index(("join", LIVING, KITCHEN))
    assert calls.index(("set_volume", LIVING, 60)) < calls.index(("join", LIVING, KITCHEN))
    assert "Turned Kitchen down to 60 first." in msg and "Turned Living Room down to 60 first." in msg


async def test_play_refusals(sonos, svc):
    with pytest.raises(ValueError, match="Choose at least one speaker"):
        await sonos.panel_play([], "Dinner")
    with pytest.raises(ValueError, match="No Sonos room named 'Attic'"):
        await sonos.panel_play(["Attic"], "Dinner")
    with pytest.raises(ValueError, match="No Sonos Favorite or playlist named 'Dinnr'.*Dinner"):
        await sonos.panel_play(["Kitchen"], "Dinnr")
    svc.settings.enabled = False
    with pytest.raises(ValueError, match=OFF):
        await sonos.panel_play(["Kitchen"], "Dinner")


async def test_play_names_a_speaker_once(sonos):
    assert await sonos.panel_play(["Kitchen", "kitchen"], "Dinner") == "Playing Dinner in Kitchen."


async def test_control_goes_to_the_coordinator(sonos, speakers, vault):
    assert await sonos.panel_control("Patio", "pause") == "Done: pause in Patio."
    assert speakers.calls == [("transport", LIVING, "pause")]
    assert commands(vault)[-1]["tool"] == "panel_control"
    with pytest.raises(ValueError, match="The action is one of"):
        await sonos.panel_control("Patio", "shuffle")


async def test_resume_lowers_first(sonos, speakers):
    speakers.volumes[PATIO] = 95
    await sonos.panel_control("Living Room", "resume")
    assert speakers.calls.index(("set_volume", PATIO, 60)) < speakers.calls.index(("transport", LIVING, "resume"))


async def test_volume_sets_and_keeps_the_ceiling(sonos, speakers, vault):
    assert await sonos.panel_volume("Kitchen", 45) == "Kitchen volume is 45."
    assert speakers.volumes[KITCHEN] == 45
    with pytest.raises(ValueError, match="The highest volume ZenBot may set is 60."):
        await sonos.panel_volume("Kitchen", 75)
    assert commands(vault)[-1]["tool"] == "panel_volume"
    with pytest.raises(ValueError, match="0 to 100"):
        await sonos.panel_volume("Kitchen", 101)


async def test_panel_works_while_a_bot_room_turn_runs(svc, speakers):
    """The panel is the user's own: the bot-room rule is about bots, so it never refuses a panel command."""
    from src.harness.tools import current_call
    tools = SonosTools(svc, in_room=lambda sid: True)
    token = current_call.set(("zenbot", "room-session"))
    try:
        assert await tools.panel_play(["Kitchen"], "Dinner") == "Playing Dinner in Kitchen."
    finally:
        current_call.reset(token)

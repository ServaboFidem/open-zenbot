import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.briefing import service as service_mod
from src.briefing.content import Built
from src.briefing.service import Busy, BriefingService, NotReady
from src.briefing.settings import Briefing, BriefingState, BriefingStore, Slot, ToldJokes
from src.generation.speech import SpeechError
from src.memory.ledger import Ledger
from src.sonos.tools import NO_SPEAKER, Announcement

TEXT = "Good morning Sam, SECRET-BRIEFING-TEXT about Dentist."
JOKE = "Why did the UNIQUE-JOKE-WORD cross the road?"
WAV = b"RIFF-fake-wav"


def at(hour, minute, second=0, day=24):
    return datetime(2026, 11, day, hour, minute, second).astimezone()


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    async def sleep(self, s):
        self.now += timedelta(seconds=s)
        await asyncio.sleep(0)


class Fakes:
    def __init__(self, vault, clock, announce=None):
        self.vault, self.clock = vault, clock
        self.builds, self.says, self.announces = [], [], []
        self.build_hook = None
        self.announce_result = announce or Announcement(played=["Kitchen", "Office"], failed={}, seconds=12.0)
        self.announce_error = None
        self.announce_gate = None
        self.sonos = SimpleNamespace(settings=SimpleNamespace(enabled=True, max_volume=60),
                                     rooms=[SimpleNamespace(name="Kitchen"), SimpleNamespace(name="Office")])
        self.voice_ok = True

    async def build(self, now, jokes):
        self.builds.append((self.clock(), now))
        if self.build_hook:
            await self.build_hook()
        return Built(TEXT, ["greeting", "joke", "calendar", "signoff"], JOKE)

    async def say(self, text):
        self.says.append(text)
        return WAV

    async def announce(self, names, wav, volume):
        self.announces.append((self.clock(), names, wav, volume))
        if self.announce_gate:
            await self.announce_gate.wait()
        if self.announce_error:
            raise self.announce_error
        return self.announce_result


def save(vault, enabled=True, time="06:45", speakers=None, volume=25):
    store = BriefingStore.load(vault)
    store.put(Briefing(kind="morning", enabled=enabled, slots=[Slot([0, 1, 2, 3, 4, 5, 6], time)],
                       speakers=speakers, volume=volume, id="b1"))
    store.save(vault)


def make(vault, clock, fakes, ledger=None):
    return BriefingService(vault, ledger or Ledger(vault), build=fakes.build, say=fakes.say,
                           announce=fakes.announce, sonos=fakes.sonos, speech_ready=lambda: fakes.voice_ok,
                           clock=clock, sleep=clock.sleep)


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    save(v)
    return v


async def test_plays_once_at_the_slot(vault):
    clock = Clock(at(6, 40))
    f = Fakes(vault, clock)
    svc = make(vault, clock, f)
    await svc.tick()
    assert len(f.announces) == 1
    assert f.says == [TEXT]
    run = BriefingState.load(vault).last_run("morning")
    assert run["outcome"] == "played" and run["played"] == 2 and run["failed"] == {}
    clock.now = at(6, 46)
    await svc.tick()
    clock.now = at(6, 55)
    await svc.tick()
    assert len(f.announces) == 1 and len(f.builds) == 1


async def test_builds_five_minutes_early_and_plays_on_time(vault):
    clock = Clock(at(6, 39, 50))
    f = Fakes(vault, clock)
    svc = make(vault, clock, f)
    await svc.tick()
    assert f.builds == []  # not yet five minutes before
    clock.now = at(6, 40, 10)
    await svc.tick()
    assert f.builds[0][0] == at(6, 40, 10)
    assert f.builds[0][1] == at(6, 45)  # built for the slot
    played_at = f.announces[0][0]
    assert at(6, 45) <= played_at < at(6, 46)
    assert f.announces[0][1] is None and f.announces[0][3] == 25


async def test_a_claimed_slot_is_not_played_after_restart(vault):
    class Crash(BaseException):
        pass

    clock = Clock(at(6, 41))
    f = Fakes(vault, clock)

    async def crash():
        raise Crash()

    f.build_hook = crash
    with pytest.raises(Crash):
        await make(vault, clock, f).tick()
    assert BriefingState.load(vault).claimed("morning") is not None
    f.build_hook = None
    clock.now = at(6, 46)
    again = make(vault, clock, f)
    await again.tick()
    assert f.announces == [] and len(f.builds) == 1


async def test_more_than_fifteen_minutes_late_is_skipped(vault):
    st = BriefingState.load(vault)
    st.claim("morning", at(6, 45, day=23).isoformat())  # yesterday's slot was claimed
    st.save(vault)
    clock = Clock(at(7, 1))  # 16 minutes after today's slot: the harness was off
    f = Fakes(vault, clock)
    await make(vault, clock, f).tick()
    assert f.builds == [] and f.announces == []
    st = BriefingState.load(vault)
    assert st.last_run("morning")["outcome"] == "skipped_late"
    assert st.last_skip("morning")["outcome"] == "skipped_late"
    assert datetime.fromisoformat(st.claimed("morning")) == at(6, 45)

    # a build that runs past the limit is skipped too
    clock.now = at(6, 40, day=25)
    f2 = Fakes(vault, clock)

    async def slow():
        clock.now += timedelta(minutes=21)

    f2.build_hook = slow
    await make(vault, clock, f2).tick()
    assert f2.announces == []
    assert BriefingState.load(vault).last_run("morning")["outcome"] == "skipped_late"


async def test_switching_off_during_the_build_plays_nothing(vault):
    clock = Clock(at(6, 40))
    f = Fakes(vault, clock)

    async def off():
        save(vault, enabled=False)

    f.build_hook = off
    await make(vault, clock, f).tick()
    assert f.announces == []
    assert BriefingState.load(vault).last_run("morning") is None

    # a slot moved during the build is dropped as well
    save(vault, time="06:45")
    clock.now = at(6, 40, day=25)
    f2 = Fakes(vault, clock)

    async def moved():
        save(vault, time="07:30")

    f2.build_hook = moved
    await make(vault, clock, f2).tick()
    assert f2.announces == []
    assert BriefingState.load(vault).last_run("morning") is None


async def test_one_failed_speaker_is_in_last_run(vault):
    clock = Clock(at(6, 40))
    f = Fakes(vault, clock, Announcement(played=["Kitchen"], failed={"Office": "did not answer"}, seconds=9.5,
                                         phone_paused=["Kitchen"]))
    await make(vault, clock, f).tick()
    run = BriefingState.load(vault).last_run("morning")
    assert run["outcome"] == "played" and run["played"] == 1
    assert run["failed"] == {"Office": "did not answer"}
    assert run["phone_paused"] == ["Kitchen"]


async def test_joke_saved_only_when_something_played(vault):
    clock = Clock(at(10, 0))
    f = Fakes(vault, clock)
    f.announce_error = ValueError(NO_SPEAKER)
    svc = make(vault, clock, f)
    out = await svc.play_now("morning")
    assert out["outcome"] == "failed" and out["reason"] == NO_SPEAKER
    assert ToldJokes.load(vault).recent() == []
    f.announce_error = None
    f.announce_result = Announcement(played=[], failed={"Office": "did not answer"}, seconds=0.0)
    await svc.play_now("morning")
    assert ToldJokes.load(vault).recent() == []
    f.announce_result = Announcement(played=["Kitchen"], failed={}, seconds=5.0)
    out = await svc.play_now("morning")
    assert out["outcome"] == "played"
    assert ToldJokes.load(vault).recent() == [JOKE]


async def test_not_ready_records_skip_with_reason(vault):
    clock = Clock(at(6, 40))
    f = Fakes(vault, clock)
    f.sonos.settings.enabled = False
    svc = make(vault, clock, f)
    assert svc.readiness() == "sonos_off"
    await svc.tick()  # before the slot it waits: a speaker look may still come in time
    assert BriefingState.load(vault).last_skip("morning") is None
    clock.now = at(6, 45)
    await svc.tick()
    assert f.builds == [] and f.announces == []
    st = BriefingState.load(vault)
    assert st.last_skip("morning")["reason"] == "sonos_off"
    assert st.last_run("morning")["outcome"] == "skipped"
    with pytest.raises(NotReady) as e:
        await svc.play_now("morning")
    assert e.value.reason == "sonos_off"
    f.sonos.settings.enabled = True
    f.sonos.settings.max_volume = 0
    assert svc.readiness() == "ceiling_zero"
    f.sonos.settings.max_volume = 60
    f.sonos.rooms = []
    assert svc.readiness() == "no_speakers"
    f.sonos.rooms = [SimpleNamespace(name="Kitchen")]
    f.voice_ok = False
    assert svc.readiness() == "no_voice"
    f.voice_ok = True
    assert svc.readiness() is None


async def test_play_now_claims_no_slot_and_refuses_while_busy(vault):
    clock = Clock(at(10, 0))
    f = Fakes(vault, clock)
    f.announce_gate = asyncio.Event()
    svc = make(vault, clock, f)
    first = asyncio.create_task(svc.play_now("morning"))
    while not f.announces:
        await asyncio.sleep(0)
    assert svc.status()["briefings"][0]["state"] == "playing"
    with pytest.raises(Busy) as e:
        await svc.play_now("morning")
    assert str(e.value) == "A briefing is already playing."
    f.announce_gate.set()
    out = await first
    assert out["outcome"] == "played"
    assert BriefingState.load(vault).claimed("morning") is None
    assert svc.status()["briefings"][0]["state"] == "idle"


async def test_play_now_volume_never_above_the_ceiling(vault):
    save(vault, volume=50)
    clock = Clock(at(10, 0))
    f = Fakes(vault, clock)
    f.sonos.settings.max_volume = 30
    await make(vault, clock, f).play_now("morning")
    assert f.announces[0][3] == 30


async def test_ledger_entries_hold_no_text(vault):
    save(vault, speakers=["Kitchen", "Office"])
    clock = Clock(at(6, 40))
    f = Fakes(vault, clock, Announcement(played=["Kitchen"], failed={"Office": "did not answer"}, seconds=9.5))
    ledger = Ledger(vault)
    svc = make(vault, clock, f, ledger)
    await svc.tick()
    f.announce_error = ValueError("Office did not answer.")
    await svc.play_now("morning")

    async def bad_say(text):
        raise SpeechError(409, "The voice is not set up.")

    svc._say = bad_say
    out = await svc.play_now("morning")
    assert out["outcome"] == "failed"
    raw = ledger.file.read_text(encoding="utf-8")
    assert '"briefing"' in raw
    for secret in ("SECRET-BRIEFING-TEXT", "UNIQUE-JOKE-WORD", "Kitchen", "Office", "Dentist", "Sam"):
        assert secret not in raw


async def test_status_lists_next_run_and_readiness(vault):
    clock = Clock(at(6, 0))
    f = Fakes(vault, clock)
    s = make(vault, clock, f).status()
    b = s["briefings"][0]
    assert b["kind"] == "morning" and b["ready"] is True and b["reason"] is None
    assert datetime.fromisoformat(b["next_run"]) == at(6, 45)
    assert s["ceiling"] == 60 and s["rooms"] == ["Kitchen", "Office"] and s["hosted"] is None


async def test_loop_survives_an_exception(vault, monkeypatch):
    class Stop(BaseException):
        pass

    clock = Clock(at(6, 0))
    f = Fakes(vault, clock)
    svc = make(vault, clock, f)
    calls = []

    async def tick():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    sleeps = []

    async def sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 3:
            raise Stop()

    svc.tick = tick
    svc._sleep = sleep
    with pytest.raises(Stop):
        await svc.run()
    assert len(calls) == 3 and sleeps == [service_mod.INTERVAL_S] * 3


async def test_an_edit_after_a_play_is_no_late_skip(vault):
    clock = Clock(at(6, 40))
    f = Fakes(vault, clock)
    svc = make(vault, clock, f)
    await svc.tick()
    assert len(f.announces) == 1
    clock.now = at(9, 0)
    save(vault, time="07:30")
    svc.settings_saved("morning")
    await svc.tick()
    st = BriefingState.load(vault)
    assert st.last_run("morning")["outcome"] == "played" and st.last_skip("morning") is None
    assert datetime.fromisoformat(svc.status()["briefings"][0]["next_run"]) == at(7, 30, day=25)
    clock.now = at(7, 25, day=25)
    await svc.tick()
    assert len(f.announces) == 2  # tomorrow's 07:30 plays


async def test_one_briefing_a_day_after_moving_it_later(vault):
    clock = Clock(at(6, 40))
    f = Fakes(vault, clock)
    svc = make(vault, clock, f)
    await svc.tick()
    clock.now = at(9, 1)
    save(vault, time="09:05")
    svc.settings_saved("morning")
    assert datetime.fromisoformat(svc.status()["briefings"][0]["next_run"]) == at(9, 5, day=25)
    for t in (at(9, 2), at(9, 5), at(9, 10)):
        clock.now = t
        await svc.tick()
    assert len(f.announces) == 1 and len(f.builds) == 1
    assert BriefingState.load(vault).last_skip("morning") is None


async def test_re_enabled_after_days_off_is_no_late_skip(vault):
    clock = Clock(at(6, 40, day=20))
    f = Fakes(vault, clock)
    svc = make(vault, clock, f)
    await svc.tick()  # played on the 20th
    save(vault, enabled=False)
    svc.settings_saved("morning")
    clock.now = at(9, 0, day=24)
    save(vault, enabled=True)
    svc.settings_saved("morning")
    await svc.tick()
    st = BriefingState.load(vault)
    assert st.last_skip("morning") is None and st.last_run("morning")["outcome"] == "played"
    assert datetime.fromisoformat(svc.status()["briefings"][0]["next_run"]) == at(6, 45, day=25)


async def test_a_gap_while_the_harness_was_off_is_still_a_late_skip(vault):
    clock = Clock(at(6, 40, day=20))
    f = Fakes(vault, clock)
    await make(vault, clock, f).tick()
    clock.now = at(9, 0, day=24)  # no save in between: the harness was off
    svc = make(vault, clock, f)
    assert datetime.fromisoformat(svc.status()["briefings"][0]["next_run"]) == at(6, 45, day=25)
    await svc.tick()
    assert BriefingState.load(vault).last_skip("morning")["outcome"] == "skipped_late"


async def test_ten_minutes_late_still_plays(vault):
    st = BriefingState.load(vault)
    st.claim("morning", at(6, 45, day=23).isoformat())
    st.save(vault)
    clock = Clock(at(6, 55))
    f = Fakes(vault, clock)
    await make(vault, clock, f).tick()
    assert len(f.announces) == 1
    assert BriefingState.load(vault).last_run("morning")["outcome"] == "played"


async def test_a_cancel_during_announce_records_nothing_and_frees_the_service(vault):
    clock = Clock(at(6, 40))
    f = Fakes(vault, clock)
    f.announce_gate = asyncio.Event()
    svc = make(vault, clock, f)
    task = asyncio.create_task(svc.tick())
    while not f.announces:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert svc._running is None
    assert BriefingState.load(vault).last_run("morning") is None
    f.announce_gate = None
    out = await svc.play_now("morning")
    assert out["outcome"] == "played"


async def test_start_play_now_refuses_at_once_and_runs_in_the_background(vault):
    clock = Clock(at(10, 0))
    f = Fakes(vault, clock)
    f.announce_gate = asyncio.Event()
    svc = make(vault, clock, f)
    task = svc.start_play_now("morning")
    assert svc.status()["briefings"][0]["state"] == "building"  # set before the task first runs
    with pytest.raises(Busy):
        svc.start_play_now("morning")
    f.sonos.settings.enabled = False
    with pytest.raises(Busy):  # busy is checked first
        svc.start_play_now("morning")
    f.sonos.settings.enabled = True
    f.announce_gate.set()
    assert (await task)["outcome"] == "played"
    assert svc._running is None
    # a task cancelled before it ever ran still frees the service
    t2 = svc.start_play_now("morning")
    t2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t2
    await asyncio.sleep(0)
    assert svc._running is None
    f.sonos.rooms = []
    with pytest.raises(NotReady):
        svc.start_play_now("morning")

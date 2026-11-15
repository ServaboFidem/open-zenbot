"""scheduler: routines run on their schedule, in the user's local time."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.harness.loop import Session, SessionState
from src.harness.scheduler import (
    Routine, RoutineError, RoutineStore, Schedule, Scheduler, due, next_run, parse_schedule,
)
from src.memory.ledger import Ledger


@pytest.fixture
def ny(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def at(y, mo, d, h=0, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi).astimezone()


def test_next_weekly(ny):
    s = Schedule("weekly", days=[4], time="09:00")  # Friday
    assert next_run(s, at(2026, 11, 13, 8)) == at(2026, 11, 13, 9)
    assert next_run(s, at(2026, 11, 13, 9)) == at(2026, 11, 20, 9)  # strictly after
    assert next_run(Schedule("weekly", days=[0, 4], time="09:00"), at(2026, 11, 13, 10)) == at(2026, 11, 16, 9)


def test_next_daily(ny):
    s = Schedule("daily", time="08:30")
    assert next_run(s, at(2026, 11, 13, 8, 29)) == at(2026, 11, 13, 8, 30)
    assert next_run(s, at(2026, 11, 13, 8, 30)) == at(2026, 11, 14, 8, 30)


def test_next_interval(ny):
    s = Schedule("interval", hours=6)
    assert next_run(s, at(2026, 11, 13, 8)) == at(2026, 11, 13, 14)


def test_dst_spring_forward_and_fall_back(ny):
    nine = Schedule("daily", time="09:00")
    for before, after in [((2026, 3, 7), (2026, 3, 8)), ((2026, 3, 8), (2026, 3, 9)),
                          ((2026, 10, 31), (2026, 11, 1)), ((2026, 11, 1), (2026, 11, 2))]:
        n = next_run(nine, at(*before, 9, 1))
        assert (n.year, n.month, n.day, n.hour, n.minute) == (*after, 9, 0)
    odd = Schedule("daily", time="02:30")  # does not exist on 2026-03-08
    n = next_run(odd, at(2026, 3, 8, 0, 0))
    assert (n.day, n.hour, n.minute) == (8, 3, 0)
    assert n.utcoffset() == timedelta(hours=-4)


def routine(**kw) -> Routine:
    base = dict(id="r1", name="Digest", bot="b", skill="weekly-digest", enabled=True,
                schedule=Schedule("weekly", days=[4], time="09:00"), last_run=None)
    return Routine(**{**base, **kw})


def test_due_run_catch_up_skip(ny):
    r = routine()
    slot = at(2026, 11, 13, 9)
    assert due(r, slot - timedelta(minutes=1), slot - timedelta(minutes=2)) is None
    assert due(r, slot + timedelta(seconds=5), slot - timedelta(seconds=25)) == "run"
    # a gap (start or wake) after the slot: within 24 h it runs once, older is skipped
    assert due(r, slot + timedelta(hours=5), slot - timedelta(hours=1)) == "catch_up"
    assert due(r, slot + timedelta(hours=30), slot - timedelta(hours=1)) == "skip_missed"
    assert due(routine(enabled=False), slot + timedelta(seconds=5), slot - timedelta(seconds=25)) is None


class FakeSession(Session):
    pass


class Rig:
    def __init__(self, vault: Path, routines: list[Routine]):
        self.vault = vault
        self.store = RoutineStore()
        self.store.root = vault
        for r in routines:
            self.store.upsert(r)
        self.ledger = Ledger(vault)
        self.started: list[Routine] = []
        self.sessions: list[Session] = []
        self.fail: str | None = None
        self.sched = Scheduler(lambda: self.store, self._start, self.ledger, clock=lambda: self.now, tick_s=30)

    def _start(self, r: Routine) -> Session:
        if self.fail:
            raise RoutineError(self.fail)
        self.started.append(r)
        s = Session(bot_id=r.bot, task=r.name)
        self.sessions.append(s)
        return s

    def entries(self):
        return [e for e in self.ledger.iter() if e.kind == "routine"]


async def test_catch_up_within_24h_once(vault, ny):
    slot = at(2026, 11, 13, 9)
    rig = Rig(vault, [routine()])
    rig.sched.last_tick = slot - timedelta(hours=2)  # asleep through the slot
    rig.now = slot + timedelta(hours=3)
    await rig.sched.tick()
    await rig.sched.tick()
    rig.now += timedelta(seconds=30)
    await rig.sched.tick()
    assert len(rig.started) == 1
    assert rig.store.get("r1").last_run["outcome"] == "started"
    assert rig.store.get("r1").last_run["session_id"] == rig.sessions[0].id
    assert [e.data["outcome"] for e in rig.entries()] == ["started"]


async def test_skip_missed_older_than_24h(vault, ny):
    slot = at(2026, 11, 13, 9)
    rig = Rig(vault, [routine()])
    rig.sched.last_tick = slot - timedelta(hours=2)
    rig.now = slot + timedelta(hours=30)
    await rig.sched.tick()
    assert rig.started == []
    assert rig.store.get("r1").last_run is None
    assert rig.store.get("r1").last_skip["outcome"] == "skipped_missed"
    assert [e.data["outcome"] for e in rig.entries()] == ["skipped_missed"]


async def test_overlap_skips(vault, ny):
    rig = Rig(vault, [routine(schedule=Schedule("interval", hours=1), last_run={"at": at(2026, 11, 13, 7).isoformat(), "session_id": "", "outcome": "ok"})])
    rig.now = at(2026, 11, 13, 8, 1)
    rig.sched.last_tick = rig.now - timedelta(seconds=30)
    await rig.sched.tick()
    assert len(rig.started) == 1
    rig.sessions[0].state = SessionState.running
    rig.now += timedelta(hours=1)
    rig.sched.last_tick = rig.now - timedelta(seconds=30)
    await rig.sched.tick()
    assert len(rig.started) == 1
    assert rig.store.get("r1").last_skip["outcome"] == "skipped_overlap"
    assert rig.store.get("r1").last_run["outcome"] == "started"  # the skip leaves the real run alone
    rig.sessions[0].state = SessionState.done
    rig.now += timedelta(hours=1)
    rig.sched.last_tick = rig.now - timedelta(seconds=30)
    await rig.sched.tick()
    assert len(rig.started) == 2
    assert [e.data["outcome"] for e in rig.entries()] == ["started", "skipped_overlap", "ok", "started"]


async def test_failed_start_records_failure_and_run_now_raises(vault, ny):
    rig = Rig(vault, [routine()])
    rig.fail = "Bot 'b' not found."
    slot = at(2026, 11, 13, 9)
    rig.now = slot + timedelta(seconds=5)
    rig.sched.last_tick = slot - timedelta(seconds=25)
    await rig.sched.tick()
    assert rig.store.get("r1").last_run["outcome"] == "failed"
    assert "not found" in rig.store.get("r1").last_run["reason"]
    e = rig.entries()[-1]
    assert e.data["outcome"] == "failed" and "not found" in e.data["error"]
    with pytest.raises(RoutineError, match="not found"):
        await rig.sched.run_now("r1")
    with pytest.raises(KeyError):
        await rig.sched.run_now("nope")


async def test_run_now_starts_and_records(vault, ny):
    rig = Rig(vault, [routine(enabled=False)])
    rig.now = at(2026, 11, 13, 12)
    s = await rig.sched.run_now("r1")
    assert s is rig.sessions[0]
    assert rig.store.get("r1").last_run["outcome"] == "started"


async def test_the_outcome_follows_the_session(vault, ny):
    rig = Rig(vault, [routine(enabled=False)])
    rig.now = at(2026, 11, 13, 12)
    s = await rig.sched.run_now("r1")
    await rig.sched.tick()
    assert rig.store.get("r1").last_run["outcome"] == "started"  # still running
    s.state = SessionState.done
    await rig.sched.tick()
    run = rig.store.get("r1").last_run
    assert run["outcome"] == "ok" and run["session_id"] == s.id and run["at"] == at(2026, 11, 13, 12).isoformat()
    s2 = await rig.sched.run_now("r1")
    s2.state, s2.error = SessionState.failed, "RuntimeError: boom"
    await rig.sched.tick()
    assert rig.store.get("r1").last_run == {"at": rig.now.isoformat(), "session_id": s2.id, "outcome": "failed",
                                            "reason": "RuntimeError: boom"}
    s3 = await rig.sched.run_now("r1")
    s3.state = SessionState.stopped
    await rig.sched.tick()
    assert rig.store.get("r1").last_run["outcome"] == "failed" and rig.store.get("r1").last_run["reason"]
    assert [e.data["outcome"] for e in rig.entries()] == ["started", "ok", "started", "failed", "started", "failed"]


async def test_a_managed_run_without_its_result_failed(vault, ny):
    """A music run that ends without a digest carrying its session id is a failed run."""
    rig = Rig(vault, [routine(enabled=False, managed_by="music")])
    saved: set[str] = set()
    rig.sched.verifiers["music"] = lambda s: "" if s.id in saved else "no digest saved"
    rig.now = at(2026, 11, 13, 12)
    s = await rig.sched.run_now("r1")
    s.state = SessionState.done
    await rig.sched.tick()
    assert rig.store.get("r1").last_run["outcome"] == "failed"
    assert rig.store.get("r1").last_run["reason"] == "no digest saved"
    s2 = await rig.sched.run_now("r1")
    saved.add(s2.id)
    s2.state = SessionState.done
    await rig.sched.tick()
    assert rig.store.get("r1").last_run["outcome"] == "ok"
    s3 = await rig.sched.run_now("r1")
    s3.state, s3.error = SessionState.failed, "max steps (40) reached"
    await rig.sched.tick()
    assert rig.store.get("r1").last_run["reason"] == "max steps (40) reached"


async def test_a_run_lost_to_a_restart_is_failed_not_running_forever(vault, ny):
    r = routine(last_run={"at": at(2026, 11, 13, 9).isoformat(), "session_id": "gone", "outcome": "started"})
    rig = Rig(vault, [r])
    rig.now = at(2026, 11, 13, 12)
    await rig.sched.tick()
    assert rig.store.get("r1").last_run["outcome"] == "failed" and "restart" in rig.store.get("r1").last_run["reason"]


def test_store_round_trip(vault):
    store = RoutineStore.load(vault)
    assert store.all() == []
    r = routine(managed_by="music", last_run={"at": "2026-11-13T09:00:00-05:00", "session_id": "s", "outcome": "ok"})
    store.upsert(r)
    store.last_tick = datetime(2026, 11, 13, 9, tzinfo=timezone.utc)
    store.save(vault)
    again = RoutineStore.load(vault)
    assert again.all() == [r]
    assert again.last_tick == store.last_tick
    assert (vault.parent / "vault-settings" / "routines.json").exists()
    again.delete("r1")
    assert again.get("r1") is None


def test_import_parses_strings():
    assert parse_schedule("Fridays 9:00") == Schedule("weekly", days=[4], time="09:00")
    assert parse_schedule("Daily 8:30") == Schedule("daily", time="08:30")
    assert parse_schedule("Every 6 hours") == Schedule("interval", hours=6)
    assert parse_schedule("whenever I feel like it") is None
    assert parse_schedule("Fridays 25:00") is None


def test_import_parses_the_old_gui_strings():
    """The old GUI wrote exactly these four; every one must come across."""
    assert parse_schedule("Weekdays at 09:00") == Schedule("weekly", days=[0, 1, 2, 3, 4], time="09:00")
    assert parse_schedule("Daily at 12:00") == Schedule("daily", time="12:00")
    assert parse_schedule("Fridays at 16:00") == Schedule("weekly", days=[4], time="16:00")
    assert parse_schedule("Mondays at 09:00") == Schedule("weekly", days=[0], time="09:00")
    assert parse_schedule("Weekdays 7:30") == Schedule("weekly", days=[0, 1, 2, 3, 4], time="07:30")
    assert parse_schedule("Wednesdays at 8:00") == Schedule("weekly", days=[2], time="08:00")
    assert parse_schedule("at 09:00") is None


def test_due_measures_the_latest_missed_slot(ny):
    daily = routine(schedule=Schedule("daily", time="09:00"))
    off = at(2026, 11, 1, 10)  # last seen after day 1's slot
    # off ~2.5 days: day 2's slot is old, but day 3's was missed 23 h ago: catch up once
    assert due(daily, at(2026, 11, 4, 8), off) == "catch_up"
    # the latest slot (day 3, 09:00) is more than 24 h old
    assert due(daily, at(2026, 11, 4, 10), off) == "catch_up"  # day 4's slot at 09:00 is 1 h old
    weekly = routine(schedule=Schedule("weekly", days=[0, 4], time="09:00"))  # Mon, Fri
    assert due(weekly, at(2026, 11, 17, 8), at(2026, 11, 12, 10)) == "catch_up"  # Fri 13th 9:00 missed 3 d ago, Mon 16th 23 h ago
    assert due(weekly, at(2026, 11, 18, 10), at(2026, 11, 12, 10)) == "skip_missed"  # latest is Mon 16th 9:00, 49 h ago
    assert due(routine(schedule=Schedule("weekly", days=[4], time="09:00")), at(2026, 11, 18, 10), at(2026, 11, 12, 10)) == "skip_missed"


def test_empty_weekly_has_no_slot(ny):
    with pytest.raises(ValueError, match="at least one day"):
        next_run(Schedule("weekly", days=[]), at(2026, 11, 13))


async def test_run_now_survives_a_failed_save(vault, ny):
    rig = Rig(vault, [routine()])
    rig.store.save = lambda *a: (_ for _ in ()).throw(OSError("read-only"))
    rig.now = at(2026, 11, 13, 12)
    assert await rig.sched.run_now("r1") is rig.sessions[0]
    rig.fail = "gone"
    with pytest.raises(RoutineError, match="gone"):
        await rig.sched.run_now("r1")


async def test_bad_schedule_skips_only_that_routine(vault, ny):
    import json
    d = vault.parent / "vault-settings"
    d.mkdir()
    good = {"id": "g", "name": "Good", "bot": "b", "skill": "s", "enabled": True,
            "schedule": {"kind": "daily", "days": [], "time": "09:00", "hours": 0}, "last_run": None, "managed_by": ""}
    bad_week = {**good, "id": "w", "name": "Bad", "schedule": {"kind": "weekly", "days": [], "time": "09:00", "hours": 0}}
    bad_int = {**good, "id": "i", "name": "Bad2", "schedule": {"kind": "interval", "days": [], "time": "09:00", "hours": 0}}
    (d / "routines.json").write_text(json.dumps({"routines": [bad_week, bad_int, good], "last_tick": None}))
    rig = Rig(vault, [])
    rig.store = RoutineStore.load(vault)
    slot = at(2026, 11, 13, 9)
    rig.sched.last_tick = slot - timedelta(seconds=25)
    rig.now = slot + timedelta(seconds=5)
    await rig.sched.tick()
    assert [r.id for r in rig.started] == ["g"]
    assert rig.sched.last_tick == rig.now
    rig.now += timedelta(seconds=30)
    await rig.sched.tick()
    failed = [e for e in rig.entries() if e.data["outcome"] == "failed"]
    assert sorted(e.data["routine"] for e in failed) == ["i", "w"]  # once each, not per tick
    assert "invalid schedule" in failed[0].data["error"]
    assert rig.store.get("w").last_run is None and "invalid schedule" in rig.store.get("w").last_skip["reason"]
    with pytest.raises(ValueError):
        next_run(Schedule("interval", hours=0), rig.now)


def _write(vault, text):
    d = vault.parent / "vault-settings"
    d.mkdir(exist_ok=True)
    (d / "routines.json").write_text(text)
    return d


def test_a_bad_routine_is_skipped_and_kept(vault):
    import json
    good = {"id": "g", "name": "Good", "bot": "b", "skill": "s", "enabled": True,
            "schedule": {"kind": "daily", "days": [], "time": "09:00", "hours": 0}, "last_run": None, "managed_by": ""}
    bad = {"id": "x", "name": "Broken", "bot": "b"}  # no schedule
    d = _write(vault, json.dumps({"routines": [bad, good, "nonsense"], "last_tick": "not a time"}))
    store = RoutineStore.load(vault)
    assert [r.id for r in store.all()] == ["g"] and store.last_tick is None and not store.read_only
    assert len(store.errors) == 2 and "'Broken'" in store.errors[0]
    store.save()
    kept = json.loads((d / "routines.json").read_text())["routines"]
    assert bad in kept and "nonsense" in kept and len(kept) == 3  # nothing the user wrote is lost


def test_an_unreadable_file_is_backed_up_and_never_saved_over(vault):
    d = _write(vault, '{"routines": [ {"id": "g", ')
    store = RoutineStore.load(vault)
    assert store.read_only and store.all() == [] and "could not be read" in store.errors[0]
    backups = list(d.glob("routines.json.bad-*"))
    assert len(backups) == 1 and backups[0].read_text() == '{"routines": [ {"id": "g", '
    store.upsert(routine())
    store.save()
    assert (d / "routines.json").read_text() == '{"routines": [ {"id": "g", '
    _write(vault, '["a list"]')
    assert RoutineStore.load(vault).read_only


async def test_a_tick_does_not_wipe_an_unreadable_file(vault, ny):
    d = _write(vault, "garbage")
    store = RoutineStore.load(vault)
    sched = Scheduler(lambda: store, lambda r: None, Ledger(vault), clock=lambda: at(2026, 11, 13, 9))
    await sched.tick()
    assert (d / "routines.json").read_text() == "garbage"


def test_a_missing_file_is_an_empty_writable_store(vault):
    store = RoutineStore.load(vault)
    assert store.all() == [] and not store.read_only and store.errors == []


async def test_interval_overlap_records_once_per_slot_not_per_tick(vault, ny):
    rig = Rig(vault, [routine(schedule=Schedule("interval", hours=1), last_run={"at": at(2026, 11, 13, 7).isoformat(), "session_id": "", "outcome": "ok"})])
    rig.now = at(2026, 11, 13, 8) + timedelta(seconds=10)
    rig.sched.last_tick = rig.now - timedelta(seconds=30)
    await rig.sched.tick()
    assert len(rig.started) == 1
    rig.sessions[0].state = SessionState.running
    for _ in range(250):  # about two hours of 30 s ticks: the session outlives its interval
        rig.now += timedelta(seconds=30)
        await rig.sched.tick()
    outcomes = [e.data["outcome"] for e in rig.entries()]
    assert outcomes.count("skipped_overlap") == 2  # the 9:00 and 10:00 slots, one record each
    assert len(rig.started) == 1


async def test_interval_missed_over_24h_is_skipped_once_then_runs_next_slot(vault, ny):
    rig = Rig(vault, [routine(schedule=Schedule("interval", hours=6), last_run={"at": at(2026, 11, 11, 0).isoformat(), "session_id": "", "outcome": "ok"})])
    rig.sched.last_tick = at(2026, 11, 11, 1)
    rig.now = at(2026, 11, 11, 6) + timedelta(hours=30)  # first slot (06:00 on the 11th) is 30 h old
    await rig.sched.tick()
    assert rig.started == []
    assert [e.data["outcome"] for e in rig.entries()] == ["skipped_missed"]
    skipped_at = rig.now
    for _ in range(3):  # following ticks: nothing runs, nothing more is recorded
        rig.now += timedelta(seconds=30)
        await rig.sched.tick()
    assert rig.started == []
    assert [e.data["outcome"] for e in rig.entries()] == ["skipped_missed"]
    rig.now = skipped_at + timedelta(hours=6, seconds=10)  # the next slot after the skip
    rig.sched.last_tick = rig.now - timedelta(seconds=30)
    await rig.sched.tick()
    assert len(rig.started) == 1
    assert rig.store.get("r1").last_run["outcome"] == "started"

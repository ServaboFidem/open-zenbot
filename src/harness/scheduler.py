"""Routines: a bot runs a skill on a schedule, in the user's local time. Kept in `vault-settings/routines.json`.

A run is `start(routine)`, which the harness wires to `Harness.start_task(bot, "<name>: <skill.use>",
in_conversation=False, pinned_skills=[skill])`. Policy:

- A slot missed while the harness was off or the machine asleep (a gap between ticks of more than two
  tick lengths) runs once on start or wake when it is under 24 h old; older ones record `skipped_missed`.
- A routine whose previous session is still running records `skipped_overlap` instead of starting another.
- A run is recorded `started` with its session id at launch; a later tick settles it from the session's end:
  done is `ok`, failed or stopped is `failed` with the session's error as `reason`. A managed routine can name a
  verifier (`Scheduler.verifiers[managed_by]`) that fails a run which ended without its result.
- Every start, settled run, skip and failure is one ledger entry (`kind="routine"`). Runs are the routine's
  `last_run`; skips (missed, overlap, an invalid schedule) are its `last_skip`, so they never hide the last run.
- An interval routine counts from its last run or skip, whichever is later; with none yet it runs at the first tick."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from src.memory.ledger import Kind, Ledger
from src.vault_settings import settings_dir

from .loop import Session, SessionState

log = logging.getLogger(__name__)

FILE = "routines.json"
CATCH_UP = timedelta(hours=24)
_ENDED = (SessionState.done, SessionState.failed, SessionState.stopped)


class RoutineError(Exception):
    """A routine could not start (its bot or skill is gone); the message says which."""


@dataclass
class Schedule:
    kind: Literal["weekly", "daily", "interval"]
    days: list[int] = field(default_factory=list)  # 0=Mon .. 6=Sun, for weekly
    time: str = "09:00"  # local wall clock, for weekly and daily
    hours: int = 0  # for interval


@dataclass
class Routine:
    id: str
    name: str
    bot: str
    skill: str  # a skill slug
    enabled: bool
    schedule: Schedule
    last_run: dict[str, Any] | None = None  # {"at", "session_id", "outcome": started|ok|failed, "reason" when failed}
    managed_by: str = ""  # "music" for the digest routine: its owner may recreate it
    last_skip: dict[str, Any] | None = None  # {"at", "outcome": skipped_missed|skipped_overlap|failed, "reason"?}


def _now() -> datetime:
    return datetime.now().astimezone()


def _local(naive: datetime) -> datetime:
    """A wall-clock time in the local zone. One that does not exist (spring forward) moves on to the first valid minute."""
    aware = naive.astimezone()
    while aware.replace(tzinfo=None) != naive:
        naive += timedelta(minutes=1)
        aware = naive.astimezone()
    return aware


def _hm(s: Schedule) -> tuple[int, int]:
    h, m = s.time.split(":")
    return int(h), int(m)


def check(s: Schedule) -> None:
    """ValueError when the schedule can never give a sensible slot (a file edited by hand can hold one)."""
    if s.kind == "interval" and s.hours < 1:
        raise ValueError("an interval schedule needs hours of 1 or more")
    if s.kind == "weekly" and not s.days:
        raise ValueError("a weekly schedule needs at least one day")


def next_run(s: Schedule, after: datetime) -> datetime:
    """The first slot strictly after `after`, as an aware local datetime."""
    check(s)
    after = after.astimezone()
    if s.kind == "interval":
        return after + timedelta(hours=s.hours)
    h, m = _hm(s)
    days = set(s.days) if s.kind == "weekly" else set(range(7))
    if not days:
        raise ValueError("a weekly schedule needs at least one day")
    day = after.date()
    for _ in range(9):
        if day.weekday() in days:
            cand = _local(datetime(day.year, day.month, day.day, h, m))
            if cand > after:
                return cand
        day += timedelta(days=1)
    raise ValueError(f"no slot after {after} for {s}")


def _latest_slot(r: Routine, now: datetime, last_tick: datetime) -> datetime | None:
    """The most recent slot in (last_tick, now], or None when none fell there."""
    s = r.schedule
    if s.kind == "interval":
        marks = [datetime.fromisoformat(x["at"]) for x in (r.last_run, r.last_skip) if x]  # a skip consumes its slot too
        slot = next_run(s, max(marks)) if marks else last_tick  # never run or skipped: at the first tick
        return slot if slot <= now else None
    cur = max(last_tick, now - timedelta(days=8))  # a weekly slot recurs within 7 days, so older ones cannot be the latest
    latest = None
    while (nxt := next_run(s, cur)) <= now:
        latest, cur = nxt, nxt
    return latest


def due(r: Routine, now: datetime, last_tick: datetime, tick_s: float = 30) -> Literal["run", "catch_up", "skip_missed"] | None:
    if not r.enabled:
        return None
    check(r.schedule)
    slot = _latest_slot(r, now, last_tick)
    if slot is None:
        return None
    if (now - last_tick).total_seconds() <= 2 * tick_s:
        return "run"
    return "catch_up" if now - slot <= CATCH_UP else "skip_missed"


_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_schedule(text: str) -> Schedule | None:
    """The GUI's old schedule strings: "Weekdays at 09:00", "Fridays at 16:00", "Daily 8:30", "Every 6 hours"
    ("at" optional). None when it is none of those."""
    t = text.strip().lower()
    if m := re.fullmatch(r"every\s+(\d+)\s+hours?", t):
        return Schedule("interval", hours=int(m[1])) if int(m[1]) > 0 else None
    if m := re.fullmatch(r"(daily|[a-z]+?)s?\s+(?:at\s+)?(\d{1,2}):(\d{2})", t):
        h, mi = int(m[2]), int(m[3])
        if h > 23 or mi > 59:
            return None
        hhmm = f"{h:02d}:{mi:02d}"
        if m[1] == "daily":
            return Schedule("daily", time=hhmm)
        if m[1] == "weekday":
            return Schedule("weekly", days=[0, 1, 2, 3, 4], time=hhmm)
        for i, d in enumerate(_DAYS):
            if m[1].startswith(d):
                return Schedule("weekly", days=[i], time=hhmm)
    return None


class RoutineStore:
    def __init__(self) -> None:
        self._routines: dict[str, Routine] = {}
        self.last_tick: datetime | None = None  # when the scheduler last looked, so a restart can tell what it missed
        self.root: Path | None = None
        self.errors: list[str] = []  # what the load could not read, for the ledger and GET /routines/store
        self.read_only = False  # the file could not be read at all: never save over it
        self._bad: list[Any] = []  # routines the load could not read, written back as they were

    @classmethod
    def load(cls, vault_root: Path) -> "RoutineStore":
        """Read routine by routine: a bad one is skipped (and kept in the file). A file that cannot be read at all
        is copied to `routines.json.bad-<time>` and the store goes read-only, so a save never wipes it."""
        store = cls()
        store.root = vault_root
        path = settings_dir(vault_root) / FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("routines", []), list):
                raise ValueError("not a routines file")
        except FileNotFoundError:
            return store
        except (OSError, ValueError) as e:
            store._unreadable(path, e)
            return store
        for item in raw.get("routines", []):
            try:
                fields = dict(item)
                sch = fields.pop("schedule")
                store.upsert(Routine(**fields, schedule=Schedule(**sch)))
            except (TypeError, KeyError, ValueError, AttributeError) as e:
                store._bad.append(item)
                name = item.get("name") if isinstance(item, dict) else None
                which = f" {name!r}" if name else ""
                store.errors.append(f"routines: skipped an unreadable routine{which}: {type(e).__name__}: {e}")
                log.warning(store.errors[-1])
        try:
            store.last_tick = datetime.fromisoformat(raw["last_tick"]) if raw.get("last_tick") else None
        except (TypeError, ValueError):
            store.last_tick = None
        return store

    def _unreadable(self, path: Path, e: Exception) -> None:
        self.read_only = True
        backup = path.with_name(f"{FILE}.bad-{datetime.now().strftime('%Y%m%dT%H%M%S')}")
        try:
            shutil.copy2(path, backup)
            kept = f"; a copy is at {backup.name}"
        except OSError:
            kept = ""
        self.errors.append(f"routines: {FILE} could not be read ({type(e).__name__}: {e}){kept}. "
                           f"Routines will not be saved until it is fixed or removed and the app restarted.")
        log.error(self.errors[-1])

    def save(self, vault_root: Path | None = None) -> None:
        if self.read_only:
            return  # the unreadable file stays as it is
        path = settings_dir(vault_root or self.root) / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        doc = {"routines": [asdict(r) for r in self._routines.values()] + self._bad,
               "last_tick": self.last_tick.isoformat() if self.last_tick else None}
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def get(self, rid: str) -> Routine | None:
        return self._routines.get(rid)

    def upsert(self, r: Routine) -> Routine:
        self._routines[r.id] = r
        return r

    def delete(self, rid: str) -> bool:
        return self._routines.pop(rid, None) is not None

    def all(self) -> list[Routine]:
        return list(self._routines.values())


class Scheduler:
    def __init__(self, store_loader: Callable[[], RoutineStore], start: Callable[[Routine], Session], ledger: Ledger,
                 clock: Callable[[], datetime] = _now, tick_s: float = 30) -> None:
        self.store_loader, self.start, self.ledger, self.clock, self.tick_s = store_loader, start, ledger, clock, tick_s
        self.last_tick: datetime | None = None
        self._reported: set[str] = set()  # routines with an invalid schedule already recorded, so a tick does not repeat it
        self.sessions: dict[str, Session] = {}  # routine id -> the session it last started
        # managed_by -> fn(ended session) giving why the run failed although the session ended ("" when it did its job)
        self.verifiers: dict[str, Callable[[Session], str]] = {}
        self._stop = asyncio.Event()

    def _log(self, r: Routine, session_id: str, outcome: str, error: str = "") -> None:
        extra = {"error": error} if error else {}
        self.ledger.append(Kind.routine, r.bot, session_id, routine=r.id, name=r.name, skill=r.skill,
                           outcome=outcome, **extra)

    def _record(self, r: Routine, now: datetime, outcome: str, session: Session | None = None, error: str = "") -> None:
        """A run: `started` with its session, or `failed` when it could not start."""
        r.last_run = {"at": now.isoformat(), "session_id": session.id if session else "", "outcome": outcome,
                      **({"reason": error} if error else {})}
        self._log(r, session.id if session else "", outcome, error)

    def _skip(self, r: Routine, now: datetime, outcome: str, error: str = "") -> None:
        r.last_skip = {"at": now.isoformat(), "outcome": outcome, **({"reason": error} if error else {})}
        self._log(r, "", outcome, error)

    def _settle(self, r: Routine) -> None:
        """A `started` run whose session has ended becomes `ok` or `failed`."""
        run = r.last_run
        if not run or run.get("outcome") != "started":
            return
        s = self.sessions.get(r.id)
        if s is None or s.id != run.get("session_id"):
            outcome, reason = "failed", "the harness restarted before the run ended"
        elif s.state not in _ENDED:
            return
        else:
            verify = self.verifiers.get(r.managed_by) if r.managed_by else None
            if s.state == SessionState.done:
                reason = verify(s) if verify else ""
            else:
                reason = s.error or (verify(s) if verify else "") or f"the run {s.state.value}"
            outcome = "failed" if reason else "ok"
        r.last_run = {"at": run.get("at"), "session_id": run.get("session_id", ""), "outcome": outcome,
                      **({"reason": reason} if reason else {})}
        self._log(r, run.get("session_id", ""), outcome, reason)

    def _launch(self, r: Routine, now: datetime) -> Session:
        try:
            session = self.start(r)
        except Exception as e:  # noqa: BLE001 - any start failure is that routine's failure, not the scheduler's
            self._record(r, now, "failed", error=str(e))
            raise RoutineError(str(e)) from e
        self.sessions[r.id] = session
        self._record(r, now, "started", session)
        return session

    def _running(self, r: Routine) -> bool:
        s = self.sessions.get(r.id)
        return s is not None and s.state not in _ENDED

    async def tick(self) -> None:
        now = self.clock()
        store = self.store_loader()
        if self.last_tick is None:
            self.last_tick = store.last_tick or now  # first ever start: nothing to catch up on
        for r in store.all():
            self._settle(r)
            try:
                verdict = due(r, now, self.last_tick, self.tick_s)
            except ValueError as e:  # a bad schedule skips that routine only
                if r.id not in self._reported:
                    self._reported.add(r.id)
                    self._skip(r, now, "failed", error=f"invalid schedule: {e}")
                continue
            self._reported.discard(r.id)
            if verdict is None:
                continue
            if verdict == "skip_missed":
                self._skip(r, now, "skipped_missed")
            elif self._running(r):
                self._skip(r, now, "skipped_overlap")
            else:
                try:
                    self._launch(r, now)
                except RoutineError:
                    pass  # recorded
        self.last_tick = store.last_tick = now
        try:
            store.save()
        except OSError:
            pass  # a read-only settings folder must not stop routines running

    async def run_now(self, rid: str) -> Session:
        """Start one routine at once, enabled or not. KeyError for an unknown id; RoutineError when it cannot start."""
        store = self.store_loader()
        r = store.get(rid)
        if r is None:
            raise KeyError(rid)
        try:
            return self._launch(r, self.clock())
        finally:
            try:
                store.save()
            except OSError:
                pass  # the run's own result must not be masked by a settings write

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001 - a bad tick is logged and the next one tries again
                self.ledger.append(Kind.error, "", "", error=f"scheduler: {type(e).__name__}: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), self.tick_s)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

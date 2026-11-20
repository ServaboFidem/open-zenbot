"""Slice suite: close-friend birthday holds and the single week-before nudge."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.events.birthdays import (
    BIRTHDAY_MANAGED_BY,
    NUDGE_DAYS_OUT,
    SURFACES,
    ensure_routine,
    hold_birthday,
    hold_birthdays,
    is_due_for_nudge,
    nudges_due,
    send_nudges,
)
from src.events.facebook_pool import BIRTHDAY_SOURCE
from src.events.store import CalendarStore
from src.harness.scheduler import RoutineStore
from src.memory.registry import PeopleRegistry


def _people(tmp_path: Path) -> PeopleRegistry:
    people = PeopleRegistry(tmp_path / "people.json")
    people.add("Ada Lovelace", tier="close")
    people.add("Grace Hopper", tier="known")
    return people


def _row(name: str, start_date: str, source: str = BIRTHDAY_SOURCE) -> dict:
    return {
        "id": f"fb-bday-{name.lower().replace(' ', '-')}",
        "source": source,
        "name": name,
        "start_date": start_date,
        "start_time": "",
        "end_date": start_date,
        "end_time": "",
        "timezone": "",
    }


@pytest.fixture
def store(tmp_path: Path):
    s = CalendarStore(tmp_path / "calendar.db")
    yield s
    s.close()


def test_close_friend_birthday_holds(tmp_path: Path, store: CalendarStore) -> None:
    people = _people(tmp_path)
    kicks: list[bool] = []
    out = hold_birthdays(
        [_row("Ada Lovelace", "2026-12-10")],
        people,
        store,
        kick=lambda: kicks.append(True),
    )
    assert len(out["held"]) == 1
    assert out["skipped"] == {}
    assert kicks == [True]
    event = out["held"][0]
    assert event["title"] == "Ada Lovelace's birthday"
    assert event["all_day"] is True
    assert event["start"] == "2026-12-10"
    assert event["end"] == "2026-12-11"  # all-day holds end exclusive
    assert event["origin"] == "local"  # an ordinary made-here event: push sends it
    assert store.events("2026-12-10", "2026-12-11")


def test_non_friend_birthday_does_not_hold(
    tmp_path: Path, store: CalendarStore
) -> None:
    people = _people(tmp_path)
    out = hold_birthdays(
        [_row("Grace Hopper", "2026-12-10"), _row("Stranger", "2026-12-10")],
        people,
        store,
        kick=lambda: None,
    )
    assert out["held"] == []
    assert out["skipped"] == {"non_friend": 2}
    assert store.events("2026-12-10", "2026-12-11") == []


def test_owner_birthday_does_not_hold(tmp_path: Path, store: CalendarStore) -> None:
    people = _people(tmp_path)
    people.set_owner("Ada Lovelace", [])
    event, reason = hold_birthday(_row("Ada Lovelace", "2026-12-10"), people, store)
    assert event is None
    assert reason == "non_friend"


def test_other_sources_never_hold(tmp_path: Path, store: CalendarStore) -> None:
    people = _people(tmp_path)
    out = hold_birthdays(
        [
            _row("Ada Lovelace", "2026-12-10", source="facebook"),
            _row("Ada Lovelace", "2026-12-10", source="eventbrite"),
        ],
        people,
        store,
    )
    assert out["held"] == []
    assert out["skipped"] == {"not_birthday_source": 2}


def test_hold_is_idempotent(tmp_path: Path, store: CalendarStore) -> None:
    people = _people(tmp_path)
    row = _row("Ada Lovelace", "2026-12-10")
    first, _ = hold_birthday(row, people, store)
    second, reason = hold_birthday(row, people, store)
    assert first is not None
    assert second is None
    assert reason == "existing"
    assert len(store.events("2026-12-10", "2026-12-11")) == 1


def test_bad_rows_never_hold(tmp_path: Path, store: CalendarStore) -> None:
    people = _people(tmp_path)
    out = hold_birthdays(
        [
            {**_row("Ada Lovelace", "2026-12-10"), "name": ""},
            {**_row("Ada Lovelace", "2026-12-10"), "start_date": "not-a-date"},
        ],
        people,
        store,
    )
    assert out["held"] == []
    assert out["skipped"] == {"bad_row": 2}


def test_nudge_fires_seven_days_out() -> None:
    assert NUDGE_DAYS_OUT == 7
    row = _row("Ada Lovelace", "2026-11-27")
    assert is_due_for_nudge(row, "2026-11-20") is True
    assert is_due_for_nudge(row, "2026-11-21") is False  # six days out: not yet
    assert is_due_for_nudge(row, "2026-11-19") is False  # eight days out: too early
    assert is_due_for_nudge({**row, "source": "facebook"}, "2026-11-20") is False


def test_nudges_due_only_lists_week_out_rows() -> None:
    rows = [
        _row("Ada Lovelace", "2026-11-27"),
        _row("Grace Hopper", "2026-11-26"),
        _row("Stranger", "2026-12-25"),
    ]
    due = nudges_due(rows, "2026-11-20")
    assert [n["name"] for n in due] == ["Ada Lovelace"]
    assert due[0]["text"] == "Ada Lovelace's birthday is in a week (2026-11-27)."


def test_nudge_goes_to_both_surfaces() -> None:
    assert set(SURFACES) == {"chat", "feed"}
    seen: list[tuple[str, dict]] = []
    out = send_nudges(
        [_row("Ada Lovelace", "2026-11-27")],
        "2026-11-20",
        lambda s, n: seen.append((s, n)),
    )
    assert out == {"nudged": 1, "delivered": 2, "errors": 0}
    assert [surface for surface, _ in seen] == ["chat", "feed"]
    assert seen[0][1] == seen[1][1]


def test_nudge_failure_never_blocks() -> None:
    def flaky(surface: str, nudge: dict) -> None:
        if surface == "chat":
            raise RuntimeError("chat is down")

    out = send_nudges([_row("Ada Lovelace", "2026-11-27")], "2026-11-20", flaky)
    assert out == {"nudged": 1, "delivered": 1, "errors": 1}  # feed still fired


def test_no_nudge_when_nothing_due() -> None:
    seen: list[tuple[str, dict]] = []
    out = send_nudges(
        [_row("Ada Lovelace", "2026-12-25")],
        "2026-11-20",
        lambda s, n: seen.append((s, n)),
    )
    assert out == {"nudged": 0, "delivered": 0, "errors": 0}
    assert seen == []


def test_ensure_routine_registers_daily_rhythm(tmp_path: Path) -> None:
    routines = RoutineStore()
    routines.root = tmp_path / "vault"
    first = ensure_routine(routines)
    assert first is not None
    assert first.managed_by == BIRTHDAY_MANAGED_BY
    assert first.enabled is True
    assert first.schedule.kind == "daily"
    second = ensure_routine(routines)
    assert second is not None
    assert second.id == first.id  # one rhythm, not one per run
    assert len([r for r in routines.all() if r.managed_by == BIRTHDAY_MANAGED_BY]) == 1

"""Slice suite: Facebook hold lifecycle — updates and cancels from pool changes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from src.events import facebook_lifecycle as lifecycle
from src.events.store import CalendarStore
from src.memory.registry import PeopleRegistry


def _people(tmp_path: Path) -> PeopleRegistry:
    people = PeopleRegistry(tmp_path / "people.json")
    people.add("Ada Lovelace", tier="close")
    return people


def _row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "fb-111",
        "source": "facebook",
        "name": "Rooftop Jazz Night",
        "summary": "Live quartet on the roof.",
        "url": "https://www.facebook.com/events/111",
        "venue": "The Roof",
        "address": "1 High St",
        "city": "Columbus",
        "region": "OH",
        "start_date": "2026-12-10",
        "start_time": "19:00",
        "end_date": "2026-12-10",
        "end_time": "21:00",
        "timezone": "America/New_York",
        "rsvp": "Going",
    }
    row.update(over)
    return row


def _timed_hold(
    store: CalendarStore,
    start: str = "2026-12-11T00:00:00Z",
    end: str = "2026-12-11T02:00:00Z",
    **over: Any,
) -> dict[str, Any]:
    text = "Live quartet on the roof.\n\nhttps://www.facebook.com/events/111"
    fields: dict[str, Any] = {
        "title": "Rooftop Jazz Night",
        "location": "The Roof, 1 High St",
        "description": text,
        "all_day": False,
        "start": start,
        "end": end,
        "tz": "America/New_York",
    }
    fields.update(over)
    return store.add(fields)


def _allday_row(**over: Any) -> dict[str, Any]:
    row = _row(
        start_time="",
        end_time="",
        end_date="2026-12-10",
        timezone="",
    )
    row.update(over)
    return row


@pytest.fixture
def store(tmp_path: Path) -> Any:
    s = CalendarStore(tmp_path / "calendar.db")
    yield s
    s.close()


TODAY = "2026-11-20"
NOW = "2026-11-20T12:00:00Z"


async def test_time_change_updates_the_hold(store: CalendarStore) -> None:
    hold = _timed_hold(store)
    kicks: list[int] = []
    changed = _row(start_time="20:00", end_time="22:00")
    body = SimpleNamespace(start="2026-12-11T01:00:00Z", end="2026-12-11T03:00:00Z")
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]], changed,
        body=body, today=TODAY, now=NOW, kick=lambda: kicks.append(1),
    )
    assert out["status"] == "updated"
    assert out["updated"] == [hold["id"]]
    assert out["removed"] == []
    assert kicks == [1]
    fresh = store.event(hold["id"])
    assert fresh is not None and not fresh["deleted"]
    assert fresh["start"] == "2026-12-11T01:00:00Z"
    assert fresh["end"] == "2026-12-11T03:00:00Z"
    assert len(store.events("2026-12-10", "2026-12-13")) == 1  # no stale copy


async def test_place_change_updates_the_hold_without_new_instants(
    store: CalendarStore,
) -> None:
    hold = _timed_hold(store)
    kicks: list[int] = []
    moved = _row(venue="The Park Pavilion", address="2 Green Ave")
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]], moved,
        today=TODAY, now=NOW, kick=lambda: kicks.append(1),
    )
    assert out["status"] == "updated"
    assert out["updated"] == [hold["id"]]
    assert kicks == [1]
    fresh = store.event(hold["id"])
    assert fresh is not None
    assert fresh["location"] == "The Park Pavilion, 2 Green Ave"
    assert fresh["start"] == hold["start"]  # timing kept: wall time did not move
    assert fresh["end"] == hold["end"]


async def test_allday_place_change_updates_the_hold(store: CalendarStore) -> None:
    text = "Live quartet on the roof.\n\nhttps://www.facebook.com/events/111"
    hold = store.add(
        {
            "title": "Rooftop Jazz Night",
            "location": "The Roof, 1 High St",
            "description": text,
            "all_day": True,
            "start": "2026-12-10",
            "end": "2026-12-11",
            "tz": "",
        }
    )
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]], _allday_row(venue="The Park Pavilion"),
        today=TODAY, now=NOW,
    )
    assert out["status"] == "updated"
    fresh = store.event(hold["id"])
    assert fresh is not None
    assert fresh["location"] == "The Park Pavilion, 1 High St"
    assert fresh["all_day"] is True


async def test_personal_row_updates_through_the_friend_check(
    tmp_path: Path, store: CalendarStore
) -> None:
    people = _people(tmp_path)
    hold = _timed_hold(store)
    personal = _row(rsvp="", inviter="Ada Lovelace", venue="The Park Pavilion")
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]], personal, people,
        today=TODAY, now=NOW,
    )
    assert out["status"] == "updated"
    assert store.event(hold["id"]) is not None


async def test_wall_time_move_beyond_the_hold_removes_it_as_stale(
    store: CalendarStore,
) -> None:
    hold = _timed_hold(store)
    kicks: list[int] = []
    moved_days = _row(start_date="2026-12-12", end_date="2026-12-12")
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]], moved_days,
        today=TODAY, now=NOW, kick=lambda: kicks.append(1),
    )
    assert out["status"] == "removed"
    assert out["reason"] == "stale_timing"
    assert out["removed"] == [hold["id"]]
    assert kicks == [1]
    assert store.event(hold["id"]) is None  # unpushed: purged, no live hold
    assert store.events("2026-12-10", "2026-12-15") == []


async def test_cancelled_row_purges_an_unpushed_hold(store: CalendarStore) -> None:
    hold = _timed_hold(store)
    kicks: list[int] = []
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]], None,
        today=TODAY, now=NOW, kick=lambda: kicks.append(1),
    )
    assert out["status"] == "removed"
    assert out["reason"] == "cancelled"
    assert out["removed"] == [hold["id"]]
    assert out["tombstoned"] == []
    assert kicks == [1]
    assert store.event(hold["id"]) is None
    assert store.events("2026-12-10", "2026-12-13") == []  # no live hold remains


async def test_cancelled_row_tombstones_a_pushed_hold(store: CalendarStore) -> None:
    hold = _timed_hold(store)
    store.link(hold["id"], "gcal", "abc123g0")
    kicks: list[int] = []
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]], None,
        today=TODAY, now=NOW, kick=lambda: kicks.append(1),
    )
    assert out["status"] == "removed"
    assert out["reason"] == "cancelled"
    assert out["tombstoned"] == [hold["id"]]
    assert kicks == [1]  # push still sends the delete under locked rules
    tomb = store.event(hold["id"])
    assert tomb is not None
    assert tomb["deleted"] is True and tomb["dirty"] is True  # tombstone, not purge
    assert store.events("2026-12-10", "2026-12-13") == []


async def test_unrsvp_removes_the_hold(store: CalendarStore) -> None:
    hold = _timed_hold(store)
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]], _row(rsvp=""),
        today=TODAY, now=NOW,
    )
    assert out["status"] == "removed"
    assert out["reason"] == "un_rsvp"
    assert out["removed"] == [hold["id"]]
    assert store.event(hold["id"]) is None


async def test_like_removes_the_hold(store: CalendarStore) -> None:
    hold = _timed_hold(store)
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]], _row(rsvp="Liked"),
        today=TODAY, now=NOW,
    )
    assert out["status"] == "removed"
    assert out["reason"] == "un_rsvp"
    assert store.event(hold["id"]) is None


async def test_unchanged_row_writes_nothing(store: CalendarStore) -> None:
    hold = _timed_hold(store)
    kicks: list[int] = []
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]], _row(),
        body=SimpleNamespace(start=hold["start"], end=hold["end"]),
        today=TODAY, now=NOW, kick=lambda: kicks.append(1),
    )
    assert out["status"] == "unchanged"
    assert out["updated"] == [] and out["removed"] == []
    assert kicks == []  # nothing to push


async def test_missing_hold_creates_nothing(store: CalendarStore) -> None:
    out = await lifecycle.sync_hold(
        store, "fb-111", [], _row(),
        body=SimpleNamespace(
            start="2026-12-11T00:00:00Z", end="2026-12-11T02:00:00Z"
        ),
        today=TODAY, now=NOW,
    )
    assert out["status"] == "missing"  # creation belongs to the add routes
    assert store.events("2026-12-10", "2026-12-13") == []


async def test_past_listing_leaves_history_alone(store: CalendarStore) -> None:
    hold = _timed_hold(store)
    kicks: list[int] = []
    out = await lifecycle.sync_hold(
        store, "fb-111", [hold["id"]],
        _row(start_date="2026-11-10", end_date="2026-11-10"),
        today=TODAY, now=NOW, kick=lambda: kicks.append(1),
    )
    assert out["status"] == "past"
    assert kicks == []
    assert store.event(hold["id"]) is not None


async def test_explicit_body_that_is_not_the_listing_is_422(
    store: CalendarStore,
) -> None:
    hold = _timed_hold(store)
    with pytest.raises(HTTPException) as exc:
        await lifecycle.sync_hold(
            store, "fb-111", [hold["id"]], _row(),
            body=SimpleNamespace(
                start="2026-12-11T02:00:00Z", end="2026-12-11T00:00:00Z"
            ),
            today=TODAY, now=NOW,
        )
    assert exc.value.status_code == 422
    assert store.event(hold["id"]) == hold  # nothing written


async def test_garbage_row_never_wipes_a_hold(store: CalendarStore) -> None:
    hold = _timed_hold(store)
    with pytest.raises(ValueError):
        await lifecycle.sync_hold(
            store, "fb-111", [hold["id"]], "cancelled",
            today=TODAY, now=NOW,  # type: ignore[arg-type]
        )
    assert store.event(hold["id"]) is not None


async def test_other_source_row_never_wipes_a_hold(store: CalendarStore) -> None:
    hold = _timed_hold(store)
    with pytest.raises(ValueError):
        await lifecycle.sync_hold(
            store, "fb-111", [hold["id"]],
            _row(source="facebook-birthday", rsvp=""),
            today=TODAY, now=NOW,
        )
    assert store.event(hold["id"]) is not None

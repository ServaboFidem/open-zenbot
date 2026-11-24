"""What is due: reminders before timed events, and the evening rundown of tomorrow. A real calendar store."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from src.events.store import CalendarStore
from src.telnyx.due import Due, event_id, reminders, rundown

ZONE = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 23, 14, 0, tzinfo=ZONE)  # 19:00 UTC


def utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def store(tmp_path):
    s = CalendarStore(tmp_path / "c.db")
    yield s
    s.close()


def timed(store, title, start, minutes=60, location=""):
    return store.add({"title": title, "location": location, "all_day": False, "start": utc(start),
                      "end": utc(start + timedelta(minutes=minutes)), "tz": "America/New_York"})


def all_day(store, title, first, last_exclusive):
    return store.add({"title": title, "all_day": True, "start": first, "end": last_exclusive})


def pulled(store, calendar, google_id, title, start):
    store.apply_remote(calendar, google_id, {"title": title, "location": "", "description": "", "all_day": False,
                                             "start": utc(start), "end": utc(start + timedelta(hours=1)),
                                             "tz": "America/New_York"}, etag="e", link="", series_id="")


def test_due_window(store):
    timed(store, "Soon", NOW + timedelta(minutes=29), location="1 Main St")
    timed(store, "Later", NOW + timedelta(minutes=31))
    timed(store, "Started", NOW - timedelta(minutes=1))
    got = reminders(store, NOW, 30)
    assert [d.text for d in got] == ["Reminder: Soon at 2:29 PM - 1 Main St"]
    assert got[0].kind == "before" and got[0].key.startswith("before:")


def test_event_at_the_lead_edge_is_due(store):
    timed(store, "Edge", NOW + timedelta(minutes=30))
    assert len(reminders(store, NOW, 30)) == 1


def test_moved_event_gets_new_key(store):
    event = timed(store, "Dentist", NOW + timedelta(minutes=20))
    first = reminders(store, NOW, 30)[0].key
    store.update(event["id"], {**event, "start": utc(NOW + timedelta(minutes=25)),
                               "end": utc(NOW + timedelta(minutes=85))})
    assert reminders(store, NOW, 30)[0].key != first


def test_hidden_calendar_skipped(store):
    timed(store, "Soon", NOW + timedelta(minutes=10))
    store.set_calendar("local", visible=False)
    assert reminders(store, NOW, 30) == []


def test_pulled_event_keyed_by_google_id_once_across_calendars(store):
    store.upsert_google([{"id": "cal-a", "name": "A", "writable": False}, {"id": "cal-b", "name": "B", "writable": False}])
    pulled(store, "cal-a", "gev1", "Invite", NOW + timedelta(minutes=10))
    pulled(store, "cal-b", "gev1", "Invite", NOW + timedelta(minutes=10))
    got = reminders(store, NOW, 30)
    assert len(got) == 1 and got[0].key.startswith("before:g:gev1:")
    store.clear_pulled("cal-a")
    store.clear_pulled("cal-b")
    pulled(store, "cal-a", "gev1", "Invite", NOW + timedelta(minutes=10))  # a new local id
    assert reminders(store, NOW, 30)[0].key == got[0].key


def test_event_id():
    assert event_id({"id": "x", "origin": "google", "google_id": "g1"}) == "g:g1"
    assert event_id({"id": "x", "origin": "local", "google_id": "g1"}) == "x"
    assert event_id({"id": "x", "origin": "google", "google_id": ""}) == "x"


def test_all_day_not_reminded(store):
    all_day(store, "Holiday", "2026-11-23", "2026-11-24")
    assert reminders(store, NOW, 120) == []


def test_rundown_window_and_midnight(store):
    timed(store, "Dentist", datetime(2026, 11, 24, 9, 30, tzinfo=ZONE))
    assert rundown(store, NOW.replace(hour=19, minute=59), "20:00") is None
    at8 = rundown(store, NOW.replace(hour=20, minute=0), "20:00")
    assert at8 == Due("rundown:2026-11-23", "rundown", "Tomorrow: 9:30 AM Dentist")
    assert rundown(store, NOW.replace(hour=23, minute=59), "20:00").key == "rundown:2026-11-23"
    assert rundown(store, datetime(2026, 11, 24, 0, 0, 30, tzinfo=ZONE), "20:00") is None


def test_rundown_lists_multiday_all_day_first(store):
    timed(store, "Dentist", datetime(2026, 11, 24, 9, 30, tzinfo=ZONE))
    all_day(store, "Trip", "2026-11-22", "2026-11-26")
    all_day(store, "Gone", "2026-11-22", "2026-11-24")  # ends before tomorrow
    timed(store, "Tonight", datetime(2026, 11, 23, 22, 0, tzinfo=ZONE))
    timed(store, "Next day", datetime(2026, 11, 25, 0, 30, tzinfo=ZONE))
    got = rundown(store, NOW.replace(hour=21), "20:00")
    assert got.text == "Tomorrow: All day Trip; 9:30 AM Dentist"


def test_rundown_counts_a_pulled_event_once(store):
    store.upsert_google([{"id": "cal-a", "name": "A", "writable": False}, {"id": "cal-b", "name": "B", "writable": False}])
    for cal in ("cal-a", "cal-b"):
        pulled(store, cal, "gev1", "Invite", datetime(2026, 11, 24, 10, 0, tzinfo=ZONE))
    assert rundown(store, NOW.replace(hour=21), "20:00").text == "Tomorrow: 10:00 AM Invite"


def test_rundown_empty_day(store):
    assert rundown(store, NOW.replace(hour=21), "20:00") == Due("rundown:2026-11-23", "rundown", "")

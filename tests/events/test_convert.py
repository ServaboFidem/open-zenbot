from src.events.convert import calendar_row, to_google, to_local
from src.events.store import MAX_DESCRIPTION, MAX_LOCATION, MAX_TITLE


def test_to_local_timed_all_day_and_thin():
    assert to_local({"id": "x", "status": "cancelled"}) is None
    assert to_local({"summary": "S"}) is None
    assert to_local({"start": {"date": "2026-11-17"}, "end": {"date": "2026-11-18"}})["title"] == "(No title)"
    t = to_local({"summary": "S", "start": {"dateTime": "2026-11-17T09:00:00-05:00", "timeZone": "America/New_York"},
                  "endTimeUnspecified": True})
    assert (t["start"], t["end"], t["tz"]) == ("2026-11-17T14:00:00Z", "2026-11-17T15:00:00Z", "America/New_York")


def test_to_local_all_day_and_defaults():
    d = to_local({"summary": "S", "start": {"date": "2026-11-17"}, "end": {"date": "2026-11-19"}})
    assert d == {"title": "S", "location": "", "description": "", "all_day": True,
                 "start": "2026-11-17", "end": "2026-11-19", "tz": ""}
    assert to_local({"summary": "S", "start": {"date": "2026-11-30"}})["end"] == "2026-12-01"
    t = to_local({"summary": "S", "start": {"dateTime": "2026-11-17T09:00:00Z"},
                  "end": {"dateTime": "2026-11-17T10:30:00+01:00"}})
    assert (t["all_day"], t["end"], t["tz"]) == (False, "2026-11-17T09:30:00Z", "")
    assert to_local({"summary": "S", "start": {"dateTime": "2026-11-17T09:00:00Z"}})["end"] == "2026-11-17T10:00:00Z"


def test_to_local_cuts_long_text():
    t = to_local({"summary": "t" * 400, "location": "l" * 600, "description": "d" * 9000,
                  "start": {"date": "2026-11-17"}, "end": {"date": "2026-11-18"}})
    assert (len(t["title"]), len(t["location"]), len(t["description"])) == (MAX_TITLE, MAX_LOCATION, MAX_DESCRIPTION)


def test_to_google_timed_and_all_day():
    ev = {"title": "S", "location": "L", "description": "D", "all_day": False,
          "start": "2026-11-17T14:00:00Z", "end": "2026-11-17T15:00:00Z", "tz": "America/New_York"}
    assert to_google(ev) == {"summary": "S", "location": "L", "description": "D",
                             "start": {"dateTime": "2026-11-17T14:00:00Z", "timeZone": "America/New_York"},
                             "end": {"dateTime": "2026-11-17T15:00:00Z", "timeZone": "America/New_York"}}
    day = to_google({**ev, "all_day": True, "start": "2026-11-17", "end": "2026-11-18"})
    assert day["start"] == {"date": "2026-11-17"} and day["end"] == {"date": "2026-11-18"}


def test_to_google_never_sends_guests_or_reminders():
    ev = {"title": "S", "location": "", "description": "", "all_day": False, "start": "2026-11-17T14:00:00Z",
          "end": "2026-11-17T15:00:00Z", "tz": "", "attendees": [{"email": "a@b"}], "reminders": {}}
    out = to_google(ev)
    assert set(out) == {"summary", "location", "description", "start", "end"}
    assert out["start"] == {"dateTime": "2026-11-17T14:00:00Z"}


def test_calendar_row_writable_only_for_owner_and_writer():
    item = {"id": "i", "summary": "S", "backgroundColor": "#fff", "timeZone": "UTC", "accessRole": "owner",
            "primary": True}
    assert calendar_row(item) == {"id": "i", "name": "S", "color": "#fff", "tz": "UTC", "writable": True,
                                  "primary": True}
    assert calendar_row({**item, "summaryOverride": "Mine", "accessRole": "reader", "primary": None}) == {
        "id": "i", "name": "Mine", "color": "#fff", "tz": "UTC", "writable": False, "primary": False}
    assert calendar_row({**item, "accessRole": "writer"})["writable"] is True
    assert calendar_row({**item, "accessRole": "freeBusyReader"})["writable"] is False

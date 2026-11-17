import pytest

from src.events.google import GoogleError
from src.events.store import LOCAL


def timed(id, title="Dentist", start="2026-11-20T09:00:00Z", end="2026-11-20T10:00:00Z", **k):
    return {"id": id, "status": "confirmed", "summary": title,
            "start": {"dateTime": start, "timeZone": "Europe/London"}, "end": {"dateTime": end}, **k}


def all_day(id, first, last, title="Holiday", **k):
    return {"id": id, "status": "confirmed", "summary": title,
            "start": {"date": first}, "end": {"date": last}, **k}


def cancel(fake, cal, id):
    return fake.put(cal, {**fake.events[cal][id], "status": "cancelled"})


def local(**k):
    return {"title": "Made here", "all_day": False, "start": "2026-11-21T09:00:00Z",
            "end": "2026-11-21T10:00:00Z", "tz": "Europe/London", **k}


def pushed(store, cal="work", gid="m1", **k):
    """A made-here event already pushed to Google as ``gid`` on ``cal`` (not dirty)."""
    e = store.add(local(**k))
    store.mark_pushed(e["id"], google_calendar=cal, google_id=gid, etag='"0"', link="")
    return store.event(e["id"])


def event_requests(fake, cal):
    return [r for r in fake.requests if r.method == "GET" and r.url.path.endswith(f"/calendars/{cal}/events")]


def titles(store):
    return sorted(e["title"] for e in store.events("2026-01-01", "2028-12-31"))


async def test_refresh_calendars_stores_googles_list(sync, store, fake):
    fake.calendars.append({"id": "club", "summary": "Club", "accessRole": "writer"})
    await sync.refresh_calendars()
    cals = {c["id"]: c for c in store.calendars()}
    assert set(cals) == {LOCAL, "work", "home", "club"}
    assert cals["club"]["writable"] is True and cals["club"]["selected"] is False
    assert cals["work"]["selected"] is True


async def test_nothing_is_pulled_until_a_calendar_is_selected(sync, store, fake):
    fake.put("home", timed("h1", "Home thing"))
    out = await sync.pull()
    assert out == {"calendars": 1, "changed": 0, "removed": 0, "lost_edits": 0}
    assert event_requests(fake, "home") == [] and titles(store) == []
    store.set_calendar("work", selected=False)
    assert (await sync.pull())["calendars"] == 0
    store.set_calendar("home", selected=True)
    out = await sync.pull()
    assert out["calendars"] == 1 and out["changed"] == 1 and titles(store) == ["Home thing"]
    row = store.by_google("home", "h1")
    assert row["origin"] == "google" and row["calendar_id"] == "home" and row["dirty"] is False


async def test_pull_does_nothing_unless_connected(sync, store, fake, conn):
    fake.put("work", timed("a"))
    conn.keys.refresh_token = ""
    assert await sync.pull() == {"calendars": 0, "changed": 0, "removed": 0, "lost_edits": 0}
    conn.keys.refresh_token = "refresh"
    conn.mark_needs_reconnect()
    assert await sync.pull() == {"calendars": 0, "changed": 0, "removed": 0, "lost_edits": 0}
    assert fake.requests == [] and titles(store) == []


async def test_first_pull_is_bounded_then_incremental(sync, store, fake):
    fake.put("work", timed("a"))
    await sync.pull()
    first = event_requests(fake, "work")[0].url.params
    assert first["timeMin"] == "2026-10-17T12:00:00Z" and first["timeMax"] == "2027-11-18T12:00:00Z"
    assert "syncToken" not in first
    token = {c["id"]: c for c in store.calendars()}["work"]["sync_token"]
    assert token
    await sync.pull()
    second = event_requests(fake, "work")[1].url.params
    assert second["syncToken"] == token and "timeMin" not in second and "timeMax" not in second


async def test_a_changed_event_replaces_the_copy_and_a_cancelled_one_removes_it(sync, store, fake):
    fake.put("work", timed("a", "Old"))
    fake.put("work", timed("b", "Going"))
    assert (await sync.pull())["changed"] == 2
    fake.put("work", timed("a", "New", location="Room 4"))
    cancel(fake, "work", "b")
    out = await sync.pull()
    assert (out["changed"], out["removed"], out["lost_edits"]) == (1, 1, 0)
    assert titles(store) == ["New"]
    a = store.by_google("work", "a")
    assert a["location"] == "Room 4" and a["etag"] == fake.events["work"]["a"]["etag"]
    assert store.by_google("work", "b") is None


async def test_gone_clears_pulled_events_and_reads_in_full(sync, store, fake):
    fake.put("work", timed("a", "Stays"))
    fake.put("work", timed("x", "Vanished"))
    await sync.pull()
    del fake.events["work"]["x"]  # gone from Google without a change the sync token would report
    plain = store.add(local(title="Never pushed"))
    mine = pushed(store, gid="m1", title="Pushed here")
    store.set_sync_token("work", "stale")
    out = await sync.pull()
    params = [r.url.params for r in event_requests(fake, "work")[1:]]
    assert params[0]["syncToken"] == "stale" and "timeMin" in params[1] and "syncToken" not in params[1]
    assert out["changed"] == 1
    assert titles(store) == ["Never pushed", "Pushed here", "Stays"]
    assert store.event(plain["id"]) == plain and store.event(mine["id"]) == mine
    assert {c["id"]: c for c in store.calendars()}["work"]["sync_token"] not in ("", None, "stale")


async def test_gone_twice_is_raised(sync, store, fake):
    fake.fail["calendars/work/events"] = 410
    with pytest.raises(GoogleError) as e:
        await sync.pull()
    assert e.value.kind == "gone" and len(event_requests(fake, "work")) == 2


async def test_a_google_error_stops_the_pull_and_keeps_what_was_stored(sync, store, fake):
    store.set_calendar("home", selected=True)
    fake.put("work", timed("a", "Work thing"))
    fake.put("home", timed("h", "Home thing"))
    fake.fail["calendars/home/events"] = 500
    with pytest.raises(GoogleError) as e:
        await sync.pull()
    assert e.value.kind == "provider"
    assert titles(store) == ["Work thing"]
    cals = {c["id"]: c for c in store.calendars()}
    assert cals["work"]["sync_token"] and cals["home"]["sync_token"] is None


async def test_thin_items_do_not_break_the_pull(sync, store, fake):
    fake.events["work"] = {"a": {"id": "a", "status": "cancelled"},
                           "b": {"id": "b", "start": {"date": "2026-11-20"}, "end": {"date": "2026-11-21"}},
                           "c": {"id": "c", "summary": "C", "start": {"dateTime": "2026-11-20T09:00:00Z"},
                                 "endTimeUnspecified": True}}
    out = await sync.pull()
    assert out["changed"] == 2 and {e["title"] for e in store.events("2026-11-01", "2026-12-01")} == {"(No title)", "C"}


async def test_an_item_without_an_id_is_skipped(sync, store, fake):
    fake.events["work"] = {"": {"summary": "No id", "start": {"date": "2026-11-20"}, "end": {"date": "2026-11-21"}}}
    out = await sync.pull()
    assert out["changed"] == 0 and titles(store) == []


async def test_a_zero_length_event_is_stored_as_it_is(sync, store, fake):
    fake.put("work", timed("z", "Reminder", start="2026-11-20T09:00:00Z", end="2026-11-20T09:00:00Z"))
    await sync.pull()
    z = store.by_google("work", "z")
    assert z["start"] == z["end"] == "2026-11-20T09:00:00Z"


async def test_an_occurrence_keeps_its_series_id(sync, store, fake):
    fake.put("work", timed("s1_20261120T090000Z", "Standup", recurringEventId="s1"))
    fake.put("work", timed("single"))
    await sync.pull()
    assert store.by_google("work", "s1_20261120T090000Z")["series_id"] == "s1"
    assert store.by_google("work", "single")["series_id"] == ""


async def test_an_incremental_item_outside_the_window_is_not_stored(sync, store, fake):
    await sync.pull()
    fake.put("work", timed("late", "Too late", start="2027-11-18T12:00:01Z", end="2027-11-18T13:00:00Z"))
    fake.put("work", timed("early", "Too early", start="2026-10-17T10:00:00Z", end="2026-10-17T11:59:59Z"))
    fake.put("work", timed("edge", "At the edge", start="2026-10-17T11:00:00Z", end="2026-10-17T12:00:00Z"))
    fake.put("work", all_day("d0", "2026-10-16", "2026-10-17", "Day before"))
    fake.put("work", all_day("d1", "2026-10-17", "2026-10-18", "First day"))
    fake.put("work", all_day("d2", "2027-11-18", "2027-11-19", "Last day"))
    fake.put("work", all_day("d3", "2027-11-19", "2027-11-20", "Day after"))
    out = await sync.pull()
    assert out["changed"] == 3
    assert titles(store) == ["At the edge", "First day", "Last day"]


async def test_an_event_moved_out_of_the_window_is_removed(sync, store, fake):
    fake.put("work", timed("a", "Moving"))
    await sync.pull()
    fake.put("work", timed("a", "Moving", start="2028-03-01T09:00:00Z", end="2028-03-01T10:00:00Z"))
    out = await sync.pull()
    assert (out["changed"], out["removed"]) == (0, 1)
    assert store.by_google("work", "a") is None


async def test_a_dirty_local_copy_is_not_overwritten(sync, store, fake):
    fake.put("work", timed("a", "From Google"))
    await sync.pull()
    row = store.by_google("work", "a")
    store.update(row["id"], {**local(), "title": "Edited here"})
    fake.put("work", timed("a", "Changed on Google"))
    out = await sync.pull()
    assert out["changed"] == 0
    kept = store.event(row["id"])
    assert kept["title"] == "Edited here" and kept["dirty"] is True and kept["etag"] == row["etag"]


async def test_a_pushed_event_coming_back_is_matched_not_duplicated(sync, store, fake):
    mine = pushed(store, gid="m1")
    fake.put("work", timed("m1", "Renamed on Google", start="2028-06-01T09:00:00Z", end="2028-06-01T10:00:00Z"))
    out = await sync.pull()
    assert out["changed"] == 1
    rows = store.events("2026-01-01", "2029-01-01")
    assert [r["id"] for r in rows] == [mine["id"]]
    back = rows[0]
    assert back["title"] == "Renamed on Google" and back["start"] == "2028-06-01T09:00:00Z"
    assert (back["origin"], back["calendar_id"], back["google_calendar"], back["google_id"]) == (
        "local", LOCAL, "work", "m1")
    assert back["dirty"] is False and back["etag"] == fake.events["work"]["m1"]["etag"]
    assert back["tz"] == "Europe/London"


async def test_deleted_on_google_unchanged_here_is_deleted_here(sync, store, fake):
    fake.put("work", timed("a", "Pulled"))
    await sync.pull()
    mine = pushed(store, gid="m1")
    fake.put("work", timed("m1", "Made here"))
    await sync.pull()
    cancel(fake, "work", "a")
    cancel(fake, "work", "m1")
    out = await sync.pull()
    assert (out["removed"], out["lost_edits"]) == (2, 0)
    assert store.by_google("work", "a") is None and store.event(mine["id"]) is None


async def test_deleted_on_google_but_edited_here_survives_when_made_here(sync, store, fake):
    mine = pushed(store, gid="m1")
    fake.put("work", timed("m1", "Made here"))
    await sync.pull()
    store.update(mine["id"], local(title="Edited here"))
    cancel(fake, "work", "m1")
    out = await sync.pull()
    assert (out["removed"], out["lost_edits"]) == (0, 0)
    kept = store.event(mine["id"])
    assert kept["title"] == "Edited here" and kept["dirty"] is True and kept["deleted"] is False


async def test_deleted_on_google_but_edited_here_is_lost_when_pulled(sync, store, fake):
    fake.put("work", timed("a", "Pulled"))
    fake.put("work", timed("b", "Also pulled"))
    await sync.pull()
    for gid in ("a", "b"):
        row = store.by_google("work", gid)
        store.update(row["id"], {**local(), "title": f"Edited {gid}"})
    cancel(fake, "work", "a")
    out = await sync.pull()
    assert out["lost_edits"] == 1 and store.meta("lost_edits") == "1"
    assert store.by_google("work", "a") is None and titles(store) == ["Edited b"]
    cancel(fake, "work", "b")
    out = await sync.pull()
    assert out["lost_edits"] == 1 and store.meta("lost_edits") == "2"


async def test_a_tombstone_is_not_brought_back_by_pull(sync, store, fake):
    fake.put("work", timed("a", "Pulled"))
    await sync.pull()
    mine = pushed(store, gid="m1")
    fake.put("work", timed("m1", "Made here"))
    await sync.pull()
    pulled = store.by_google("work", "a")
    store.delete(pulled["id"])
    store.delete(mine["id"])
    fake.put("work", timed("a", "Changed on Google"))
    fake.put("work", timed("m1", "Changed on Google too"))
    out = await sync.pull()
    assert out["changed"] == 0 and titles(store) == []
    assert store.event(pulled["id"])["deleted"] is True and store.event(mine["id"])["deleted"] is True
    cancel(fake, "work", "a")
    cancel(fake, "work", "m1")
    out = await sync.pull()
    assert (out["removed"], out["lost_edits"]) == (2, 0) and store.meta("lost_edits") == ""
    assert store.event(pulled["id"]) is None and store.event(mine["id"]) is None


async def test_unselecting_removes_that_calendars_pulled_events(store):
    store.upsert_google([{"id": "work", "name": "Work", "writable": True}])
    store.set_calendar("work", selected=True)
    store.apply_remote("work", "a", {"title": "Pulled", "all_day": False, "start": "2026-11-20T09:00:00Z",
                                     "end": "2026-11-20T10:00:00Z", "tz": ""}, etag="e", link="", series_id="")
    store.set_sync_token("work", "sync-1")
    mine = store.add(local())
    store.mark_pushed(mine["id"], google_calendar="work", google_id="m1", etag="e", link="")
    store.set_calendar("work", selected=False)
    store.clear_pulled("work")
    assert store.by_google("work", "a") is None and store.event(mine["id"]) is not None
    assert {c["id"]: c for c in store.calendars()}["work"]["sync_token"] is None

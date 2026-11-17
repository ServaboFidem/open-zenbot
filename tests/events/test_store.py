import re

import pytest

from src.events.store import CalendarStore, EventError, LOCAL, google_event_id, validate


def ev(**k):
    return {"title": "Dentist", "all_day": False, "start": "2026-11-17T14:00:00Z",
            "end": "2026-11-17T15:00:00Z", "tz": "America/New_York", **k}


def gcal(id="c1", **k):
    return {"id": id, "name": "Work", "color": "#112233", "tz": "Europe/Paris",
            "writable": True, "primary": False, **k}


def remote(**k):
    return {"title": "Pulled", "location": "", "description": "", "all_day": False,
            "start": "2026-11-17T10:00:00Z", "end": "2026-11-17T11:00:00Z", "tz": "", **k}


def pull(store, cal="c1", gid="g1", **k):
    store.apply_remote(cal, gid, remote(**k), etag="e1", link="http://l", series_id="")


def test_built_in_calendar_always_exists(store):
    assert [c["id"] for c in store.calendars()] == ["local"]
    c = store.calendars()[0]
    assert (c["name"], c["source"], c["writable"], c["visible"]) == ("OpenZenBot", "local", True, True)
    assert LOCAL == "local"


def test_add_then_range_query(store):
    e = store.add(ev())
    assert e["origin"] == "local" and e["dirty"] is True and e["generation"] == 0
    assert e["calendar_id"] == "local" and e["deleted"] is False
    assert [x["id"] for x in store.events("2026-11-17", "2026-11-18")] == [e["id"]]
    assert store.events("2026-11-18", "2026-11-19") == []


def test_events_ordered_by_start(store):
    b = store.add(ev(start="2026-11-17T16:00:00Z", end="2026-11-17T17:00:00Z"))
    a = store.add(ev())
    assert [x["id"] for x in store.events("2026-11-17", "2026-11-18")] == [a["id"], b["id"]]


def test_all_day_range_is_end_exclusive(store):
    store.add(ev(all_day=True, start="2026-11-17", end="2026-11-18", tz=""))
    assert len(store.events("2026-11-17", "2026-11-18")) == 1
    assert store.events("2026-11-18", "2026-11-19") == []


@pytest.mark.parametrize("bad", [{"title": " "}, {"title": "x" * 301}, {"end": "2026-11-17T13:00:00Z"},
                                 {"start": "17/11/2026"}, {"tz": ""}, {"description": "d" * 8001},
                                 {"location": "l" * 501}, {"all_day": True}])
def test_validate_refuses(store, bad):
    with pytest.raises(EventError):
        store.validate(ev(**bad))


def test_validate_all_day_refusals(store):
    with pytest.raises(EventError):
        validate(ev(all_day=True, start="2026-11-17", end="2026-11-17", tz=""))
    with pytest.raises(EventError):
        validate(ev(all_day=False, start="2026-11-17", end="2026-11-18"))


def test_validate_cleans(store):
    v = store.validate(ev(title="  Dentist  "))
    assert v == {"title": "Dentist", "location": "", "description": "", "all_day": False,
                 "start": "2026-11-17T14:00:00Z", "end": "2026-11-17T15:00:00Z",
                 "tz": "America/New_York"}
    d = validate(ev(all_day=True, start="2026-11-17", end="2026-11-18", tz=""))
    assert d["all_day"] is True and d["tz"] == ""


def test_update_sets_dirty_and_missing_is_none(store):
    e = store.add(ev())
    store.mark_pushed(e["id"], google_calendar="c", google_id="x", etag="e", link="l")
    assert store.event(e["id"])["dirty"] is False
    u = store.update(e["id"], ev(title="New"))
    assert u["title"] == "New" and u["dirty"] is True
    assert store.update("nope", ev()) is None
    assert store.event("nope") is None


def test_delete_keeps_a_tombstone_only_for_a_pushed_event(store):
    a, b = store.add(ev()), store.add(ev())
    store.mark_pushed(b["id"], google_calendar="cal", google_id=google_event_id(b), etag="e", link="l")
    assert store.delete(a["id"]) and store.event(a["id"]) is None
    assert store.delete(b["id"]) and store.event(b["id"])["deleted"] is True
    assert store.events("2026-01-01", "2027-01-01") == [] and [p["id"] for p in store.pending()] == [b["id"]]
    assert store.delete("nope") is False


def test_google_event_id_is_base32hex(store):
    e = store.add(ev())
    assert re.fullmatch(r"[a-v0-9]{5,1024}", google_event_id(e)) and google_event_id(e).endswith("g0")
    assert store.bump_generation(e["id"]) == 1
    assert google_event_id(store.event(e["id"])).endswith("g1")


def test_upsert_google_keeps_choices_and_drops_gone_calendars(store):
    store.upsert_google([gcal("c1"), gcal("c2")])
    c1 = next(c for c in store.calendars() if c["id"] == "c1")
    assert (c1["selected"], c1["visible"], c1["source"], c1["writable"]) == (False, True, "google", True)
    store.set_calendar("c1", selected=True, visible=False)
    store.set_sync_token("c1", "tok")
    pull(store, "c1", "g1")
    pull(store, "c2", "g2")
    store.upsert_google([gcal("c1", name="Renamed", writable=False)])
    cals = {c["id"]: c for c in store.calendars()}
    assert set(cals) == {"local", "c1"}
    assert cals["c1"]["name"] == "Renamed" and cals["c1"]["writable"] is False
    assert (cals["c1"]["selected"], cals["c1"]["visible"], cals["c1"]["sync_token"]) == (True, False, "tok")
    assert store.by_google("c1", "g1") is not None and store.by_google("c2", "g2") is None


def test_set_calendar(store):
    store.upsert_google([gcal("c1")])
    r = store.set_calendar("c1", visible=False)
    assert r["visible"] is False and r["selected"] is False
    assert store.set_calendar("c1", selected=True)["selected"] is True
    assert store.set_calendar("nope", visible=True) is None
    assert store.set_calendar("c1")["id"] == "c1"


def test_set_sync_token(store):
    store.upsert_google([gcal("c1")])
    store.set_sync_token("c1", "t")
    assert store.calendars()[1]["sync_token"] == "t"
    store.set_sync_token("c1", None)
    assert store.calendars()[1]["sync_token"] is None


def test_clear_pulled_and_drop_google(store):
    store.upsert_google([gcal("c1"), gcal("c2")])
    store.set_sync_token("c1", "t")
    pull(store, "c1", "g1")
    pull(store, "c2", "g2")
    local = store.add(ev())
    store.clear_pulled("c1")
    assert store.by_google("c1", "g1") is None and store.by_google("c2", "g2") is not None
    assert next(c for c in store.calendars() if c["id"] == "c1")["sync_token"] is None
    store.drop_google()
    assert [c["id"] for c in store.calendars()] == ["local"]
    assert store.by_google("c2", "g2") is None and store.event(local["id"]) is not None


def test_clear_clean_pulled_keeps_pending_changes(store):
    store.upsert_google([gcal("c1"), gcal("c2")])
    store.set_sync_token("c1", "t")
    store.set_sync_token("c2", "t2")
    pull(store, "c1", "clean")
    pull(store, "c1", "edited")
    pull(store, "c1", "deleted")
    pull(store, "c2", "other")
    edited = store.by_google("c1", "edited")
    store.update(edited["id"], ev(title="Edited"))
    store.delete(store.by_google("c1", "deleted")["id"])
    mine = store.add(ev())
    store.mark_pushed(mine["id"], google_calendar="c1", google_id="m1", etag="e", link="")
    store.clear_clean_pulled("c1")
    assert store.by_google("c1", "clean") is None
    assert store.by_google("c1", "edited")["title"] == "Edited" and store.by_google("c1", "edited")["dirty"]
    assert store.by_google("c1", "deleted")["deleted"] is True
    assert store.by_google("c2", "other") is not None and store.event(mine["id"]) is not None
    cals = {c["id"]: c for c in store.calendars()}
    assert cals["c1"]["sync_token"] is None and cals["c2"]["sync_token"] == "t2"


def test_apply_remote_inserts_then_replaces(store):
    store.upsert_google([gcal("c1")])
    pull(store, "c1", "g1")
    e = store.by_google("c1", "g1")
    assert (e["origin"], e["calendar_id"], e["google_calendar"], e["dirty"], e["etag"], e["link"]) == \
        ("google", "c1", "c1", False, "e1", "http://l")
    assert e["tz"] == ""
    store.apply_remote("c1", "g1", remote(title="Changed"), etag="e2", link="l2", series_id="s")
    e2 = store.by_google("c1", "g1")
    assert e2["id"] == e["id"] and e2["title"] == "Changed" and e2["etag"] == "e2"
    assert e2["series_id"] == "s" and store.counts()["pulled"] == 1


def test_apply_remote_replaces_a_made_here_event_in_place(store):
    e = store.add(ev())
    store.mark_pushed(e["id"], google_calendar="c1", google_id="gx", etag="e", link="l")
    store.apply_remote("c1", "gx", remote(title="Echo"), etag="e9", link="l9", series_id="")
    got = store.by_google("c1", "gx")
    assert got["id"] == e["id"] and got["title"] == "Echo" and got["dirty"] is False
    assert got["origin"] == "local"


def test_apply_remote_skips_a_dirty_row(store):
    e = store.add(ev(title="Mine"))
    store.mark_pushed(e["id"], google_calendar="c1", google_id="gx", etag="e", link="l")
    store.update(e["id"], ev(title="Edited"))
    store.apply_remote("c1", "gx", remote(title="Remote"), etag="e9", link="l9", series_id="")
    got = store.event(e["id"])
    assert got["title"] == "Edited" and got["dirty"] is True and got["etag"] == "e"


def test_apply_remote_leaves_a_tombstone(store):
    e = store.add(ev())
    store.mark_pushed(e["id"], google_calendar="c1", google_id="gx", etag="e", link="l")
    store.delete(e["id"])
    store.apply_remote("c1", "gx", remote(title="Back"), etag="e9", link="l9", series_id="")
    got = store.event(e["id"])
    assert got["deleted"] is True and got["dirty"] is True and got["title"] == "Dentist"


def test_apply_remote_keeps_tz_of_a_timed_event(store):
    e = store.add(ev())
    store.mark_pushed(e["id"], google_calendar="c1", google_id="gx", etag="e", link="l")
    store.apply_remote("c1", "gx", remote(title="Echo"), etag="e2", link="l", series_id="")
    got = store.event(e["id"])
    assert got["tz"] == "America/New_York" and got["title"] == "Echo"
    store.update(got["id"], ev())


def test_validate_refuses_non_ascii_digits(store):
    with pytest.raises(EventError):
        validate(ev(start="2026-11-17T14:00:00Z".replace("2", "\u0662", 1)))
    with pytest.raises(EventError):
        validate(ev(all_day=True, start="\u0662026-11-17", end="2026-11-18", tz=""))


def test_by_google_includes_tombstones(store):
    e = store.add(ev())
    store.mark_pushed(e["id"], google_calendar="c", google_id="gz", etag="e", link="l")
    store.delete(e["id"])
    assert store.by_google("c", "gz")["deleted"] is True
    assert store.by_google("c", "other") is None


def test_remove_remote(store):
    pull(store, "c1", "g1")
    store.remove_remote("c1", "g1")
    assert store.by_google("c1", "g1") is None
    store.remove_remote("c1", "g1")


def test_mark_pushed_and_push_failed(store):
    e = store.add(ev())
    store.push_failed(e["id"], "forbidden")
    assert store.event(e["id"])["push_error"] == "forbidden"
    store.mark_pushed(e["id"], google_calendar="c", google_id="gid", etag="et", link="li")
    g = store.event(e["id"])
    assert (g["dirty"], g["push_error"], g["google_calendar"], g["google_id"], g["etag"], g["link"]) == \
        (False, "", "c", "gid", "et", "li")
    assert store.pending() == []


def test_purge(store):
    e = store.add(ev())
    store.purge(e["id"])
    assert store.event(e["id"]) is None


def test_pushed_calendars_only_made_here_with_google_id(store):
    a, b = store.add(ev()), store.add(ev())
    store.add(ev())
    store.mark_pushed(a["id"], google_calendar="c1", google_id="a", etag="", link="")
    store.mark_pushed(b["id"], google_calendar="c2", google_id="b", etag="", link="")
    pull(store, "c3", "g3")
    assert store.pushed_calendars() == {"c1", "c2"}


def test_clear_links_keeps_events_made_here(store):
    e = store.add(ev())
    store.mark_pushed(e["id"], google_calendar="c", google_id="g", etag="et", link="li")
    store.bump_generation(e["id"])
    store.clear_links()
    c = store.event(e["id"])
    assert (c["google_calendar"], c["google_id"], c["etag"], c["link"], c["generation"], c["dirty"]) == \
        ("", "", "", "", 0, True)
    assert c["title"] == "Dentist" and store.pushed_calendars() == set()


def test_clear_links_drops_tombstones(store):
    e = store.add(ev())
    store.mark_pushed(e["id"], google_calendar="c", google_id="g", etag="", link="")
    store.delete(e["id"])
    store.clear_links()
    assert store.event(e["id"]) is None and store.pending() == []


def test_clear_links_leaves_pulled_events(store):
    pull(store, "c1", "g1")
    store.clear_links()
    assert store.by_google("c1", "g1") is not None


def test_meta(store):
    assert store.meta("k") == ""
    store.set_meta("k", "v")
    store.set_meta("k", "w")
    assert store.meta("k") == "w"


def test_counts(store):
    assert store.counts() == {"local": 0, "pulled": 0, "pending": 0}
    store.add(ev())
    pull(store, "c1", "g1")
    assert store.counts() == {"local": 1, "pulled": 1, "pending": 1}


def test_reopening_the_file_keeps_everything(tmp_path):
    p = tmp_path / "x" / "calendar.db"
    s = CalendarStore(p)
    e = s.add(ev())
    s.upsert_google([gcal("c1")])
    s.set_meta("a", "b")
    s.close()
    s = CalendarStore(p)
    try:
        assert s.event(e["id"])["title"] == "Dentist"
        assert [c["id"] for c in s.calendars()] == ["local", "c1"]
        assert s.meta("a") == "b"
    finally:
        s.close()

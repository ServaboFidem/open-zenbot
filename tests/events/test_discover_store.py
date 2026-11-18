import pytest

from src.events.discover_store import LAST_ERROR, LAST_REFRESH, DiscoverStore


def ev(id, **o):
    d = {"id": id, "name": f"Event {id}", "summary": "", "start_date": "2026-11-20", "start_time": "19:00",
         "end_date": "2026-11-20", "end_time": "21:00", "timezone": "America/New_York", "venue": "Hall",
         "address": "1 Main St", "city": "Boston", "region": "MA", "image_url": "", "url": f"https://x/{id}",
         "categories": [], "category_names": [], "format": ""}
    d.update(o)
    return d


@pytest.fixture
def store(tmp_path):
    s = DiscoverStore(tmp_path / "discover.db")
    yield s
    s.close()


def ids(rows):
    return [r["id"] for r in rows]


def test_upsert_updates_known_id_and_deletes_no_other(store):
    assert store.upsert_pool([ev("a"), ev("b")], "t1") == 2
    store.upsert_pool([ev("a", name="Renamed")], "t2")
    assert store.pool_size() == 2
    assert store.pool_row("a")["name"] == "Renamed"
    assert store.pool_row("b")["name"] == "Event b"
    assert store.pool_row("zzz") is None


def test_drop_past_keeps_today(store):
    store.upsert_pool([ev("old", start_date="2026-11-17"), ev("today", start_date="2026-11-18"),
                       ev("later", start_date="2026-12-01")], "t")
    assert store.drop_past("2026-11-18") == 1
    assert store.pool_row("old") is None
    assert store.pool_row("today") is not None
    assert store.pool_size() == 2


def test_candidates_skip_rows_on_any_saved_card(store):
    store.upsert_pool([ev("a"), ev("b"), ev("c")], "t")
    d = store.save_digest([(store.pool_row("a"), "w"), (store.pool_row("b"), "w")],
                          kind="weekly", note="", session_id="s", created_at="2026-11-18T10:00:00Z")
    store.set_state(d["cards"][0]["id"], "dismissed")
    store.mark_added(d["cards"][1]["id"], "cal1")
    assert ids(store.candidates("2026-11-18", [])) == ["c"]
    assert not store.is_candidate("a", "2026-11-18", [])
    assert store.is_candidate("c", "2026-11-18", [])


def test_candidates_skip_rows_before_today(store):
    store.upsert_pool([ev("past", start_date="2026-11-17"), ev("now", start_date="2026-11-18")], "t")
    assert ids(store.candidates("2026-11-18", [])) == ["now"]
    assert not store.is_candidate("past", "2026-11-18", [])
    assert not store.is_candidate("missing", "2026-11-18", [])


def test_categories_ticked_filter_rows(store):
    store.upsert_pool([ev("music", categories=["EventbriteCategory/103"]),
                       ev("sports", categories=["EventbriteCategory/108"]),
                       ev("none", categories=[])], "t")
    got = ids(store.candidates("2026-11-18", ["EventbriteCategory/103"]))
    assert sorted(got) == ["music", "none"]
    assert store.is_candidate("music", "2026-11-18", ["EventbriteCategory/103"])
    assert store.is_candidate("none", "2026-11-18", ["EventbriteCategory/103"])
    assert not store.is_candidate("sports", "2026-11-18", ["EventbriteCategory/103"])


def test_no_categories_ticked_every_row_is_a_candidate(store):
    store.upsert_pool([ev("a", categories=["EventbriteCategory/103"]),
                       ev("b", categories=["EventbriteCategory/108"]), ev("c")], "t")
    assert sorted(ids(store.candidates("2026-11-18", []))) == ["a", "b", "c"]


def test_text_matches_name_summary_and_venue_without_case(store):
    store.upsert_pool([ev("n", name="Jazz Night"), ev("s", summary="a JAZZ combo"),
                       ev("v", venue="The Jazz Club"), ev("x", name="Polka")], "t")
    assert sorted(ids(store.candidates("2026-11-18", [], text="jAzZ"))) == ["n", "s", "v"]


def test_order_by_date_time_then_id_and_paging(store):
    store.upsert_pool([ev("b", start_date="2026-11-19", start_time="10:00"),
                       ev("a", start_date="2026-11-19", start_time="10:00"),
                       ev("c", start_date="2026-11-20", start_time="08:00"),
                       ev("d", start_date="2026-11-19", start_time="09:00")], "t")
    assert ids(store.candidates("2026-11-18", [])) == ["d", "a", "b", "c"]
    assert ids(store.candidates("2026-11-18", [], limit=2, offset=1)) == ["a", "b"]
    assert ids(store.candidates("2026-11-18", [], start="2026-11-20")) == ["c"]
    assert ids(store.candidates("2026-11-18", [], end="2026-11-19")) == ["d", "a", "b"]


def test_category_argument_matches_tag_or_name(store):
    store.upsert_pool([ev("m", categories=["EventbriteCategory/103"], category_names=["Music"]),
                       ev("o", categories=["EventbriteCategory/108"], category_names=["Sports"])], "t")
    assert ids(store.candidates("2026-11-18", [], category="music")) == ["m"]
    assert ids(store.candidates("2026-11-18", [], category="EventbriteCategory/108")) == ["o"]


def test_limit_is_capped_at_60(store):
    store.upsert_pool([ev(f"e{i:03d}") for i in range(80)], "t")
    assert len(store.candidates("2026-11-18", [], limit=500)) == 60
    assert len(store.candidates("2026-11-18", [])) == 60


def test_card_keeps_its_snapshot_after_the_pool_changes(store):
    store.upsert_pool([ev("a", name="Before", categories=["EventbriteCategory/103"])], "t")
    d = store.save_digest([(store.pool_row("a"), "because")], kind="on_demand", note="n",
                          session_id="s", created_at="2026-11-18T10:00:00Z")
    store.upsert_pool([ev("a", name="After")], "t2")
    card = store.card(d["cards"][0]["id"])
    assert card["name"] == "Before"
    assert card["categories"] == ["EventbriteCategory/103"]
    assert card["event_id"] == "a" and card["why"] == "because"
    assert card["state"] == "new" and card["calendar_event_id"] == "" and card["has_image"] is False
    assert card["digest_id"] == d["id"]
    assert store.pool_row("a")["name"] == "After"


def test_digest_listing_and_has_digest_for(store):
    store.upsert_pool([ev("a"), ev("b")], "t")
    assert not store.has_digest_for("s1")
    d1 = store.save_digest([(store.pool_row("a"), "w")], kind="weekly", note="one", session_id="s1",
                           created_at="2026-11-18T10:00:00Z")
    d2 = store.save_digest([(store.pool_row("b"), "w")], kind="on_demand", note="two", session_id="s2",
                           created_at="2026-11-19T10:00:00Z")
    assert store.has_digest_for("s1") and store.has_digest_for("s2") and not store.has_digest_for("s3")
    assert [d["id"] for d in store.digests(None, 10)] == [d2["id"], d1["id"]]
    assert [d["id"] for d in store.digests(d2["created_at"], 10)] == [d1["id"]]
    assert len(store.digests(None, 1)) == 1
    got = store.digest(d1["id"])
    assert got["kind"] == "weekly" and got["note"] == "one" and len(got["cards"]) == 1
    assert store.digest("nope") is None


def test_mark_added_twice_keeps_first_calendar_event_id(store):
    store.upsert_pool([ev("a")], "t")
    d = store.save_digest([(store.pool_row("a"), "w")], kind="weekly", note="", session_id="s",
                          created_at="2026-11-18T10:00:00Z")
    cid = d["cards"][0]["id"]
    assert store.mark_added(cid, "first")["state"] == "added"
    again = store.mark_added(cid, "second")
    assert again["calendar_event_id"] == "first" and again["state"] == "added"
    assert store.mark_added("missing", "x") is None


def test_set_state_and_image(store):
    store.upsert_pool([ev("a")], "t")
    d = store.save_digest([(store.pool_row("a"), "w")], kind="weekly", note="", session_id="s",
                          created_at="2026-11-18T10:00:00Z")
    cid = d["cards"][0]["id"]
    assert store.set_state(cid, "dismissed")["state"] == "dismissed"
    with pytest.raises(ValueError):
        store.set_state(cid, "bogus")
    store.set_image(cid, True)
    assert store.card(cid)["has_image"] is True
    assert store.set_state("missing", "new") is None


def test_meta(store):
    assert store.meta(LAST_REFRESH) == ""
    store.set_meta(LAST_REFRESH, "t1")
    store.set_meta(LAST_REFRESH, "t2")
    store.set_meta(LAST_ERROR, "network")
    assert store.meta(LAST_REFRESH) == "t2" and store.meta(LAST_ERROR) == "network"


def test_file_opens_again_with_its_rows(tmp_path):
    path = tmp_path / "sub" / "discover.db"
    s = DiscoverStore(path)
    s.upsert_pool([ev("a", categories=["EventbriteCategory/103"])], "t")
    d = s.save_digest([(s.pool_row("a"), "w")], kind="weekly", note="n", session_id="s",
                      created_at="2026-11-18T10:00:00Z")
    s.set_meta(LAST_REFRESH, "t")
    s.close()
    s = DiscoverStore(path)
    try:
        assert s.pool_row("a")["categories"] == ["EventbriteCategory/103"]
        assert s.digest(d["id"])["cards"][0]["event_id"] == "a"
        assert s.meta(LAST_REFRESH) == "t" and s.has_digest_for("s")
    finally:
        s.close()


def test_an_old_file_gains_the_source_column(tmp_path):
    import sqlite3

    from src.events import discover_store as ds

    old = ds.SCHEMA.replace(", source TEXT DEFAULT ''", "").replace("source TEXT DEFAULT '',", "")
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(old)
    assert "source" not in [r[1] for r in db.execute("PRAGMA table_info(pool)")]
    db.execute("INSERT INTO pool(id, name, start_date) VALUES('p', 'Old', '2026-11-20')")
    db.execute("INSERT INTO cards(id, digest_id, event_id, why, name) VALUES('c', 'd', 'p', 'w', 'Old')")
    db.commit()
    db.close()
    s = DiscoverStore(path)
    assert s.pool_row("p")["source"] == "eventbrite"
    assert s.card("c")["source"] == "eventbrite"
    s.close()
    DiscoverStore(path).close()


def test_rows_keep_their_source(store):
    store.upsert_pool([ev("fl-1", source="findlocal"), ev("2")], "t")
    assert store.pool_row("fl-1")["source"] == "findlocal"
    assert store.pool_row("2")["source"] == "eventbrite"
    assert store.pool_size("findlocal") == 1
    assert store.pool_size("eventbrite") == 1


def test_clear_pool_by_source(store):
    store.upsert_pool([ev("fl-1", source="findlocal"), ev("2")], "t")
    assert store.clear_pool("findlocal") == 1
    assert store.pool_row("fl-1") is None
    assert store.pool_row("2") is not None


def test_clear_pool_clears_all_without_one(store):
    store.upsert_pool([ev("fl-1", source="findlocal"), ev("2")], "t")
    assert store.clear_pool() == 2
    assert store.pool_size() == 0


def test_drop_stale_takes_only_old_rows_of_that_source(store):
    store.upsert_pool([ev("fl-old", source="findlocal"), ev("eb-old")], "2026-11-17T09:00:00Z")
    store.upsert_pool([ev("fl-new", source="findlocal")], "2026-11-18T09:00:00Z")
    assert store.drop_stale("findlocal", "2026-11-18T00:00:00Z") == 1
    assert store.pool_row("fl-old") is None
    assert store.pool_row("fl-new") is not None
    assert store.pool_row("eb-old") is not None


def test_a_stale_findlocal_row_is_no_candidate(store):
    store.upsert_pool([ev("fl-1", source="findlocal"), ev("2")], "2026-11-18T09:00:00Z")
    late = "2026-11-18T09:00:01Z"
    assert ids(store.candidates("2026-11-18", [], fresh_after=late)) == ["2"]
    assert store.candidate_count("2026-11-18", [], fresh_after=late) == 1
    assert not store.is_candidate("fl-1", "2026-11-18", [], fresh_after=late)
    assert store.is_candidate("2", "2026-11-18", [], fresh_after=late)
    assert sorted(ids(store.candidates("2026-11-18", [], fresh_after=""))) == ["2", "fl-1"]
    assert store.candidate_count("2026-11-18", []) == 2
    assert store.is_candidate("fl-1", "2026-11-18", [])


def test_a_card_keeps_the_source_of_its_row(store):
    store.upsert_pool([ev("fl-1", source="findlocal")], "t")
    d = store.save_digest([(store.pool_row("fl-1"), "w")], kind="weekly", note="", session_id="s",
                          created_at="2026-11-18T10:00:00Z")
    assert d["cards"][0]["source"] == "findlocal"


def test_name_key():
    from src.events.discover_store import name_key

    assert name_key("Jazz Night!") == "jazznight"
    assert name_key("  !! ") == ""


def test_known_duplicate_by_eventbrite_id_in_the_pool(store):
    store.upsert_pool([ev("123")], "t")
    assert store.known_duplicate({"id": "fl-1", "eventbrite_id": "123", "name": "x", "start_date": "2030-01-01"})


def test_known_duplicate_by_eventbrite_id_on_a_card(store):
    store.upsert_pool([ev("123")], "t")
    store.save_digest([(store.pool_row("123"), "w")], kind="weekly", note="", session_id="s",
                      created_at="2026-11-18T10:00:00Z")
    store.clear_pool()
    assert store.known_duplicate({"id": "fl-1", "eventbrite_id": "123", "name": "x", "start_date": "2030-01-01"})


def test_known_duplicate_by_day_and_name(store):
    store.upsert_pool([ev("1", name="jazz night")], "t")
    fl = {"id": "fl-1", "source": "findlocal", "name": "Jazz Night!", "start_date": "2026-11-20"}
    assert store.known_duplicate(fl)
    assert not store.known_duplicate({**fl, "start_date": "2026-11-21"})


def test_known_duplicate_ignores_findlocal_rows_and_the_same_events_card(store):
    store.upsert_pool([ev("fl-2", name="Jazz Night", source="findlocal"), ev("fl-1", name="Jazz Night",
                                                                            source="findlocal")], "t")
    fl = {"id": "fl-1", "source": "findlocal", "name": "Jazz Night", "start_date": "2026-11-20"}
    assert not store.known_duplicate(fl)
    store.save_digest([(store.pool_row("fl-1"), "w")], kind="weekly", note="", session_id="s",
                      created_at="2026-11-18T10:00:00Z")
    assert not store.known_duplicate(fl)
    assert store.known_duplicate({**fl, "id": "fl-9"})


def test_known_duplicate_needs_a_name_with_letters_or_digits(store):
    store.upsert_pool([ev("1", name="!!!")], "t")
    assert not store.known_duplicate({"id": "fl-1", "name": "???", "start_date": "2026-11-20"})

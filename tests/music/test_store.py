import time

import pytest

from src.music.store import MusicStore


@pytest.fixture
def store(tmp_path):
    s = MusicStore(tmp_path / "music.db")
    yield s
    s.close()


def rel(i, master=None, **kw):
    row = {"release_id": i, "master_id": master, "title": f"T{i}", "year": 1990,
           "artists": [{"id": 1, "name": "A"}], "formats": ["LP"], "styles": ["Ambient"],
           "genres": ["Electronic"], "rating": 4, "date_added": "2020-01-01", "mapped": ["ambient"]}
    row.update(kw)
    return row


def test_replace_releases_round_trip(store):
    store.replace_releases([rel(1, 10), rel(2, None)])
    got = {r["release_id"]: r for r in store.releases()}
    assert got[1]["master_id"] == 10 and got[2]["master_id"] is None
    assert got[1]["artists"] == [{"id": 1, "name": "A"}] and got[1]["mapped"] == ["ambient"]


def test_replace_releases_is_atomic(store):
    store.replace_releases([rel(1, 10)])
    with pytest.raises(TypeError):
        store.replace_releases([rel(2, 20), rel(3, 30, styles={1, 2})])  # set: not JSON
    assert [r["release_id"] for r in store.releases()] == [1]


def test_owned_includes_marks(store):
    store.replace_releases([rel(1, 10), rel(2, None)])
    store.mark_owned(77, "Artist", "Title", ["Rock"])
    assert store.owned_master_ids() == {10, 77}
    marks = store.owned_marks()
    assert marks[0]["master_id"] == 77 and marks[0]["genres"] == ["Rock"]
    store.mark_owned(77, "Artist", "Title", ["Rock", "Pop"])
    assert len(store.owned_marks()) == 1


def test_suggested_spans_digests_and_on_demand(store):
    store.add_digest({"kind": "weekly", "week_of": "2026-11-09", "note": "", "session_id": "s"},
                     [{"master_id": 1, "release_id": 11}, {"master_id": 2}])
    store.add_digest({"kind": "on_demand", "week_of": "2026-11-13", "note": "", "session_id": "s"},
                     [{"master_id": 3}, {"title": "no master"}])
    assert store.suggested_master_ids() == {1, 2, 3}


def test_digest_round_trip_and_paging(store):
    ids = []
    for w in ("2026-11-02", "2026-11-09", "2026-11-16"):
        ids.append(store.add_digest({"kind": "weekly", "week_of": w, "note": w, "session_id": "s"},
                                    [{"master_id": 1, "title": "x"}]))
        time.sleep(0.002)
    assert len(set(ids)) == 3
    page = store.digests(None, 2)
    assert [d["id"] for d in page] == [ids[2], ids[1]]
    assert page[0]["cards"][0]["title"] == "x" and page[0]["cards"][0]["digest_id"] == ids[2]
    rest = store.digests(page[1]["created_at"], 2)
    assert [d["id"] for d in rest] == [ids[0]]
    d = store.digest(ids[0])
    assert d["note"] == "2026-11-02" and d["cards"][0]["id"]
    assert store.card(d["cards"][0]["id"])["title"] == "x"
    assert store.digest("nope") is None and store.card("nope") is None


def test_weekly_id_is_iso_week_with_suffix(store):
    d = {"kind": "weekly", "week_of": "2026-11-09", "note": "", "session_id": "s"}
    assert store.add_digest(d, []) == "2026-W46"
    assert store.add_digest(d, []) == "2026-W46-2"
    assert len(store.add_digest({**d, "kind": "on_demand"}, [])) == 32


def test_unread_and_mark_read(store):
    a = store.add_digest({"kind": "weekly", "week_of": "2026-11-09", "note": "", "session_id": "s"}, [])
    store.add_digest({"kind": "on_demand", "week_of": "2026-11-09", "note": "", "session_id": "s",
                      "read_at": "2026-11-09T00:00:00+00:00"}, [])
    assert store.unread_count() == 1
    store.mark_read(a)
    assert store.unread_count() == 0
    first = store.digest(a)["read_at"]
    store.mark_read(a)
    assert store.digest(a)["read_at"] == first


def test_reaction_timestamps(store):
    d = store.add_digest({"kind": "weekly", "week_of": "2026-11-09", "note": "", "session_id": "s"},
                         [{"master_id": 1}])
    cid = store.digest(d)["cards"][0]["id"]
    c = store.card(cid)
    assert c["reaction"] is None and c["reaction_at"] is None
    store.set_reaction(cid, "passed")
    c = store.card(cid)
    assert c["reaction"] == "passed" and c["reaction_at"]
    store.set_reaction(cid, None)
    c = store.card(cid)
    assert c["reaction"] is None and c["reaction_at"] is None
    with pytest.raises(ValueError):
        store.set_reaction(cid, "loved")
    store.set_wantlist(cid, True, True)
    c = store.card(cid)
    assert c["on_wantlist"] is True and c["wanted_by_card"] is True
    store.set_wantlist(cid, False, False)
    assert store.card(cid)["on_wantlist"] is False


def test_scan_defaults_and_updates(store):
    s = store.scan()
    assert s["state"] == "idle" and s["done"] == 0 and s["unmapped"] == []
    store.set_scan(state="running", stage="fetch", done=3, total=10, unmapped=["x"])
    s = store.scan()
    assert (s["state"], s["done"], s["total"], s["unmapped"]) == ("running", 3, 10, ["x"])
    with pytest.raises(ValueError):
        store.set_scan(bogus=1)


def test_profile_and_links(store):
    assert store.profile() is None
    store.save_profile({"genres": {"rock": 1.0}})
    store.save_profile({"genres": {"pop": 2.0}})
    assert store.profile() == {"genres": {"pop": 2.0}}
    assert store.link(5) is None
    store.set_link(5, "mbid-1")
    assert store.link(5) == "mbid-1"
    store.set_link(5, None)
    assert store.link(5) is None


def test_checked_ids_includes_misses(store):
    store.set_link(5, None)
    store.set_link(6, "mbid")
    assert store.checked_ids() == {5, 6}
    assert 7 not in store.checked_ids()


def test_unknown_ids_return_false(store):
    assert store.set_reaction("nope", "passed") is False
    assert store.set_wantlist("nope", True, True) is False
    assert store.mark_read("nope") is False
    d = store.add_digest({"kind": "weekly", "week_of": "2026-11-09", "note": "", "session_id": "s"},
                         [{"master_id": 1}])
    cid = store.digest(d)["cards"][0]["id"]
    assert store.set_reaction(cid, "passed") is True
    assert store.set_wantlist(cid, True, True) is True
    assert store.mark_read(d) is True and store.mark_read(d) is True


def test_master_id_zero_is_none(store):
    store.replace_releases([rel(1, 0), rel(2, 10)])
    assert store.releases()[0]["master_id"] is None
    store.mark_owned(0, "A", "T", [])
    store.add_digest({"kind": "weekly", "week_of": "2026-11-09", "note": "", "session_id": "s"},
                     [{"master_id": 0}])
    assert store.owned_master_ids() == {10}
    assert store.suggested_master_ids() == set()
    assert store.owned_marks() == []

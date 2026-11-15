import httpx
import pytest

from src.music.collection import build_profile, link_artists, map_payload, related_genres, scan
from src.music.discogs import Discogs
from src.music.store import MusicStore
from src.music.styles import StyleMap


def r(styles=("Blues Rock",), rating=0, formats=("Vinyl",), year=1970, **kw):
    mapped = [s.lower() for s in styles]
    return {"release_id": 1, "styles": list(styles), "genres": [], "rating": rating,
            "formats": list(formats), "year": year, "mapped": mapped, **kw}


def test_profile_weights_and_shares(atlas):
    rel = [r(styles=["Blues Rock"], rating=4), r(styles=["Blues Rock", "Hard Rock"], rating=0)]
    p = build_profile(rel, atlas)
    shares = {g["genre"]: g["share"] for g in p["genres"]}
    assert shares["blues rock"] == pytest.approx((2.0 + 0.5) / 3.0)
    assert shares["hard rock"] == pytest.approx(0.5 / 3.0)
    assert {g["genre"]: g["owned"] for g in p["genres"]}["blues rock"] == 2


def test_formats_count_per_format_and_unknown_decade(atlas):
    p = build_profile([r(formats=["Vinyl", "CD"], year=0), r(formats=["Vinyl", "Vinyl"], year=1974)], atlas)
    assert p["formats"] == {"Vinyl": 2, "CD": 1} and p["decades"] == {"1970": 1, "unknown": 1}


def test_empty_collection(atlas):
    p = build_profile([], atlas)
    assert p["genres"] == [] and related_genres(p, atlas, []) == []
    assert map_payload(p, [], atlas)["genres"] == []


def test_map_payload_carries_releases_formats_decades(atlas):
    p = build_profile([r(formats=["Vinyl", "CD"], year=0), r(formats=["Vinyl"], year=1974)], atlas)
    m = map_payload(p, [], atlas)
    assert m["releases"] == 2 and m["formats"] == {"Vinyl": 2, "CD": 1} and m["decades"] == {"1970": 1, "unknown": 1}
    empty = map_payload({"genres": []}, [], atlas)
    assert empty["releases"] == 0 and empty["formats"] == {} and empty["decades"] == {}


def test_all_unmapped(atlas):
    p = build_profile([r(styles=["Zzz"], mapped=[])], atlas)
    assert p["genres"] == [] and p["mapped"] == 0 and p["unmapped"] == [{"style": "Zzz", "count": 1}]


def test_owned_marks_count(atlas):
    p = build_profile([], atlas, [{"genres": ["blues rock"]}])
    assert p["releases"] == 1 and p["genres"][0]["share"] == pytest.approx(1.0)


def test_related_skips_broad_and_owned(atlas):
    p = build_profile([r(styles=["Blues Rock"])], atlas)
    rel = related_genres(p, atlas, [])
    assert rel[0]["genre"] == "british blues" and rel[0]["because"] == "blues rock"
    names = [x["genre"] for x in rel]
    assert "rock" not in names and "blues rock" not in names
    assert rel[0]["score"] == pytest.approx(1.0 * rel[0]["temperature"] / 100)
    assert related_genres(p, atlas, [], limit=1) == rel[:1]


def entry(i, styles=("Blues Rock",), genres=("Rock",)):
    return {"id": i, "rating": 4, "date_added": "2020-01-01", "basic_information": {
        "id": i, "master_id": 0, "title": f"T{i}", "year": 1971, "styles": list(styles), "genres": list(genres),
        "artists": [{"id": 7, "name": "A (2)"}], "formats": [{"name": "Vinyl"}]}}


def discogs(handler):
    return Discogs("u", "tok", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                   sleep=lambda s: _nosleep())


async def _nosleep():
    return None


async def test_scan_stores_releases_and_profile(tmp_path, atlas):
    def handler(req):
        page = int(req.url.params["page"])
        return httpx.Response(200, json={"pagination": {"pages": 2},
                                         "releases": [entry(page), entry(page + 10, ["Nope"], [])]})
    store = MusicStore(tmp_path / "m.db")
    out = await scan(discogs(handler), store, StyleMap.load(atlas), atlas)
    assert out["state"] == "done" and out["stage"] == "done" and out["mapped"] == 2 and out["unmapped"] == [{"style": "Nope", "count": 2}]
    rows = store.releases()
    assert len(rows) == 4 and rows[0]["master_id"] is None and rows[0]["artists"][0]["id"] == 7
    assert store.profile()["releases"] == 4


async def test_scan_failure_keeps_previous_profile(tmp_path, atlas):
    store = MusicStore(tmp_path / "m.db")
    ok = lambda req: httpx.Response(200, json={"pagination": {"pages": 1}, "releases": [entry(1)]})
    await scan(discogs(ok), store, StyleMap.load(atlas), atlas)
    previous, before = store.profile(), store.releases()

    def handler(req):
        if req.url.params["page"] == "1":
            return httpx.Response(200, json={"pagination": {"pages": 2}, "releases": [entry(5), entry(6)]})
        return httpx.Response(429)
    out = await scan(discogs(handler), store, StyleMap.load(atlas), atlas)
    assert out["state"] == "failed" and store.scan()["error"] == "rate_limited"
    assert store.profile() == previous and store.releases() == before


async def test_link_artists_skips_cached(tmp_path):
    store = MusicStore(tmp_path / "m.db")
    store.set_link(8, None)
    calls = []

    class MB:
        async def artist_by_discogs(self, i):
            calls.append(i)
            return "mbid-7" if i == 7 else None
    rels = [{"artists": [{"id": 7}, {"id": 8}, {"id": 9}]}, {"artists": [{"id": 7}]}]
    assert await link_artists(store, MB(), rels) == 1
    assert calls == [7, 9] and store.link(7) == "mbid-7" and store.checked_ids() == {7, 8, 9}


def test_related_because_is_always_owned(atlas):
    p = build_profile([r(styles=["Blues Rock"]), r(styles=["Hard Rock"])], atlas)
    owned = {g["genre"] for g in p["genres"]}
    rel = related_genres(p, atlas, ["blues rock", "heavy metal", "southern rock"])
    assert rel and all(x["because"] in owned for x in rel)
    assert len({x["genre"] for x in rel}) == len(rel)


def test_related_genres_put_focus_first(atlas):
    """A small focus genre's neighbour comes before a bigger non-focus genre's: the score sorts within each group."""
    p = build_profile([r(styles=["Blues Rock"])] + [r(styles=["Hard Rock"])] * 3, atlas)
    rel = related_genres(p, atlas, ["blues rock"])
    assert len({x["because"] for x in rel}) == 2
    assert rel[0]["because"] == "blues rock"
    rest = [x for x in rel if x["because"] != "blues rock"]
    assert rel[1:] == rest and [x["score"] for x in rest] == sorted((x["score"] for x in rest), reverse=True)


def test_unowned_focus_narrower_gives_no_suggestion(atlas):
    p = build_profile([r(styles=["Blues Rock"])], atlas)
    rel = related_genres(p, atlas, ["heavy metal"])
    assert all(x["because"] == "blues rock" for x in rel)


async def test_scan_internal_error_keeps_previous(tmp_path, atlas):
    store = MusicStore(tmp_path / "m.db")
    ok = lambda req: httpx.Response(200, json={"pagination": {"pages": 1}, "releases": [entry(1)]})
    await scan(discogs(ok), store, StyleMap.load(atlas), atlas)
    previous, before = store.profile(), store.releases()
    bad = entry(2)
    bad["basic_information"]["formats"] = ["Vinyl"]
    out = await scan(discogs(lambda req: httpx.Response(200, json={"pagination": {"pages": 1}, "releases": [bad]})),
                     store, StyleMap.load(atlas), atlas)
    assert out["state"] == "failed" and out["error"] == "internal"
    assert store.profile() == previous and store.releases() == before

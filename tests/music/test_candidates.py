import itertools

import pytest

from src.music.candidates import pipeline_candidates
from src.music.check import Verdict
from src.music.discogs import DiscogsError
from src.music.settings import MusicSettings


class FakeAtlas:
    def __init__(self, artists, narrow=None, neigh=None):
        self.artists, self.narrow, self.neigh = artists, narrow or {}, neigh or {}

    def artists_in(self, name, min_share=0.3):
        return [{"name": n, "mbid": n, "share": 1, "votes": 1, "sitelinks": 1} for n in self.artists.get(name, [])]

    def narrower(self, name):
        return self.narrow.get(name, [])

    def neighbours(self, name):
        return self.neigh.get(name, [])

    def detail(self, name):
        return {}


class FakeStore:
    def __init__(self, owned):
        # the scan's shape (collection.py): each artist an {"id", "name"} object, not a bare string
        self.rows = [{"artists": [{"id": i, "name": a}]} for i, a in enumerate(owned, 1)]

    def releases(self):
        return self.rows


class FakeDiscogs:
    def __init__(self, error=None):
        self.error, self.ids, self.newest_calls = error, {}, []
        self._counter = itertools.count(1)

    async def search_artists(self, name):
        if self.error:
            raise self.error
        return [{"id": self.ids.setdefault(name, next(self._counter)), "title": name}]

    async def artist_masters(self, aid, newest=False):
        self.newest_calls.append(newest)
        return [{"id": aid * 10, "title": "Old", "year": 1975, "role": "Main", "have": 10},
                {"id": aid * 10 + 1, "title": "New", "year": 2024, "role": "Main", "have": 5}]


class FakeYtm:
    def __init__(self, related=None):
        self.related, self.seeds = related or {}, []

    def related_artists(self, artist):
        self.seeds.append(artist)
        return self.related.get(artist, [])


class FakeGate:
    now_year = 2026

    def __init__(self, fail=(), genres=("blues",), error=None):
        self.fail, self.genres, self.calls, self.error = set(fail), list(genres), [], error

    async def check_master(self, mid):
        self.calls.append(mid)
        if self.error:  # as the real gate does: a stop kind raises, it is never a reason
            raise DiscogsError(self.error)
        return Verdict(mid not in self.fail, [] if mid not in self.fail else ["owned"],
                       {"master_id": mid, "genres": self.genres})


def profile(*genres):
    return {"genres": [{"genre": g, "share": 0.5, "owned": 1} for g in genres]}


async def run(n=4, atlas=None, owned=("Muddy Waters",), gate=None, ytm=None, focus=("blues",), prof=None,
              discogs=None, **kw):
    atlas = atlas or FakeAtlas({"blues": ["Howlin Wolf", "Elmore James", "Muddy Waters"]})
    gate = gate or FakeGate()
    out = await pipeline_candidates(n, prof if prof is not None else profile("blues"),
                                    MusicSettings(focus=list(focus), **kw), atlas, discogs or FakeDiscogs(), ytm or FakeYtm(),
                                    gate, FakeStore(owned))
    return out, gate


async def test_returns_only_passing():
    probe, _ = await run()
    failing = {probe[0]["verdict"]["master_id"]}
    out, gate = await run(gate=FakeGate(fail=failing))
    assert len(out) == 1 and out[0]["verdict"]["master_id"] not in failing
    assert len(gate.calls) == 2


async def test_one_album_per_artist():
    atlas = FakeAtlas({"blues": ["Howlin Wolf", "Howlin Wolf", "howlin wolf"]})
    out, gate = await run(atlas=atlas)
    assert len(out) == 1 and len(gate.calls) == 1


async def test_skips_owned_artists():
    out, gate = await run(owned=("Howlin Wolf", "Elmore James", "Muddy Waters"))
    assert out == [] and gate.calls == []


async def test_skips_band_name_variants():
    atlas = FakeAtlas({"blues": ["The Allman Brothers Band", "Kyle Gass"]})
    out, gate = await run(atlas=atlas, owned=("Allman Brothers", "Tenacious D"))
    assert len(gate.calls) == 1 and len(out) == 1


async def test_check_budget_40():
    subs = [f"sub{i}" for i in range(6)]
    atlas = FakeAtlas({g: [f"{g} artist {i}" for i in range(10)] for g in ["blues", *subs]},
                      narrow={"blues": subs})
    gate = FakeGate()
    gate.fail = _All()
    out, gate = await run(atlas=atlas, gate=gate, n=5)
    assert out == [] and len(gate.calls) == 40


class _All:
    def __contains__(self, x):
        return True


async def test_empty_profile_returns_empty():
    out, gate = await run(prof={"genres": []})
    assert out == [] and gate.calls == []


async def test_focus_genre_assignment():
    atlas = FakeAtlas({"blues": ["A One"], "jazz": []}, narrow={"jazz": ["Cool Jazz"]},
                      neigh={"blues": [("Soul", 90.0, [])], "jazz": [("Soul", 50.0, [])]})
    out, _ = await run(atlas=atlas, focus=("blues", "jazz"), gate=FakeGate(genres=["Cool Jazz"]),
                       prof=profile("blues", "jazz"))
    assert out[0]["focus_genre"] == "jazz"
    out, _ = await run(atlas=atlas, focus=("blues", "jazz"), gate=FakeGate(genres=["Soul"]),
                       prof=profile("blues", "jazz"))
    assert out[0]["focus_genre"] == "blues"


async def test_ytm_related_of_top_owned_tagged():
    ytm = FakeYtm({"Muddy Waters": ["Little Walter"]})
    out, _ = await run(atlas=FakeAtlas({}), ytm=ytm)
    assert ytm.seeds == ["Muddy Waters"]
    assert out[0]["sources"] == ["youtube_music"]


async def test_focus_genre_prefers_narrower_match_over_fallback():
    atlas = FakeAtlas({"blues": ["A One"]}, narrow={"jazz": ["cool jazz"]},
                      neigh={"blues": [("cool jazz", 99.0, [])]})
    out, _ = await run(atlas=atlas, focus=("blues", "jazz"), gate=FakeGate(genres=["cool jazz"]),
                       prof=profile("blues", "jazz"))
    assert out[0]["focus_genre"] == "jazz"


@pytest.mark.parametrize("kind", ["token_invalid", "rate_limited"])
async def test_discogs_stop_errors_propagate(kind):
    with pytest.raises(DiscogsError) as e:
        await run(discogs=FakeDiscogs(error=DiscogsError(kind)))
    assert e.value.kind == kind


async def test_other_discogs_errors_skip_the_artist():
    out, gate = await run(discogs=FakeDiscogs(error=DiscogsError("not_found")))
    assert out == [] and gate.calls == []


@pytest.mark.parametrize("kind", ["token_invalid", "rate_limited"])
async def test_gate_lookup_failure_stops_the_run(kind):
    with pytest.raises(DiscogsError) as e:
        await run(gate=FakeGate(error=kind))
    assert e.value.kind == kind


async def test_era_mix_picks_classic_or_newest():
    d = FakeDiscogs()
    out, _ = await run(n=1, era_mix=0.0, discogs=d)
    assert out[0]["verdict"]["master_id"] % 10 == 0 and d.newest_calls == [False]
    d = FakeDiscogs()
    out, _ = await run(n=1, era_mix=1.0, discogs=d)
    assert out[0]["verdict"]["master_id"] % 10 == 1 and d.newest_calls == [True]


async def test_ytm_seeds_use_the_display_name():
    ytm = FakeYtm({"Equip": ["Little Walter"]})
    await run(atlas=FakeAtlas({}), ytm=ytm, owned=("Equip (3)",))
    assert ytm.seeds == ["Equip"]

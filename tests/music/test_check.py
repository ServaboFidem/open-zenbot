import pytest

from src.music.check import Gate, obscurity_label, rejected_labels
from src.music.discogs import DiscogsError
from src.music.settings import MusicSettings
from src.music.store import MusicStore
from src.music.styles import StyleMap


class FakeDiscogs:
    def __init__(self, hits=None, masters=None, releases=None, wants=(), error=None):
        self.hits, self.masters, self.releases = hits or [], masters or {}, releases or {}
        self.wants, self.error, self.calls = set(wants), error, []

    async def search_masters(self, artist, title):
        self.calls.append("search")
        if self.error:
            raise self.error
        return self.hits

    async def master(self, id):
        self.calls.append("master")
        return self.masters[id]

    async def release(self, id):
        self.calls.append("release")
        return self.releases[id]

    async def wantlist_ids(self):
        self.calls.append("wants")
        return self.wants


def hit(title="Some Band - Some Album", mid=100):
    return {"id": mid, "master_id": mid, "title": title}


def world(mid=100, year=1990, have=500, want=40, artist="Some Band", title="Some Album", **kw):
    return FakeDiscogs(
        hits=kw.pop("hits", [hit(f"{artist} - {title}", mid)]),
        masters={mid: {"id": mid, "title": title, "year": year, "genres": ["Rock"],
                       "styles": ["Shoegaze"], "artists": [{"name": artist}],
                       "main_release": mid + 1, "uri": f"https://www.discogs.com/master/{mid}-x"}},
        releases={mid + 1: {"community": {"have": have, "want": want},
                            "images": [{"type": "secondary", "uri": "b"}, {"type": "primary", "uri": "a"}],
                            "tracklist": [{"type_": "heading", "title": "H"},
                                          {"type_": "track", "title": "One"}]}},
        **kw)


def gate(tmp_path, atlas, discogs, obscurity=0.7, now_year=2026):
    return Gate(discogs, MusicStore(tmp_path / "m.db"), StyleMap.load(atlas),
                MusicSettings(obscurity=obscurity), now_year)


async def test_passes_unowned_hidden_gem(tmp_path, atlas):
    v = await gate(tmp_path, atlas, world()).check("Some Band", "Some Album")
    assert v.ok and v.reasons == []
    f = v.facts
    assert f["master_id"] == 100 and f["release_id"] == 101 and f["artist"] == "Some Band"
    assert f["have"] == 500 and f["obscurity_label"] == "hidden gem" and f["tracks"] == ["One"]
    assert f["cover_uri"] == "a" and f["discogs_url"].endswith("100-x") and f["era"] == "forgotten classic"


async def test_owned_by_master(tmp_path, atlas):
    g = gate(tmp_path, atlas, world())
    g.store.mark_owned(100, "Some Band", "Other", [])
    v = await g.check("Some Band", "Some Album")
    assert v.reasons == ["owned"] and v.facts["album"] == "Some Album"


async def test_owned_by_name_when_no_master(tmp_path, atlas):
    g = gate(tmp_path, atlas, world(artist="The Beatles", title="Help!", hits=[hit("The Beatles - Help!")]))
    g.store.replace_releases([{"release_id": 9, "master_id": None, "title": "Help!", "year": 1965,
                               "artists": ["Beatles, The"], "formats": [], "styles": [], "genres": [],
                               "rating": 0, "date_added": "", "mapped": []}])
    v = await g.check("The Beatles", "Help!")
    assert "owned" in v.reasons


async def test_owned_by_name_reads_the_scans_artist_objects(tmp_path, atlas):
    # collection.py stores each artist as {"id", "name"}; a bare-string reader crashed with a TypeError here
    g = gate(tmp_path, atlas, world(artist="The Beatles", title="Help!", hits=[hit("The Beatles - Help!")]))
    g.store.replace_releases([{"release_id": 9, "master_id": None, "title": "Help!", "year": 1965,
                               "artists": [{"id": 82730, "name": "The Beatles"}], "formats": [], "styles": [],
                               "genres": [], "rating": 0, "date_added": "", "mapped": []}])
    assert "owned" in (await g.check("The Beatles", "Help!")).reasons


async def test_repeat(tmp_path, atlas):
    g = gate(tmp_path, atlas, world())
    g.store.suggested_master_ids = lambda: {100}
    assert (await g.check("Some Band", "Some Album")).reasons == ["repeat"]


async def test_on_wantlist(tmp_path, atlas):
    assert (await gate(tmp_path, atlas, world(wants={101})).check("Some Band", "Some Album")).reasons == ["on_wantlist"]
    assert (await gate(tmp_path, atlas, world(wants={100})).check("Some Band", "Some Album")).reasons == ["on_wantlist"]


async def test_not_found(tmp_path, atlas):
    v = await gate(tmp_path, atlas, FakeDiscogs()).check("Nobody", "Nothing")
    assert not v.ok and v.reasons == ["not_found"]


async def test_artist_mismatch(tmp_path, atlas):
    v = await gate(tmp_path, atlas, world()).check("Other Band", "Some Album")
    assert v.reasons == ["artist_mismatch"]


async def test_deluxe_suffix_matches(tmp_path, atlas):
    d = world(hits=[hit("Some Band - Some Album (Deluxe Edition)")])
    assert (await gate(tmp_path, atlas, d).check("Some Band", "Some Album")).ok


async def test_card_artist_is_the_display_name(tmp_path, atlas):
    d = world(artist="Equip (3)", hits=[hit("Equip - Some Album")])
    v = await gate(tmp_path, atlas, d).check("Equip", "Some Album")
    assert v.ok and v.facts["artist"] == "Equip"
    d = world(artist="Beatles, The", hits=[hit("Beatles, The - Some Album")])
    assert (await gate(tmp_path, atlas, d).check("The Beatles", "Some Album")).facts["artist"] == "The Beatles"


async def test_an_exact_title_wins_over_an_earlier_prefix_match(tmp_path, atlas):
    d = world(mid=200, artist="Joni Mitchell", title="Blue",
              hits=[hit("Joni Mitchell - Blue Highways", 300), hit("Joni Mitchell - Blue", 200)])
    v = await gate(tmp_path, atlas, d).check("Joni Mitchell", "Blue")
    assert v.ok and v.facts["master_id"] == 200
    # with no exact title, a prefix match is still taken
    d = world(mid=300, artist="Joni Mitchell", title="Blue Highways", hits=[hit("Joni Mitchell - Blue Highways", 300)])
    assert (await gate(tmp_path, atlas, d).check("Joni Mitchell", "Blue")).facts["master_id"] == 300


async def test_discogs_name_variants_match(tmp_path, atlas):
    d = world(artist="Equip (3)", title="Rock & Roll", hits=[hit("Equip (3) - Rock & Roll")])
    assert (await gate(tmp_path, atlas, d).check("Equip", "Rock and Roll")).ok


async def test_too_well_known_respects_setting(tmp_path, atlas):
    assert (await gate(tmp_path, atlas, world(have=50000), obscurity=0.0).check("Some Band", "Some Album")).ok
    v = await gate(tmp_path, atlas, world(have=50000), obscurity=0.7).check("Some Band", "Some Album")
    assert v.reasons == ["too_well_known"] and v.facts["obscurity_label"] == "well known"


async def test_widened_gate_lets_well_known_through(tmp_path, atlas):
    g = gate(tmp_path, atlas, world(have=50000), obscurity=0.7)
    assert (await g.check("Some Band", "Some Album")).reasons == ["too_well_known"]
    g = gate(tmp_path, atlas, world(have=50000), obscurity=0.7)
    g.widened = True
    v = await g.check("Some Band", "Some Album")
    assert v.ok and v.facts["obscurity_label"] == "well known"


def test_obscurity_label_bands():
    assert [obscurity_label(h) for h in (2499, 2500, 11999, 12000)] == [
        "hidden gem", "under the radar", "under the radar", "well known"]


def test_the_obscurity_setting_chooses_the_rejected_labels():
    assert rejected_labels(0.0) == rejected_labels(0.29) == set()
    assert rejected_labels(0.3) == rejected_labels(0.84) == {"well known"}
    assert rejected_labels(0.85) == rejected_labels(1.0) == {"well known", "under the radar"}
    # widened: one band looser
    assert rejected_labels(0.1, widened=True) == set()
    assert rejected_labels(0.5, widened=True) == set()
    assert rejected_labels(0.9, widened=True) == {"well known"}


async def test_high_obscurity_rejects_under_the_radar(tmp_path, atlas):
    v = await gate(tmp_path, atlas, world(have=5000), obscurity=0.9).check("Some Band", "Some Album")
    assert v.reasons == ["too_well_known"] and v.facts["obscurity_label"] == "under the radar"
    assert (await gate(tmp_path, atlas, world(have=5000), obscurity=0.7).check("Some Band", "Some Album")).ok
    assert (await gate(tmp_path, atlas, world(have=1000), obscurity=0.9).check("Some Band", "Some Album")).ok
    g = gate(tmp_path, atlas, world(have=5000), obscurity=0.9)
    g.widened = True
    assert (await g.check("Some Band", "Some Album")).ok


async def test_era_boundary(tmp_path, atlas):
    old = await gate(tmp_path, atlas, world(year=2011)).check("Some Band", "Some Album")
    new = await gate(tmp_path, atlas, world(year=2012)).check("Some Band", "Some Album")
    assert old.facts["era"] == "forgotten classic" and new.facts["era"] == "newer discovery"


async def test_lookup_failure_is_a_reason_not_an_exception(tmp_path, atlas):
    v = await gate(tmp_path, atlas, FakeDiscogs(error=DiscogsError("network"))).check("A", "B")
    assert v.reasons == ["lookup_failed"] and v.facts["error"] == "network" and not v.ok


async def test_lookup_failure_after_master_keeps_partial_facts(tmp_path, atlas):
    d = world()

    async def boom():
        raise DiscogsError("network")
    d.wantlist_ids = boom
    v = await gate(tmp_path, atlas, d).check("Some Band", "Some Album")
    assert v.reasons == ["lookup_failed"] and v.facts["album"] == "Some Album"
    assert v.facts["error"] == "network"


async def test_cache_avoids_second_request(tmp_path, atlas):
    d = world()
    g = gate(tmp_path, atlas, d)
    await g.check("Some Band", "Some Album")
    n = len(d.calls)
    await g.check("Some Band", "Some Album")
    await g.check_master(100)
    assert len(d.calls) == n and d.calls.count("wants") == 1


@pytest.mark.parametrize("kind", ["token_invalid", "rate_limited"])
async def test_stop_kinds_raise_from_search_and_from_master(tmp_path, atlas, kind):
    with pytest.raises(DiscogsError) as e:
        await gate(tmp_path, atlas, FakeDiscogs(error=DiscogsError(kind))).check("A", "B")
    assert e.value.kind == kind
    d = world()

    async def boom(_id):
        raise DiscogsError(kind)
    d.master = boom
    with pytest.raises(DiscogsError):
        await gate(tmp_path, atlas, d).check_master(100)

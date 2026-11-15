import os
from pathlib import Path

import pytest

from src.music.atlas import Atlas
from src.music.styles import StyleMap, read_table
from src.music.textnorm import artist_key, display_name, norm


@pytest.mark.parametrize("raw,key", [
    ("Equip (3)", "equip"), ("Beatles, The", "the beatles"),
    ("Simon & Garfunkel", "simon and garfunkel"), ("Björk", "bjork"),
    ("TOM★CAT", "tom cat"), ("フィンガー5", "フィンガー5"),
])
def test_artist_key(raw, key):
    assert artist_key(raw) == key


@pytest.mark.parametrize("raw,shown", [
    ("Equip (3)", "Equip"), ("Beatles, The", "The Beatles"), ("Beatles, The (2)", "The Beatles"),
    ("Simon & Garfunkel", "Simon & Garfunkel"), ("Sly & The Family Stone", "Sly & The Family Stone"),
])
def test_display_name(raw, shown):
    assert display_name(raw) == shown


def test_norm_basics():
    assert norm("Drum 'n' Bass!") == "drum n bass"
    assert norm("  Rock & Roll ") == "rock and roll"


def test_table_has_no_duplicate_styles():
    names = [norm(s) for s, _ in read_table()]
    assert len(names) == len(set(names))


def test_table_genres_are_all_profiled(atlas):
    m = StyleMap.load(atlas)
    mapped = {norm(s): g for s, g in read_table() if g and atlas.is_profiled(g)}
    assert mapped, "fixture atlas should keep some rows"
    assert m._table == mapped


def test_style_maps_and_falls_back_to_broad_genre(atlas):
    m = StyleMap.load(atlas)
    assert m.genres_for(["Blues Rock"], ["Rock"]) == ["blues rock"]
    assert m.genres_for(["Comedy"], ["Rock"]) == ["rock"]      # dropped style -> broad genre
    assert m.genres_for([], ["Non-Music"]) == []


def test_dedupes_in_order_and_matches_names_exactly(atlas):
    m = StyleMap.load(atlas)
    assert m.genres_for(["Hard Rock", "Blues Rock", "Hard Rock"], []) == ["hard rock", "blues rock"]
    assert m.genres_for(["Kayōkyoku"], []) == ["kayōkyoku"]


LIVE = os.environ.get("ZEN_MUSIC_ATLAS_DIR")


@pytest.mark.skipif(not LIVE, reason="set ZEN_MUSIC_ATLAS_DIR to a full Soundlore atlas directory")
def test_live_every_table_genre_is_profiled_in_full_atlas():
    full = Atlas.load(Path(LIVE))
    bad = [(s, g) for s, g in read_table() if g and not full.is_profiled(g)]
    assert bad == []
    m = StyleMap.load(full)
    top = ["Pop Rock", "Kayōkyoku", "City Pop", "Blues Rock", "Synth-pop", "Soul", "Funk",
           "Fusion", "Country", "Hard Rock", "Rock & Roll", "Vocal", "Folk Rock", "Soft Rock",
           "Prog Rock", "Disco", "Jazz-Funk", "Country Rock"]
    assert [s for s in top if not m.genres_for([s], [])] == []
    assert m.genres_for(["Rhythm & Blues"], ["Funk / Soul"]) == ["r&b"]
    assert m.genres_for(["Contemporary R&B"], []) == ["contemporary r&b"]
    assert m.genres_for(["Soundtrack", "Ballad"], ["Stage & Screen"]) == []

import json
from pathlib import Path

import httpx
import pytest

from src.music.atlas import BASE_URL, FILES, Atlas, AtlasError, ensure_atlas

FIX = Path(__file__).parent.parent / "fixtures" / "music" / "atlas"


def fixture_sums() -> dict[str, str]:
    return {l.split()[1]: l.split()[0] for l in (FIX / "SHA256SUMS").read_text().splitlines() if l}


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def serving(calls: list, tamper: bool = False):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        name = str(req.url).removeprefix(BASE_URL)
        body = (FIX / name).read_bytes()
        return httpx.Response(200, content=body + b" " if tamper else body)
    return handler


def test_pinned_urls_and_hashes():
    assert "6ed7616bab051083213328712d582911c8497dff" in BASE_URL
    assert set(FILES) == set(fixture_sums())


async def test_ensure_downloads_and_verifies(tmp_path):
    calls: list = []
    async with client(serving(calls)) as c:
        await ensure_atlas(tmp_path, c, fixture_sums())
        assert len(calls) == 6 and calls[0].startswith(BASE_URL)
        await ensure_atlas(tmp_path, c, fixture_sums())
    assert len(calls) == 6
    assert not list(tmp_path.glob("*.tmp"))
    assert Atlas.load(tmp_path).genre("british blues")


async def test_ensure_redownloads_a_corrupt_file(tmp_path):
    calls: list = []
    (tmp_path / "genre_map.json").write_text("junk")
    async with client(serving(calls)) as c:
        await ensure_atlas(tmp_path, c, {"genre_map.json": fixture_sums()["genre_map.json"]})
    assert len(calls) == 1
    assert (tmp_path / "genre_map.json").read_bytes() == (FIX / "genre_map.json").read_bytes()


async def test_ensure_rejects_bad_hash(tmp_path):
    async with client(serving([], tamper=True)) as c:
        with pytest.raises(AtlasError):
            await ensure_atlas(tmp_path, c, fixture_sums())
    assert list(tmp_path.iterdir()) == []


async def test_ensure_reports_http_failure(tmp_path):
    async with client(lambda r: httpx.Response(404)) as c:
        with pytest.raises(AtlasError):
            await ensure_atlas(tmp_path, c, fixture_sums())
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def atlas():
    return Atlas.load(FIX)


def test_neighbours_and_detail(atlas):
    first = json.loads((FIX / "temperature_viewer.json").read_text(encoding="utf-8"))["british blues"][0]
    name, temp, shared = atlas.neighbours("british blues")[0]
    assert (name, temp) == (first[0], first[1]) and shared == first[4]
    d = atlas.detail("british blues")
    assert d["made_of"]["characteristic"][0]["label"] == "Blues scale"
    assert d["made_of"]["characteristic"][0]["facet"] == "Scales and modes"
    assert d["broader"] == ["blues"]
    assert d["closest_genres"][0]["genre"] == name
    assert d["family"] == atlas.family("british blues")
    assert "_note" not in d and atlas.detail("no such genre") == {}


def test_narrower_is_transitive(atlas):
    assert "chicago blues" in atlas.narrower("blues")
    assert atlas.narrower("nope") == []


def test_lookups(atlas):
    assert "kayōkyoku" in atlas.names()
    assert atlas.is_profiled("british blues") and not atlas.is_profiled("nope")
    assert atlas.genre("nope") is None
    pts = atlas.points()
    assert pts["families"] and len(pts["points"]) <= 40
    assert atlas.family("nope") is None
    assert atlas.licenses() and atlas.artist_neighbors()["neighbors"]


def test_artists_in_filters_by_share(atlas):
    loose = atlas.artists_in("british blues", min_share=0.0)
    strict = atlas.artists_in("british blues", min_share=0.6)
    assert loose and len(strict) < len(loose)
    assert all(a["share"] >= 0.6 for a in strict)
    assert set(loose[0]) == {"name", "mbid", "share", "votes", "sitelinks"}
    keys = [(-a["sitelinks"], -a["votes"]) for a in loose]
    assert keys == sorted(keys)

import json
from pathlib import Path

import httpx
import pytest

from src.music.discogs import Discogs, DiscogsError, USER_AGENT
from src.music.musicbrainz import MusicBrainz
from src.music.ytmusic import YTMusicLookup, parse_plays

FIX = Path(__file__).parent.parent / "fixtures" / "music"


def fx(path: str) -> dict:
    return json.loads((FIX / path).read_text(encoding="utf-8"))


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class Sleeps:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)


def collection_handler(req: httpx.Request) -> httpx.Response:
    page = int(req.url.params["page"])
    return httpx.Response(200, json=fx(f"discogs/collection_page{page}.json"))


async def test_collection_pages_reads_all_pages():
    seen = []
    async with client(collection_handler) as c:
        d = Discogs("collector", client=c, sleep=Sleeps())
        async for page, pages, releases in d.collection_pages():
            seen.append((page, pages, len(releases)))
    assert seen == [(1, 3, 3), (2, 3, 3), (3, 3, 2)]


@pytest.mark.parametrize("status,kind", [(404, "not_found"), (403, "private"), (401, "token_invalid")])
async def test_collection_errors_map_to_kinds(status, kind):
    async with client(lambda r: httpx.Response(status, json={"message": "x"})) as c:
        d = Discogs("collector", client=c, sleep=Sleeps())
        with pytest.raises(DiscogsError) as e:
            async for _ in d.collection_pages():
                pass
    assert e.value.kind == kind


async def test_connect_error_is_network():
    def boom(req):
        raise httpx.ConnectError("down")
    async with client(boom) as c:
        d = Discogs("collector", client=c, sleep=Sleeps())
        with pytest.raises(DiscogsError) as e:
            async for _ in d.collection_pages():
                pass
    assert e.value.kind == "network"


async def test_429_backs_off_then_raises():
    sleeps = Sleeps()
    n = []
    def h(req):
        n.append(1)
        return httpx.Response(429, json={})
    async with client(h) as c:
        d = Discogs("collector", client=c, sleep=sleeps)
        with pytest.raises(DiscogsError) as e:
            await d.master(1)
    assert e.value.kind == "rate_limited"
    assert sleeps.calls == [2, 4, 8]
    assert len(n) == 4


async def test_429_then_success_recovers():
    sleeps = Sleeps()
    codes = iter([429, 200])
    def h(req):
        return httpx.Response(next(codes), json=fx("discogs/master.json"))
    async with client(h) as c:
        d = Discogs("collector", client=c, sleep=sleeps)
        assert (await d.master(33228))["id"] == 33228
    assert sleeps.calls == [2]


async def test_respects_remaining_zero():
    sleeps = Sleeps()
    def h(req):
        return httpx.Response(200, json=fx("discogs/master.json"),
                              headers={"X-Discogs-Ratelimit-Remaining": "0"})
    async with client(h) as c:
        d = Discogs("collector", client=c, sleep=sleeps, clock=lambda: 100.0)
        await d.master(1)
        assert sleeps.calls == []
        await d.master(1)
    assert len(sleeps.calls) == 1 and 0 < sleeps.calls[0] <= 60


async def test_bucket_waits_when_window_full():
    sleeps = Sleeps()
    async with client(lambda r: httpx.Response(200, json={})) as c:
        d = Discogs("collector", client=c, sleep=sleeps, clock=lambda: 10.0)  # 25/min, no token
        for _ in range(26):
            await d.master(1)
    assert len(sleeps.calls) == 1 and sleeps.calls[0] == 60


async def test_token_header_and_user_agent_sent():
    seen = []
    def h(req):
        seen.append(req.headers)
        return httpx.Response(200, json=fx("discogs/identity.json"))
    async with client(h) as c:
        assert await Discogs("collector", token="abc", client=c, sleep=Sleeps()).identity() == "collector"
        await Discogs("collector", client=c, sleep=Sleeps()).master(1)
    assert seen[0]["authorization"] == "Discogs token=abc"
    assert seen[0]["user-agent"] == USER_AGENT
    assert "authorization" not in seen[1]
    assert USER_AGENT.startswith("zen-agents/") and "collector" not in USER_AGENT


async def test_search_and_lookups_need_token_and_parse():
    reqs = []
    def h(req):
        reqs.append(req)
        name = {"/masters/33228": "master", "/releases/249504": "release"}.get(req.url.path)
        if req.url.path == "/database/search":
            name = "search_" + req.url.params["type"] + "s"
        return httpx.Response(200, json=fx(f"discogs/{name}.json"))
    async with client(h) as c:
        d = Discogs("collector", token="t", client=c, sleep=Sleeps())
        assert (await d.search_masters("Philip Glass", "Powaqqatsi"))[0]["id"] == 33228
        assert (await d.search_artists("Pink Floyd"))[0]["id"] == 45467
        assert (await d.master(33228))["title"] == "Powaqqatsi"
        assert (await d.release(249504))["id"] == 249504
        tokenless = Discogs("collector", client=c, sleep=Sleeps())
        with pytest.raises(DiscogsError) as e:
            await tokenless.search_artists("x")
    assert e.value.kind == "token_invalid"
    assert reqs[0].url.params["artist"] == "Philip Glass" and reqs[0].url.params["release_title"] == "Powaqqatsi"
    assert reqs[1].url.params["q"] == "Pink Floyd"


async def test_wantlist_ids_holds_masters_and_releases_and_want_unwant():
    calls = []
    def h(req):
        calls.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(200, json=fx("discogs/wants.json"))
        return httpx.Response(201 if req.method == "PUT" else 204)
    async with client(h) as c:
        d = Discogs("collector", token="t", client=c, sleep=Sleeps())
        assert await d.wantlist_ids() == {249504, 33228, 555}
        await d.want(249504)
        await d.unwant(249504)
    assert calls[1:] == [("PUT", "/users/collector/wants/249504"), ("DELETE", "/users/collector/wants/249504")]


async def test_mb_artist_by_discogs():
    reqs = []
    def h(req):
        reqs.append(req)
        if "/artist/45467" in req.url.params["resource"]:
            return httpx.Response(200, json=fx("musicbrainz/url_artist.json"))
        return httpx.Response(404, json={"error": "Not Found"})
    async with client(h) as c:
        mb = MusicBrainz(client=c, sleep=Sleeps())
        assert await mb.artist_by_discogs(45467) == "83d91898-7763-47d7-b03b-b92132375c47"
        assert await mb.artist_by_discogs(1) is None
    assert reqs[0].url.params["fmt"] == "json" and reqs[0].url.params["inc"] == "artist-rels"
    assert reqs[0].headers["user-agent"] == USER_AGENT


async def test_mb_release_groups():
    async with client(lambda r: httpx.Response(200, json=fx("musicbrainz/release_groups.json"))) as c:
        rgs = await MusicBrainz(client=c, sleep=Sleeps()).release_groups("mbid")
    assert rgs[0] == {"title": "The Wall", "first_release_date": "1979-11-30", "primary_type": "Album"}
    assert len(rgs) == 2


async def test_mb_spaces_requests():
    sleeps = Sleeps()
    t = [0.0]
    async with client(lambda r: httpx.Response(200, json=fx("musicbrainz/url_artist.json"))) as c:
        mb = MusicBrainz(client=c, sleep=sleeps, clock=lambda: t[0])
        await mb.artist_by_discogs(45467)
        assert sleeps.calls == []
        await mb.artist_by_discogs(45467)
    assert len(sleeps.calls) == 1 and sleeps.calls[0] >= 1


async def test_mb_failure_is_soft():
    def boom(req):
        raise httpx.ConnectError("down")
    async with client(boom) as c:
        mb = MusicBrainz(client=c, sleep=Sleeps())
        assert await mb.artist_by_discogs(1) is None
        assert await mb.release_groups("x") == []


def test_ytm_soft_fails():
    def no_pkg():
        raise ImportError("broken")
    yt = YTMusicLookup(factory=no_pkg)
    assert yt.album("a", "b") is None and yt.related_artists("a") == []

    class Bad:
        def search(self, *a, **k):
            raise KeyError("x")
    yt = YTMusicLookup(factory=Bad)
    assert yt.album("a", "b") is None and yt.related_artists("a") == []


def test_ytm_album_and_related():
    class Fake:
        def search(self, q, filter=None, limit=20):
            if filter == "albums":
                return [{"browseId": "MPREb_1", "title": "Powaqqatsi", "artists": [{"name": "Philip Glass"}]}]
            return [{"browseId": "UC1"}]
        def get_album(self, bid):
            return {"tracks": [{"title": "A", "views": "2.4M"}, {"title": "B"}]}
        def get_artist(self, cid):
            return {"related": {"results": [{"title": "Steve Reich"}, {"title": "Arvo Pärt"}]}}
    yt = YTMusicLookup(factory=Fake)
    assert yt.album("Philip Glass", "Powaqqatsi") == {
        "browse_id": "MPREb_1", "url": "https://music.youtube.com/browse/MPREb_1",
        "tracks": [{"title": "A", "plays": 2400000}, {"title": "B", "plays": None}]}
    assert yt.related_artists("Philip Glass") == ["Steve Reich", "Arvo Pärt"]
    assert yt.search_url("Philip Glass", "Powaqqatsi") == "https://music.youtube.com/search?q=Philip%20Glass%20Powaqqatsi"


def test_ytm_album_skips_a_wrong_first_hit():
    class Fake:
        def __init__(self, hits):
            self.hits = hits

        def search(self, q, filter=None, limit=20):
            return self.hits

        def get_album(self, bid):
            return {"tracks": [{"title": bid}]}

    wrong_artist = {"browseId": "W1", "title": "Blue", "artists": [{"name": "Somebody Else"}]}
    wrong_title = {"browseId": "W2", "title": "Court and Spark", "artists": [{"name": "Joni Mitchell"}]}
    right = {"browseId": "R", "title": "Blue (Remastered)", "artists": [{"name": "Joni Mitchell"}]}
    yt = YTMusicLookup(factory=lambda: Fake([wrong_artist, wrong_title, right]))
    assert yt.album("Joni Mitchell", "Blue")["browse_id"] == "R"
    yt = YTMusicLookup(factory=lambda: Fake([wrong_artist, wrong_title]))
    assert yt.album("Joni Mitchell", "Blue") is None  # the caller falls back to the search URL
    yt = YTMusicLookup(factory=lambda: Fake([{"browseId": "N", "title": "Blue"}]))  # no artists on the hit
    assert yt.album("Joni Mitchell", "Blue") is None


def test_ytm_play_parsing():
    assert parse_plays("2.4M") == 2400000
    assert parse_plays("610K") == 610000
    assert parse_plays("1,234") == 1234
    assert parse_plays("1.2B") == 1200000000
    assert parse_plays("3,693,390,359 views") == 3693390359
    assert parse_plays(None) is None
    assert parse_plays("") is None
    assert parse_plays("n/a") is None


async def test_artist_masters_keeps_masters_with_have():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"], seen["params"] = req.url.path, dict(req.url.params)
        return httpx.Response(200, json={"releases": [
            {"id": 1, "type": "master", "title": "A", "year": 1970, "role": "Main",
             "stats": {"community": {"in_collection": 900}}},
            {"id": 2, "type": "release", "title": "B", "year": 1971, "role": "Main"},
            {"id": 3, "type": "master", "title": "C", "year": 0, "role": "Appearance"},
        ]})
    async with client(handler) as c:
        d = Discogs("collector", client=c, sleep=Sleeps())
        rows = await d.artist_masters(77)
    assert seen["path"] == "/artists/77/releases"
    assert seen["params"] == {"sort": "year", "sort_order": "asc", "per_page": "100"}
    assert rows == [{"id": 1, "title": "A", "year": 1970, "role": "Main", "have": 900},
                    {"id": 3, "title": "C", "year": 0, "role": "Appearance"}]


async def test_artist_masters_newest_sorts_descending():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.url.params)
        return httpx.Response(200, json={"releases": []})
    async with client(handler) as c:
        await Discogs("collector", client=c, sleep=Sleeps()).artist_masters(77, newest=True)
    assert seen["sort_order"] == "desc"

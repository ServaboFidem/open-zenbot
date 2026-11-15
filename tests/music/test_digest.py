import json
from pathlib import Path

import httpx
import pytest

from src.music.check import Verdict
from src.music.digest import DigestRefused, Digests
from src.music.discogs import DiscogsError
from src.music.store import MusicStore

SAMPLE = Path(__file__).parents[2] / "tests/fixtures/music/digest-sample.json"
SAMPLE_KEYS = set(json.loads(SAMPLE.read_text())["digest"]["cards"][0])
NEW_KEYS = {"master_id", "release_id", "checked_at", "kind", "genres"}


def facts(n, artist="Band", album=None):
    return {"master_id": n, "release_id": n + 1, "artist": artist, "album": album or f"Album {n}",
            "year": 1971, "styles": ["Blues Rock", "Hard Rock"], "genres": ["blues rock"],
            "tracks": ["One", "Two Sides", "Three"], "have": 100, "want": 5,
            "obscurity_label": "hidden gem", "era": "forgotten classic",
            "cover_uri": f"https://i.discogs.com/{n}.jpg", "discogs_url": f"https://www.discogs.com/master/{n}"}


class FakeGate:
    def __init__(self, bad=(), widened=False):
        self.bad = set(bad)
        self.n = 0
        self.widened = widened

    async def check(self, artist, album):
        if album in self.bad:
            return Verdict(False, ["owned"], {})
        if album == "Famous" and not self.widened:
            return Verdict(False, ["too_well_known"], facts(FAMOUS, artist, album))
        self.n += 1
        return Verdict(True, [], facts(self.n * 10, artist, album))


    async def check_master(self, master_id):
        return Verdict(True, [], facts(master_id, "Band", "Famous")) if self.widened else Verdict(False, ["too_well_known"], {})


FAMOUS = 999


class FakeYtm:
    def __init__(self, found=None):
        self.found = found

    def album(self, artist, title):
        return self.found

    def search_url(self, artist, title):
        return f"https://music.youtube.com/search?q={artist}+{title}"


class FakeDiscogs:
    def __init__(self, error=None):
        self.error, self.wanted, self.unwanted = error, [], []

    async def want(self, rid):
        if self.error:
            raise self.error
        self.wanted.append(rid)

    async def unwant(self, rid):
        if self.error:
            raise self.error
        self.unwanted.append(rid)


class FakeMemory:
    def __init__(self, fail=False):
        self.fail, self.lines = fail, []

    def remember(self, text, ns, tier, **kw):
        if self.fail:
            raise RuntimeError("boom")
        self.lines.append((text, ns, kw))


def make(tmp_path, gate=None, ytm=None, memory=None, discogs=None, cover_status=200):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(cover_status, content=b"JPEG")

    store = MusicStore(tmp_path / "m.db")
    d = Digests(store, lambda widened=False: gate or FakeGate(widened=widened), lambda: discogs or FakeDiscogs(),
                ytm or FakeYtm(), memory, tmp_path / "covers",
                httpx.AsyncClient(transport=httpx.MockTransport(handler)), lambda: "tok")
    d.requests = seen
    return d, store


def card(album="Album 1", tracks=("one", "Two Sides"), artist="Band"):
    return {"artist": artist, "album": album, "why": "w", "standout_tracks": list(tracks),
            "focus_genre": "blues rock", "sources": ["model"]}


async def test_save_refuses_whole_batch_when_one_fails(tmp_path):
    d, store = make(tmp_path, FakeGate(bad={"Bad"}))
    with pytest.raises(DigestRefused) as e:
        await d.save([card("Good"), card("Bad")])
    assert e.value.failures == [{"artist": "Band", "album": "Bad", "reasons": ["owned"]}]
    assert "Band, Bad: owned" in str(e.value)
    assert store.digests(None, 10) == [] and d.requests == []


async def test_save_refuses_invented_track(tmp_path):
    d, store = make(tmp_path)
    with pytest.raises(DigestRefused) as e:
        await d.save([card(tracks=("One", "Made Up"))])
    assert e.value.failures[0]["reasons"] == ["bad_track"]
    assert store.digests(None, 10) == []


async def test_save_refuses_duplicate_in_batch(tmp_path):
    d, store = make(tmp_path)
    with pytest.raises(DigestRefused) as e:
        await d.save([card("A"), card("a")])
    assert e.value.failures[0]["reasons"] == ["duplicate_in_batch"]
    with pytest.raises(ValueError):
        await d.save([])


async def test_saved_card_shape(tmp_path):
    ytm = FakeYtm({"browse_id": "b", "url": "https://music.youtube.com/browse/b",
                   "tracks": [{"title": "One", "plays": 5}]})
    d, store = make(tmp_path, ytm=ytm)
    dig = await d.save([card()])
    c = dig["cards"][0]
    assert set(c) - {"digest_id", "reaction_at", "on_wantlist", "wanted_by_card"} == SAMPLE_KEYS | NEW_KEYS
    assert c["style"] == "Blues Rock, Hard Rock" and c["kind"] == "weekly"
    assert c["standout_tracks"] == [{"title": "One", "plays": 5}, {"title": "Two Sides", "plays": None}]
    assert c["links"] == {"youtube_music": "https://music.youtube.com/browse/b",
                          "discogs": "https://www.discogs.com/master/10"}
    assert c["checks"] == {"exists": True, "owned": False, "previously_suggested": False,
                           "on_wantlist": False}
    assert c["cover_url"] == "/music/covers/10" and d.cover_path(10).read_bytes() == b"JPEG"
    assert d.requests[0].headers["Authorization"] == "Discogs token=tok"
    assert d.requests[0].headers["User-Agent"].startswith("zen-agents/")


async def test_ytm_down_still_saves(tmp_path):
    d, _ = make(tmp_path, ytm=FakeYtm(None))
    c = (await d.save([card()]))["cards"][0]
    assert c["links"]["youtube_music"].startswith("https://music.youtube.com/search")
    assert all(t["plays"] is None for t in c["standout_tracks"])


async def test_cover_failure_sets_none(tmp_path):
    d, _ = make(tmp_path, cover_status=500)
    c = (await d.save([card()]))["cards"][0]
    assert c["cover_url"] is None and d.cover_path(10) is None


async def test_want_success_sets_wanted(tmp_path):
    disc, mem = FakeDiscogs(), FakeMemory()
    d, store = make(tmp_path, memory=mem, discogs=disc)
    cid = (await d.save([card()]))["cards"][0]["id"]
    r = await d.want(cid)
    assert r["ok"] and disc.wanted == [11]
    c = store.card(cid)
    assert c["reaction"] == "wanted" and c["on_wantlist"] and c["wanted_by_card"]
    assert mem.lines[0][0] == "Wanted Band, Album 1"
    assert mem.lines[0][1] == "bot:music-librarian" and mem.lines[0][2]["source"] == "music"


async def test_want_failure_changes_nothing(tmp_path):
    mem = FakeMemory()
    d, store = make(tmp_path, memory=mem, discogs=FakeDiscogs(DiscogsError("token_invalid")))
    cid = (await d.save([card()]))["cards"][0]["id"]
    assert await d.want(cid) == {"ok": False, "error": "token_invalid"}
    c = store.card(cid)
    assert c["reaction"] is None and not c["on_wantlist"] and mem.lines == []


async def test_unwant_only_removes_what_card_added(tmp_path):
    disc = FakeDiscogs()
    d, store = make(tmp_path, discogs=disc)
    cid = (await d.save([card()]))["cards"][0]["id"]
    store.set_wantlist(cid, True, False)
    store.set_reaction(cid, "listened")
    r = await d.unwant(cid)
    assert r["ok"] and disc.unwanted == [] and store.card(cid)["on_wantlist"]
    assert store.card(cid)["reaction"] == "listened"
    store.set_wantlist(cid, False, False)
    await d.want(cid)
    r = await d.unwant(cid)
    assert disc.unwanted == [11] and r["card"]["reaction"] is None and not r["card"]["on_wantlist"]


async def test_react_wanted_refused(tmp_path):
    d, _ = make(tmp_path)
    cid = (await d.save([card()]))["cards"][0]["id"]
    with pytest.raises(ValueError):
        await d.react(cid, "wanted")
    with pytest.raises(KeyError):
        await d.react("nope", "passed")


async def test_react_lines_and_owned(tmp_path):
    mem = FakeMemory()
    d, store = make(tmp_path, memory=mem)
    cid = (await d.save([card()]))["cards"][0]["id"]
    await d.react(cid, "passed")
    await d.react(cid, "listened")
    c = await d.react(cid, "owned")
    assert c["reaction"] == "owned"
    assert [m[0] for m in mem.lines] == [
        "Passed on Band, Album 1 (Blues Rock, Hard Rock, 1971)",
        "Listened to Band, Album 1", "Already owns Band, Album 1"]
    assert store.owned_marks()[0]["master_id"] == 10
    assert store.owned_marks()[0]["genres"] == ["blues rock"]
    assert (await d.react(cid, None))["reaction"] is None


async def test_memory_failure_does_not_block_reaction(tmp_path):
    d, _ = make(tmp_path, memory=FakeMemory(fail=True))
    cid = (await d.save([card()]))["cards"][0]["id"]
    assert (await d.react(cid, "passed"))["reaction"] == "passed"


async def test_weekly_unread_on_demand_read(tmp_path):
    d, _ = make(tmp_path)
    assert (await d.save([card()]))["read_at"] is None
    assert (await d.save([card("Other")], kind="on_demand"))["read_at"] is not None


async def test_want_on_preexisting_wantlist_is_not_undone(tmp_path):
    disc = FakeDiscogs()
    d, store = make(tmp_path, discogs=disc)
    cid = (await d.save([card()]))["cards"][0]["id"]
    store.set_wantlist(cid, True, False)
    await d.want(cid)
    assert not store.card(cid)["wanted_by_card"]
    await d.unwant(cid)
    assert disc.unwanted == []


async def test_same_master_two_spellings_is_duplicate(tmp_path):
    class SameMaster(FakeGate):
        async def check(self, artist, album):
            return Verdict(True, [], facts(10, artist, album))

    d, store = make(tmp_path, SameMaster())
    with pytest.raises(DigestRefused) as e:
        await d.save([card("Album A"), card("Album B")])
    assert e.value.failures[0]["reasons"] == ["duplicate_in_batch"]
    assert store.digests(None, 10) == []


@pytest.mark.parametrize("tracks", [("One",), ("One", "one"), ("One", "Two Sides", "Three")])
async def test_standout_needs_two_distinct_tracks(tmp_path, tracks):
    d, _ = make(tmp_path)
    with pytest.raises(DigestRefused) as e:
        await d.save([card(tracks=tracks)])
    assert e.value.failures[0]["reasons"] == ["bad_track"]


async def test_ytm_raising_still_saves(tmp_path):
    class Boom(FakeYtm):
        def album(self, a, t):
            raise RuntimeError("x")

        def search_url(self, a, t):
            raise RuntimeError("y")

    d, _ = make(tmp_path, ytm=Boom())
    c = (await d.save([card()]))["cards"][0]
    assert all(t["plays"] is None for t in c["standout_tracks"])


async def test_cover_exception_sets_none(tmp_path):
    d, _ = make(tmp_path)

    def boom(req):
        raise ValueError("bad")

    d.client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    assert (await d.save([card()]))["cards"][0]["cover_url"] is None


async def test_a_widened_master_passes_where_a_normal_save_refuses_it(tmp_path):
    d, store = make(tmp_path)
    with pytest.raises(DigestRefused) as e:
        await d.save([card("Famous")])
    assert e.value.failures[0]["reasons"] == ["too_well_known"]
    with pytest.raises(DigestRefused):  # another master was widened: this one still gets the normal gate
        await d.save([card("Famous")], widened_masters=frozenset({123}))
    dig = await d.save([card("Famous")], widened_masters=frozenset({FAMOUS}))
    assert dig["cards"][0]["album"] == "Famous"


async def test_an_on_demand_session_cannot_save_a_weekly_digest(tmp_path):
    d, store = make(tmp_path)
    d.on_demand_sessions.add("od1")
    with pytest.raises(ValueError, match="this is an on-demand run; save with kind on_demand"):
        await d.save([card()], kind="weekly", session_id="od1")
    assert store.digests(None, 5) == []
    assert (await d.save([card()], kind="on_demand", session_id="od1"))["kind"] == "on_demand"
    assert (await d.save([card("Album 2")], kind="weekly", session_id="weekly1"))["kind"] == "weekly"


async def test_want_and_unwant_drop_the_cached_wantlist(tmp_path):
    d, store = make(tmp_path)
    changes = []
    d.on_wantlist_change = lambda: changes.append(1)
    cid = (await d.save([card()]))["cards"][0]["id"]
    await d.want(cid)
    await d.unwant(cid)
    assert len(changes) == 2


async def test_the_token_goes_only_to_a_discogs_cover_host(tmp_path):
    from src.music.digest import _discogs_host
    assert _discogs_host("https://i.discogs.com/a.jpg") and _discogs_host("https://discogs.com/a.jpg")
    for other in ("https://evil.example/a.jpg", "https://notdiscogs.com/a.jpg", "https://discogs.com.evil.example/a",
                  "http://i.discogs.com/a.jpg"):
        assert not _discogs_host(other), other
    d, _ = make(tmp_path)
    assert await d._cover(5, "https://evil.example/5.jpg")
    assert "Authorization" not in d.requests[-1].headers
    assert await d._cover(6, "https://i.discogs.com/6.jpg")
    assert d.requests[-1].headers["Authorization"] == "Discogs token=tok"

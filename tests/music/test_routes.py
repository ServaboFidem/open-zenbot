"""MusicService and the /music routes, offline: Discogs is an httpx.MockTransport, the atlas is the test fixture."""

import asyncio

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.music.settings import MusicKeys
from src.skills.catalog import Catalog
from tests.conftest import ScriptedProvider
from tests.music.conftest import FIXTURE_ATLAS

GOOD, OTHER, REVOKED = "good-token-1234", "other-token-5678", "revoked-token-9999"
CARD = {"artist": "Band", "album": "Album", "master_id": 7, "release_id": 70, "style": "Blues Rock", "year": 1971}


CALLS: list[str] = []


def discogs_handler(request: httpx.Request) -> httpx.Response:
    CALLS.append(request.url.path)
    path, auth = request.url.path, request.headers.get("Authorization", "")
    if f"token={REVOKED}" in auth:
        return httpx.Response(401, json={"message": "invalid"})
    if path == "/oauth/identity":
        who = {GOOD: "alice", OTHER: "bob"}.get(auth.removeprefix("Discogs token="))
        return httpx.Response(200, json={"username": who}) if who else httpx.Response(401)
    if "/users/ghost/" in path:
        return httpx.Response(404)
    if "/users/hidden/" in path:
        return httpx.Response(403)
    if "/collection/" in path:
        entry = {"id": 5, "basic_information": {"id": 5, "title": "T", "artists": [{"id": 99, "name": "A"}]}}
        return httpx.Response(200, json={"pagination": {"pages": 1, "items": 12}, "releases": [entry]})
    return httpx.Response(204)


async def no_download(dir, client):
    return None


@pytest.fixture
def harness(vault, tmp_path):
    CALLS.clear()
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path)
    cfg.validate()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    h.add_bot(Bot(name="Librarian", role="Records", id="music-librarian"))
    h.music.client = httpx.AsyncClient(transport=httpx.MockTransport(discogs_handler))
    h.music.atlas_dir = FIXTURE_ATLAS
    h.music.ensure = no_download
    return h


@pytest.fixture
async def client(harness):
    app = create_app(harness)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        yield c
    await harness.music.settle()


async def connect(client, username="alice", token=GOOD):
    return await client.post("/music/connect", json={"username": username, "token": token})


async def test_status_never_returns_token(client, harness):
    assert (await connect(client)).json()["state"] == "ok"
    await client.put("/music/token", json={"token": GOOD})
    body = (await client.get("/music/status")).json()
    assert GOOD not in str(body)
    assert body["connection"]["token"] is True and body["connection"]["token_hint"] == GOOD[-4:]
    assert (await client.get("/music/status")).json()["atlas"]["state"] in ("missing", "ready", "downloading")
    assert GOOD not in (await client.put("/music/token", json={"token": GOOD})).text


async def test_connect_states(client):
    r = await connect(client, "ghost", token="")
    assert r.json()["state"] == "not_found"
    assert (await connect(client, "hidden", token="")).json()["state"] == "private"
    assert (await connect(client, "alice", OTHER)).json()["state"] == "token_other_user"
    ok = (await connect(client, "alice", GOOD)).json()
    assert ok["state"] == "ok" and ok["records"] == 12 and ok["digest_needs_token"] is False
    nokey = (await connect(client, "alice", "")).json()
    assert nokey["state"] == "ok" and nokey["digest_needs_token"] is True
    assert (await connect(client, "alice", REVOKED)).json()["state"] == "token_invalid"


async def test_enable_with_token_creates_routine(client, harness):
    await connect(client)
    await client.put("/music/token", json={"token": GOOD})
    st = (await client.put("/music/settings", json={"enabled": True, "username": "alice", "focus": []})).json()
    assert st["digest"] == "on"
    routines = [r for r in (await client.get("/routines")).json() if r["managed_by"] == "music"]
    assert len(routines) == 1 and st["routine_id"] == routines[0]["id"]
    r = routines[0]
    assert (r["name"], r["bot"], r["skill"], r["enabled"]) == ("Weekly album digest", "music-librarian", "weekly-digest", True)
    assert r["schedule"] == {"kind": "weekly", "days": [4], "time": "09:00", "hours": 0}
    await client.put("/music/settings", json={"enabled": True, "count": 5})  # again: still one
    assert len([r for r in (await client.get("/routines")).json() if r["managed_by"] == "music"]) == 1
    assert harness.tools.fn("music_profile") is not None
    await client.delete(f"/routines/{routines[0]['id']}")
    st = (await client.get("/music/status")).json()
    assert st["digest"] == "off" and st["routine_id"] is None


async def test_enable_without_token_needs_token(client):
    st = (await client.put("/music/settings", json={"enabled": True, "username": "alice"})).json()
    assert st["digest"] == "needs_token"
    assert [r for r in (await client.get("/routines")).json() if r["managed_by"] == "music"] == []
    await client.put("/music/token", json={"token": GOOD})  # a token arrives: the routine follows
    assert (await client.get("/music/status")).json()["digest"] == "on"
    await client.delete("/music/token")
    st = (await client.get("/music/status")).json()
    assert st["digest"] == "needs_token" and st["connection"]["token"] is False


async def test_token_revoked_marks_status(client, harness):
    await client.put("/music/token", json={"token": REVOKED})
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    harness.music.store.add_digest({"kind": "weekly", "week_of": "2026-11-13", "note": "", "session_id": "s"}, [CARD])
    card_id = harness.music.store.digests(None, 5)[0]["cards"][0]["id"]
    r = await client.post(f"/music/cards/{card_id}/want")
    assert r.json() == {"ok": False, "error": "token_invalid"}
    status = (await client.get("/music/status")).json()
    assert status["connection"]["state"] == "token_invalid"
    await client.put("/music/token", json={"token": GOOD})  # a new token clears it
    assert (await client.get("/music/status")).json()["connection"]["state"] != "token_invalid"


async def test_map_empty_profile(client, harness):
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    body = (await client.get("/music/map")).json()
    assert body["genres"] == [] and body["related"] == [] and "points" in body
    assert body["releases"] == 0 and body["formats"] == {} and body["decades"] == {}
    assert (await client.get("/music/genres/No Such Genre")).status_code == 404
    await harness.music.prepare()
    name = harness.music.atlas.names()[0]
    assert (await client.get(f"/music/genres/{name}")).json()["name"] == name


async def test_want_route_error_passthrough(client, harness):
    await client.put("/music/token", json={"token": GOOD})
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    assert (await client.post("/music/cards/nope/want")).status_code == 404
    harness.music.store.add_digest({"kind": "weekly", "week_of": "2026-11-13", "note": "", "session_id": "s"},
                                   [dict(CARD, release_id=None)])
    card_id = harness.music.store.digests(None, 5)[0]["cards"][0]["id"]
    r = await client.post(f"/music/cards/{card_id}/want")
    assert r.status_code == 200 and r.json() == {"ok": False, "error": "not_found"}
    assert (await client.put(f"/music/cards/{card_id}/reaction", json={"reaction": "wanted"})).status_code == 422
    assert (await client.put(f"/music/cards/{card_id}/reaction", json={"reaction": "passed"})).json()["reaction"] == "passed"


async def test_digests_read_and_unread(client, harness):
    harness.music.store.add_digest({"kind": "weekly", "week_of": "2026-11-13", "note": "", "session_id": "s"}, [CARD])
    did = (await client.get("/music/digests")).json()[0]["id"]
    assert (await client.get("/music/status")).json()["unread"] == 1
    assert (await client.get(f"/music/digests/{did}")).json()["cards"][0]["album"] == "Album"
    assert (await client.get("/music/digests/zzz")).status_code == 404
    assert (await client.post(f"/music/digests/{did}/read")).status_code == 200
    assert (await client.get("/music/status")).json()["unread"] == 0


async def test_cover_404(client):
    assert (await client.get("/music/covers/123")).status_code == 404


async def test_discover_pins_skill(client, harness):
    await client.put("/music/token", json={"token": GOOD})
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    seen = {}

    class S:
        id = "sess1"

    def fake(bot, task, **kw):
        seen.update(bot=bot.id, task=task, **kw)
        return S()

    harness.start_task = fake
    await harness.music.prepare()
    name = harness.music.atlas.names()[0]
    r = await client.post("/music/discover", json={"genre": name})
    assert r.json() == {"session_id": "sess1"}
    assert seen["bot"] == "music-librarian" and seen["pinned_skills"] == ["weekly-digest"]
    assert name in seen["task"] and "kind=on_demand" in seen["task"]
    assert f'music_candidates(genre="{name}")' in seen["task"]
    assert seen["in_conversation"] is False
    assert "sess1" in harness.music.digests.on_demand_sessions
    assert (await client.post("/music/discover", json={"genre": "Nope"})).status_code == 422


async def test_settings_validation_and_scan(client, harness):
    assert (await client.put("/music/settings", json={"count": 99})).status_code == 422
    assert (await client.put("/music/settings", json={"focus": ["Not A Genre"]})).status_code == 422
    assert (await client.post("/music/scan")).status_code == 422  # no username yet
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    await harness.music.settle()
    r = await client.post("/music/scan")
    assert r.status_code == 200
    await harness.music.settle()
    assert (await client.get("/music/status")).json()["scan"]["state"] == "done"
    assert (await client.get("/music/status")).json()["atlas"]["state"] == "ready"


def test_token_file_is_private(harness):
    harness.music.set_token(GOOD)
    import os, stat
    from src.vault_settings import settings_dir
    p = settings_dir(harness.config.vault_root) / "music_keys.json"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert MusicKeys.load(harness.config.vault_root).token == GOOD


def scans() -> int:
    return len([p for p in CALLS if "/collection/folders/0/releases" in p])


class FakeMB:
    def __init__(self):
        self.asked = []

    async def artist_by_discogs(self, aid):
        self.asked.append(aid)
        return "mbid-1"


async def test_username_change_rescans(client, harness):
    m = harness.music
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    await m.settle()
    assert m._scanned_user == "alice" and m.store.profile() is not None
    before = scans()
    await client.put("/music/settings", json={"username": "bob"})
    await m.settle()
    assert scans() > before and m._scanned_user == "bob"


async def test_refresh_scans_when_stale_and_skips_when_fresh(client, harness):
    m = harness.music
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    await m.settle()
    n = scans()
    await m.refresh()
    assert scans() == n  # finished moments ago
    m.store.set_scan(finished_at="2020-01-01T00:00:00+00:00")
    await m.refresh()
    assert scans() == n + 1


async def test_refresh_failed_scan_keeps_profile_with_error_or_stops(client, harness):
    from src.music.discogs import DiscogsError
    m = harness.music
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    await m.settle()
    m.store.set_scan(finished_at="2020-01-01T00:00:00+00:00")
    m.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    m._discogs = None
    p = await m.refresh()
    assert p["scan_error"] == "network" and p["genres"] is not None
    m.store.set_scan(finished_at="2020-01-01T00:00:00+00:00")
    m.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    m._discogs = None
    m.discogs()._sleep = lambda s: asyncio.sleep(0)
    with pytest.raises(DiscogsError) as e:
        await m.refresh()
    assert e.value.kind == "rate_limited"


async def test_a_good_scan_starts_artist_linking(client, harness):
    mb = harness.music.mb = FakeMB()
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    await harness.music.settle()
    assert mb.asked == [99]


async def test_concurrent_scans_share_one_run(client, harness):
    m = harness.music
    await client.put("/music/settings", json={"username": "alice"})
    await m.prepare()
    CALLS.clear()
    await asyncio.gather(m.scan(), m.scan(), m.scan())
    m.start_scan()
    m.start_scan()
    await m.settle()
    assert scans() == 2  # one for the three joined calls, one for the two start_scan calls


async def test_background_failure_is_logged(client, harness):
    async def boom():
        raise RuntimeError("kaput")
    harness.music.spawn(boom())
    await harness.music.settle()
    assert any("kaput" in str(e.data) for e in harness.ledger.tail(0, kind="error"))


async def test_401_in_tools_marks_status(client, harness):
    from src.music.tools import MusicTools
    await client.put("/music/token", json={"token": REVOKED})
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    await harness.music.settle()
    harness.music.store.save_profile({"releases": 1, "decades": {}, "formats": {},
                                      "genres": [{"genre": "hard rock", "share": 1.0, "owned": 1}]})
    t = MusicTools(harness.music)
    assert (await t.check("Band", "Album")).startswith("Stopped: Discogs token_invalid")
    assert (await t.candidates(3)).startswith("Stopped: Discogs token_invalid")
    assert (await client.get("/music/status")).json()["connection"]["state"] == "token_invalid"


async def test_the_digest_routine_fails_a_run_that_saved_no_digest(client, harness):
    from types import SimpleNamespace
    assert harness.music.run_failure(SimpleNamespace(id="s1")) == "no digest saved"
    harness.music.store.add_digest({"kind": "weekly", "week_of": "2026-11-13", "note": "", "session_id": "s1"}, [CARD])
    assert harness.music.run_failure(SimpleNamespace(id="s1")) == ""
    assert harness.music.store.has_digest_for("") is False
    assert harness.music.scheduler.verifiers["music"] == harness.music.run_failure


async def test_one_gate_per_session_and_a_cached_wantlist(client, harness):
    from src.harness.tools import current_call
    svc = harness.music
    await client.put("/music/token", json={"token": GOOD})
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    fetched = []

    async def wantlist_ids():
        fetched.append(1)
        return {1, 2}

    d = svc.discogs()
    d.wantlist_ids = wantlist_ids
    token = current_call.set(("music-librarian", "s1"))
    try:
        g = svc.gate()
        assert svc.gate() is g and svc.gate(widened=True) is not g and svc.gate(widened=True).widened
    finally:
        current_call.reset(token)
    token = current_call.set(("music-librarian", "s2"))
    try:
        assert svc.gate() is not g
    finally:
        current_call.reset(token)
    assert svc.gate() is not svc.gate()  # outside a session: a new gate each time
    now = [100.0]
    svc.clock = lambda: now[0]
    assert await svc.wantlist_ids() == {1, 2} and await svc.wantlist_ids() == {1, 2} and len(fetched) == 1
    now[0] += 601
    await svc.wantlist_ids()
    assert len(fetched) == 2
    svc.digests.on_wantlist_change()  # a want or unwant
    await svc.wantlist_ids()
    assert len(fetched) == 3


async def test_a_good_scan_with_the_token_clears_token_invalid(client, harness):
    m = harness.music
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    await m.settle()
    m.set_token(GOOD)
    m._set_state("token_invalid")  # an earlier 401 marked it
    await m.prepare()
    await m.scan()
    assert (await client.get("/music/status")).json()["connection"]["state"] == "ok"


async def test_one_librarian_run_at_a_time(client, harness):
    from src.harness.loop import Session, SessionState
    await client.put("/music/token", json={"token": GOOD})
    await client.put("/music/settings", json={"enabled": True, "username": "alice"})
    started = []

    def fake(bot, task, **kw):
        s = Session(bot_id=bot.id, task=task)
        harness.sessions[s.id] = s
        started.append(s)
        return s

    harness.start_task = fake
    await harness.music.prepare()
    name = harness.music.atlas.names()[0]
    assert (await client.post("/music/discover", json={"genre": name})).status_code == 200
    r = await client.post("/music/discover", json={"genre": name})
    assert r.status_code == 409 and "already on a run" in r.json()["detail"] and started[0].id in r.json()["detail"]
    started[0].state = SessionState.done
    assert (await client.post("/music/discover", json={"genre": name})).status_code == 200
    started[1].state = SessionState.failed
    # the weekly digest routine's run counts too
    routine = harness.music.managed_routine()
    weekly = Session(bot_id="music-librarian", task="weekly")
    harness.music.scheduler.sessions[routine.id] = weekly
    assert (await client.post("/music/discover", json={"genre": name})).status_code == 409
    weekly.state = SessionState.stopped
    assert (await client.post("/music/discover", json={"genre": name})).status_code == 200

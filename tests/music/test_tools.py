import json
from types import SimpleNamespace

import pytest

from src.harness.bot_prompts import load_dir as load_prompts
from src.music.check import Verdict
from src.music.digest import DigestRefused
from src.music.settings import MusicKeys, MusicSettings
from src.music.store import MusicStore
from src.music.tools import GROUP, SPECS, MusicTools
from src.skills.catalog import CatalogEntry
from src.skills.loader import load_dir as load_skills
from tests.conftest import REPO

PROFILE = {"releases": 3, "genres": [{"genre": f"g{i}", "share": 1 / (i + 2), "owned": i} for i in range(20)],
           "decades": {"1970": 2}, "formats": {"Vinyl": 3}}
FACTS = {"master_id": 7, "artist": "Band", "album": "Album", "year": 1971, "tracks": ["One", "Two"],
         "obscurity_label": "hidden gem"}


class FakeTools:
    def __init__(self):
        self.tools = {}

    def register(self, name, description, parameters, fn, group=None, room_only=False):
        self.tools[name] = (fn, group, parameters)

    def unregister(self, name):
        self.tools.pop(name, None)


class FakeCatalog:
    def __init__(self):
        self.entries = []

    def mcps(self):
        return self.entries

    def add_mcp(self, e):
        self.entries.append(e)

    def remove_mcp(self, ref):
        self.entries = [e for e in self.entries if e.ref != ref]


class FakeGate:
    async def check(self, artist, album):
        if album == "Bad":
            return Verdict(False, ["owned"], {})
        return Verdict(True, [], dict(FACTS, artist=artist, album=album))

    async def check_master(self, master_id):
        return Verdict(True, [], dict(FACTS, master_id=master_id))


class FakeDigests:
    def __init__(self):
        self.saved = []
        self.refuse = None

    async def save(self, cards, *, kind="weekly", note="", session_id="", widened_masters=frozenset()):
        if self.refuse:
            raise DigestRefused(self.refuse)
        self.saved.append((cards, kind, note))
        return {"id": "2026-W46", "kind": kind, "cards": cards}


class FakeYtm:
    def album(self, artist, title):
        return {"url": "u", "tracks": [{"title": "One", "plays": 1200}]}


class FakeAtlas:
    def detail(self, name):
        return {"name": name, "area": []} if name == "g1" else {}

    def genre(self, name):
        return {"name": name} if name in ("g1", "g5") else None


def service(tmp_path, enabled=True, token="tok"):
    store = MusicStore(tmp_path / "music.db")
    store.save_profile(PROFILE)
    return SimpleNamespace(settings=MusicSettings(enabled=enabled, focus=["g0"]), keys=MusicKeys(token=token),
                           atlas=FakeAtlas(), store=store, styles=None, discogs=lambda: None, ytm=FakeYtm(),
                           gate=lambda widened=False: FakeGate(), digests=FakeDigests(), profile=lambda: store.profile(),
                           refresh=lambda: store.profile(), widened_masters={})


def test_tools_register_only_when_enabled_with_token(tmp_path):
    tools, catalog = FakeTools(), FakeCatalog()
    names = {"music_profile", "music_candidates", "music_check", "music_album", "music_save_digest", "music_genre"}
    svc = service(tmp_path)
    assert MusicTools(svc).register(tools, catalog) is True
    assert set(tools.tools) == names and {g for _, g, _ in tools.tools.values()} == {GROUP}
    assert [e.ref for e in catalog.mcps()] == [GROUP]
    MusicTools(svc).register(tools, catalog)  # again: no duplicate catalog entry
    assert len(catalog.mcps()) == 1
    for off in (service(tmp_path, enabled=False), service(tmp_path, token="")):
        assert MusicTools(off).register(tools, catalog) is False
        assert tools.tools == {} and catalog.mcps() == []
        MusicTools(svc).register(tools, catalog)


async def test_profile_tool_trims_and_lists_recent_reactions(tmp_path):
    svc = service(tmp_path)
    svc.store.add_digest({"kind": "weekly", "week_of": "2026-11-06"},
                         [{"artist": "A", "album": "X", "master_id": 1}, {"artist": "B", "album": "Y", "master_id": 2}])
    cid = svc.store.digests(None, 1)[0]["cards"][0]["id"]
    svc.store.set_reaction(cid, "passed")
    out = json.loads(await MusicTools(svc).profile())
    assert out["count"] == 4 and out["era_mix"] == 0.5 and out["obscurity"] == 0.7
    assert out["focus"] == ["g0"] and len(out["genres"]) == 15 and out["genres"][0]["share"] == 0.5
    assert out["decades"] == {"1970": 2} and out["formats"] == {"Vinyl": 3}
    assert out["reactions"] == [{"artist": "A", "album": "X", "reaction": "passed", "reaction_at": out["reactions"][0]["reaction_at"]}]


async def test_profile_tool_without_a_scan_says_so(tmp_path):
    svc = service(tmp_path)
    svc.profile = svc.refresh = lambda: None
    assert "scan" in (await MusicTools(svc).profile()).lower()


async def test_check_tool_json(tmp_path):
    t = MusicTools(service(tmp_path))
    ok = json.loads(await t.check("Band", "Album"))
    assert ok["ok"] is True and ok["reasons"] == [] and ok["facts"]["master_id"] == 7
    bad = json.loads(await t.check("Band", "Bad"))
    assert bad["ok"] is False and bad["reasons"] == ["owned"]


async def test_album_tool_joins_facts_and_ytm(tmp_path):
    out = json.loads(await MusicTools(service(tmp_path)).album(7))
    assert out["facts"]["master_id"] == 7 and out["ytmusic"]["tracks"][0]["plays"] == 1200


async def test_save_digest_tool_returns_failures_as_text(tmp_path):
    svc = service(tmp_path)
    t = MusicTools(svc)
    saved = json.loads(await t.save_digest([{"artist": "Band", "album": "Album"}], note="n"))
    assert saved["id"] == "2026-W46" and svc.digests.saved[0][2] == "n"
    svc.digests.refuse = [{"artist": "Band", "album": "Bad", "reasons": ["owned"]}]
    text = await t.save_digest([{"artist": "Band", "album": "Bad"}])
    assert text.startswith("The digest was not saved") and "Band, Bad: owned" in text
    assert "Refused" in await t.save_digest([])


async def test_genre_tool_adds_holdings(tmp_path):
    t = MusicTools(service(tmp_path))
    out = json.loads(await t.genre("g1"))
    assert out["name"] == "g1" and out["holdings"] == {"share": 0.3333333333333333, "owned": 1}
    assert "unknown" in (await t.genre("nope")).lower()


async def test_candidates_tool_runs_the_pipeline(tmp_path, monkeypatch):
    seen = {}

    async def fake(n, profile, settings, atlas, discogs, ytm, gate, store):
        seen.update(n=n, profile=profile)
        return [{"verdict": dict(FACTS, tracks=["One", "Two", "Three", "Four"], styles=["x"], have=10, want=1,
                                 genres=["g0"], era="forgotten classic", cover_uri="c", discogs_url="d"),
                 "sources": ["atlas"], "focus_genre": "g0"}]

    monkeypatch.setattr("src.music.tools.pipeline_candidates", fake)
    out = json.loads(await MusicTools(service(tmp_path)).candidates(5))
    assert out == [{"artist": "Band", "album": "Album", "year": 1971, "master_id": 7, "genres": ["g0"],
                    "obscurity_label": "hidden gem", "era": "forgotten classic", "focus_genre": "g0",
                    "sources": ["atlas"], "sample_tracks": ["One", "Two", "Three"]}]
    assert seen["n"] == 5 and seen["profile"]["releases"] == 3


async def test_sixteen_candidates_stay_well_under_the_output_cap(tmp_path, monkeypatch):
    from src.harness.tools import OUTPUT_CAP
    long = dict(FACTS, album="A" * 80, artist="B" * 60, tracks=[f"Track {i} " + "x" * 60 for i in range(40)],
                genres=["genre name"] * 6, styles=["s"] * 10)

    async def fake(n, *a):
        return [{"verdict": dict(long, master_id=i), "sources": ["atlas", "youtube_music"], "focus_genre": "g0"}
                for i in range(n)]

    monkeypatch.setattr("src.music.tools.pipeline_candidates", fake)
    out = await MusicTools(service(tmp_path)).candidates(8, widen=True)
    assert len(json.loads(out)) == 16 and len(out) < OUTPUT_CAP * 0.6  # long names and titles throughout


def test_librarian_prompt_and_skill_load():
    prompts, errors = load_prompts(REPO / "bot-prompts", report=True)
    assert errors == []
    [p] = [b for b in prompts if b.id == "music-librarian"]
    assert p.name == "MusicLibrarian" and p.skills == ["weekly-digest"] and p.mcps == ["music"]
    [s] = [r.skill for r in load_skills(REPO / "skills") if r.ok and r.skill.slug == "weekly-digest"]
    assert s.use == "Make this week's album digest for the user's record collection."
    for tool in ("music_profile", "music_candidates", "music_check", "music_save_digest"):
        assert tool in s.body


async def test_candidates_widen_doubles_n_and_builds_a_widened_gate(tmp_path, monkeypatch):
    seen = []

    async def fake(n, profile, settings, atlas, discogs, ytm, gate, store):
        seen.append((n, gate))
        return []

    monkeypatch.setattr("src.music.tools.pipeline_candidates", fake)
    svc = service(tmp_path)
    built = []
    svc.gate = lambda widened=False: built.append(widened) or f"gate-{widened}"
    t = MusicTools(svc)
    await t.candidates(4)
    await t.candidates(4, widen=True)
    assert seen == [(4, "gate-False"), (8, "gate-True")] and built == [False, True]


async def test_only_the_widened_calls_masters_get_the_widened_step(tmp_path, monkeypatch):
    from src.harness.tools import current_call

    async def fake(n, profile, settings, atlas, discogs, ytm, gate, store):
        return [{"verdict": {"master_id": 70 if gate == "wide" else 10}, "sources": ["atlas"]}]

    monkeypatch.setattr("src.music.tools.pipeline_candidates", fake)
    svc = service(tmp_path)
    svc.gate = lambda widened=False: "wide" if widened else "normal"
    seen = []

    async def save(cards, **kw):
        seen.append(kw)
        return {"id": "x", "kind": "weekly", "cards": cards}

    svc.digests.save = save
    t = MusicTools(svc)
    token = current_call.set(("music-librarian", "s1"))
    try:
        await t.candidates(4)
        await t.save_digest([{"artist": "A", "album": "B"}], widened=True)  # the bot's say-so loosens nothing
        await t.candidates(4, widen=True)
        await t.save_digest([{"artist": "A", "album": "B"}])
    finally:
        current_call.reset(token)
    assert [k["widened_masters"] for k in seen] == [frozenset(), frozenset({70})]
    assert "widened" not in SPECS["music_save_digest"][1]["properties"]


async def test_save_digest_refuses_malformed_input_as_text(tmp_path):
    svc = service(tmp_path)
    t = MusicTools(svc)
    for bad in (["Band - Album"], "nope", [{"artist": "A"}, 3], None):
        out = await t.save_digest(bad)
        assert out.startswith("Refused:"), out
    assert svc.digests.saved == []


@pytest.mark.parametrize("kind,text", [("token_invalid", "Stopped: Discogs token_invalid. The user must reconnect in Music \u2192 Settings."),
                                       ("rate_limited", "Stopped: Discogs rate_limited. Try again later.")])
async def test_discogs_stop_kinds_come_back_as_stopped_text(tmp_path, kind, text):
    from src.music.discogs import DiscogsError

    class Boom(FakeGate):
        async def check(self, artist, album):
            raise DiscogsError(kind)

        async def check_master(self, master_id):
            raise DiscogsError(kind)

    svc = service(tmp_path)
    svc.gate = lambda widened=False: Boom()
    svc.digests.save = None

    async def refresh():
        raise DiscogsError(kind)
    t = MusicTools(svc)
    assert await t.check("A", "B") == text
    assert await t.album(1) == text
    svc.refresh = refresh
    assert await t.profile() == text


async def test_profile_tool_reports_a_failed_rescan(tmp_path):
    svc = service(tmp_path)
    svc.refresh = lambda: {**PROFILE, "scan_error": "network"}
    assert json.loads(await MusicTools(svc).profile())["scan_error"] == "network"


async def test_save_digest_carries_the_session_id(tmp_path):
    from src.harness.tools import current_call
    svc = service(tmp_path)
    seen = []

    async def save(cards, **kw):
        seen.append(kw)
        return {"id": "x", "kind": "weekly", "cards": cards}

    svc.digests.save = save
    token = current_call.set(("music-librarian", "sess-1"))
    try:
        await MusicTools(svc).save_digest([{"artist": "A", "album": "B"}])
    finally:
        current_call.reset(token)
    assert seen[0]["session_id"] == "sess-1"


async def test_candidates_for_one_genre_runs_the_pipeline_with_that_focus(tmp_path, monkeypatch):
    seen = []

    async def fake(n, profile, settings, atlas, discogs, ytm, gate, store):
        seen.append(list(settings.focus))
        return []

    monkeypatch.setattr("src.music.tools.pipeline_candidates", fake)
    svc = service(tmp_path)
    t = MusicTools(svc)
    await t.candidates(4, genre="g5")
    await t.candidates(4)
    assert seen == [["g5"], ["g0"]] and svc.settings.focus == ["g0"]  # the saved settings are untouched
    assert "Unknown genre" in await t.candidates(4, genre="nope")
    assert "genre" in SPECS["music_candidates"][1]["properties"]


def test_run_budget_counts_from_the_first_call_and_leaves_the_save_open_longer():
    from src.music.tools import BUDGET_STOP, RunBudget
    now = [0.0]
    b = RunBudget(clock=lambda: now[0])
    assert b.check("s", "music_profile") == ""
    now[0] = 899
    assert b.check("s", "music_candidates") == ""
    assert b.check("other", "music_check") == ""  # its own clock starts now
    now[0] = 900
    assert b.check("s", "music_check") == BUDGET_STOP
    assert b.check("s", "music_save_digest") == ""
    now[0] = 1019
    assert b.check("s", "music_save_digest") == ""
    now[0] = 1020
    assert b.check("s", "music_save_digest") == BUDGET_STOP
    assert b.check("", "music_check") == ""  # not a bot run


async def test_registered_tools_stop_when_the_budget_is_used_up(tmp_path):
    from src.harness.tools import current_call
    from src.music.tools import BUDGET_STOP, RunBudget
    now = [0.0]
    svc = service(tmp_path)
    svc.budget = RunBudget(clock=lambda: now[0])
    tools = FakeTools()
    MusicTools(svc).register(tools, FakeCatalog())
    token = current_call.set(("music-librarian", "sess-9"))
    try:
        assert json.loads(await tools.tools["music_check"][0]("Band", "Album"))["ok"] is True
        now[0] = 16 * 60
        assert await tools.tools["music_check"][0]("Band", "Album") == BUDGET_STOP
        saved = await tools.tools["music_save_digest"][0]([{"artist": "Band", "album": "Album"}])
        assert json.loads(saved)["id"] == "2026-W46"
        now[0] = 18 * 60
        assert await tools.tools["music_save_digest"][0]([{"artist": "Band", "album": "Album"}]) == BUDGET_STOP
    finally:
        current_call.reset(token)

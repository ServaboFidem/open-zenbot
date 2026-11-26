"""The pass over CaseMark and the owner's GitHub repositories: one search per source, one fit, one conversion."""
import asyncio
import json

import httpx
import pytest

from src.skilltuner.github import GitHub
from src.skilltuner.github_source import GitHubSkills, gh_id
from src.skilltuner.index import Listed
from src.skilltuner.repos import RepoList, RepoStore

from .conftest import GH_SHA, FakeGitHub
from .test_github_source import entry, tree_of
from .test_tuner import CONVERTED, NOW, TODAY, TOPIC, WIN, WORDS, Rig, iso, needs, results

MEMO = "---\nname: Memo Writer\ndescription: Write an investment memo from a deal.\n---\n# Memo\n\nSteps.\n"
OLD = "0" * 40
MEMO_ID = gh_id("o", "r", "skills/memo")
LONG_AGO = 30 * 86400


@pytest.fixture
def t(vault, ledger, tmp_path):
    rig = Rig(vault, ledger)
    rig.fake = FakeGitHub()
    rig.owned = []
    rig.repos = RepoStore(tmp_path / "repos")
    client = GitHub(lambda: rig.fake.client, sleep=rig.fake.sleep,
                    on_send=lambda k: rig.state.spend_github(rig.today, k))
    rig.gs = GitHubSkills(client, rig.repos, lambda: rig.owned, lambda: rig.now)
    rig.tuner.github = rig.gs
    rig.site.search_answer = results()  # CaseMark finds nothing unless a test says so
    return rig


def listed(name="Memo Writer", path="skills/memo", desc="Write an investment memo from a deal.", extra=False):
    return Listed(path, name, desc, "", extra)


def store(t, e, skills, *, commit=GH_SHA, read_at=None):
    """Put `e` in the owner's repositories with a list already read (fresh unless `read_at` says otherwise)."""
    t.owned.append(e)
    t.repos.put(e.id, RepoList(commit, "MIT", read_at if read_at is not None else iso(t.now), "", "", 0, skills, True))


def files(t, owner="o", repo="r", path="skills/memo", text=MEMO, extras=()):
    t.fake.trees[f"{owner}/{repo}/{GH_SHA}"] = tree_of([f"{path}/SKILL.md", *extras])
    t.fake.files[f"{owner}/{repo}/{GH_SHA}/{path}/SKILL.md"] = text.encode()


def requests_to(t, repo):
    return [r for r in t.fake.requests if f"/{repo}" in r.url.path]


def the_need(t):
    return next(iter(t.state._needs.values()))


def add_need(t, words=WORDS):
    t.state.add_need(TOPIC, words)


async def wait_for_tree(t):
    for _ in range(300):
        if any("/git/trees/" in r.url.path for r in t.fake.requests):
            return
        await asyncio.sleep(0.01)


async def test_a_need_is_searched_in_both_sources_and_one_fit_decides(t):
    store(t, entry(), [listed()])
    files(t)
    t.site.search_answer = results((WIN, "Drafting Investment Memos"))
    t.scorer.scores = {WIN: 0.3, MEMO_ID: 0.95}
    add_need(t)
    t.model.answers = [CONVERTED]
    got = await t.tune()
    assert got["suggested"] == 1 and got["searches"] == 1
    assert len(t.scorer.requests) == 1 and set(t.scorer.requests[0].questions) == {f"r:{WIN}", f"r:{MEMO_ID}"}
    [s] = t.store.list()
    assert (s.source, s.source_slug, s.source_name, s.left_out) == ("github.com/o/r", MEMO_ID, "Memo Writer", False)
    assert s.source_url == f"https://github.com/o/r/tree/{GH_SHA}/skills/memo" and s.license == "MIT"
    skill = t.store.files(s.id)
    assert f"source_url: https://github.com/o/r/tree/{GH_SHA}/skills/memo" in skill["SKILL.md"]
    assert "github.com/o/r" in skill["NOTICE.txt"] and GH_SHA in skill["NOTICE.txt"]
    assert t.state.known == {MEMO_ID}
    assert [p["name"] for p in t.sent()] == ["resolve_skill"]  # CaseMark was searched, not read
    assert [e["source"] for e in t.events("search")] == ["casemark", "github"]
    # both sources are marked, so neither is asked again for 30 days
    assert t.state.due_for(the_need(t), "2027-01-16") == []


async def test_the_same_skill_in_two_repositories_reaches_the_decider_once(t):
    store(t, entry(), [listed("Memo Writer")])
    store(t, entry(id="fedcba9876543210", owner="p", repo="q"), [listed("memo writer")])
    add_need(t)
    await t.tune()
    [req] = t.scorer.requests
    assert list(req.questions) == [f"r:{MEMO_ID}"]
    [dropped] = t.events("dropped")
    assert dropped["reason"] == "duplicate" and dropped["source_slug"] == gh_id("p", "q", "skills/memo")


async def test_merged_results_are_ranked_by_one_score_and_cut_to_five(t):
    greek = ("Alpha", "Beta", "Gamma")
    store(t, entry(), [listed(f"Investment Memo {w}", f"skills/m{i}", "Memo for a deal.") for i, w in enumerate(greek)])
    cm = ("Memo", "Zebra Care", "Investment Memo Drafting", "Yak Care", "Xylophone")
    t.site.search_answer = {"results": [{"slug": f"cm-{i}", "name": n, "summary": "x", "tags": [], "score": 1}
                                        for i, n in enumerate(cm)]}
    add_need(t)
    m = t.gs.matcher()
    rows = [(-m.score(WORDS, n, "x"), 0, i, f"cm-{i}") for i, n in enumerate(cm)]
    rows += [(-m.score(WORDS, f"Investment Memo {w}", "Memo for a deal."), 1, i, gh_id("o", "r", f"skills/m{i}"))
             for i, w in enumerate(greek)]
    await t.tune()
    keys = set(t.scorer.requests[0].questions)
    assert keys == {f"r:{row[3]}" for row in sorted(rows)[:5]} and len(keys) == 5
    assert any(k.startswith("r:gh:") for k in keys) and any(k.startswith("r:cm-") for k in keys)
    assert len(rows) == 8  # the union was cut, not just each list


async def test_a_rejected_name_is_dropped_from_any_source(t):
    store(t, entry(), [listed("Memo Writer")])
    t.site.search_answer = results(("cm-1", "MEMO WRITER"), ("cm-2", "Other Thing About Memos"))
    t.state.remember_name("memo writer")
    add_need(t)
    await t.tune()
    assert set(t.scorer.requests[0].questions) == {"r:cm-2"}
    assert sorted(e["source_slug"] for e in t.events("dropped")) == sorted(["cm-1", MEMO_ID])


async def test_a_casemark_outage_pauses_casemark_and_github_goes_on(t):
    store(t, entry(), [listed()])
    files(t)
    t.site.status = 429
    t.scorer.scores = {MEMO_ID: 0.95}
    add_need(t)
    t.model.answers = [CONVERTED]
    got = await t.tune()
    assert got["suggested"] == 1 and t.store.list()[0].source_slug == MEMO_ID
    assert t.state.source_paused("casemark", TODAY) and not t.state.source_paused("casemark", "2027-01-16")
    assert not t.state.source_paused("github", TODAY) and not t.state.paused(TODAY)
    assert t.tuner.status()["error"] == "refused" and t.state.last_error == "refused"
    assert t.events("pass_end")[0]["error"] == "refused"
    assert t.tuner.status()["paused"] is False  # GitHub can still be searched


async def test_a_github_wide_failure_pauses_github_only(t):
    t.owned.append(entry())  # never read: the pass reads it first
    t.fake.raises = httpx.ConnectError("down")
    add_need(t)
    got = await t.tune()
    assert t.state.source_paused("github", TODAY) and not t.state.source_paused("casemark", TODAY)
    assert not t.state.paused(TODAY) and t.state.last_error == "network"
    assert got["searches"] == 1  # CaseMark was still searched
    [read] = t.events("repo_read")
    assert read["error"] == "network" and read["id"] == "0123456789abcdef"
    assert t.tuner.waiting() == 0  # the stale repository is not retried while GitHub is paused


async def test_one_repositorys_failure_is_its_own(t):
    store(t, entry(id="aaaaaaaaaaaaaaaa", owner="x", repo="y"), [listed("Old Memo", "skills/old")], commit=OLD,
          read_at=iso(t.now - LONG_AGO))
    store(t, entry(), [listed()])  # fresh
    files(t)
    t.scorer.scores = {MEMO_ID: 0.95}
    add_need(t)
    t.model.answers = [CONVERTED]
    got = await t.tune()
    assert got["suggested"] == 1
    kept = t.repos.get("aaaaaaaaaaaaaaaa")
    assert kept.error == "not_found" and [k.name for k in kept.skills] == ["Old Memo"] and kept.commit == OLD
    assert not t.state.source_paused("github", TODAY) and t.state.last_error == ""
    # tried once a day: the failing repository is not asked again on the next pass of the same day
    n = len(requests_to(t, "x/y"))
    t.state.add_need("other", "legal brief drafting")
    await t.tune()
    assert len(requests_to(t, "x/y")) == n


async def test_one_stale_repository_is_refreshed_per_pass(t):
    for e in (entry(id="aaaaaaaaaaaaaaaa"), entry(id="bbbbbbbbbbbbbbbb", owner="p", repo="q")):
        store(t, e, [listed()], commit=OLD, read_at=iso(t.now - LONG_AGO))
    t.fake.repos["p/q"], t.fake.commits["p/q/main"] = t.fake.repos["o/r"], t.fake.commits["o/r/main"]
    files(t)
    files(t, "p", "q")
    add_need(t)
    await t.tune()
    assert [bool(requests_to(t, r)) for r in ("o/r", "p/q")].count(True) == 1
    add_need(t, "second need words")
    await t.tune()
    assert requests_to(t, "o/r") and requests_to(t, "p/q")  # the other one the next pass


async def test_the_owner_returning_during_a_refresh_cancels_it_and_keeps_the_old_list(t):
    store(t, entry(), [listed("Old Memo", "skills/old")], commit=OLD, read_at=iso(t.now - LONG_AGO))
    files(t)
    t.fake.hold = asyncio.Event()
    add_need(t)
    run = asyncio.ensure_future(t.tune())
    await wait_for_tree(t)
    t.busy = True
    got = await asyncio.wait_for(run, 2.0)
    kept = t.repos.get("0123456789abcdef")
    assert [k.name for k in kept.skills] == ["Old Memo"] and kept.commit == OLD
    assert t.gs.reading == set() and t.events("pass_end")[0]["interrupted"] is True and got["suggested"] == 0
    assert not t.state.source_paused("github", TODAY)


async def test_a_refresh_that_changed_a_list_makes_needs_due_for_github(t):
    t.owned.append(entry())
    files(t)
    add_need(t)
    t.state.mark_searched(WORDS, TODAY, "none", "github")  # GitHub was searched today, with nothing to search in
    t.state.mark_searched(WORDS, TODAY, "none", "casemark")
    add_need(t, "second need words")  # a due need keeps the pass worth running
    await t.tune()
    assert t.gs.changed is False
    assert len([e for e in t.events("search") if e["source"] == "github"]) == 2  # the read reset the first one's mark


async def test_a_skill_with_extras_is_suggested_with_left_out(t):
    store(t, entry(), [listed(extra=True)])
    files(t, extras=["skills/memo/scripts/run.py"])
    t.scorer.scores = {MEMO_ID: 0.95}
    add_need(t)
    t.model.answers = [CONVERTED]
    await t.tune()
    assert t.store.list()[0].left_out is True


async def test_a_github_winner_whose_read_is_interrupted_is_given_back_to_both_sources(t):
    store(t, entry(), [listed()])
    files(t)
    t.scorer.scores = {MEMO_ID: 0.95}
    add_need(t)
    t.fake.hold = asyncio.Event()  # the read's tree call waits
    run = asyncio.ensure_future(t.tune())
    await wait_for_tree(t)
    t.busy = True
    await asyncio.wait_for(run, 2.0)
    assert t.state.due_for(the_need(t), TODAY) == ["casemark", "github"] and t.state.known == set()


async def test_a_winner_from_a_repository_that_no_longer_has_it_is_dropped_as_not_found(t):
    store(t, entry(), [listed()])
    t.scorer.scores = {MEMO_ID: 0.95}  # no tree or file in the fake: the read answers 404
    add_need(t)
    got = await t.tune()
    assert got["dropped"] == 1 and t.events("dropped")[0]["reason"] == "not_found"
    assert not t.state.source_paused("github", TODAY)


async def test_a_github_licence_not_accepted_is_dropped(t):
    store(t, entry(licence=""), [listed()])
    files(t)
    t.scorer.scores = {MEMO_ID: 0.95}
    add_need(t)
    await t.tune()
    assert t.events("dropped")[0]["reason"] == "licence" and t.store.count() == 0


async def test_status_is_paused_only_when_no_searchable_source_is_open(t):
    assert t.tuner.status()["paused"] is False
    t.state.pause_source("casemark", "2027-01-16")
    assert t.tuner.status()["paused"] is True  # GitHub has nothing stored: not searchable
    store(t, entry(), [listed()])
    assert t.tuner.status()["paused"] is False
    t.state.pause_source("github", "2027-01-16")
    assert t.tuner.status()["paused"] is True


async def test_no_ledger_entry_holds_need_words_or_skill_names_on_the_github_path(t):
    store(t, entry(), [listed("Secretive Memo Name", desc="Quartz hippo wrote this description.")])
    files(t, text=MEMO.replace("Memo Writer", "Secretive Memo Name"))
    t.scorer.scores = {MEMO_ID: 0.95}
    t.conv("Draft an investment memo for the Acme deal")
    t.model.answers = [needs((TOPIC, WORDS)), CONVERTED]
    await t.tune()
    assert t.store.count() == 1
    blob = json.dumps([e.data for e in t.entries()])
    for text in (TOPIC, WORDS, "Secretive Memo Name", "Quartz hippo", "Acme deal", "Steps."):
        assert text not in blob
    assert all(e["source"] in ("casemark", "github") for e in t.events("search"))


async def test_the_error_names_its_source_and_a_clean_pass_elsewhere_keeps_it_while_the_pause_holds(t):
    store(t, entry(), [listed()])
    t.site.status = 429
    add_need(t)
    await t.tune()
    assert t.tuner.status()["error"] == "refused" and t.tuner.status()["error_source"] == "casemark"
    t.site.status = 200
    add_need(t, "second need words")  # GitHub-only work: CaseMark is still paused
    await t.tune()
    assert t.state.last_error == "refused" and t.state.error_source == "casemark"  # not cleared
    t.today = "2027-01-16"  # the pause has ended: a clean pass clears it
    add_need(t, "third need words")
    await t.tune()
    assert t.state.last_error == "" and t.tuner.status()["error_source"] == ""


async def test_a_github_outage_is_marked_github_and_model_failures_have_no_source(t):
    t.owned.append(entry())
    t.fake.raises = httpx.ConnectError("down")
    add_need(t)
    await t.tune()
    assert (t.state.last_error, t.state.error_source) == ("network", "github")
    t.state.set_error("decider")
    assert t.state.error_source == ""


def test_an_older_state_file_loads_with_no_error_source(tmp_path):
    from src.skilltuner.settings import TunerState
    path = tmp_path / "s.json"
    st = TunerState(path)
    st.set_error("refused", "casemark")
    d = json.loads(path.read_text("utf-8"))
    del d["last_error_source"]
    path.write_text(json.dumps(d), encoding="utf-8")
    assert TunerState(path).error_source == ""
    d["last_error_source"] = 3
    path.write_text(json.dumps(d), encoding="utf-8")
    assert TunerState(path).last_error == ""  # a wrongly typed file is set aside, as any other


async def test_status_reads_each_stored_list_once_while_nothing_changes(t, monkeypatch):
    store(t, entry(), [listed()])
    store(t, entry(id="fedcba9876543210", owner="p", repo="q"), [listed("Other", "skills/o")])
    import src.skilltuner.repos as repos_mod
    real, count = repos_mod.json.loads, []
    monkeypatch.setattr(repos_mod.json, "loads", lambda *a, **k: (count.append(1), real(*a, **k))[1])
    t.tuner.status()
    first = len(count)
    t.tuner.status()
    t.tuner.waiting()
    assert first == 2 and len(count) == first
    t.repos.set_error("0123456789abcdef", "network")  # a change is read again
    t.tuner.status()
    assert len(count) == first + 1


async def test_casemark_budget_ending_during_a_read_gives_its_winner_back_and_github_winners_go_on(t):
    store(t, entry(), [listed()])
    files(t)
    t.state.add_need(TOPIC, WORDS)
    t.state.add_need(TOPIC, WORDS)  # searched first: CaseMark only wins here
    add_need(t, "memo writer deal")
    for _ in range(38):
        t.state.spend(TODAY)
    # need 1 (WORDS): CaseMark result WIN wins; need 2: only GitHub answers with a winner (CaseMark has no budget left)
    t.site.search_answer = results((WIN, "Drafting Investment Memos"))
    t.scorer.scores = {WIN: 0.9, MEMO_ID: 0.0}
    original = t.tuner._fit

    async def fit(words, fresh, go, guarded):
        if words == "memo writer deal":
            t.scorer.scores = {MEMO_ID: 0.95}
        return await original(words, fresh, go, guarded)

    t.tuner._fit = fit
    t.model.answers = [CONVERTED]
    got = await t.tune()
    assert got["suggested"] == 1 and t.store.list()[0].source_slug == MEMO_ID
    assert WIN not in t.state.known and not t.state.paused(TODAY)
    assert "casemark" in t.state.due_for(next(n for n in t.state._needs.values() if n.words == WORDS), "2027-01-16")


async def test_a_github_wide_failure_during_a_winners_read_pauses_github_and_gives_the_winner_back(t):
    store(t, entry(), [listed()])
    files(t)
    t.scorer.scores = {MEMO_ID: 0.95}
    add_need(t)

    async def down(request):
        raise httpx.ConnectError("down")

    t.fake.client = httpx.AsyncClient(transport=httpx.MockTransport(down))
    got = await t.tune()
    assert got["suggested"] == 0 and t.state.source_paused("github", TODAY) and not t.state.paused(TODAY)
    assert (t.state.last_error, t.state.error_source) == ("network", "github")
    assert t.state.due_for(the_need(t), "2027-01-16") == ["casemark", "github"] and t.state.known == set()
    assert t.store.count() == 0


async def test_an_interrupted_refresh_is_tried_again_the_next_pass(t):
    store(t, entry(), [listed("Old Memo", "skills/old")], commit=OLD, read_at=iso(t.now - LONG_AGO))
    files(t)
    t.fake.hold = asyncio.Event()
    add_need(t)
    run = asyncio.ensure_future(t.tune())
    await wait_for_tree(t)
    t.busy = True
    await asyncio.wait_for(run, 2.0)
    t.busy = False
    t.fake.hold = None
    await t.tune()
    assert [s.name for s in t.repos.get("0123456789abcdef").skills] == ["Memo Writer"]  # read on the second pass


async def test_a_pending_suggestions_name_is_compared_stripped_and_case_folded(t):
    from src.skilltuner.suggestions import Suggestion
    from src.skilltuner.convert import BuiltSkill
    store(t, entry(), [listed("memo writer")])
    t.store.add(Suggestion("a" * 32, "x-y", "gh:p/q:z", "N", "u", "t", 0.9, "MIT", "https://x", "o", "c", "2027-01-15T00:00:00Z",
                           source_name="  MEMO Writer "), BuiltSkill("x-y", {"SKILL.md": "x"}))
    add_need(t)
    await t.tune()
    assert t.scorer.requests == [] and t.events("dropped")[0]["reason"] == "duplicate"


async def test_the_excluded_entry_names_its_source(t):
    from src.memory.exclusion import exclusion
    store(t, entry(), [listed()])
    t.state.add_need(TOPIC, "memo zz-late-secret-55")
    exclusion.register_secret_value("zz-late-secret-55")
    await t.tune()
    got = [e["source"] for e in t.events("search") if e["error"] == "excluded"]
    assert sorted(got) == ["casemark", "github"]

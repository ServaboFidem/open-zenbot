import json
from pathlib import Path

from src.memory.ledger import Kind, ledger_path_for
from src.skilltuner.settings import DAILY_REQUESTS, RESEARCH_AFTER_DAYS, TunerSettings, TunerState
from src.vault_settings import settings_dir


def _state(tmp_path: Path) -> tuple[Path, TunerState]:
    vault = tmp_path / "vault"
    return vault, TunerState(ledger_path_for(vault) / "skilltuner.json")


def test_kind_and_constants():
    assert Kind.skilltuner == "skilltuner"
    assert (DAILY_REQUESTS, RESEARCH_AFTER_DAYS) == (40, 30)


def test_settings_defaults_round_trip_and_place(tmp_path):
    vault = tmp_path / "vault"
    assert TunerSettings.load(vault).enabled is False
    TunerSettings(enabled=True).save(vault)
    assert TunerSettings.load(vault).enabled is True
    assert (settings_dir(vault) / "skilltuner.json").is_file()
    assert settings_dir(vault).name == "vault-settings"


def test_settings_damaged_reads_defaults(tmp_path):
    vault = tmp_path / "vault"
    p = settings_dir(vault) / "skilltuner.json"
    p.parent.mkdir(parents=True)
    p.write_text("{nope", encoding="utf-8")
    assert TunerSettings.load(vault).enabled is False
    p.write_text('{"enabled": "yes"}', encoding="utf-8")
    assert TunerSettings.load(vault).enabled is False


def test_state_beside_vault_and_persists(tmp_path):
    vault, st = _state(tmp_path)
    st.set_read("c1", 4)
    assert (tmp_path / "vault-ledger" / "skilltuner.json").is_file()
    st2 = TunerState(st.path)
    assert st2.read("c1") == 4 and st2.read("zz") == 0


def test_damaged_state_kept_aside(tmp_path):
    vault, st = _state(tmp_path)
    st.path.parent.mkdir(parents=True)
    st.path.write_text("garbage", encoding="utf-8")
    st2 = TunerState(st.path)
    assert st2.read("c") == 0
    assert (st.path.parent / "skilltuner.json.bad").read_text() == "garbage"
    st2.set_read("c", 1)
    assert TunerState(st.path).read("c") == 1


def test_state_that_is_not_utf8_is_kept_aside(tmp_path):
    vault, st = _state(tmp_path)
    st.path.parent.mkdir(parents=True)
    st.path.write_bytes(b'{"marks": "\xff\xfe"}')
    st2 = TunerState(st.path)
    assert st2.read("c") == 0
    assert (st.path.parent / "skilltuner.json.bad").read_bytes() == b'{"marks": "\xff\xfe"}'
    st2.set_read("c", 1)
    assert TunerState(st.path).read("c") == 1


def test_spend_stops_at_limit_and_resets(tmp_path):
    _, st = _state(tmp_path)
    for _ in range(40):
        assert st.spend("2026-11-25") is True
    assert st.spend("2026-11-25") is False
    assert st.spent("2026-11-25") == 40
    assert st.spend("2026-11-26") is True
    assert st.spent("2026-11-26") == 1
    assert TunerState(st.path).spent("2026-11-26") == 1


def test_needs_one_per_case_and_due_order(tmp_path):
    _, st = _state(tmp_path)
    st.add_need("t", "Alpha Beta")
    st.add_need("t", "alpha beta")
    st.add_need("t", "gamma")
    st.add_need("t", "gamma")
    st.add_need("t", "gamma")
    due = st.due_needs("2026-11-25", 10)
    assert [n.words for n in due] == ["gamma", "Alpha Beta"]
    assert [n.seen for n in due] == [3, 2]
    assert len(st.due_needs("2026-11-25", 1)) == 1


def test_due_needs_thirty_days(tmp_path):
    _, st = _state(tmp_path)
    st.add_need("t", "gamma")
    st.mark_searched("GAMMA", "2026-11-01", "none")
    assert st.due_needs("2026-11-30", 5) == []
    assert st.searched("2026-11-01") == 0  # marking is not sending: searches are counted by add_search
    assert st.due_needs("2026-12-01", 5)[0].outcome == "none"
    assert TunerState(st.path).due_needs("2026-12-01", 5)


def test_an_undecided_need_is_due_again_the_next_day(tmp_path):
    _, st = _state(tmp_path)
    st.add_need("t", "gamma")
    st.mark_searched("gamma", "2026-11-01", "undecided")
    assert st.due_needs("2026-11-01", 5) == []
    assert [n.words for n in st.due_needs("2026-11-02", 5)] == ["gamma"]


def test_searches_and_batches_are_counted_per_day_and_kept(tmp_path):
    _, st = _state(tmp_path)
    st.add_search("2026-11-25")
    st.add_search("2026-11-25")
    st.add_batch("2026-11-25")
    st.add_need("t", "gamma")
    st.mark_searched("gamma", "", "")  # a need given back
    again = TunerState(st.path)
    assert again.searched("2026-11-25") == 2 and again.batches_read("2026-11-25") == 1
    assert again.searched("2026-11-26") == 0 and again.batches_read("2026-11-26") == 0


def test_a_file_from_before_the_day_counts_loads(tmp_path):
    _, st = _state(tmp_path)
    st.add_need("t", "gamma")
    d = json.loads(st.path.read_text(encoding="utf-8"))
    del d["searches"], d["batches"]
    st.path.write_text(json.dumps(d), encoding="utf-8")
    again = TunerState(st.path)
    assert again.due_needs("2026-11-25", 5) and again.searched("2026-11-25") == 0
    assert not st.path.with_name(st.path.name + ".bad").exists()


def test_converted_pause_known_and_last(tmp_path):
    _, st = _state(tmp_path)
    assert st.converted("2026-11-25") == 0
    st.add_converted("2026-11-25")
    assert st.converted("2026-11-25") == 1 and st.converted("2026-11-26") == 0
    assert not st.paused("2026-11-25")
    st.pause_until("2026-11-27")
    assert st.paused("2026-11-26") and not st.paused("2026-11-27")
    st.remember("s1", "suggested")
    st.remember("s2", "rejected")
    st.last_pass_at = "x"
    st.last_error = "boom"
    st2 = TunerState(st.path)
    assert st2.known == {"s1", "s2"}
    assert (st2.last_pass_at, st2.last_error) == ("x", "boom")
    assert st2.paused("2026-11-26")
    try:
        st.remember("s3", "bogus")
    except ValueError:
        pass
    else:
        raise AssertionError


def test_settings_keep_repos(tmp_path):
    from src.skilltuner.repos import RepoEntry
    vault = tmp_path / "vault"
    repos = [RepoEntry("0123456789abcdef", "o", "r", "main", "skills", "data/i.json", "MIT")]
    TunerSettings(True, repos).save(vault)
    assert TunerSettings.load(vault).repos == repos


def test_settings_without_repos_load_with_none(tmp_path):
    vault = tmp_path / "vault"
    p = settings_dir(vault) / "skilltuner.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"enabled": true}', encoding="utf-8")
    s = TunerSettings.load(vault)
    assert s.enabled is True and s.repos == []


def test_a_bad_repo_entry_is_skipped_not_the_file(tmp_path):
    vault = tmp_path / "vault"
    good = {"id": "0123456789abcdef", "owner": "o", "repo": "r", "ref": "", "folder": "", "index": "", "licence": "MIT"}
    p = settings_dir(vault) / "skilltuner.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"enabled": True, "repos": [
        good, {**good, "id": "1123456789abcdef", "licence": "GPL-3.0"},
        {k: v for k, v in good.items() if k != "owner"}, "nope"]}), encoding="utf-8")
    s = TunerSettings.load(vault)
    assert s.enabled is True and [r.id for r in s.repos] == ["0123456789abcdef"]


# --- several sources ---------------------------------------------------------

def _older_file(st_path: Path) -> None:
    st_path.parent.mkdir(parents=True, exist_ok=True)
    st_path.write_text(json.dumps({
        "marks": {}, "needs": {"a b": {"words": "a b", "topic": "t", "seen": 2,
                                       "searched_on": "2027-01-10", "outcome": "none"}},
        "spent": {}, "converted": {}, "known": {}, "paused_until": "", "last_pass_at": "",
        "last_error": ""}), encoding="utf-8")


def test_older_file_is_due_for_github_and_keeps_casemark_mark(tmp_path):
    from src.skilltuner.settings import GH_API_PER_DAY, GH_FILES_PER_DAY
    assert (GH_API_PER_DAY, GH_FILES_PER_DAY) == (30, 400)
    vault = tmp_path / "vault"
    p = ledger_path_for(vault) / "skilltuner.json"
    _older_file(p)
    st = TunerState(p)
    n = st.due_needs("2027-01-11", 8, sources=("github",))[0]
    assert n.github_on == "" and n.github_outcome == "" and n.searched_on == "2027-01-10"
    assert st.due_needs("2027-01-11", 8) == []  # CaseMark searched yesterday
    assert st.due_for(n, "2027-01-11") == ["github"]
    assert [x.words for x in st.due_needs("2027-01-11", 8, sources=("casemark", "github"))] == ["a b"]
    assert st.github_spent("2027-01-11") == {"api": 0, "file": 0}
    assert not st.source_paused("github", "2027-01-11") and st.rejected_names == set()


def test_mark_searched_per_source_and_reset_github(tmp_path):
    _, st = _state(tmp_path)
    st.add_need("t", "a b")
    st.mark_searched("a b", "2027-01-10", "none", source="github")
    assert st.due_for(st.due_needs("2027-01-10", 8)[0], "2027-01-10") == ["casemark"]
    st.mark_searched("a b", "2027-01-10", "suggested")
    assert st.due_needs("2027-01-11", 8, sources=("casemark", "github")) == []
    st.mark_searched("a b", "2027-01-10", "undecided", source="github")
    assert [n.words for n in st.due_needs("2027-01-11", 8, sources=("github",))] == ["a b"]
    assert st.due_needs("2027-01-11", 8) == []
    st.mark_searched("a b", "2027-01-10", "none", source="github")
    assert st.due_needs("2027-02-08", 8, sources=("github",)) == []
    assert [n.words for n in st.due_needs("2027-02-09", 8, sources=("github",))] == ["a b"]
    st.reset_github()
    n = st.due_needs("2027-01-10", 8, sources=("github",))[0]
    assert n.github_on == "" and n.searched_on == "2027-01-10" and n.outcome == "suggested"
    st2 = TunerState(st.path)  # the marks survive a reload
    assert st2.due_needs("2027-01-10", 8, sources=("github",))[0].searched_on == "2027-01-10"


def test_github_budgets_run_separately_and_reset_by_date(tmp_path):
    import pytest
    _, st = _state(tmp_path)
    assert all(st.spend_github("d1", "api") for _ in range(30)) and not st.spend_github("d1", "api")
    assert st.spend_github("d1", "file")
    assert st.github_spent("d1") == {"api": 30, "file": 1}
    assert all(st.spend_github("d1", "file") for _ in range(399)) and not st.spend_github("d1", "file")
    assert st.github_spent("d2") == {"api": 0, "file": 0}
    assert st.spend_github("d2", "api") and st.github_spent("d2") == {"api": 1, "file": 0}
    assert st.spend("d1") and st.spent("d1") == 1  # CaseMark's budget is its own
    with pytest.raises(ValueError):
        st.spend_github("d2", "other")
    assert TunerState(st.path).github_spent("d2") == {"api": 1, "file": 0}


def test_source_pause_ends_on_the_day_and_is_not_the_global_pause(tmp_path):
    _, st = _state(tmp_path)
    st.pause_source("github", "2027-01-11")
    assert st.source_paused("github", "2027-01-10") and not st.source_paused("github", "2027-01-11")
    assert not st.source_paused("casemark", "2027-01-10") and not st.paused("2027-01-10")
    assert TunerState(st.path).source_paused("github", "2027-01-10")
    st.pause_until("2027-01-12")
    assert st.paused("2027-01-10") and not st.source_paused("casemark", "2027-01-10")


def test_rejected_names_casefolded_capped_and_persist(tmp_path):
    _, st = _state(tmp_path)
    st.remember_name("  PDF Maker ")
    st.remember_name("   ")
    assert st.rejected_names == {"pdf maker"}
    assert TunerState(st.path).rejected_names == {"pdf maker"}
    for i in range(1005):
        st.remember_name(f"n{i}")
    names = st.rejected_names
    assert len(names) == 1000 and "n1004" in names and "n4" not in names and "n5" in names


def test_malformed_new_field_is_damaged(tmp_path):
    vault = tmp_path / "vault"
    p = ledger_path_for(vault) / "skilltuner.json"
    _older_file(p)
    d = json.loads(p.read_text(encoding="utf-8"))
    d["github_spent"] = {"d": {"api": "x"}}
    p.write_text(json.dumps(d), encoding="utf-8")
    st = TunerState(p)
    assert p.with_name("skilltuner.json.bad").exists() and st.due_needs("2027-01-11", 8, sources=("github",)) == []


def test_approved_names_are_kept_apart_casefolded_and_persist(tmp_path):
    st = TunerState(tmp_path / "s.json")
    st.remember_name("  PDF Maker ", "approved")
    st.remember_name("Other")
    assert st.approved_names == {"pdf maker"} and st.rejected_names == {"other"}
    assert TunerState(st.path).approved_names == {"pdf maker"}

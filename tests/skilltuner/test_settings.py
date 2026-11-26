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
    assert st.searched("2026-11-01") == 1
    assert st.due_needs("2026-12-01", 5)[0].outcome == "none"
    assert TunerState(st.path).due_needs("2026-12-01", 5)


def test_converted_pause_known_and_last(tmp_path):
    _, st = _state(tmp_path)
    assert st.converted("2026-11-25") == 0
    st.add_converted("2026-11-25")
    assert st.converted("2026-11-25") == 1 and st.converted("2026-11-26") == 0
    assert not st.paused("2026-11-25")
    st.pause_until("2026-11-27")
    assert st.paused("2026-11-27") and not st.paused("2026-11-28")
    st.remember("s1", "suggested")
    st.remember("s2", "rejected")
    st.last_pass_at = "x"
    st.last_error = "boom"
    st2 = TunerState(st.path)
    assert st2.known == {"s1", "s2"}
    assert (st2.last_pass_at, st2.last_error) == ("x", "boom")
    assert st2.paused("2026-11-27")
    try:
        st.remember("s3", "bogus")
    except ValueError:
        pass
    else:
        raise AssertionError

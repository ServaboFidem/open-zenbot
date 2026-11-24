from datetime import datetime

import pytest

from src.briefing.settings import (Briefing, BriefingState, BriefingStore, Slot, ToldJokes, check,
                                   next_slot)
from src.vault_settings import settings_dir


def make(slots=..., **kw) -> Briefing:
    base = dict(kind="morning", enabled=True, slots=[Slot([0, 1, 2, 3, 4], "06:45")] if slots is ... else slots,
                speakers=None, volume=25, id="abc")
    base.update(kw)
    return Briefing(**base)


def test_overlapping_days_refused():
    b = make([Slot([0, 1], "06:45"), Slot([1, 5], "08:30")])
    with pytest.raises(ValueError) as e:
        check(b, 60)
    assert str(e.value) == "Each day can have only one time."


def test_other_refusals():
    for b in (make(slots=[]), make([Slot([0], "06:45")] * 8), make([Slot([], "06:45")]),
              make([Slot([0], "24:00")]), make([Slot([0], "6:45")]), make(speakers=[]),
              make(speakers=[str(i) for i in range(33)]), make(kind="evening")):
        with pytest.raises(ValueError):
            check(b, 60)
    check(make(), 60)


def test_next_slot_picks_the_earliest_line():
    b = make([Slot([0, 1, 2, 3, 4], "06:45"), Slot([5, 6], "08:30")])
    friday = datetime(2026, 11, 20, 7, 0).astimezone()
    got = next_slot(b, friday)
    assert (got.year, got.month, got.day, got.hour, got.minute) == (2026, 11, 21, 8, 30)


def test_volume_above_ceiling_refused():
    for v in (0, 61):
        with pytest.raises(ValueError):
            check(make(volume=v), 60)
    check(make(volume=60), 60)


def test_broken_file_reads_empty_with_error(tmp_path):
    vault = tmp_path / "vault"
    d = settings_dir(vault)
    d.mkdir(parents=True)
    (d / "briefings.json").write_text("{not json", encoding="utf-8")
    (d / "briefing_state.json").write_text("[", encoding="utf-8")
    (d / "briefing_jokes.json").write_text("[", encoding="utf-8")
    s = BriefingStore.load(vault)
    assert s.items == {} and s.error
    assert BriefingState.load(vault).claimed("morning") is None
    assert ToldJokes.load(vault).recent() == []


def test_jokes_capped_and_normalised(tmp_path):
    j = ToldJokes.load(tmp_path / "vault")
    j.add("Why did the chicken cross?", "2026-11-01")
    assert j.seen("why did the chicken cross ...!")
    assert not j.seen("another")
    for i in range(600):
        j.add(f"joke {i}", "2026-11-02")
    assert len(j.recent(1000)) == 500
    assert j.recent(3)[0] == "joke 599"
    assert len(j.recent()) == 100


def test_files_live_in_vault_settings(tmp_path):
    vault = tmp_path / "vault"
    store = BriefingStore.load(vault)
    store.put(make())
    store.save(vault)
    st = BriefingState.load(vault)
    st.claim("morning", "2026-11-21T08:30:00-05:00")
    st.record_run("morning", {"at": "x", "outcome": "ok"})
    st.record_skip("morning", {"at": "y", "outcome": "skipped_late"})
    st.save(vault)
    j = ToldJokes.load(vault)
    j.add("a joke", "2026-11-21")
    j.save(vault)
    d = settings_dir(vault)
    assert {p.name for p in d.iterdir()} == {"briefings.json", "briefing_state.json", "briefing_jokes.json"}
    assert not vault.exists() or not any(vault.iterdir())
    again = BriefingState.load(vault)
    assert again.claimed("morning") == "2026-11-21T08:30:00-05:00"
    assert again.last_run("morning")["outcome"] == "ok"
    assert again.last_skip("morning")["outcome"] == "skipped_late"
    assert BriefingStore.load(vault).items["morning"].slots[0].time == "06:45"
    assert ToldJokes.load(vault).seen("a joke")
    assert store.delete("morning") and not store.delete("morning")

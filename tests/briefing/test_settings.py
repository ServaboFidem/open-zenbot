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


def _entry(**kw):
    d = {"id": "a", "kind": "morning", "enabled": True,
         "slots": [{"days": [0, 1], "time": "06:45"}], "speakers": None, "volume": 25}
    d.update(kw)
    return d


def _write_file(vault, doc):
    import json
    d = settings_dir(vault)
    d.mkdir(parents=True, exist_ok=True)
    (d / "briefings.json").write_text(json.dumps(doc) if not isinstance(doc, str) else doc, encoding="utf-8")
    return d / "briefings.json"


def test_broken_file_survives_save(tmp_path):
    vault = tmp_path / "vault"
    path = _write_file(vault, "{not json")
    s = BriefingStore.load(vault)
    s.put(make())
    with pytest.raises(Exception):
        s.save(vault)
    assert path.read_text(encoding="utf-8") == "{not json"
    backups = list(path.parent.glob("briefings.json.bad-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "{not json"


def test_one_bad_entry_keeps_the_good_and_survives_save(tmp_path):
    import json
    vault = tmp_path / "vault"
    bad = {"kind": "evening", "junk": 1}
    path = _write_file(vault, {"briefings": [bad, _entry()]})
    s = BriefingStore.load(vault)
    assert list(s.items) == ["morning"] and s.error
    s.save(vault)
    saved = json.loads(path.read_text(encoding="utf-8"))["briefings"]
    assert bad in saved and any(x.get("kind") == "morning" for x in saved)
    assert BriefingStore.load(vault).items["morning"].volume == 25


@pytest.mark.parametrize("change", [
    {"volume": True}, {"volume": 1.9}, {"volume": "25"}, {"volume": 0}, {"volume": 101},
    {"enabled": "false"}, {"slots": []}, {"slots": "x"},
    {"slots": [{"days": "12", "time": "06:45"}]},
    {"slots": [{"days": [7], "time": "06:45"}]},
    {"slots": [{"days": [True], "time": "06:45"}]},
    {"slots": [{"days": [], "time": "06:45"}]},
    {"slots": [{"days": [0], "time": "abc"}]},
    {"slots": [{"days": [0, 1], "time": "06:45"}, {"days": [1], "time": "07:00"}]},
    {"speakers": []}, {"speakers": ""}, {"speakers": [""]}, {"speakers": [1]},
    {"speakers": ["a"] * 2}, {"speakers": [str(i) for i in range(33)]},
    {"kind": "evening"}, {"id": ""},
])
def test_hostile_entries_are_bad(tmp_path, change):
    vault = tmp_path / "vault"
    _write_file(vault, {"briefings": [_entry(**change)]})
    s = BriefingStore.load(vault)
    assert s.items == {} and s.error


def test_check_gives_sentences_not_type_errors():
    with pytest.raises(ValueError):
        check(make(slots="x"), 60)
    with pytest.raises(ValueError):
        check(make([Slot("12", "06:45")]), 60)
    with pytest.raises(ValueError):
        check(make(speakers=["a", ""]), 60)
    with pytest.raises(ValueError):
        check(make(speakers=["Den", "den"]), 60)


def test_state_last_run_must_be_a_dict(tmp_path):
    import json
    vault = tmp_path / "vault"
    d = settings_dir(vault)
    d.mkdir(parents=True)
    (d / "briefing_state.json").write_text(
        json.dumps({"morning": {"last_run": "x", "last_skip": 3}}), encoding="utf-8")
    st = BriefingState.load(vault)
    assert st.last_run("morning") is None and st.last_skip("morning") is None


def test_jokes_empty_normalised_and_long_text(tmp_path):
    j = ToldJokes.load(tmp_path / "vault")
    j.add("!!! ...", "2026-11-01")
    assert j.recent() == [] and not j.seen("???")
    j.add("x" * 900, "2026-11-01")
    assert len(j.recent(1)[0]) == 500
    assert j.seen("x" * 900)


def test_save_on_read_only_store_raises(tmp_path):
    from src.briefing.settings import StoreReadOnly
    vault = tmp_path / "vault"
    _write_file(vault, "{not json")
    s = BriefingStore.load(vault)
    s.put(make())
    with pytest.raises(StoreReadOnly) as e:
        s.save(vault)
    assert str(e.value) == s.error and s.error


def test_delete_does_not_resurrect_a_duplicate(tmp_path):
    vault = tmp_path / "vault"
    _write_file(vault, {"briefings": [_entry(id="a"), _entry(id="b")]})
    s = BriefingStore.load(vault)
    assert s.items["morning"].id == "a" and s.error
    assert s.delete("morning")
    s.save(vault)
    again = BriefingStore.load(vault)
    assert again.items == {} and not again.error


def test_put_replaces_an_invalid_same_kind_entry(tmp_path):
    import json
    vault = tmp_path / "vault"
    path = _write_file(vault, {"briefings": [_entry(volume=True)]})
    s = BriefingStore.load(vault)
    assert s.items == {} and s.error
    s.put(make())
    s.save(vault)
    saved = json.loads(path.read_text(encoding="utf-8"))["briefings"]
    assert [x["kind"] for x in saved] == ["morning"]
    again = BriefingStore.load(vault)
    assert "morning" in again.items and not again.error


def test_bad_copy_not_repeated_and_never_overwritten(tmp_path):
    vault = tmp_path / "vault"
    path = _write_file(vault, "{not json")
    BriefingStore.load(vault)
    BriefingStore.load(vault)
    assert len(list(path.parent.glob("briefings.json.bad-*"))) == 1
    path.write_text("[also broken", encoding="utf-8")
    BriefingStore.load(vault)
    copies = list(path.parent.glob("briefings.json.bad-*"))
    assert sorted(p.read_text(encoding="utf-8") for p in copies) == ["[also broken", "{not json"]

"""The learn log (per-URL md5 and fact ids, learned names, recorded sites,
denials) and the holds store (held learn payloads keyed by approval id):
both are small JSON files under the vault that survive a restart."""

from __future__ import annotations

from pathlib import Path

from src.memory.learn_log import HoldStore, LearnEntry, LearnLog


def test_learn_log_round_trips_entries_and_learned_names(tmp_path: Path):
    log = LearnLog(tmp_path / "learn_log.json")
    log.put(LearnEntry("https://x.com/a", "m1", ["f1"], [], ["Northwind Labs", " Nexus "], "2026-11-07T00:00:00+00:00"))
    log.add_fact_id("https://x.com/a", "m1", "f2")
    log.add_fact_id("https://x.com/a", "old", "f3")  # a write from an earlier version of the page landing late: stale
    log.add_approved("https://x.com/a", ["f9"], name="Jane Doe")
    assert not log.get("https://x.com/a").done
    log.mark_done("https://x.com/a", "m1")
    again = LearnLog(tmp_path / "learn_log.json")
    e = again.get("https://x.com/a")
    assert e.md5 == "m1" and e.fact_ids == ["f1", "f2"] and e.approved_fact_ids == ["f9"] and e.done
    assert again.take_stale("https://x.com/a") == ["f3"] and again.get("https://x.com/a").stale == []
    assert again.learned() == {"northwind labs", "nexus", "jane doe"}
    assert again.get("https://x.com/b") is None


def test_learn_log_sites_and_denials_are_case_insensitive_and_persist(tmp_path: Path):
    log = LearnLog(tmp_path / "learn_log.json")
    assert not log.site_recorded("Northwind Labs", "northwindlabs.example")
    log.record_site("Northwind Labs", "northwindlabs.example")
    log.set_site_fact("Northwind Labs", "northwindlabs.example", "f7")
    log.deny("northwindlabs.example", "Jane Doe")
    again = LearnLog(tmp_path / "learn_log.json")
    assert again.site_recorded("northwind labs", "NORTHWINDLABS.example") and again.site_fact_id("NORTHWIND LABS", "northwindlabs.example") == "f7"
    again.clear_site("Northwind Labs", "northwindlabs.example")
    assert not LearnLog(tmp_path / "learn_log.json").site_recorded("Northwind Labs", "northwindlabs.example")
    assert again.is_denied("northwindlabs.example", "jane doe") and not again.is_denied("other.com", "jane doe")


def test_hold_store_put_pop_pending(tmp_path: Path):
    holds = HoldStore(tmp_path / "learn_holds.json")
    holds.put({"id": "h1", "title": "t"}, {"writes": [1]})
    holds.put({"id": "h2", "title": "u"}, {"writes": [2]})
    again = HoldStore(tmp_path / "learn_holds.json")
    assert [r["id"] for r, _ in again.pending()] == ["h1", "h2"]
    assert again.pop("h1") == ({"id": "h1", "title": "t"}, {"writes": [1]})
    assert again.pop("h1") is None and [r["id"] for r, _ in HoldStore(tmp_path / "learn_holds.json").pending()] == ["h2"]

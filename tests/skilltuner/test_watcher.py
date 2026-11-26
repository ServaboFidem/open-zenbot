import asyncio
import os
from pathlib import Path

import pytest

from src.harness.bot import Bot
from src.memory.ledger import Kind
from src.skills.catalog import Catalog
from src.skills.loader import SkillConfig, parse_skill
from src.skilltuner.watcher import POLL_S, SkillWatcher


def md(name="Alpha Skill", use="Does alpha things."):
    return f"---\nname: {name}\nuse: {use}\n---\nBody.\n"


def put(folder: Path, slug: str, text: str | None = None, mtime_ns: int | None = None) -> Path:
    d = folder / slug
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    f.write_text(text if text is not None else md(), encoding="utf-8")
    if mtime_ns is not None:
        os.utime(f, ns=(mtime_ns, mtime_ns))
    return f


class Led:
    def __init__(self):
        self.rows = []

    def append(self, kind, bot_id, session_id, **data):
        self.rows.append((kind, data))

    def events(self):
        return [(d["event"], d.get("folder", d.get("slug")), d.get("reason")) for _, d in self.rows]


class Rig:
    def __init__(self, tmp_path):
        self.folder = tmp_path / "discovered"
        self.folder.mkdir()
        self.catalog = Catalog()
        self.bots = [Bot(id="b", name="B", role="r", instructions="")]
        self.ledger = Led()
        self.calls = 0
        self.w = SkillWatcher(self.folder, self.catalog, lambda: self.bots, self.ledger, SkillConfig(), self._changed)

    def _changed(self):
        self.calls += 1


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_add_change_remove(rig):
    put(rig.folder, "alpha", mtime_ns=1_000_000_000)
    assert rig.w.scan() == {"added": 1, "changed": 0, "removed": 0, "refused": 0}
    assert rig.catalog.skill("alpha-skill").use == "Does alpha things."
    assert rig.calls == 1
    assert rig.w.scan() == {"added": 0, "changed": 0, "removed": 0, "refused": 0}
    assert rig.calls == 1
    put(rig.folder, "alpha", md(use="Does alpha thing."), mtime_ns=2_000_000_000)  # same size, new mtime
    assert rig.w.scan()["changed"] == 1
    assert rig.catalog.skill("alpha-skill").use == "Does alpha thing."
    assert rig.calls == 2
    rig.bots[0].attach_skill(rig.catalog.skill("alpha-skill"))
    (rig.folder / "alpha" / "SKILL.md").unlink()
    (rig.folder / "alpha").rmdir()
    assert rig.w.scan()["removed"] == 1
    assert rig.catalog.skill("alpha-skill") is None
    assert rig.bots[0].skills == []
    assert rig.calls == 3
    assert [e[0] for e in rig.ledger.events()] == ["loaded", "reloaded", "removed"]
    assert all(k == Kind.skilltuner for k, _ in rig.ledger.rows)


def test_symlinked_skill_file_refused(rig, tmp_path):
    real = tmp_path / "real.md"
    real.write_text(md(), encoding="utf-8")
    d = rig.folder / "alpha"
    d.mkdir()
    try:
        (d / "SKILL.md").symlink_to(real)
    except OSError:
        pytest.skip("symlinks not permitted")
    assert rig.w.scan()["refused"] == 1
    assert rig.catalog.skills() == []


def test_symlinked_folder_refused(rig, tmp_path):
    other = tmp_path / "other"
    put(other, "x")
    try:
        (rig.folder / "alpha").symlink_to(other / "x", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted")
    assert rig.w.scan()["refused"] == 1
    assert rig.catalog.skills() == []


def test_hard_linked_skill_file_refused(rig, tmp_path):
    real = tmp_path / "real.md"
    real.write_text(md(), encoding="utf-8")
    d = rig.folder / "alpha"
    d.mkdir()
    os.link(real, d / "SKILL.md")
    assert rig.w.scan()["refused"] == 1
    assert rig.catalog.skills() == []


def test_duplicate_of_shipped_is_not_loaded_and_logged(rig):
    shipped = parse_skill(md(), path=Path("shipped/SKILL.md"))
    rig.catalog.add_skill(shipped)
    put(rig.folder, "alpha")
    s = rig.w.scan()
    assert s["added"] == 0
    assert rig.catalog.skill("alpha-skill") is shipped
    assert ("dropped", "alpha", "duplicate") in rig.ledger.events()
    rig.w.scan()  # not logged again while unchanged
    assert rig.ledger.events().count(("dropped", "alpha", "duplicate")) == 1
    (rig.folder / "alpha" / "SKILL.md").unlink()
    (rig.folder / "alpha").rmdir()
    rig.w.scan()
    assert rig.catalog.skill("alpha-skill") is shipped  # never removes what it did not load


def test_reload_that_becomes_invalid_removes_earlier_copy(rig):
    put(rig.folder, "alpha", mtime_ns=1_000_000_000)
    rig.w.scan()
    rig.bots[0].attach_skill(rig.catalog.skill("alpha-skill"))
    put(rig.folder, "alpha", "not a skill", mtime_ns=2_000_000_000)
    rig.w.scan()
    assert rig.catalog.skills() == []
    assert rig.bots[0].skills == []
    assert ("dropped", "alpha", "invalid") in rig.ledger.events()
    assert rig.calls == 2


def test_reload_into_shipped_slug_does_not_overwrite_it(rig):
    put(rig.folder, "alpha", mtime_ns=1_000_000_000)
    rig.w.scan()
    shipped = parse_skill(md("Beta", "x"), path=Path("s/SKILL.md"))
    rig.catalog.add_skill(shipped)
    put(rig.folder, "alpha", md("Beta", "y"), mtime_ns=2_000_000_000)
    rig.w.scan()
    assert rig.catalog.skill("beta") is shipped
    assert rig.catalog.skill("alpha-skill") is None


def test_temp_folders_are_ignored_and_swept(rig, tmp_path):
    put(rig.folder, ".tmp-abc")
    (rig.folder / "Bad_Name").mkdir()
    (rig.folder / "file.txt").write_text("x")
    assert rig.w.scan() == {"added": 0, "changed": 0, "removed": 0, "refused": 0}
    assert rig.catalog.skills() == []
    rig.w.sweep_temp()
    assert not (rig.folder / ".tmp-abc").exists()
    assert (rig.folder / "Bad_Name").exists()


def test_sweep_does_not_follow_links(rig, tmp_path):
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "f").write_text("x")
    try:
        (rig.folder / ".tmp-link").symlink_to(keep, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted")
    rig.w.sweep_temp()
    assert (keep / "f").exists()
    assert not (rig.folder / ".tmp-link").exists()


def test_missing_folder_scans_empty(tmp_path):
    w = SkillWatcher(tmp_path / "nope", Catalog(), lambda: [], Led(), SkillConfig(), lambda: None)
    assert w.scan()["added"] == 0


async def test_run_survives_errors_and_stops(rig, monkeypatch):
    monkeypatch.setattr("src.skilltuner.watcher.POLL_S", 0.01)
    n = {"c": 0}
    real = rig.w.scan

    def flaky():
        n["c"] += 1
        if n["c"] == 1:
            raise RuntimeError("boom")
        return real()

    rig.w.scan = flaky
    stop = asyncio.Event()
    task = asyncio.create_task(rig.w.run(stop))
    await asyncio.sleep(0.15)
    stop.set()
    await asyncio.wait_for(task, 2)
    assert n["c"] >= 2
    assert any(d.get("error") == "RuntimeError" for _, d in rig.ledger.rows)


def test_poll_constant():
    assert POLL_S == 3.0


def test_concurrent_scans_load_once(rig):
    import threading
    put(rig.folder, "alpha")
    ts = [threading.Thread(target=rig.w.scan) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [e[0] for e in rig.ledger.events()] == ["loaded"]
    (rig.folder / "alpha" / "SKILL.md").unlink()
    (rig.folder / "alpha").rmdir()
    rig.w.scan()
    assert rig.catalog.skills() == []


def test_junction_folder_refused(rig, tmp_path):
    import subprocess
    import sys
    if sys.platform != "win32":
        pytest.skip("junctions are Windows only")
    other = tmp_path / "other"
    put(other, "x")
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(rig.folder / "alpha"), str(other / "x")], capture_output=True)
    if r.returncode:
        pytest.skip("cannot make a junction")
    assert rig.w.scan()["refused"] == 1
    assert rig.catalog.skills() == []


def test_startup_loaded_skill_is_adopted(rig):
    from src.skills.loader import load_dir
    put(rig.folder, "alpha")
    put(rig.folder, ".tmp-x", md("Tmp", "t"))
    for r in load_dir(rig.folder):
        rig.catalog.add_skill(r.skill)
    assert [s.slug for s in rig.catalog.skills()] == ["alpha-skill"]
    rig.bots[0].attach_skill(rig.catalog.skill("alpha-skill"))
    assert rig.w.scan() == {"added": 0, "changed": 0, "removed": 0, "refused": 0}
    assert rig.ledger.events() == []
    (rig.folder / "alpha" / "SKILL.md").unlink()
    (rig.folder / "alpha").rmdir()
    assert rig.w.scan()["removed"] == 1
    assert rig.catalog.skills() == [] and rig.bots[0].skills == []


def test_startup_loaded_hard_link_is_removed(rig, tmp_path):
    from src.skills.loader import load_dir
    real = tmp_path / "real.md"
    real.write_text(md(), encoding="utf-8")
    d = rig.folder / "alpha"
    d.mkdir()
    os.link(real, d / "SKILL.md")
    for r in load_dir(rig.folder):
        rig.catalog.add_skill(r.skill)
    rig.bots[0].attach_skill(rig.catalog.skill("alpha-skill"))
    rig.w.scan()
    assert rig.catalog.skills() == [] and rig.bots[0].skills == []
    assert ("dropped", "alpha", "refused") in rig.ledger.events()


def test_one_folder_exception_does_not_abort_scan(rig, monkeypatch):
    put(rig.folder, "alpha")
    put(rig.folder, "beta", md("Beta", "b"))
    import src.skilltuner.watcher as w
    real = w.load_skill

    def boom(path, cfg):
        if "alpha" in str(path):
            raise OSError("x")
        return real(path, cfg)

    monkeypatch.setattr(w, "load_skill", boom)
    rig.w.scan()
    assert rig.catalog.skill("beta") is not None
    assert any(d.get("error") == "OSError" and d["event"] == "dropped" for _, d in rig.ledger.rows)


def test_a_reloaded_skill_replaces_the_copy_each_bot_holds(rig):
    put(rig.folder, "alpha", md(), mtime_ns=1_000_000_000)
    rig.w.scan()
    other = Bot(id="c", name="C", role="r", instructions="")
    rig.bots.append(other)
    rig.bots[0].attach_skill(rig.catalog.skill("alpha-skill"))
    put(rig.folder, "alpha", md(use="Does it better.") + "New body.\n", mtime_ns=2_000_000_000)
    assert rig.w.scan()["changed"] == 1
    held = rig.bots[0].skills
    assert len(held) == 1 and held[0] is rig.catalog.skill("alpha-skill")
    assert held[0].use == "Does it better." and "New body." in held[0].body
    assert other.skills == []  # a bot that did not have it is not given it

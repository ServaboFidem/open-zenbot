"""Skill folders: shipped skills are read-only, the user's skills folder is always written and loaded."""

from pathlib import Path

from src.harness.config import HarnessConfig
from src.harness.main import build_harness
from src.skills.catalog import Catalog
from src.skills.loader import Reason, Skill, SkillState, load_dir
from src.vault_settings import discovered_skills_dir, user_skills_dir


def test_skills_write_dir_is_the_user_folder_with_the_switch_off(tmp_path):
    c = HarnessConfig(vault_root=tmp_path / "vault", user_content=False)
    assert c.skills_write_dir == user_skills_dir(tmp_path / "vault")


def test_discovered_dir_is_inside_the_user_skills_folder(tmp_path):
    assert discovered_skills_dir(tmp_path / "v") == tmp_path / "v-skills" / "discovered"


def test_load_dir_reads_discovered_skills_one_level_down(tmp_path):
    d = tmp_path / "v-skills" / "discovered" / "memo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: Memo\nuse: Draft a memo.\n---\nBody\n", encoding="utf-8")
    assert [r.skill.slug for r in load_dir(tmp_path / "v-skills") if r.ok] == ["memo"]


def test_replace_skill_swaps_same_slug_and_refuses_a_name_of_another():
    c = Catalog()
    c.add_skill(Skill("A", "a", "use a"))
    c.add_skill(Skill("B", "b", "use b"))
    assert c.replace_skill(Skill("A2", "a", "new")).state == SkillState.added and c.skill("a").use == "new"
    assert c.replace_skill(Skill("B", "a", "x")).reason == Reason.duplicate
    assert c.skill("a").use == "new"


def test_main_loads_the_user_skills_folder_with_the_switch_off(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    d = user_skills_dir(vault) / "x"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: Xray\nuse: Do x.\n---\nBody\n", encoding="utf-8")
    h = build_harness(HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", user_content=False))
    assert h.catalog.skill("xray") is not None


def _put(folder: Path, name: str, title: str, use: str) -> None:
    (folder / name).mkdir(parents=True, exist_ok=True)
    (folder / name / "SKILL.md").write_text(f"---\nname: {title}\nuse: {use}\n---\nBody\n", encoding="utf-8")


def _harness(tmp_path: Path, monkeypatch, bots: Path | None = None):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ZEN_MEMORY_BACKEND", "local")
    return build_harness(HarnessConfig(vault_root=tmp_path / "vault", skills_dir=tmp_path / "shipped",
                                       bot_prompts_dir=bots or tmp_path / "no-bots", user_content=True))


def _dropped(h) -> list[tuple]:
    return [(e.data.get("folder"), e.data.get("reason")) for e in h.ledger.tail(500, kind="skilltuner")
            if e.data.get("event") == "dropped"]


def test_a_shipped_skill_wins_over_a_discovered_one_of_the_same_slug_or_name(tmp_path: Path, monkeypatch):
    (tmp_path / "vault").mkdir()
    _put(tmp_path / "shipped", "memo", "Memo", "shipped")
    _put(tmp_path / "shipped", "brief", "Brief", "shipped brief")
    disc = discovered_skills_dir(tmp_path / "vault")
    _put(disc, "memo", "Memo", "discovered")  # same slug and name
    _put(disc, "other-brief", "Brief", "discovered brief")  # same name, its own slug
    _put(disc, "fresh", "Fresh", "a new one")
    h = _harness(tmp_path, monkeypatch)
    assert h.catalog.skill("memo").use == "shipped"
    assert h.catalog.skill("brief").use == "shipped brief"
    assert h.catalog.skill("other-brief") is None
    assert h.catalog.skill("fresh").use == "a new one"
    assert sorted(_dropped(h)) == [("memo", "duplicate"), ("other-brief", "duplicate")]


def test_an_owner_skill_wins_over_a_discovered_one_whatever_its_folder_sorts_as(tmp_path: Path, monkeypatch):
    (tmp_path / "vault").mkdir()
    _put(user_skills_dir(tmp_path / "vault"), "zzz-mine", "Memo", "mine")  # sorts after discovered/
    _put(discovered_skills_dir(tmp_path / "vault"), "memo", "Memo", "discovered")
    h = _harness(tmp_path, monkeypatch)
    assert h.catalog.skill("memo").use == "mine"
    assert _dropped(h) == [("memo", "duplicate")]


def test_a_bot_file_naming_a_discovered_skill_has_it_after_start(tmp_path: Path, monkeypatch):
    (tmp_path / "vault").mkdir()
    _put(discovered_skills_dir(tmp_path / "vault"), "fresh", "Fresh", "a new one")
    bots = tmp_path / "bots"
    bots.mkdir()
    (bots / "helper.md").write_text("---\nname: Helper\nrole: Helps.\nskills: [fresh]\n---\nBe helpful.\n", encoding="utf-8")
    h = _harness(tmp_path, monkeypatch, bots)
    assert [s.slug for s in h.bots["helper"].skills] == ["fresh"]
    # the watcher owns it from the start: its next scan neither reloads nor drops it
    assert h.skill_watcher.scan() == {"added": 0, "changed": 0, "removed": 0, "refused": 0}

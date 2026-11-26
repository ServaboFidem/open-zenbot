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

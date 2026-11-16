from pathlib import Path

from src.skills.catalog import Catalog, CatalogEntry
from src.skills.loader import Reason, SkillConfig, SkillState, load_dir, load_skill, parse_skill

from .conftest import REPO


def test_example_skill_loads(example_skill_dir: Path):
    r = load_skill(example_skill_dir)
    assert r.state == SkillState.added and r.ok
    s = r.skill
    assert s.name == "Research brief" and s.slug == "research-brief"
    assert s.use.startswith("When asked to research")
    assert s.access == ["shell"]
    assert len(s.steps) == 4
    assert s.returns.startswith("A Markdown brief")
    assert "shell:git push*" in s.approvals
    assert s.band_width == 0.1
    assert s.size > 0 and s.path == example_skill_dir / "SKILL.md"
    assert "Turn a question" in s.body


def test_prompt_rendering_carries_the_contract(example_skill_dir: Path):
    text = load_skill(example_skill_dir).skill.to_prompt()
    assert "## Skill: Research brief" in text
    assert "1. Pin down the question" in text
    assert "Needs approval before: shell:git push*" in text
    assert "Return: A Markdown brief" in text


def test_load_dir_finds_shipped_skills():
    results = load_dir(REPO / "skills")
    assert any(r.ok and r.skill.slug == "research-brief" for r in results)


def test_too_large_is_a_guide_not_a_constant(tmp_path: Path):
    d = tmp_path / "big"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: Big\nuse: x\n---\n" + "x" * 500)
    assert load_skill(d, SkillConfig(max_bytes=100)).reason == Reason.too_large
    assert load_skill(d, SkillConfig(max_bytes=10_000)).ok


def test_empty_unsupported_missing_invalid(tmp_path: Path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("")
    assert load_skill(d).reason == Reason.empty
    assert load_skill(tmp_path / "skill.txt").reason == Reason.unsupported
    assert load_skill(tmp_path / "missing").reason == Reason.empty
    (d / "SKILL.md").write_text("no frontmatter here")
    r = load_skill(d)
    assert r.state == SkillState.failed and r.reason == Reason.invalid
    (d / "SKILL.md").write_text("---\nname: X\n---\nbody")
    assert load_skill(d).reason == Reason.invalid  # no use line


def test_slug_defaults_from_name():
    s = parse_skill("---\nname: Weekly Update!\nuse: y\n---\n")
    assert s.slug == "weekly-update"


def test_catalog_dedups_on_slug_path_or_title(example_skill_dir: Path):
    c = Catalog()
    s = load_skill(example_skill_dir).skill
    assert c.add_skill(s).state == SkillState.added
    assert c.add_skill(s).reason == Reason.duplicate
    same_name = parse_skill("---\nname: Research brief\nslug: other\nuse: y\n---\n")
    assert c.add_skill(same_name).reason == Reason.duplicate
    other = parse_skill("---\nname: Other\nuse: y\n---\n")
    assert c.add_skill(other).state == SkillState.added
    assert {e.ref for e in c.entries()} == {"research-brief", "other"}
    assert c.entries()[0].kind == "skill" and c.entries()[0].needs == ["shell"]


def test_catalog_keeps_mcps_separate():
    c = Catalog()
    e = CatalogEntry(kind="mcp", name="Drive list", use="list files", ref="drive:list", needs=["google"], limits=["no delete"])
    assert c.add_mcp(e).state == SkillState.added
    assert c.add_mcp(e).reason == Reason.duplicate
    assert c.skills() == [] and [m.ref for m in c.mcps()] == ["drive:list"]


def test_vendored_docx_skill_loads():
    """The docx skill vendored from anthropics/skills: the harness
    frontmatter on top, Anthropic's instructions and scripts underneath."""
    r = load_skill(REPO / "skills" / "anthropic" / "docx")
    assert r.ok, (r.reason, r.detail)
    s = r.skill
    assert s.slug == "docx" and "shell" in s.access and ".docx" in s.use
    assert "merge_runs.py" in s.body and "Apache" in (REPO / "skills" / "anthropic" / "LICENSE.txt").read_text(encoding="utf-8")
    for script in ("merge_runs.py", "comment.py", "accept_changes.py", "office/validate.py", "office/soffice.py"):
        assert (REPO / "skills" / "anthropic" / "docx" / "scripts" / script).is_file(), script


def test_vendored_pptx_skill_loads():
    r = load_skill(REPO / "skills" / "anthropic" / "pptx")
    assert r.ok, (r.reason, r.detail)
    s = r.skill
    assert s.slug == "pptx" and "shell" in s.access and ".pptx" in s.use
    assert "thumbnail.py" in s.body and "Apache" in (REPO / "skills" / "anthropic" / "LICENSE.txt").read_text(encoding="utf-8")
    for script in ("add_slide.py", "clean.py", "thumbnail.py", "office/validate.py", "office/soffice.py"):
        assert (REPO / "skills" / "anthropic" / "pptx" / "scripts" / script).is_file(), script


def test_vendored_xlsx_skill_loads():
    r = load_skill(REPO / "skills" / "anthropic" / "xlsx")
    assert r.ok, (r.reason, r.detail)
    assert r.skill.slug == "xlsx" and "shell" in r.skill.access and ".xlsx" in r.skill.use
    assert "recalc.py" in r.skill.body and (REPO / "skills" / "anthropic" / "xlsx" / "scripts" / "recalc.py").is_file()


def test_vendored_pdf_skill_loads():
    r = load_skill(REPO / "skills" / "anthropic" / "pdf")
    assert r.ok, (r.reason, r.detail)
    assert r.skill.slug == "pdf" and "shell" in r.skill.access and "PDF" in r.skill.use
    for f in ("forms.md", "reference.md", "scripts/fill_fillable_fields.py", "scripts/convert_pdf_to_images.py"):
        assert (REPO / "skills" / "anthropic" / "pdf" / f).is_file(), f


def test_vendored_prompt_only_skills_load():
    """discernment-nudge and doc-coauthoring are instructions only: no
    scripts, no tool access; skill-creator carries its scripts and needs the shell."""
    for slug, access in (("discernment-nudge", []), ("doc-coauthoring", []), ("skill-creator", ["shell"])):
        r = load_skill(REPO / "skills" / "anthropic" / slug)
        assert r.ok, (slug, r.reason, r.detail)
        assert r.skill.slug == slug and r.skill.access == access, slug
    assert (REPO / "skills" / "anthropic" / "skill-creator" / "scripts" / "quick_validate.py").is_file()
    assert (REPO / "skills" / "anthropic" / "skill-creator" / "references" / "schemas.md").is_file()


def test_load_dir_descends_into_a_vendor_folder(tmp_path: Path):
    """`skills/anthropic/docx/SKILL.md`: a folder without a SKILL.md groups
    skills (one license file for all of them) and is searched one level down."""
    (tmp_path / "own").mkdir()
    (tmp_path / "own" / "SKILL.md").write_text("---\nname: Own\nuse: own things\n---\nbody\n", encoding="utf-8")
    (tmp_path / "vendor" / "theirs").mkdir(parents=True)
    (tmp_path / "vendor" / "theirs" / "SKILL.md").write_text("---\nname: Theirs\nuse: their things\n---\nbody\n", encoding="utf-8")
    (tmp_path / "vendor" / "LICENSE.txt").write_text("Apache", encoding="utf-8")
    assert sorted(r.skill.slug for r in load_dir(tmp_path) if r.ok) == ["own", "theirs"]


def test_vendored_prompt_master_skill_loads_with_its_attribution():
    """nidhinjs/prompt-master, MIT: the upstream README and LICENSE ship beside it, and NOTICE.md names the source."""
    d = REPO / "skills" / "prompt-master"
    r = load_skill(d)
    assert r.ok, (r.reason, r.detail)
    assert r.skill.slug == "prompt-master" and r.skill.access == ["shell"] and "prompt" in r.skill.use
    assert "github.com/nidhinjs/prompt-master" in r.skill.body  # the provenance comment
    assert "MIT License" in (d / "LICENSE").read_text(encoding="utf-8")
    notice = (d / "NOTICE.md").read_text(encoding="utf-8")
    assert "Nidhin Joseph Nelson" in notice and "github.com/nidhinjs/prompt-master" in notice
    for f in ("README.md", "references/templates.md", "references/patterns.md"):
        assert (d / f).is_file(), f


def test_learn_company_skill_loads_from_the_repo():
    s = load_skill(REPO / "skills" / "learn-company").skill
    assert s.slug == "learn-company" and "learn_page" in s.access and s.band_width == 0.1
    assert any("20" in step for step in s.steps) and "unrelated" in s.body


def test_the_delegation_skill_loads():
    cat = Catalog()
    for r in load_dir(REPO / "skills"):
        if r.ok:
            cat.add_skill(r.skill)
    entry = next((e for e in cat.entries() if e.ref == "delegation"), None)
    assert entry is not None and entry.use.strip()
    s = cat.skill("delegation")
    assert "delegate" in s.access and "Claude Code" in s.body and "not instructions" in s.body

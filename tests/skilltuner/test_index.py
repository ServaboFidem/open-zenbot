import json
from pathlib import Path

import pytest

from src.skilltuner import index as ix

FIX = Path(__file__).parent / "fixtures"
SK = ("skills/007", "skills/pdf", "skills/sql-helper", "skills/web-audit")


def blobs(*paths):
    return [(p, 10) for p in paths]


def test_skill_folders_inside_the_folder_only():
    b = blobs("skills/a/SKILL.md", "other/b/SKILL.md", "skills/c d/SKILL.md", "SKILL.md", "skills/e/README.md")
    assert ix.skill_folders(b, "skills") == ["skills/a"]
    assert ix.skill_folders(b, "") == ["other/b", "skills/a"]


def test_a_skill_inside_a_skill_is_ignored():
    b = blobs("skills/a/SKILL.md", "skills/a/inner/SKILL.md", "skills/b/SKILL.md")
    assert ix.skill_folders(b, "skills") == ["skills/a", "skills/b"]


def test_find_index_prefers_the_named_then_the_shortest():
    b = blobs("skills/skills_index.json", "skill-index.json", "data/x.json", "skills/deep/skills-index.json")
    assert ix.find_index(b, "skills", "data/x.json") == "data/x.json"
    assert ix.find_index(b, "skills", "") == "skill-index.json"
    two = blobs("skills/skills_index.json", "skills/skill_index.json")
    assert ix.find_index(two, "skills", "") == "skills/skill_index.json"
    assert ix.find_index(blobs("a.json"), "skills", "") is None


def test_marketplace_is_not_an_index():
    assert ix.find_index(blobs(".claude-plugin/marketplace.json"), "", "") is None


def test_parse_a_list_index():
    listed, skipped = ix.parse_index((FIX / "index_list.json").read_bytes(), "skills_index.json", set(SK))
    assert skipped == 0
    assert len(listed) == 4
    assert (listed[1].path, listed[1].name) == ("skills/pdf", "pdf")
    assert listed[0].description.startswith("Security audit")
    assert listed[0].category == "security"


def test_parse_an_object_index_resolves_paths_against_its_folder():
    folders = {"agent-launcher/skills/agent-launcher-orchestrator", "engineering/senior-qa", "marketing/copywriter"}
    listed, skipped = ix.parse_index((FIX / "index_object.json").read_bytes(), ".codex/skills-index.json", folders)
    assert skipped == 0
    assert {x.path for x in listed} == folders


def test_index_paths_that_escape_are_skipped():
    bad = ["../../etc", "/abs", "https://x/y", "a\\b", "skills/nothing", "../nothing", "../../a/../skills/a", ""]
    data = [{"name": f"n{i}", "path": p, "description": "d"} for i, p in enumerate(bad)] + [{"name": "ok", "path": "skills/a", "description": "d"}]
    listed, skipped = ix.parse_index(json.dumps(data).encode(), "skills_index.json", {"skills/a"})
    assert [x.path for x in listed] == ["skills/a"]
    assert skipped == len(bad)


def test_index_values_are_cleaned_cut_and_deduplicated():
    data = [{"name": "x" * 500, "path": "skills/a", "description": "d" * 5000},
            {"name": "b \x00c\nd", "path": "skills/b", "description": "e f"},
            {"name": 5, "path": "skills/c"},
            {"name": "again", "path": "skills/a"}]
    listed, skipped = ix.parse_index(json.dumps(data).encode(), "i.json", {"skills/a", "skills/b", "skills/c"})
    assert len(listed[0].name) == 120 and len(listed[0].description) == 1000
    assert listed[1].name == "bc d" and listed[1].description == "ef"
    assert [x.path for x in listed] == ["skills/a", "skills/b"] and skipped == 2


def test_an_index_with_no_usable_entry_gives_none():
    assert ix.parse_index(b'[{"name": "a", "path": "nope"}, 3]', "i.json", {"skills/a"}) == ([], 2)
    for raw in (b"not json", b'{"x": 1}', b'"s"'):
        with pytest.raises(ValueError):
            ix.parse_index(raw, "i.json", set())


def test_frontmatter_reads_name_description_licence():
    t = "---\nname: Pdf\ndescription: Read\\n pdfs\nlicense: MIT\n---\nbody"
    assert ix.frontmatter(t) == {"name": "Pdf", "description": "Read\\n pdfs", "license": "MIT"}
    assert ix.frontmatter("no front") == {}
    assert ix.frontmatter("---\nname: [a, b]\ndescription: d\n---\n") == {"description": "d"}
    assert ix.frontmatter("---\nname: [unclosed\n---\n") == {}


def test_extras():
    assert ix.extras(blobs("s/a/SKILL.md", "s/a/scripts/run.py"), "s/a")
    assert not ix.extras(blobs("s/a/SKILL.md", "s/a/LICENSE", "s/a/references/a.md", "s/ab/x.py"), "s/a")


def test_an_index_path_climbing_above_the_root_is_tried_without_its_leading_dots():
    d = "agent-launcher/skills/agent-launcher-orchestrator"
    data = [{"name": "agent-launcher-orchestrator", "source": "../../" + d, "description": "x"}]
    b = blobs(d + "/SKILL.md", ".codex/skills-index.json")
    listed, skipped = ix.parse_index(json.dumps({"skills": data}).encode(), ".codex/skills-index.json",
                                     set(ix.skill_folders(b, "")))
    assert [x.path for x in listed] == [d] and skipped == 0


def test_name_falls_back_to_id_and_description_is_required():
    data = [{"id": "x", "path": "skills/a", "description": "d"},
            {"name": "b", "path": "skills/b"}, {"name": "c", "path": "skills/c", "description": " "},
            {"path": "skills/d", "description": "d"}]
    listed, skipped = ix.parse_index(json.dumps(data).encode(), "i.json", {f"skills/{c}" for c in "abcd"})
    assert [(x.path, x.name) for x in listed] == [("skills/a", "x")] and skipped == 3


def test_dots_in_the_middle_are_refused_and_source_only_when_path_is_absent():
    folders = {"skills/a", "skills/b"}
    data = [{"name": "a", "path": "x/../skills/a", "description": "d"},
            {"name": "b", "path": "./skills/a", "description": "d"},
            {"name": "c", "path": "nope", "source": "skills/b", "description": "d"},
            {"name": "d", "source": "skills/b", "description": "d"}]
    listed, skipped = ix.parse_index(json.dumps(data).encode(), "i.json", folders)
    assert [x.path for x in listed] == ["skills/b"] and skipped == 3


def test_frontmatter_refuses_aliases():
    bomb = "---\na: &a [x, x]\nb: &b [*a, *a]\nname: *b\n---\n"
    assert ix.frontmatter(bomb) == {}
    assert ix.frontmatter("---\nname: ok\nb: &a [1]\nc: *a\n---\n") == {}


def test_frontmatter_closes_only_on_a_line_of_three_dashes():
    assert ix.frontmatter("---\nname: a\n---x\n") == {}
    assert ix.frontmatter("---\nname: a\n----\n") == {}
    assert ix.frontmatter("---\nname: a\n---\nbody: ---x\n") == {"name": "a"}

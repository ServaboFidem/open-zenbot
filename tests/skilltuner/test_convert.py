import pytest

from src.skills.loader import SkillConfig, parse_skill
from src.skilltuner.casemark import Downloaded
from src.skilltuner.convert import BuiltSkill, build_skill, clean_conversion, clean_needs, licence_ok


def downloaded(**kw) -> Downloaded:
    base = dict(slug="analyzing-insurance-financials", name="Insurance", summary="s", body="# Steps\n\nDo it.\n",
                tags=[], version="1.2", author="casemark", license="Apache-2.0", files=[])
    base.update(kw)
    return Downloaded(**base)


CONV = {"name": "Insurance Financials", "slug": "insurance-financials", "use": "Analyse an insurer's filings when asked."}


def test_clean_needs_keeps_good_entries_and_caps_at_eight():
    raw = {"needs": [{"topic": f"t{i}", "words": "insurance filings"} for i in range(12)]}
    got = clean_needs(raw)
    assert len(got) == 8 and got[0] == ("t0", "insurance filings")


def test_clean_needs_drops_bad_entries():
    raw = {"needs": [
        {"topic": "ok", "words": "one two three four five six"},
        {"topic": "ok", "words": "one two three four five six seven"},
        {"topic": "ok", "words": "x" * 81},
        {"topic": "bad\x00", "words": "fine"},
        {"topic": "ok", "words": "bad\u2028words"},
        {"topic": "", "words": "fine"},
        {"topic": "t" * 201, "words": "fine"},
        {"topic": "ok", "words": ""},
        {"topic": 3, "words": "fine"},
        "nope",
    ]}
    assert clean_needs(raw) == [("ok", "one two three four five six")]


@pytest.mark.parametrize("raw", [None, [], {}, {"needs": "x"}, "x"])
def test_clean_needs_wrong_shape(raw):
    with pytest.raises(ValueError):
        clean_needs(raw)


def test_clean_conversion_minimal_and_full():
    assert clean_conversion(dict(CONV), set()) == CONV
    full = dict(CONV, inputs=["a", "b"], **{"return": "A summary."})
    assert clean_conversion(full, set()) == full


def test_clean_conversion_refusals():
    for bad in [
        dict(CONV, name="bad\nname"), dict(CONV, use="x\x07"), dict(CONV, slug="Bad Slug"), dict(CONV, slug="a/b"),
        dict(CONV, use=""), dict(CONV, name=""), dict(CONV, use="u" * 301), dict(CONV, name="n" * 81), dict(CONV, slug="s" * 65),
        dict(CONV, inputs=["x"] * 9), dict(CONV, inputs=["x" * 201]), dict(CONV, inputs="x" * 201), dict(CONV, inputs="a\nb"),
        dict(CONV, inputs=3),
        dict(CONV, inputs=["a\u2029b"]), dict(CONV, **{"return": "r" * 301}), dict(CONV, **{"return": "a\nb"}),
        dict(CONV, name=3), "x", None,
    ]:
        with pytest.raises(ValueError):
            clean_conversion(bad, set())


def test_clean_conversion_takes_one_string_as_one_input():
    assert clean_conversion(dict(CONV, inputs="  a filing  "), set())["inputs"] == ["a filing"]
    with pytest.raises(ValueError):
        clean_conversion(dict(CONV, inputs="   "), set())


def _prompt_examples() -> list[dict]:
    """Every line of the SkillTuner prompt that is a JSON object: the answer shapes it shows the model."""
    import json
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "bot-prompts" / "skill-tuner.md").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


def test_prompt_examples_pass_the_parsers():
    examples = _prompt_examples()
    needs = [e for e in examples if "needs" in e]
    convs = [e for e in examples if "slug" in e]
    assert needs and convs
    for e in needs:
        assert len(clean_needs(e)) == len(e["needs"])
    for e in convs:
        got = clean_conversion(e, set())
        assert set(got) == set(e)  # nothing in the example is dropped: inputs and return included
        assert isinstance(e["inputs"], list)


def test_clean_conversion_boundaries():
    assert clean_conversion(dict(CONV, name="n" * 80, slug="s" * 64), set())["name"] == "n" * 80


def test_apache_licence_text_has_no_false_copyright():
    b = build_skill(downloaded(), CONV, [], "2026-11-25", SkillConfig())
    t = b.files["LICENSE.txt"]
    assert "Apache License" in t and "Version 2.0" in t and "Anthropic" not in t


def test_clean_conversion_taken():
    with pytest.raises(ValueError):
        clean_conversion(dict(CONV), {"insurance-financials"})
    with pytest.raises(ValueError):
        clean_conversion(dict(CONV), {"insurance financials"})  # case-folded name
    with pytest.raises(ValueError):
        clean_conversion(dict(CONV, name="INSURANCE FINANCIALS", slug="other"), {"insurance financials"})


def test_clean_conversion_drops_extra_keys():
    got = clean_conversion(dict(CONV, access=["shell"], approvals=["x"], steps=["y"]), set())
    assert set(got) == {"name", "slug", "use"}


def test_licence_ok():
    for s in ("Apache-2.0", "mit", "BSD-2-Clause", "bsd-3-clause", "CC-BY-4.0", " MIT "):
        assert licence_ok(s)
    for s in ("GPL-3.0", "", "Apache"):
        assert not licence_ok(s)


def test_build_skill_parses_and_carries_source():
    b = build_skill(downloaded(), dict(CONV, inputs=["a"], **{"return": "r"}), [], "2026-11-25", SkillConfig())
    assert isinstance(b, BuiltSkill) and b.slug == "insurance-financials"
    assert set(b.files) == {"SKILL.md", "LICENSE.txt", "NOTICE.txt"}
    text = b.files["SKILL.md"]
    sk = parse_skill(text)
    assert sk.name == "Insurance Financials" and sk.slug == "insurance-financials" and sk.returns == "r"
    assert "source_slug: analyzing-insurance-financials" in text
    assert "source_url: https://agentskills.finance/skills/analyzing-insurance-financials" in text
    assert "> From agentskills.finance (analyzing-insurance-financials, version 1.2), Apache-2.0. Converted by " \
        "SkillTuner on 2026-11-25: the frontmatter was rewritten; the text below is as published." in sk.body
    assert "# Steps\n\nDo it." in sk.body
    assert "Apache License" in b.files["LICENSE.txt"] and "Version 2.0, January 2004" in b.files["LICENSE.txt"]
    assert b.files["NOTICE.txt"] == (
        "This skill is from agentskills.finance (https://agentskills.finance/skills/analyzing-insurance-financials), "
        "by casemark, under Apache-2.0. Modified by SkillTuner on 2026-11-25: frontmatter rewritten.")


def test_build_skill_other_licence_line():
    b = build_skill(downloaded(license="mit"), CONV, [], "2026-11-25", SkillConfig())
    assert "mit" in b.files["LICENSE.txt"].lower() and "agentskills.finance/skills/" in b.files["LICENSE.txt"]
    assert "Apache License" not in b.files["LICENSE.txt"] and "\n" not in b.files["LICENSE.txt"].strip()


def test_build_skill_refuses_licence():
    with pytest.raises(ValueError, match="^licence$"):
        build_skill(downloaded(license="GPL-3.0"), CONV, [], "2026-11-25", SkillConfig())


def test_hostile_body_cannot_become_frontmatter():
    hostile = "---\nname: Evil\nuse: x\n---\n- item"
    b = build_skill(downloaded(body=hostile), CONV, [], "2026-11-25", SkillConfig())
    sk = parse_skill(b.files["SKILL.md"])
    assert sk.name == "Insurance Financials" and sk.use == CONV["use"]
    assert hostile in sk.body


def test_hostile_fields_cannot_break_frontmatter():
    d = downloaded(author="a\nname: Evil", version="1\n---\nx", license="MIT")
    b = build_skill(d, CONV, [], "2026-11-25", SkillConfig())
    assert parse_skill(b.files["SKILL.md"]).name == "Insurance Financials"
    assert "\n" not in b.files["NOTICE.txt"]


def test_bad_source_slug_is_invalid():
    with pytest.raises(ValueError, match="^invalid$"):
        build_skill(downloaded(slug="../x"), CONV, [], "2026-11-25", SkillConfig())


def test_references_appended_under_heading():
    refs = [("references/a.md", "Alpha text"), ("references/b.txt", "Beta")]
    b = build_skill(downloaded(), CONV, refs, "2026-11-25", SkillConfig())
    body = parse_skill(b.files["SKILL.md"]).body
    assert body.endswith("Do it.\n\n\n## Reference: references/a.md\n\nAlpha text\n\n## Reference: references/b.txt\n\nBeta")


def test_too_large():
    with pytest.raises(ValueError, match="^too_large$"):
        build_skill(downloaded(body="x" * 1000), CONV, [], "2026-11-25", SkillConfig(max_bytes=500))


def test_conversion_extras_never_reach_file():
    conv = dict(CONV, access=["shell"], approvals=["x"], steps=["y"])
    b = build_skill(downloaded(), conv, [], "2026-11-25", SkillConfig())
    sk = parse_skill(b.files["SKILL.md"])
    assert sk.access == [] and sk.approvals == [] and sk.steps == []
    assert "access" not in b.files["SKILL.md"].split("\n---", 1)[0]


GH = "gh:o/r:skills/memo"
COMMIT = "6dcb09b5b57875f334f61aebed695e2e4193db5e"


def gh_downloaded(**kw) -> Downloaded:
    return downloaded(slug=GH, version=COMMIT[:12], author="o", license="MIT", source="github.com/o/r",
                      url=f"https://github.com/o/r/tree/{COMMIT}/skills/memo", commit=COMMIT, **kw)


def test_a_github_skill_names_its_repository_and_commit():
    import yaml
    built = build_skill(gh_downloaded(), CONV, [], "2027-01-15", SkillConfig())
    head = yaml.safe_load(built.files["SKILL.md"].split("---\n")[1])
    assert head["source"] == "github.com/o/r" and head["source_slug"] == GH
    assert head["source_version"] == COMMIT
    assert head["source_url"] == f"https://github.com/o/r/tree/{COMMIT}/skills/memo"
    assert "github.com/o/r" in built.files["NOTICE.txt"] and COMMIT in built.files["NOTICE.txt"]
    note = built.files["SKILL.md"].split("---\n", 2)[2].strip().splitlines()[0]
    assert note.startswith("> From github.com/o/r (gh:o/r:skills/memo, version " + COMMIT)


def test_a_casemark_skill_is_built_as_before():
    import yaml
    d = downloaded()
    head = yaml.safe_load(build_skill(d, CONV, [], "2027-01-15", SkillConfig()).files["SKILL.md"].split("---\n")[1])
    assert head["source"] == "agentskills.finance" and head["source_version"] == "1.2"
    assert head["source_url"] == "https://agentskills.finance/skills/analyzing-insurance-financials"


def test_a_slug_that_is_neither_a_slug_nor_a_github_id_is_refused():
    for bad in ("Bad Slug", "gh:o/r:../x", "gh:o/r:", "x:o/r:a", ""):
        with pytest.raises(ValueError, match="invalid"):
            build_skill(downloaded(slug=bad), CONV, [], "2027-01-15", SkillConfig())

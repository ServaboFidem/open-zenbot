import pytest

from src.events.eventbrite import SourceError
from src.skilltuner.github_source import GH_ID, gh_id, parse_gh_id

from .conftest import GH_SHA
from .test_github_source import api_count, entry, file_count, source, tree_of

MEMO = ("---\nname: Investment Memo\ndescription: Write an investment memo from a deal.\n---\n"
        "# Memo\n\nSteps.\n")
APACHE_HEAD = "\n".join(__import__("importlib").resources.files("src.skilltuner")
                        .joinpath("apache-2.0.txt").read_text("utf-8").splitlines()[:30])


def put(fake, path, data):
    fake.files[f"o/r/{GH_SHA}/{path}"] = data if isinstance(data, bytes) else data.encode()


async def listed(fake, tmp_path, paths, skill_text=MEMO, **kw):
    fake.trees[f"o/r/{GH_SHA}"] = tree_of(paths)
    put(fake, "skills/memo/SKILL.md", skill_text)
    gs, store = source(fake, tmp_path, [entry(**kw)])
    await gs.read_repo(entry(**kw))
    return gs


def test_ids():
    assert gh_id("o", "r", "skills/memo") == "gh:o/r:skills/memo"
    assert parse_gh_id("gh:o/r:skills/memo") == ("o", "r", "skills/memo")
    for bad in ("gh:o/r:", "gh:o/r:../x", "gh:o/r:a//b", "x:o/r:a", "gh:o/r:a b", "gh:o:a", "gh:o/r:" + "a/" * 300 + "a"):
        with pytest.raises(ValueError):
            parse_gh_id(bad)
    assert GH_ID.fullmatch("gh:o/r:skills/memo")


async def test_search_is_local(fake_github, tmp_path):
    gs = await listed(fake_github, tmp_path, ["skills/memo/SKILL.md"])
    fake_github.requests.clear()
    found = gs.search("investment memo")
    assert [f.slug for f in found] == ["gh:o/r:skills/memo"]
    assert found[0].name == "Investment Memo" and found[0].score > 0 and found[0].tags == []
    assert gs.search("zzz qqq") == [] and fake_github.requests == []
    assert gs.matcher() is gs.matcher()


async def test_search_only_covers_repositories_still_listed(fake_github, tmp_path):
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of(["skills/memo/SKILL.md"])
    put(fake_github, "skills/memo/SKILL.md", MEMO)
    es = [entry()]
    gs, _ = source(fake_github, tmp_path, es)
    await gs.read_repo(entry())
    es.clear()
    assert gs.search("investment memo") == []


async def test_read_takes_body_references_and_marks_extras(fake_github, tmp_path):
    paths = ["skills/memo/SKILL.md", "skills/memo/references/a.md", "skills/memo/references/b.bin",
             "skills/memo/references/sub/c.md", "skills/memo/scripts/run.py"]
    gs = await listed(fake_github, tmp_path, paths)
    got = await gs.read(gh_id("o", "r", "skills/memo"))
    assert got.body == "# Memo\n\nSteps.\n"
    assert got.files == [("references/a.md", "references/a.md")]
    assert got.version == GH_SHA[:12] and got.author == "o"
    assert got.source == "github.com/o/r"
    assert got.url == f"https://github.com/o/r/tree/{GH_SHA}/skills/memo"
    assert got.left_out is True and got.name == "Investment Memo" and got.slug == "gh:o/r:skills/memo"


async def test_no_extras_means_not_left_out_and_references_are_capped(fake_github, tmp_path):
    paths = ["skills/memo/SKILL.md"] + [f"skills/memo/references/r{i}.md" for i in range(25)]
    gs = await listed(fake_github, tmp_path, paths)
    got = await gs.read("gh:o/r:skills/memo")
    assert got.left_out is False and len(got.files) == 20


async def test_licence_rules_in_order(fake_github, tmp_path):
    base = ["skills/memo/SKILL.md"]
    with_fm = MEMO.replace("---\nBody", "").replace("description:", "license: MIT\ndescription:", 1)
    gs = await listed(fake_github, tmp_path, base + ["skills/memo/LICENSE"], with_fm, licence="BSD-3-Clause")
    put(fake_github, "skills/memo/LICENSE", APACHE_HEAD)
    assert (await gs.read("gh:o/r:skills/memo")).license == "MIT"
    assert file_count(fake_github) == 2  # the list read and SKILL.md; no LICENSE fetched

    gs = await listed(fake_github, tmp_path, base + ["skills/memo/license.md"], MEMO, licence="MIT")
    put(fake_github, "skills/memo/license.md", APACHE_HEAD)
    assert (await gs.read("gh:o/r:skills/memo")).license == "Apache-2.0"

    gs = await listed(fake_github, tmp_path, base + ["skills/memo/LICENSE"], MEMO, licence="")
    put(fake_github, "skills/memo/LICENSE", "All rights reserved.")
    assert (await gs.read("gh:o/r:skills/memo")).license == ""

    gs = await listed(fake_github, tmp_path, base, MEMO, licence="CC-BY-4.0")
    assert (await gs.read("gh:o/r:skills/memo")).license == "CC-BY-4.0"
    gs = await listed(fake_github, tmp_path, base, MEMO, licence="")
    assert (await gs.read("gh:o/r:skills/memo")).license == ""


async def test_a_frontmatter_licence_that_is_not_one_of_five_falls_through(fake_github, tmp_path):
    text = MEMO.replace("description:", "license: Complete terms in LICENSE.txt\ndescription:", 1)
    gs = await listed(fake_github, tmp_path, ["skills/memo/SKILL.md"], text, licence="MIT")
    assert (await gs.read("gh:o/r:skills/memo")).license == "MIT"


async def test_a_skill_gone_from_the_commit_is_not_found(fake_github, tmp_path):
    gs = await listed(fake_github, tmp_path, ["skills/memo/SKILL.md"])
    for bad in ("gh:o/r:skills/other", "gh:x/y:skills/memo"):
        with pytest.raises(SourceError) as e:
            await gs.read(bad)
        assert e.value.kind == "not_found"
    del fake_github.files[f"o/r/{GH_SHA}/skills/memo/SKILL.md"]
    with pytest.raises(SourceError) as e:
        await gs.read("gh:o/r:skills/memo")
    assert e.value.kind == "not_found"
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of(["skills/memo/README.md"])
    with pytest.raises(SourceError) as e:
        await gs.read("gh:o/r:skills/memo")
    assert e.value.kind == "not_found"


async def test_read_file_only_takes_listed_reference_paths(fake_github, tmp_path):
    gs = await listed(fake_github, tmp_path, ["skills/memo/SKILL.md", "skills/memo/references/a.md"])
    put(fake_github, "skills/memo/references/a.md", "ref \x00text")
    with pytest.raises(ValueError):
        await gs.read_file("gh:o/r:skills/memo", "references/a.md")  # not read yet
    await gs.read("gh:o/r:skills/memo")
    before = len(fake_github.requests)
    for bad in ("references/z.md", "../x.md", "references/../SKILL.md", "SKILL.md"):
        with pytest.raises(ValueError):
            await gs.read_file("gh:o/r:skills/memo", bad)
    assert len(fake_github.requests) == before
    assert await gs.read_file("gh:o/r:skills/memo", "references/a.md") == "ref text"

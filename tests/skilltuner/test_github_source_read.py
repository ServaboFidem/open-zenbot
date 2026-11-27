import pytest

from src.events.eventbrite import SourceError
from src.skilltuner.github_source import GH_ID, gh_id, parse_gh_id
from src.skilltuner.index import Listed
from src.skilltuner.repos import RepoList

from .conftest import GH_SHA
from .test_github_source import api_count, entry, file_count, source, tree_of

MEMO = ("---\nname: Investment Memo\ndescription: Write an investment memo from a deal.\n---\n"
        "# Memo\n\nSteps.\n")
APACHE_HEAD = __import__("importlib").resources.files("src.skilltuner").joinpath("apache-2.0.txt").read_text("utf-8")


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
    assert gh_id("o", "r", "skills/memo", "dev") == "gh:o/r@dev:skills/memo"
    assert parse_gh_id("gh:o/r:skills/memo") == ("o", "r", "skills/memo", "")
    assert parse_gh_id("gh:o/r@v1.2:skills/memo") == ("o", "r", "skills/memo", "v1.2")
    for bad in ("gh:o/r:", "gh:o/r:../x", "gh:o/r:a//b", "x:o/r:a", "gh:o/r:a b", "gh:o:a", "gh:o/r:" + "a/" * 300 + "a",
                "gh:o/r@:a", "gh:o/r@..:a", "gh:o/r@a/b:c", "gh:o/r@a b:c"):
        with pytest.raises(ValueError):
            parse_gh_id(bad)
    assert GH_ID.fullmatch("gh:o/r:skills/memo")


OTHER = "b" * 40


def two_refs(fake, tmp_path):
    """o/r listed twice, the default branch and `dev`, each holding skills/memo at its own commit."""
    main, dev = entry(), entry(id="fedcba9876543210", ref="dev")
    gs, store = source(fake, tmp_path, [main, dev])
    for e, sha, desc in ((main, GH_SHA, "Write a main memo."), (dev, OTHER, "Write a dev memo.")):
        store.put(e.id, RepoList(sha, "MIT", "2027-01-01T00:00:00+00:00", "", "", 0,
                                 [Listed("skills/memo", "Memo", desc, "")], True))
        fake.trees[f"o/r/{sha}"] = tree_of(["skills/memo/SKILL.md", "skills/memo/references/a.md"])
        fake.files[f"o/r/{sha}/skills/memo/SKILL.md"] = f"---\nname: Memo\ndescription: {desc}\n---\nBody".encode()
        fake.files[f"o/r/{sha}/skills/memo/references/a.md"] = desc.encode()
    return gs, store, dev


async def test_each_ref_has_its_own_id_and_reads_its_own_commit(fake_github, tmp_path):
    gs, _, _ = two_refs(fake_github, tmp_path)
    assert sorted(f.slug for f in gs.search("main dev memo")) == ["gh:o/r:skills/memo", "gh:o/r@dev:skills/memo"]
    got = await gs.read("gh:o/r@dev:skills/memo")
    assert got.commit == OTHER and got.summary == "Write a dev memo."
    assert (await gs.read("gh:o/r:skills/memo")).commit == GH_SHA


async def test_references_come_from_the_commit_the_body_came_from(fake_github, tmp_path):
    gs, store, dev = two_refs(fake_github, tmp_path)
    got = await gs.read("gh:o/r@dev:skills/memo")
    newer = "c" * 40  # a refresh stores a newer commit while the conversion reads the references
    store.put(dev.id, RepoList(newer, "MIT", "2027-01-02T00:00:00+00:00", "", "", 0,
                               [Listed("skills/memo", "Memo", "x", "")], True))
    before = len(fake_github.requests)
    assert await gs.read_file(got.slug, "references/a.md", got.commit) == "Write a dev memo."
    assert OTHER in str(fake_github.requests[before].url)
    with pytest.raises(ValueError):
        await gs.read_file(got.slug, "references/a.md", newer)  # not the commit that read took


async def test_nested_folders_on_one_ref_give_one_id(fake_github, tmp_path):
    wide, narrow = entry(), entry(id="fedcba9876543210", folder="skills")
    gs, store = source(fake_github, tmp_path, [wide, narrow])
    for e in (wide, narrow):
        store.put(e.id, RepoList(GH_SHA, "MIT", "2027-01-01T00:00:00+00:00", "", "", 0,
                                 [Listed("skills/memo", "Memo", "Write a memo.", "")], True))
    assert [f.slug for f in gs.search("memo")] == ["gh:o/r:skills/memo"]


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


async def test_references_over_twenty_are_capped_and_the_skill_is_left_out(fake_github, tmp_path):
    paths = ["skills/memo/SKILL.md"] + [f"skills/memo/references/r{i}.md" for i in range(25)]
    gs = await listed(fake_github, tmp_path, paths)
    got = await gs.read("gh:o/r:skills/memo")
    assert got.left_out is True and len(got.files) == 20


async def test_a_skill_with_only_what_is_taken_is_not_left_out(fake_github, tmp_path):
    paths = ["skills/memo/SKILL.md", "skills/memo/license.TXT", "skills/memo/NOTICE", "skills/memo/references/a.md"]
    gs = await listed(fake_github, tmp_path, paths)
    got = await gs.read("gh:o/r:skills/memo")
    assert got.left_out is False and len(got.files) == 1


async def test_a_reference_that_is_not_text_leaves_the_skill_out(fake_github, tmp_path):
    for odd in ("references/b.bin", "references/sub/c.md"):
        gs = await listed(fake_github, tmp_path, ["skills/memo/SKILL.md", f"skills/memo/{odd}"])
        assert (await gs.read("gh:o/r:skills/memo")).left_out is True


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

    gs = await listed(fake_github, tmp_path, base + ["skills/memo/LICENSE"], MEMO, licence="MIT")
    put(fake_github, "skills/memo/LICENSE", "All rights reserved.")
    assert (await gs.read("gh:o/r:skills/memo")).license == ""  # a file that is not recognised: no fall-through

    gs = await listed(fake_github, tmp_path, base + ["skills/memo/COPYING"], MEMO, licence="")
    put(fake_github, "skills/memo/COPYING", APACHE_HEAD)
    assert (await gs.read("gh:o/r:skills/memo")).license == "Apache-2.0"

    gs = await listed(fake_github, tmp_path, base, MEMO, licence="CC-BY-4.0")
    assert (await gs.read("gh:o/r:skills/memo")).license == "CC-BY-4.0"
    gs = await listed(fake_github, tmp_path, base, MEMO, licence="")
    assert (await gs.read("gh:o/r:skills/memo")).license == ""


async def test_a_frontmatter_licence_that_is_not_one_of_five_drops_the_skill(fake_github, tmp_path):
    for declared in ("Complete terms in LICENSE.txt", "GPL-3.0", "Proprietary"):
        text = MEMO.replace("description:", f"license: {declared}"+chr(10)+"description:", 1)
        gs = await listed(fake_github, tmp_path, ["skills/memo/SKILL.md"], text, licence="MIT")
        assert (await gs.read("gh:o/r:skills/memo")).license == ""


async def test_a_proprietary_licence_file_beats_the_repository_licence(fake_github, tmp_path):
    gs = await listed(fake_github, tmp_path, ["skills/memo/SKILL.md", "skills/memo/LICENSE.txt"], MEMO,
                      licence="Apache-2.0")
    put(fake_github, "skills/memo/LICENSE.txt", "Proprietary. Copyright Acme. You may not copy this.")
    assert (await gs.read("gh:o/r:skills/memo")).license == ""


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
        await gs.read_file("gh:o/r:skills/memo", "references/a.md", GH_SHA)  # not read yet
    await gs.read("gh:o/r:skills/memo")
    before = len(fake_github.requests)
    for bad in ("references/z.md", "../x.md", "references/../SKILL.md", "SKILL.md"):
        with pytest.raises(ValueError):
            await gs.read_file("gh:o/r:skills/memo", bad, GH_SHA)
    assert len(fake_github.requests) == before
    assert await gs.read_file("gh:o/r:skills/memo", "references/a.md", GH_SHA) == "ref text"

import json
from datetime import datetime, timezone

import pytest

from src.events.eventbrite import SourceError
from src.skilltuner.github_source import ONE_BY_ONE_MAX, REFRESH_DAYS, SKILLS_MAX, GitHubSkills
from src.skilltuner.repos import RepoEntry, RepoList, RepoStore

from .conftest import FIXTURES, GH_SHA

NOW = 1_800_000_000.0
DAY = 86400


def entry(id="0123456789abcdef", **kw):
    base = dict(id=id, owner="o", repo="r", ref="", folder="", index="", licence="MIT")
    base.update(kw)
    return RepoEntry(**base)


def tree_of(paths, truncated=False, trees=()):
    rows = [{"path": p, "type": "blob", "sha": "a" * 40, "size": 10} for p in paths]
    rows += [{"path": p, "type": "tree", "sha": s} for p, s in trees]
    return {"sha": GH_SHA, "truncated": truncated, "tree": rows}


def skill_md(name, desc="Does a thing."):
    return f"---\nname: {name}\ndescription: {desc}\n---\nBody".encode()


def source(fake, tmp_path, entries=None):
    store = RepoStore(tmp_path / "repos")
    es = entries if entries is not None else [entry()]
    return GitHubSkills(fake.reader(), store, lambda: es, lambda: NOW), store


def api_count(fake):
    return len([r for r in fake.requests if r.url.host == "api.github.com"])


def file_count(fake):
    return len([r for r in fake.requests if r.url.host == "raw.githubusercontent.com"])


def with_index(fake):
    index = (FIXTURES / "index_list.json").read_bytes()
    paths = ["skills_index.json"] + [f"skills/{n}/SKILL.md" for n in ("007", "pdf", "sql-helper", "web-audit")]
    fake.trees[f"o/r/{GH_SHA}"] = tree_of(paths)
    fake.files[f"o/r/{GH_SHA}/skills_index.json"] = index


async def test_a_repository_with_an_index_is_read_from_it(fake_github, tmp_path):
    with_index(fake_github)
    gs, store = source(fake_github, tmp_path)
    got = await gs.read_repo(entry())
    assert [s.name for s in got.skills] == ["007", "pdf", "sql-helper", "web-audit"]
    assert got.index == "skills_index.json" and got.licence_label == "MIT" and not got.one_by_one
    assert got.commit == GH_SHA and got.error == ""
    assert api_count(fake_github) == 3 and file_count(fake_github) == 1
    assert store.get("0123456789abcdef") == got
    assert gs.changed is True and gs.reading == set()
    assert got.read_at == datetime.fromtimestamp(NOW, timezone.utc).isoformat(timespec="seconds")


async def test_the_same_commit_fetches_nothing_more(fake_github, tmp_path):
    with_index(fake_github)
    gs, _ = source(fake_github, tmp_path)
    first = await gs.read_repo(entry())
    gs.changed = False
    fake_github.requests.clear()
    again = await gs.read_repo(entry())
    assert again.skills == first.skills
    assert [r.url.path.split("/")[-1] for r in fake_github.requests] == ["r", "main"]
    assert file_count(fake_github) == 0 and gs.changed is False


async def test_without_an_index_each_skill_md_is_read(fake_github, tmp_path):
    names = ("a", "b", "c")
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of([f"skills/{n}/SKILL.md" for n in names] + ["skills/a/scripts/x.py"])
    for n in names:
        fake_github.files[f"o/r/{GH_SHA}/skills/{n}/SKILL.md"] = skill_md(f"Skill {n}")
    gs, _ = source(fake_github, tmp_path)
    got = await gs.read_repo(entry())
    assert [s.name for s in got.skills] == ["Skill a", "Skill b", "Skill c"]
    assert got.one_by_one is True and got.index == "" and file_count(fake_github) == 3
    assert [s.has_extra for s in got.skills] == [True, False, False]


async def test_a_skill_without_a_name_or_a_missing_file_is_skipped(fake_github, tmp_path):
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of([f"s/{n}/SKILL.md" for n in "abc"])
    fake_github.files[f"o/r/{GH_SHA}/s/a/SKILL.md"] = skill_md("A")
    fake_github.files[f"o/r/{GH_SHA}/s/b/SKILL.md"] = b"no frontmatter"
    gs, _ = source(fake_github, tmp_path)  # s/c/SKILL.md answers 404
    got = await gs.read_repo(entry())
    assert [s.name for s in got.skills] == ["A"] and got.skipped == 2


async def test_over_three_hundred_without_an_index_is_refused(fake_github, tmp_path):
    n = ONE_BY_ONE_MAX + 1
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of([f"s/{i}/SKILL.md" for i in range(n)])
    gs, store = source(fake_github, tmp_path)
    with pytest.raises(SourceError) as e:
        await gs.read_repo(entry())
    assert e.value.kind == "no_index" and file_count(fake_github) == 0
    assert store.get("0123456789abcdef").error == "no_index"


async def test_a_truncated_tree_is_read_from_the_folder_subtree(fake_github, tmp_path):
    sub = "b" * 40
    mid = "c" * 40
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of([], truncated=True, trees=[("skills", mid), ("other", "d" * 40)])
    fake_github.trees[f"o/r/{mid}"] = tree_of([], trees=[("deep", sub)])
    fake_github.trees[f"o/r/{sub}"] = tree_of(["x/SKILL.md"])
    fake_github.files[f"o/r/{GH_SHA}/skills/deep/x/SKILL.md"] = skill_md("X")
    gs, _ = source(fake_github, tmp_path)
    got = await gs.read_repo(entry(folder="skills/deep"))
    assert [s.path for s in got.skills] == ["skills/deep/x"]
    queries = [r.url.query.decode() for r in fake_github.requests if "/git/trees/" in r.url.path]
    assert queries == ["recursive=1", "", "", "recursive=1"]


async def test_a_truncated_tree_with_no_folder_is_too_large(fake_github, tmp_path):
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of([], truncated=True)
    gs, _ = source(fake_github, tmp_path)
    with pytest.raises(SourceError) as e:
        await gs.read_repo(entry())
    assert e.value.kind == "too_large"


async def test_a_truncated_folder_subtree_is_too_large(fake_github, tmp_path):
    sub = "b" * 40
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of([], truncated=True, trees=[("skills", sub)])
    fake_github.trees[f"o/r/{sub}"] = tree_of([], truncated=True)
    gs, _ = source(fake_github, tmp_path)
    with pytest.raises(SourceError) as e:
        await gs.read_repo(entry(folder="skills"))
    assert e.value.kind == "too_large"


async def test_a_failed_read_keeps_the_old_list(fake_github, tmp_path):
    with_index(fake_github)
    gs, store = source(fake_github, tmp_path)
    first = await gs.read_repo(entry())
    fake_github.status = 301
    with pytest.raises(SourceError) as e:
        await gs.read_repo(entry())
    assert e.value.kind == "source_changed"
    kept = store.get("0123456789abcdef")
    assert kept.error == "source_changed" and kept.skills == first.skills and kept.read_at == first.read_at


async def test_a_github_wide_failure_is_raised_without_marking_the_repository(fake_github, tmp_path):
    gs, store = source(fake_github, tmp_path)
    fake_github.status = 429
    with pytest.raises(SourceError) as e:
        await gs.read_repo(entry())
    assert e.value.kind == "refused" and store.get("0123456789abcdef") is None
    fake_github.status = 503
    with pytest.raises(SourceError) as e:
        await gs.read_repo(entry())
    assert e.value.kind == "provider" and store.get("0123456789abcdef") is None and gs.reading == set()


async def test_go_is_called_before_each_request(fake_github, tmp_path):
    with_index(fake_github)
    gs, _ = source(fake_github, tmp_path)
    calls = []
    await gs.read_repo(entry(), go=lambda: calls.append(len(fake_github.requests)))
    assert calls == [0, 1, 2, 3]


async def test_an_index_that_is_not_json_falls_back_to_one_by_one(fake_github, tmp_path):
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of(["skills_index.json", "s/a/SKILL.md"])
    fake_github.files[f"o/r/{GH_SHA}/skills_index.json"] = b"not json"
    fake_github.files[f"o/r/{GH_SHA}/s/a/SKILL.md"] = skill_md("A")
    gs, _ = source(fake_github, tmp_path)
    got = await gs.read_repo(entry())
    assert got.one_by_one and got.index == "" and [s.name for s in got.skills] == ["A"]


async def test_stale_picks_the_oldest_past_seven_days(fake_github, tmp_path):
    a, b, c = entry("a" * 16), entry("b" * 16), entry("c" * 16)
    gs, store = source(fake_github, tmp_path, [a, b, c])

    def at(days):
        return datetime.fromtimestamp(NOW - days * DAY, timezone.utc).isoformat(timespec="seconds")

    store.put(a.id, RepoList(read_at=at(REFRESH_DAYS + 1)))
    store.put(b.id, RepoList(read_at=at(REFRESH_DAYS + 5)))
    store.put(c.id, RepoList(read_at=at(1)))
    assert gs.stale(NOW) == b
    store.put(b.id, RepoList(error="not_found"))  # failed, never read well
    assert gs.stale(NOW) == b
    store.put(b.id, RepoList(read_at=at(2)))
    assert gs.stale(NOW) == a
    store.put(a.id, RepoList(read_at=at(2)))
    assert gs.stale(NOW) is None
    store.remove(c.id)  # no stored list at all
    assert gs.stale(NOW) == c


async def test_a_damaged_stored_list_is_kept_aside(tmp_path):
    store = RepoStore(tmp_path / "repos")
    store.put("abc", RepoList(commit="x"))
    path = tmp_path / "repos" / "abc.json"
    path.write_text("{ not json", encoding="utf-8")
    assert store.get("abc") is None
    assert not path.exists() and (tmp_path / "repos" / "abc.json.bad").exists()
    path.write_text(json.dumps({"skills": "no"}), encoding="utf-8")
    assert store.get("abc") is None


async def test_a_stored_list_that_is_not_utf8_is_kept_aside(tmp_path):
    store = RepoStore(tmp_path / "repos")
    path = tmp_path / "repos" / "abc.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"commit": "\xff\xfe"}')
    assert store.get("abc") is None
    assert not path.exists() and (tmp_path / "repos" / "abc.json.bad").exists()


def test_the_store_round_trips_and_refuses_odd_ids(tmp_path):
    from src.skilltuner.index import Listed
    store = RepoStore(tmp_path / "repos")
    stored = RepoList("c", "MIT", "t", "", "i.json", 2, [Listed("a/b", "n", "d", "cat", True)], False)
    store.put("abc", stored)
    assert store.get("abc") == stored
    store.set_error("abc", "not_found")
    assert store.get("abc").error == "not_found" and store.get("abc").skills == stored.skills
    store.remove("abc")
    assert store.get("abc") is None
    with pytest.raises(ValueError):
        store.get("../x")


def test_stale_passes_over_the_ids_asked_to_skip_and_has_skills_says_if_anything_is_stored(fake_github, tmp_path):
    a, b = entry(id="aaaaaaaaaaaaaaaa"), entry(id="bbbbbbbbbbbbbbbb", repo="s")
    gs, store = source(fake_github, tmp_path, [a, b])
    assert gs.has_skills() is False and gs.stale(NOW) is a
    assert gs.stale(NOW, {"aaaaaaaaaaaaaaaa"}) is b and gs.stale(NOW, {a.id, b.id}) is None
    from src.skilltuner.index import Listed
    store.put(a.id, RepoList(GH_SHA, "MIT", datetime.fromtimestamp(NOW, timezone.utc).isoformat(), "", "", 0, [], False))
    assert gs.has_skills() is False
    store.put(a.id, RepoList(GH_SHA, "MIT", "", "", "", 0, [Listed("p", "n", "d", "")], False))
    assert gs.has_skills() is True


async def test_skill_files_the_index_does_not_list_do_not_stop_a_read(fake_github, tmp_path):
    # sickn33/agentic-awesome-skills: an index of 3,151 skills under skills/, and 6,927 more SKILL.md files bundled
    # under plugins/ that it does not list; the read stopped as too_large on the count of all 10,078
    with_index(fake_github)
    rows = fake_github.trees[f"o/r/{GH_SHA}"]["tree"]
    rows += tree_of([f"plugins/p{i}/skills/s/SKILL.md" for i in range(SKILLS_MAX + 1)])["tree"]
    gs, store = source(fake_github, tmp_path)
    got = await gs.read_repo(entry())
    assert got.error == "" and got.index == "skills_index.json" and len(got.skills) == 4
    assert file_count(fake_github) == 1  # the index alone


async def test_an_index_listing_more_than_the_limit_is_too_large(fake_github, tmp_path):
    names = [f"s{i}" for i in range(SKILLS_MAX + 1)]
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of(["skills_index.json"] + [f"skills/{n}/SKILL.md" for n in names])
    fake_github.files[f"o/r/{GH_SHA}/skills_index.json"] = json.dumps(
        [{"name": n, "path": f"skills/{n}", "description": "Does a thing."} for n in names]).encode()
    gs, store = source(fake_github, tmp_path)
    with pytest.raises(SourceError) as e:
        await gs.read_repo(entry())
    assert e.value.kind == "too_large" and file_count(fake_github) == 1
    assert store.get("0123456789abcdef").error == "too_large"


async def test_a_tree_path_the_client_refuses_is_a_stored_failure_not_a_crash(fake_github, tmp_path):
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of(["My Skills/skills_index.json", "skills/a/SKILL.md"])
    fake_github.files[f"o/r/{GH_SHA}/skills/a/SKILL.md"] = skill_md("A")
    gs, store = source(fake_github, tmp_path)
    got = await gs.read_repo(entry())  # the odd index is never taken: read one by one
    assert [s.name for s in got.skills] == ["A"] and got.one_by_one is True

    async def refuse(*a, **k):
        raise ValueError("not a path")
    gs.client.raw = refuse
    fake_github.trees[f"o/r/{'c' * 40}"] = tree_of(["skills/a/SKILL.md"])
    fake_github.commits["o/r/main"] = {"sha": "c" * 40}
    with pytest.raises(SourceError) as e:
        await gs.read_repo(entry())
    assert e.value.kind == "source_changed" and store.get("0123456789abcdef").error == "source_changed"


async def test_removing_a_repository_stops_a_read_under_way(fake_github, tmp_path):
    n = 20
    fake_github.trees[f"o/r/{GH_SHA}"] = tree_of([f"s/{i}/SKILL.md" for i in range(n)])
    for i in range(n):
        fake_github.files[f"o/r/{GH_SHA}/s/{i}/SKILL.md"] = skill_md(f"Skill {i}")
    es = [entry()]
    gs, store = source(fake_github, tmp_path, es)
    real = gs.client.raw

    async def raw(*a, **k):
        got = await real(*a, **k)
        es.clear()  # the owner removes the repository after the first file
        return got
    gs.client.raw = raw
    with pytest.raises(SourceError) as e:
        await gs.read_repo(entry())
    assert e.value.kind == "removed" and file_count(fake_github) == 1
    assert store.get("0123456789abcdef") is None and gs.reading == set()

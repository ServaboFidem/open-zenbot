import asyncio

import httpx
import pytest

from src.skilltuner.github import API_LIMIT, GAP_S, RepoInfo, SourceError
from tests.skilltuner.conftest import GH_SHA


async def kind_of(coro) -> SourceError:
    with pytest.raises(SourceError) as info:
        await coro
    return info.value


async def test_repo_reads_branch_and_licence(fake_github):
    gh = fake_github.reader()
    assert await gh.repo("o", "r") == RepoInfo("main", "MIT")
    fake_github.repos["o/r"] = {**fake_github.repos["o/r"], "license": None}
    assert (await gh.repo("o", "r")).licence == ""
    fake_github.repos["o/r"] = {**fake_github.repos["o/r"], "private": True}
    assert (await kind_of(gh.repo("o", "r"))).kind == "not_found"


async def test_commit_must_be_forty_hex(fake_github):
    gh = fake_github.reader()
    assert await gh.commit("o", "r", "main") == GH_SHA
    fake_github.commits["o/r/main"] = {"sha": GH_SHA[:39]}
    assert (await kind_of(gh.commit("o", "r", "main"))).kind == "source_changed"


async def test_tree_lists_blobs_and_trees_and_truncation(fake_github):
    gh = fake_github.reader()
    tree = await gh.tree("o", "r", GH_SHA)
    assert ("skills/a/SKILL.md", 1024) in tree.blobs and ("README.md", 132) in tree.blobs
    assert [p for p, _ in tree.trees] == ["skills", "skills/a"] and not tree.truncated
    assert fake_github.requests[0].url.query == b"recursive=1"
    fake_github.trees[f"o/r/{GH_SHA}"] = {**fake_github.trees[f"o/r/{GH_SHA}"], "truncated": True}
    assert (await gh.tree("o", "r", GH_SHA, recursive=False)).truncated
    assert fake_github.requests[1].url.query == b""


async def test_raw_url_is_built_from_checked_parts(fake_github):
    fake_github.files[f"o/r/{GH_SHA}/skills/a/SKILL.md"] = b"hello"
    gh = fake_github.reader()
    assert await gh.raw("o", "r", GH_SHA, "skills/a/SKILL.md", 100) == b"hello"
    assert str(fake_github.requests[0].url) == f"https://raw.githubusercontent.com/o/r/{GH_SHA}/skills/a/SKILL.md"
    for bad in ("../x", "a/../b", "a//b", "/a", ""):
        with pytest.raises(ValueError):
            await gh.raw("o", "r", GH_SHA, bad, 100)
    with pytest.raises(ValueError):
        await gh.raw("o", "r", "main", "a", 100)
    assert len(fake_github.requests) == 1


async def test_redirect_is_source_changed_and_not_followed(fake_github):
    fake_github.status = 301
    fake_github.headers = {"location": "https://evil.example/x"}
    err = await kind_of(fake_github.reader().repo("o", "r"))
    assert err.kind == "source_changed" and err.status == 301
    assert len(fake_github.requests) == 1


@pytest.mark.parametrize("status,kind", [(404, "not_found"), (403, "refused"), (429, "refused"), (500, "provider")])
async def test_status_kinds(fake_github, status, kind):
    fake_github.status = status
    err = await kind_of(fake_github.reader().repo("o", "r"))
    assert err.kind == kind and err.status == status


async def test_connect_error_is_network(fake_github):
    fake_github.raises = httpx.ConnectError("boom")
    err = await kind_of(fake_github.reader().repo("o", "r"))
    assert err.kind == "network" and err.status == 0


async def test_answers_over_the_limit_are_refused(fake_github):
    gh = fake_github.reader()
    fake_github.files[f"o/r/{GH_SHA}/big"] = b"x" * 11
    assert (await kind_of(gh.raw("o", "r", GH_SHA, "big", 10))).kind == "too_large"

    async def chunks():
        for _ in range(3):
            yield b"x" * 5
    fake_github.stream = chunks
    assert (await kind_of(gh.raw("o", "r", GH_SHA, "big", 10))).kind == "too_large"
    fake_github.stream = None
    fake_github.headers = {"content-type": "text/html"}
    assert (await kind_of(gh.repo("o", "r"))).kind == "source_changed"
    assert API_LIMIT == 1_000_000


async def test_one_at_a_time_a_second_apart(fake_github):
    gh = fake_github.reader()
    await asyncio.gather(gh.repo("o", "r"), gh.commit("o", "r", "main"))
    assert fake_github.sleeps == [GAP_S]


async def test_budget_by_kind(fake_github):
    fake_github.files[f"o/r/{GH_SHA}/a"] = b"x"
    gh = fake_github.reader()
    await gh.repo("o", "r")
    await gh.raw("o", "r", GH_SHA, "a", 10)
    assert fake_github.kinds == ["api", "file"]
    fake_github.allow = False
    n = len(fake_github.requests)
    assert (await kind_of(gh.repo("o", "r"))).kind == "budget"
    assert len(fake_github.requests) == n


async def test_requests_go_only_to_the_two_hosts(fake_github):
    fake_github.files[f"o/r/{GH_SHA}/a"] = b"x"
    gh = fake_github.reader()
    await gh.repo("o", "r")
    await gh.commit("o", "r", "main")
    await gh.tree("o", "r", GH_SHA)
    await gh.raw("o", "r", GH_SHA, "a", 10)
    assert len(fake_github.requests) == 4
    for req in fake_github.requests:
        assert req.url.host in ("api.github.com", "raw.githubusercontent.com")
        assert req.headers["accept-encoding"] == "identity"

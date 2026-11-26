"""The /skilltuner routes and Approve, offline: a real Harness over an ASGI transport."""
from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.skills.loader import parse_skill
from src.skilltuner.convert import BuiltSkill
from src.skilltuner.routes import skilltuner_router
from src.skilltuner.suggestions import Suggestion
from src.vault_settings import discovered_skills_dir

from src.skilltuner.github import GitHub
from src.skilltuner.settings import TunerSettings

from ..conftest import ScriptedProvider
from .conftest import GH_SHA, FakeGitHub

SKILL = "---\nname: PDF tool\nuse: Work with PDFs.\n---\nBody.\n"


@pytest.fixture
def harness(vault: Path, tmp_path: Path) -> Harness:
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path / "workspace")
    cfg.validate()
    return Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())


@pytest.fixture
async def client(harness: Harness):
    transport = httpx.ASGITransport(app=create_app(harness))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c
    await harness.dreamer.close()


def suggest(h: Harness, slug="pdf-tool", name="PDF tool", skill=SKILL, source="pdf", **files) -> Suggestion:
    s = Suggestion(id=secrets.token_hex(16), slug=slug, source_slug=source, name=name, use="Work with PDFs",
                   topic="pdf", score=0.8, license="MIT", source_url="https://x.example/s", original="ORIGINAL",
                   converted="CONVERTED", at="2026-11-25T10:00:00Z")
    h.suggestions.add(s, BuiltSkill(slug=slug, files={"SKILL.md": skill, "LICENSE.txt": "MIT", **files}))
    return s


def disc(h: Harness) -> Path:
    return discovered_skills_dir(h.config.vault_root)


def events(h: Harness, name: str) -> list[dict]:
    return [e.data for e in h.ledger.iter() if e.kind == "skilltuner" and e.data.get("event") == name]


async def test_every_post_needs_a_json_body(client):
    paths = sorted(r.path for r in skilltuner_router(None).routes if "POST" in (getattr(r, "methods", None) or ()))
    assert len(paths) == 6
    for path in paths:
        url = path.replace("{id}", "x" * 32).replace("{slug}", "pdf-tool")
        assert (await client.post(url)).status_code == 422
        assert (await client.post(url, content=b"x", headers={"Content-Type": "text/plain"})).status_code == 422


async def test_status_says_why_not(client, harness):
    r = (await client.get("/skilltuner/status")).json()
    assert r == {"enabled": False, "ready": False, "why_not": "Switch on dreaming first.", "last_pass_at": "",
                 "error": "", "error_source": "", "pending": 0, "spent_today": 0, "budget": 40}
    harness.dreamer.settings.enabled = True
    assert (await client.get("/skilltuner/status")).json()["why_not"] == "SkillTuner's bot file is missing."
    harness.add_bot(Bot(name="SkillTuner", role="r", id="skill-tuner", tunes_skills=True))
    assert (await client.get("/skilltuner/status")).json()["why_not"] == "Choose a decision model first."


async def test_switching_on_needs_dreaming(client, harness):
    r = await client.put("/skilltuner/settings", json={"enabled": True})
    assert r.status_code == 409 and r.json()["detail"] == "Switch on dreaming first."
    assert (await client.put("/skilltuner/settings", json={"enabled": "yes"})).status_code == 422
    assert (await client.put("/skilltuner/settings", json={"enabled": False})).status_code == 200
    harness.dreamer.settings.enabled = True
    r = await client.put("/skilltuner/settings", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True and harness.tuner.settings.enabled
    assert [e["enabled"] for e in events(harness, "settings")] == [False, True]


async def test_list_and_get(client, harness):
    s = suggest(harness)
    items = (await client.get("/skilltuner/suggestions")).json()
    assert [i["id"] for i in items] == [s.id]
    assert "original" not in items[0] and "converted" not in items[0]
    full = (await client.get(f"/skilltuner/suggestions/{s.id}")).json()
    assert full["original"] == "ORIGINAL" and full["converted"] == "CONVERTED"
    r = await client.get("/skilltuner/suggestions/" + "0" * 32)
    assert r.status_code == 404 and r.json()["detail"] == "No such suggestion."


async def test_approve_installs_the_folder_and_the_skill(client, harness):
    s = suggest(harness, **{"ref.md": "more"})
    r = await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skill"]["ref"] == "pdf-tool" and body["skill"]["name"] == "PDF tool" and body["fit"] is None
    assert sorted(os.listdir(disc(harness) / "pdf-tool")) == ["LICENSE.txt", "SKILL.md", "ref.md"]
    assert not [n for n in os.listdir(disc(harness)) if n.startswith(".tmp-")]
    assert harness.catalog.skill("pdf-tool") is not None
    assert harness.suggestions.get(s.id) is None and harness.tuner_state.known == {"pdf"}
    assert events(harness, "approved")[0]["slug"] == "pdf-tool"


async def test_approve_answers_a_fit(client, harness):
    harness.add_bot(Bot(name="Helper", role="Does things.", id="helper"))
    harness.add_bot(Bot(name="SkillTuner", role="r", id="skill-tuner", tunes_skills=True))
    seen = {}

    class Fit:
        async def suggest(self, skill, bots):
            seen["bots"] = [b.id for b in bots]

            class R:
                def to_dict(self):
                    return {"skill": skill.slug, "bot_ids": ["helper"]}
            return R()

    harness.fit = Fit()
    s = suggest(harness)
    body = (await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})).json()
    assert body["fit"] == {"skill": "pdf-tool", "bot_ids": ["helper"]}
    assert "skill-tuner" not in seen["bots"] and "helper" in seen["bots"]


async def test_approve_without_scorer_gives_empty_fit(client, harness):
    harness.fit = None
    s = suggest(harness)
    assert (await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})).json()["fit"] is None


async def test_double_approve_makes_one_folder(client, harness):
    s = suggest(harness)
    a, b = await asyncio.gather(*(client.post(f"/skilltuner/suggestions/{s.id}/approve", json={}) for _ in range(2)))
    assert sorted([a.status_code, b.status_code]) == [200, 404]
    assert os.listdir(disc(harness)) == ["pdf-tool"]


async def test_approve_refuses_a_name_already_installed(client, harness):
    first = suggest(harness)
    assert (await client.post(f"/skilltuner/suggestions/{first.id}/approve", json={})).status_code == 200
    again = suggest(harness)
    r = await client.post(f"/skilltuner/suggestions/{again.id}/approve", json={})
    assert r.status_code == 409 and r.json()["detail"] == "A skill with that name is already installed."
    assert harness.suggestions.get(again.id) is not None  # it stays
    other = suggest(harness, slug="other-tool")  # the same name under another slug is refused too
    assert (await client.post(f"/skilltuner/suggestions/{other.id}/approve", json={})).status_code == 409


async def test_approve_refuses_an_existing_folder_and_a_bad_slug(client, harness):
    (disc(harness) / "pdf-tool").mkdir(parents=True)
    s = suggest(harness)
    assert (await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})).status_code == 409
    bad = suggest(harness, slug="Bad Slug")
    assert (await client.post(f"/skilltuner/suggestions/{bad.id}/approve", json={})).status_code == 422


async def test_approve_failure_cleans_up_and_keeps_the_suggestion(client, harness, monkeypatch):
    s = suggest(harness)

    def boom(*a, **k):
        raise PermissionError("secret path")

    monkeypatch.setattr("src.skilltuner.routes.os.replace", boom)
    r = await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})
    assert r.status_code == 500 and r.json()["detail"] == "The skill could not be installed."
    assert "secret" not in r.text
    assert not [n for n in os.listdir(disc(harness)) if n.startswith(".tmp-")]
    assert harness.suggestions.get(s.id) is not None and harness.catalog.skill("pdf-tool") is None


async def test_an_unloadable_skill_leaves_nothing_behind(client, harness):
    s = suggest(harness, skill="no front matter at all")
    r = await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})
    assert r.status_code == 500
    assert not (disc(harness) / "pdf-tool").exists() and harness.suggestions.get(s.id) is not None


async def test_reject_remembers_the_source(client, harness):
    s = suggest(harness)
    assert (await client.post(f"/skilltuner/suggestions/{s.id}/reject", json={})).json() == {"ok": True}
    assert harness.suggestions.get(s.id) is None and "pdf" in harness.tuner_state.known
    assert harness.tuner_state._known["pdf"] == "rejected"
    assert (await client.post(f"/skilltuner/suggestions/{s.id}/reject", json={})).status_code == 404
    assert events(harness, "rejected")[0]["id"] == s.id


async def test_remove_acts_only_inside_discovered(client, harness):
    s = suggest(harness)
    await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})
    harness.catalog.add_skill(parse_skill("---\nname: Other\nuse: x\n---\nb\n"))
    other = [k.slug for k in harness.catalog.skills() if k.name == "Other"][0]
    assert (await client.post(f"/skilltuner/skills/{other}/remove", json={})).status_code == 404
    assert (await client.post("/skilltuner/skills/..%2F/remove", json={})).status_code == 404
    assert harness.catalog.skill(other) is not None
    r = await client.post("/skilltuner/skills/pdf-tool/remove", json={})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert not (disc(harness) / "pdf-tool").exists() and harness.catalog.skill("pdf-tool") is None
    assert (await client.post("/skilltuner/skills/pdf-tool/remove", json={})).status_code == 404
    assert events(harness, "removed")[0]["slug"] == "pdf-tool"


async def test_remove_never_follows_a_link(client, harness, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    d = disc(harness)
    d.mkdir(parents=True)
    try:
        (d / "linked").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system will not make links")
    r = await client.post("/skilltuner/skills/linked/remove", json={})
    assert r.status_code == 409 and (outside / "keep.txt").exists()
    real = d / "real"
    real.mkdir()
    (real / "inner").symlink_to(outside, target_is_directory=True)
    assert (await client.post("/skilltuner/skills/real/remove", json={})).status_code == 200
    assert not real.exists() and (outside / "keep.txt").exists()


async def test_no_answer_holds_the_vault_path(client, harness):
    s = suggest(harness)
    texts = [(await client.get("/skilltuner/status")).text, (await client.get("/skilltuner/suggestions")).text,
             (await client.get(f"/skilltuner/suggestions/{s.id}")).text,
             (await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})).text,
             (await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})).text,
             (await client.post("/skilltuner/skills/nope/remove", json={})).text,
             (await client.put("/skilltuner/settings", json={"enabled": True})).text]
    bad = suggest(harness, slug="Bad Slug")
    texts.append((await client.post(f"/skilltuner/suggestions/{bad.id}/approve", json={})).text)  # 422
    texts.append((await client.post(f"/skilltuner/suggestions/{suggest(harness).id}/approve", json={})).text)  # 409
    rej = suggest(harness, slug="rej-tool", name="Rej tool")
    texts.append((await client.post(f"/skilltuner/suggestions/{rej.id}/reject", json={})).text)
    texts.append((await client.post("/skilltuner/skills/pdf-tool/remove", json={})).text)  # 200
    root = str(harness.config.vault_root.parent)
    for t in texts:
        assert root not in t and root.replace("\\", "\\\\") not in t


def test_the_harness_builds_one_watcher_over_discovered(harness):
    """The lifespan starts `skill_watcher.run(stop)` (it spawns real MCP servers, so it is not run here)."""
    w = harness.skill_watcher
    assert w.folder == disc(harness) and w.catalog is harness.catalog
    harness.__post_init__()
    assert harness.skill_watcher is w


def test_a_scan_that_loads_a_skill_bumps_the_routers_scope(harness):
    from src.decider.router import CatalogRouter

    harness.router = harness.router or CatalogRouter(harness.scorer, harness.catalog, harness.ledger)
    before = harness.router.scope_version
    d = disc(harness) / "bump"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL.replace("PDF tool", "Bump"), encoding="utf-8")
    harness.skill_watcher.scan()
    assert harness.router.scope_version > before


async def test_a_plain_file_in_discovered_is_no_skill(client, harness):
    disc(harness).mkdir(parents=True)
    (disc(harness) / "afile").write_text("x")
    r = await client.post("/skilltuner/skills/afile/remove", json={})
    assert r.status_code == 404 and (disc(harness) / "afile").exists()


async def test_approve_still_succeeds_when_the_bookkeeping_fails(client, harness, monkeypatch):
    s = suggest(harness)

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(harness.tuner_state, "remember", boom)
    r = await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})
    assert r.status_code == 200 and events(harness, "approved")


async def test_a_rejected_source_is_not_suggested_by_a_later_pass(client, harness):
    from .test_tuner import TOPIC, WIN, WORDS, Rig, needs

    s = suggest(harness, source=WIN)
    assert (await client.post(f"/skilltuner/suggestions/{s.id}/reject", json={})).status_code == 200
    t = Rig(harness.config.vault_root, harness.ledger)  # reads the state the route saved
    t.conv("Draft an investment memo for the Acme deal")
    t.model.answers = [needs((TOPIC, WORDS))]
    await t.tune()
    assert WIN not in {q.removeprefix("r:") for r in t.scorer.requests for q in r.questions}
    assert all(x.source_slug != WIN for x in t.store.list())


async def test_discovered_lists_only_the_watchers_skills(client, harness):
    assert (await client.get("/skilltuner/discovered")).json() == {"slugs": []}
    s = suggest(harness)
    await client.post(f"/skilltuner/suggestions/{s.id}/approve", json={})
    harness.catalog.add_skill(parse_skill("---\nname: Other\nuse: x\n---\nb\n"))
    r = await client.get("/skilltuner/discovered")
    assert r.status_code == 200 and r.json() == {"slugs": ["pdf-tool"]}
    assert str(harness.config.vault_root) not in r.text


# -- GitHub repositories -------------------------------------------------------------------------------------

SKILL_A = "---\nname: Alpha Skill\ndescription: Does alpha things.\n---\n# Alpha\n"
ADDR = "https://github.com/o/r"


@pytest.fixture
def gh(harness: Harness) -> FakeGitHub:
    fake = FakeGitHub()
    fake.files[f"o/r/{GH_SHA}/skills/a/SKILL.md"] = SKILL_A.encode()
    harness.github.client = GitHub(lambda: fake.client, sleep=fake.sleep,
                                   on_send=lambda k: harness.tuner_state.spend_github(harness.tuner._today(), k))
    return fake


async def settle(h: Harness) -> None:
    await asyncio.gather(*list(h.repo_tasks))


async def add(client, address=ADDR, licence="MIT", **kw):
    return await client.post("/skilltuner/repos", json={"address": address, "licence": licence, **kw})


async def test_sources_lists_casemark_github_and_the_licences(client, harness, gh):
    r = (await client.get("/skilltuner/sources")).json()
    assert r["casemark"] == {"label": "agentskills.finance", "builtin": True, "spent_today": 0, "budget": 40,
                             "paused": False}
    assert r["github"]["api_today"] == 0 and r["github"]["files_today"] == 0 and r["github"]["paused"] is False
    assert r["repos"] == []
    assert r["licences"] == ["Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "CC-BY-4.0"]


async def test_check_reads_the_repository_once(client, gh):
    r = await client.post("/skilltuner/repos/check", json={"address": "https://github.com/o/r/tree/dev/skills"})
    assert r.status_code == 200
    assert r.json() == {"address": "https://github.com/o/r/tree/dev/skills", "default_branch": "main",
                        "github_licence": "MIT", "licence": "MIT"}
    assert len(gh.requests) == 1
    gh.repos["o/r"] = {**gh.repos["o/r"], "license": {"spdx_id": "GPL-3.0"}}
    r = (await client.post("/skilltuner/repos/check", json={"address": ADDR})).json()
    assert r["github_licence"] == "GPL-3.0" and r["licence"] == ""


async def test_check_answers_for_each_failure(client, gh):
    bad = await client.post("/skilltuner/repos/check", json={"address": "https://example.com/o/r"})
    assert bad.status_code == 422 and "github.com" in bad.json()["detail"]
    r = await client.post("/skilltuner/repos/check", json={"address": "https://github.com/o/missing"})
    assert (r.status_code, r.json()["detail"]) == (404, "No such public repository on GitHub.")
    gh.status = 429
    r = await client.post("/skilltuner/repos/check", json={"address": ADDR})
    assert (r.status_code, r.json()["detail"]) == (429, "GitHub is limiting requests. Try again later.")
    gh.status = 500
    r = await client.post("/skilltuner/repos/check", json={"address": ADDR})
    assert (r.status_code, r.json()["detail"]) == (502, "GitHub could not answer. Try again later.")
    gh.status = 200
    gh.raises = httpx.ConnectError("x")
    r = await client.post("/skilltuner/repos/check", json={"address": ADDR})
    assert (r.status_code, r.json()["detail"]) == (502, "GitHub could not be reached.")


async def test_add_saves_the_entry_and_reads_in_the_background(client, harness, gh):
    r = await add(client, licence="mit", index="data/index.json")
    assert r.status_code == 200, r.text
    repo = r.json()
    assert repo["address"] == ADDR and repo["label"] == "github.com/o/r" and repo["licence"] == "MIT"
    assert repo["reading"] is True and len(repo["id"]) == 16
    saved = TunerSettings.load(harness.config.vault_root).repos
    assert [(e.id, e.owner, e.repo, e.index, e.licence) for e in saved] == [
        (repo["id"], "o", "r", "data/index.json", "MIT")]
    assert events(harness, "repo_added") == [{"event": "repo_added", "id": repo["id"], "owner": "o", "repo": "r"}]
    await settle(harness)
    [row] = (await client.get("/skilltuner/sources")).json()["repos"]
    assert row["reading"] is False and row["skills"] == 1 and row["one_by_one"] is True
    assert row["commit"] == GH_SHA[:12] and row["error"] == "" and row["last_read_at"]
    [read] = events(harness, "repo_read")
    assert read["id"] == repo["id"] and read["skills"] == 1 and read["from"] == "one_by_one" and read["error"] == ""
    assert read["commit"] == GH_SHA[:12]
    assert "Alpha" not in str(read)
    assert (await client.get("/skilltuner/sources")).json()["github"]["api_today"] >= 3


async def test_add_refuses_what_it_should(client, harness, gh):
    r = await add(client, address="http://github.com/o/r")
    assert r.status_code == 422 and r.json()["detail"].startswith("Paste a GitHub address")
    r = await add(client, licence="GPL-3.0")
    assert (r.status_code, r.json()["detail"]) == (422, "Pick a licence from the list.")
    assert (await add(client, index="../x")).status_code == 422
    assert (await add(client, licence="")).status_code == 200  # "Don't use"
    r = await add(client, address="https://github.com/O/R")
    assert (r.status_code, r.json()["detail"]) == (409, "That repository is already listed.")
    assert (await add(client, address="https://github.com/o/r/tree/main")).status_code == 200  # another ref
    for i in range(8):
        assert (await add(client, address=f"https://github.com/o/r{i}")).status_code == 200
    r = await add(client, address="https://github.com/o/last")
    assert (r.status_code, r.json()["detail"]) == (409, "At most 10 repositories.")
    assert len(TunerSettings.load(harness.config.vault_root).repos) == 10
    await harness.close_tuner()


async def test_two_adds_at_once_cannot_pass_the_ten_check(client, harness, gh):
    for i in range(9):
        assert (await add(client, address=f"https://github.com/o/a{i}")).status_code == 200
    got = await asyncio.gather(*[add(client, address=f"https://github.com/o/b{i}") for i in range(4)])
    assert sorted(r.status_code for r in got) == [200, 409, 409, 409]
    await harness.close_tuner()


async def test_put_changes_only_the_licence(client, harness, gh):
    repo = (await add(client)).json()
    await settle(harness)
    r = await client.put(f"/skilltuner/repos/{repo['id']}", json={"licence": "Apache-2.0"})
    assert r.status_code == 200 and r.json()["licence"] == "Apache-2.0" and r.json()["skills"] == 1
    assert TunerSettings.load(harness.config.vault_root).repos[0].licence == "Apache-2.0"
    r = await client.put(f"/skilltuner/repos/{repo['id']}", json={"licence": "WTFPL"})
    assert (r.status_code, r.json()["detail"]) == (422, "Pick a licence from the list.")
    r = await client.put("/skilltuner/repos/0123456789abcdef", json={"licence": "MIT"})
    assert (r.status_code, r.json()["detail"]) == (404, "No such repository.")


async def test_read_again_starts_a_read_and_refuses_a_second(client, harness, gh):
    repo = (await add(client)).json()
    r = await client.post(f"/skilltuner/repos/{repo['id']}/read", json={})
    assert (r.status_code, r.json()["detail"]) == (409, "That repository is being read already.")
    await settle(harness)
    r = await client.post(f"/skilltuner/repos/{repo['id']}/read", json={})
    assert r.status_code == 200 and r.json()["reading"] is True
    await settle(harness)
    assert len(events(harness, "repo_read")) == 2
    r = await client.post("/skilltuner/repos/0123456789abcdef/read", json={})
    assert (r.status_code, r.json()["detail"]) == (404, "No such repository.")


async def test_a_failed_read_is_shown_with_the_repository(client, harness, gh):
    gh.status = 404
    repo = (await add(client, address="https://github.com/o/gone")).json()
    await settle(harness)
    [row] = (await client.get("/skilltuner/sources")).json()["repos"]
    assert row["error"] == "not_found" and row["skills"] == 0 and row["reading"] is False
    assert events(harness, "repo_read")[0]["error"] == "not_found"
    gh.status = 500
    await client.post(f"/skilltuner/repos/{repo['id']}/read", json={})
    await settle(harness)
    assert (await client.get("/skilltuner/sources")).json()["repos"][0]["error"] == "provider"


async def test_delete_removes_the_list_and_keeps_pending_suggestions(client, harness, gh):
    repo = (await add(client)).json()
    await settle(harness)
    listfile = harness.github.store._path(repo["id"])
    assert listfile.exists()
    s = suggest(harness, source="gh:o/r:skills/a")
    r = await client.delete(f"/skilltuner/repos/{repo['id']}")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert not listfile.exists() and harness.tuner.settings.repos == []
    assert TunerSettings.load(harness.config.vault_root).repos == []
    assert harness.suggestions.get(s.id) is not None
    assert events(harness, "repo_removed") == [{"event": "repo_removed", "id": repo["id"]}]
    assert (await client.delete(f"/skilltuner/repos/{repo['id']}")).status_code == 404


async def test_a_repository_removed_while_reading_stays_removed(client, harness, gh):
    gh.hold = asyncio.Event()
    repo = (await add(client)).json()
    for _ in range(300):
        if any("/git/trees/" in r.url.path for r in gh.requests):
            break
        await asyncio.sleep(0.01)
    assert (await client.delete(f"/skilltuner/repos/{repo['id']}")).status_code == 200
    gh.hold.set()
    await settle(harness)
    assert not harness.github.store._path(repo["id"]).exists()
    assert (await client.get("/skilltuner/sources")).json()["repos"] == []


async def test_settings_save_keeps_the_repositories(client, harness, gh):
    repo = (await add(client)).json()
    await settle(harness)
    assert (await client.put("/skilltuner/settings", json={"enabled": False})).status_code == 200
    assert [e.id for e in TunerSettings.load(harness.config.vault_root).repos] == [repo["id"]]


async def test_reject_remembers_the_skills_own_name(client, harness):
    s = suggest(harness, source="gh:o/r:skills/a")
    harness.suggestions.add(
        Suggestion(**{**s.__dict__, "id": secrets.token_hex(16), "slug": "x-tool", "source_name": "Alpha Skill"}),
        BuiltSkill(slug="x-tool", files={"SKILL.md": SKILL}))
    other = [x for x in harness.suggestions.list() if x.slug == "x-tool"][0]
    await client.post(f"/skilltuner/suggestions/{other.id}/reject", json={})
    assert harness.tuner_state.rejected_names == {"alpha skill"}


async def test_suggestions_carry_the_source_and_left_out(client, harness):
    suggest(harness)
    [item] = (await client.get("/skilltuner/suggestions")).json()
    assert item["source"] == "agentskills.finance" and item["left_out"] is False


async def test_no_repository_answer_holds_a_disk_path(client, harness, gh):
    texts = [(await client.get("/skilltuner/sources")).text]
    repo = await add(client)
    texts += [repo.text, (await client.post("/skilltuner/repos/check", json={"address": ADDR})).text]
    await settle(harness)
    texts += [(await client.get("/skilltuner/sources")).text,
              (await client.put(f"/skilltuner/repos/{repo.json()['id']}", json={"licence": "MIT"})).text,
              (await client.delete(f"/skilltuner/repos/{repo.json()['id']}")).text,
              (await client.delete("/skilltuner/repos/0123456789abcdef")).text]
    root = str(harness.config.vault_root.parent)
    for t in texts:
        assert root not in t and root.replace("\\", "\\\\") not in t


async def test_a_later_good_read_clears_a_github_wide_error(client, harness, gh):
    gh.raises = httpx.ConnectError("x")
    repo = (await add(client)).json()
    await settle(harness)
    assert (await client.get("/skilltuner/sources")).json()["repos"][0]["error"] == "network"
    gh.raises = None
    entry = harness.tuner.settings.repos[0]
    await harness.github.read_repo(entry)  # the pass reads it directly
    assert (await client.get("/skilltuner/sources")).json()["repos"][0]["error"] == ""
    assert harness.github.errors == {} and repo["id"] == entry.id


async def test_a_newer_stored_error_replaces_the_memory_one(client, harness, gh):
    gh.raises = httpx.ConnectError("x")
    await add(client)
    await settle(harness)
    gh.raises = None
    gh.status = 404
    with pytest.raises(Exception):
        await harness.github.read_repo(harness.tuner.settings.repos[0])
    assert (await client.get("/skilltuner/sources")).json()["repos"][0]["error"] == "not_found"


async def test_a_removed_repository_gets_no_error_or_reset(client, harness, gh):
    gh.hold = asyncio.Event()
    gh.raises = None
    repo = (await add(client)).json()
    for _ in range(300):
        if any("/git/trees/" in r.url.path for r in gh.requests):
            break
        await asyncio.sleep(0.01)
    await client.delete(f"/skilltuner/repos/{repo['id']}")
    gh.hold.set()
    await settle(harness)
    assert harness.github.errors == {} and harness.github.changed is False


async def test_check_maps_budget_and_source_changed(client, harness, gh):
    gh.repos["o/r"] = {"private": False, "default_branch": "bad branch"}
    r = await client.post("/skilltuner/repos/check", json={"address": ADDR})
    assert (r.status_code, r.json()["detail"]) == (502, "GitHub answered in a way SkillTuner does not understand.")
    for _ in range(30):
        harness.tuner_state.spend_github(harness.tuner._today(), "api")
    r = await client.post("/skilltuner/repos/check", json={"address": ADDR})
    assert (r.status_code, r.json()["detail"]) == (
        429, "SkillTuner's GitHub requests for today are used up. Try again tomorrow.")


async def test_get_one_suggestion_carries_source_and_left_out(client, harness):
    s = suggest(harness)
    full = (await client.get(f"/skilltuner/suggestions/{s.id}")).json()
    assert full["source"] == "agentskills.finance" and full["left_out"] is False

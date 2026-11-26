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

from ..conftest import ScriptedProvider

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
    assert len(paths) == 3
    for path in paths:
        url = path.replace("{id}", "x" * 32).replace("{slug}", "pdf-tool")
        assert (await client.post(url)).status_code == 422
        assert (await client.post(url, content=b"x", headers={"Content-Type": "text/plain"})).status_code == 422


async def test_status_says_why_not(client, harness):
    r = (await client.get("/skilltuner/status")).json()
    assert r == {"enabled": False, "ready": False, "why_not": "Switch on dreaming first.", "last_pass_at": "",
                 "error": "", "pending": 0, "spent_today": 0, "budget": 40}
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

"""bot-prompts/: one markdown file per bot identity. Frontmatter carries the
name, role, and optional skill refs; the body is the instructions. The file
stem is the bot id, so it is stable across restarts. The harness loads the
directory at start and writes it on every create or edit through the API."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.bot_prompts import BotPrompt, load_dir, parse, render, save
from src.harness.config import HarnessConfig
from src.harness.main import build_harness
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog

from .conftest import REPO

PROMPT = """---
name: Research partner
role: Turn the noise into something useful.
skills: [research-brief]
---
Research product and technology topics. Summarize evidence.

Ask before publishing.
"""


def test_load_dir_reads_identity_from_frontmatter_and_body(tmp_path: Path):
    (tmp_path / "research-partner.md").write_text(PROMPT, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    prompts = load_dir(tmp_path)
    assert [p.id for p in prompts] == ["research-partner"]
    p = prompts[0]
    assert p.name == "Research partner" and p.role == "Turn the noise into something useful."
    assert p.instructions == "Research product and technology topics. Summarize evidence.\n\nAsk before publishing."
    assert p.skills == ["research-brief"]


def test_load_dir_reports_a_broken_file_and_keeps_the_rest(tmp_path: Path):
    (tmp_path / "good.md").write_text(PROMPT, encoding="utf-8")
    (tmp_path / "bad.md").write_text("no frontmatter here", encoding="utf-8")
    prompts, errors = load_dir(tmp_path, report=True)
    assert [p.id for p in prompts] == ["good"]
    assert len(errors) == 1 and "bad.md" in errors[0]


def test_save_round_trips_and_uniquifies_the_slug(tmp_path: Path):
    a = save(tmp_path, BotPrompt(id="", name="Ops Bot!", role="Keep things running", instructions="Watch the queue.", skills=[]))
    b = save(tmp_path, BotPrompt(id="", name="Ops Bot!", role="Another", instructions="", skills=["research-brief"]))
    assert a.id == "ops-bot" and b.id == "ops-bot-2"
    assert (tmp_path / "ops-bot.md").exists() and (tmp_path / "ops-bot-2.md").exists()
    back = {p.id: p for p in load_dir(tmp_path)}
    assert back["ops-bot"].instructions == "Watch the queue." and back["ops-bot-2"].skills == ["research-brief"]
    a.role = "Changed"
    save(tmp_path, a)  # an existing id rewrites in place
    assert {p.id: p.role for p in load_dir(tmp_path)}["ops-bot"] == "Changed"


def test_every_shipped_bot_prompt_loads():
    """A malformed frontmatter line silently drops a shipped bot, so every bot-prompts/*.md must parse."""
    prompts, errors = load_dir(REPO / "bot-prompts", report=True)
    assert errors == []
    by_id = {p.id: p for p in prompts}
    assert "research-partner" in by_id
    assert "current facts" in by_id["research-partner"].role


def test_build_harness_makes_bots_from_the_directory(monkeypatch, vault: Path, tmp_path: Path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ZEN_MEMORY_BACKEND", "local")
    prompts = tmp_path / "bot-prompts"
    prompts.mkdir()
    (prompts / "research-partner.md").write_text(PROMPT, encoding="utf-8")
    (prompts / "generalist.md").write_text("---\nname: Generalist\nrole: Anything\n---\nHelp.\n", encoding="utf-8")
    h = build_harness(HarnessConfig(vault_root=vault, skills_dir=Path("skills"), bot_prompts_dir=prompts))
    assert set(h.bots) == {"research-partner", "generalist"}
    assert [s.slug for s in h.bots["research-partner"].skills] == ["research-brief"]
    assert sorted(s.slug for s in h.bots["generalist"].skills) == ["archify", "design-studio", "discernment-nudge", "doc-coauthoring", "docx", "learn-company", "pdf", "pptx", "prompt-master", "research-brief", "skill-creator", "weekly-digest", "xlsx"]  # no skills key: every catalog skill


def test_build_harness_with_an_empty_directory_has_no_bots(monkeypatch, vault: Path, tmp_path: Path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ZEN_MEMORY_BACKEND", "local")
    h = build_harness(HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", bot_prompts_dir=tmp_path / "none"))
    assert h.bots == {}


async def test_api_create_and_patch_persist_the_prompt_file(vault: Path, tmp_path: Path):
    prompts = tmp_path / "bot-prompts"
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", bot_prompts_dir=prompts)
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        r = await c.post("/bots", json={"name": "Writer", "role": "Write things", "instructions": "Short sentences."})
        assert r.status_code == 201 and r.json()["id"] == "writer"
        assert (prompts / "writer.md").exists()
        r = await c.patch("/bots/writer", json={"instructions": "Shorter."})
        assert r.status_code == 200
    back = {p.id: p for p in load_dir(prompts)}
    assert back["writer"].instructions == "Shorter." and back["writer"].role == "Write things"


def test_own_servers_round_trip_through_the_file(tmp_path: Path):
    (tmp_path / "dark.md").write_text("---\nname: Dark\nrole: Edit images\nmcps: [files, magick]\n---\nGo.\n", encoding="utf-8")
    [p] = load_dir(tmp_path)
    assert p.mcps == ["files", "magick"]
    save(tmp_path, p)
    assert load_dir(tmp_path)[0].mcps == ["files", "magick"]
    save(tmp_path, BotPrompt(id="plain", name="Plain", role="r"))
    assert "mcps" not in (tmp_path / "plain.md").read_text(encoding="utf-8")  # none: no key written
    (tmp_path / "bad.md").write_text("---\nname: Bad\nrole: r\nmcps: files\n---\n", encoding="utf-8")
    _, errors = load_dir(tmp_path, report=True)
    assert any("bad.md" in e and "mcps" in e for e in errors)


async def test_build_harness_and_the_api_keep_a_bots_own_servers(monkeypatch, vault: Path, tmp_path: Path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ZEN_MEMORY_BACKEND", "local")
    prompts = tmp_path / "bot-prompts"
    prompts.mkdir()
    (prompts / "dark.md").write_text("---\nname: Dark\nrole: Edit images\nskills: []\nmcps: [files, magick]\n---\nGo.\n", encoding="utf-8")
    h = build_harness(HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", bot_prompts_dir=prompts))
    assert h.bots["dark"].mcps == ["files", "magick"]
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        r = await c.patch("/bots/dark", json={"instructions": "Go now."})  # what the GUI sends: no mcps key
        assert r.status_code == 200 and r.json()["mcps"] == ["files", "magick"]
        assert load_dir(prompts)[0].mcps == ["files", "magick"]
        r = await c.patch("/bots/dark", json={"mcps": ["magick"]})
        assert r.json()["mcps"] == ["magick"] and load_dir(prompts)[0].mcps == ["magick"]
        r = await c.post("/bots", json={"name": "Scout", "role": "Look", "mcps": ["files"]})
        assert r.status_code == 201 and r.json()["mcps"] == ["files"]
    assert {p.id: p.mcps for p in load_dir(prompts)}["scout"] == ["files"]


def test_every_bot_speaks_in_the_shared_voice_last():
    """The voice closes every system prompt, so it outranks a bot's or a skill's report template on tone."""
    from src.harness.bot import VOICE

    bot = Bot(name="Analyst", role="Analyse things", instructions="Report format:\n- Summary\n- Findings\n- Confidence")
    prompt = bot.system_prompt([], ["the user keeps bees"])
    assert prompt.endswith(VOICE)
    assert "barstool" in VOICE and "Markdown" in VOICE
    assert prompt.index("Report format") < prompt.index(VOICE)


def test_base_prompt_has_no_facts_or_skills():
    from src.harness.bot import VOICE

    bot = Bot(name="Analyst", role="Analyse things", instructions="Be brief.")
    p = bot.base_prompt()
    assert "Things you remember" not in p and "Skills available" not in p and bot.role in p
    assert "Be brief." in p and p.endswith(VOICE)
    assert bot.context_text([], None) == "" and bot.context_text([], []) == ""
    assert "Things you remember:\n- the user keeps bees" in bot.context_text([], ["the user keeps bees"])
    assert bot.system_prompt([], ["x"]).startswith(p.split("\n\n")[0])



def test_picasso_writes_cards_and_explainer_attaches():
    """PicassoBot's results and Explainer's diagrams show as preview cards, so the prompts must produce them."""
    picasso = (REPO / "bot-prompts" / "picasso-bot.md").read_text(encoding="utf-8")
    assert "[![" in picasso and "Photo by [photographer](photographer_url) on Pexels" in picasso
    assert "not previews" not in picasso
    assert "attach_file" in (REPO / "bot-prompts" / "explainer.md").read_text(encoding="utf-8")


def test_darkroom_edits_with_magick_and_shows_the_result():
    """ImageEditor (id darkroom-bot) reaches the magick tools only through routing, so its role must name them, and every
    option it suggests must be one the magick gate lets through."""
    import re

    from src.harness.magick_guard import OPTIONS

    [p] = [b for b in load_dir(REPO / "bot-prompts") if b.id == "darkroom-bot"]
    assert p.name == "ImageEditor" and "ImageMagick" in p.role and "magick" in p.role
    assert p.skills == [] and p.mcps == ["files", "magick"]  # it always works on disk: never left to routing
    assert "attach_file" in p.instructions and "`workspace`" in p.instructions
    [edits] = [line for line in p.instructions.splitlines() if line.startswith("Common edits:")]
    suggested = set(re.findall(r"[`\s][-+]([a-z][a-z-]+)\b", edits))
    assert suggested and not suggested - set(OPTIONS), suggested - set(OPTIONS)


def test_studio_flag_round_trips():
    p = parse("---\nname: D\nrole: r\nstudio: true\n---\nbody", "d")
    assert p.studio is True
    assert parse(render(p), "d").studio is True
    assert "studio" not in render(parse("---\nname: D\nrole: r\n---\nbody", "d"))


def test_studio_must_be_a_boolean():
    with pytest.raises(ValueError, match="studio must be true or false"):
        parse("---\nname: D\nrole: r\nstudio: yes please\n---\n", "d")


def test_the_designer_bot_file_is_a_studio_bot():
    p = parse(Path("bot-prompts/designer.md").read_text(encoding="utf-8"), "designer")
    assert p.studio is True and p.skills == ["design-studio"] and p.mcps == ["files"]
    for must in ("designs/", "data-zen-id", "attach_file", "question-form", "create_directory", "edit_file"):
        assert must in p.instructions

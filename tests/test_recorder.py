"""recorder: a driven browser's actions and trace, no pixels; source
field with screen-capture reserved; length guide from config; every
field value through the exclusion filter with secrets stored as vault
handle names; draft SKILL.md through the loader with steps by intent
and the loader's states; replay offline on the mock provider; API."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.harness.loop import SessionState
from src.memory.exclusion import exclusion
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.skills.loader import Reason, SkillConfig, SkillState, load_skill
from src.skills.recorder import Recorder, RecorderConfig, draft_skill, replay
from src.skills.recording import Action, Recording, UnsupportedSource
from src.vault_settings import user_skills_dir

from .conftest import ScriptedProvider, completion


class FakeDriver:
    """A browser the tests drive by hand: actions are pushed in, replays are logged."""

    kind = "fake-browser"

    def __init__(self) -> None:
        self.on_action = None
        self.headed = None
        self.ran: list[Action] = []
        self.started = self.stopped = False

    async def start(self, on_action, *, headed: bool) -> None:
        self.on_action, self.headed, self.started = on_action, headed, True

    async def stop(self) -> list[dict]:
        self.stopped = True
        return [{"t": 0.0, "event": "trace-start"}, {"t": 0.5, "event": "trace-end"}]

    async def run(self, action: Action) -> str:
        self.ran.append(action)
        return f"ok {action.kind} {action.target}"


def make(ledger, max_s: float = 600) -> tuple[Recorder, FakeDriver]:
    d = FakeDriver()
    return Recorder(d, ledger, RecorderConfig(max_seconds=max_s, headed=True)), d


def invoice_flow(rec: Recorder) -> None:
    rec.on_action("navigate", "https://app.example.com/invoices", intent="open the invoices page")
    rec.on_action("click", "button#filter", intent="open the filter")
    rec.on_action("select", "period", value="last month", intent="filter by last month")
    rec.on_action("type", "customer", value="Acme", intent="search for Acme")
    rec.on_action("click", "a.export-csv", intent="export CSV")


# -- recording format --------------------------------------------------------


async def test_records_actions_and_trace_not_pixels(ledger: Ledger):
    rec, driver = make(ledger)
    await rec.start("export invoices")
    assert driver.started and driver.headed is True and rec.status()["state"] == "recording"
    invoice_flow(rec)
    recording = await rec.stop()
    assert driver.stopped and recording.source == "browser-actions" and recording.name == "export invoices"
    assert [a.kind for a in recording.actions] == ["navigate", "click", "select", "type", "click"]
    assert recording.actions[0].url == "https://app.example.com/invoices"
    assert recording.trace[-1]["event"] == "trace-end" and recording.duration_s >= 0
    d = recording.to_dict()
    assert set(d) >= {"id", "name", "source", "actions", "trace", "max_s", "duration_s", "redactions", "truncated"}
    assert "pixels" not in json.dumps(d) and "png" not in json.dumps(d)
    assert Recording.from_dict(d).to_dict() == d
    e = ledger.tail(kind="recording")[-1]
    assert e.data["event"] == "stopped" and e.data["actions"] == 5 and e.data["source"] == "browser-actions"


def test_screen_capture_is_reserved_and_unused():
    d = Recording(name="x").to_dict()
    d["source"] = "screen-capture"
    with pytest.raises(UnsupportedSource, match="reserved"):
        Recording.from_dict(d)
    d["source"] = "webcam"
    with pytest.raises(UnsupportedSource):
        Recording.from_dict(d)
    assert Recording.SOURCES == ("browser-actions", "screen-capture")


async def test_length_guide_is_config(ledger: Ledger, monkeypatch):
    monkeypatch.setenv("ZEN_RECORD_MAX_S", "123")
    assert RecorderConfig().max_seconds == 123
    rec, _ = make(ledger, max_s=0.02)
    await rec.start("short")
    rec.on_action("navigate", "https://a.example", intent="open")
    time.sleep(0.03)
    rec.on_action("click", "#late", intent="too late")
    recording = await rec.stop()
    assert [a.kind for a in recording.actions] == ["navigate"] and recording.truncated and recording.max_s == 0.02
    assert ledger.tail(kind="recording")[-1].data["truncated"] is True


# -- secrets -------------------------------------------------------------------


async def test_secret_probe_in_a_form_field_is_stored_as_a_handle(ledger: Ledger):
    rec, _ = make(ledger)
    await rec.start("login")
    rec.on_action("navigate", "https://app.example.com/login", intent="open login")
    rec.on_action("type", "email", value="alex@example.com", intent="enter the email")
    rec.on_action("type", "password", value="hunter2hunter2hunter2", intent="enter the password")
    rec.on_action("type", "api_key", value="sk-or-v1-abcdefghijklmnopqrstuvwxyz", intent="paste the api key")
    recording = await rec.stop()
    blob = json.dumps(recording.to_dict())
    assert "hunter2hunter2hunter2" not in blob and "sk-or-v1" not in blob
    values = {a.target: a.value for a in recording.actions if a.kind == "type"}
    assert values == {"email": "alex@example.com", "password": "vault:PASSWORD", "api_key": "vault:API_KEY"}
    assert recording.redactions == 2
    # the real value is now a registered secret everywhere else too
    assert "registered-secret" in exclusion.find("x hunter2hunter2hunter2 y")
    e = ledger.tail(kind="recording")[-1]
    assert e.data["redactions"] == 2 and "hunter2" not in json.dumps(e.data)


# -- draft ---------------------------------------------------------------------


async def test_draft_writes_skill_md_through_the_loader(ledger: Ledger, tmp_path: Path):
    rec, _ = make(ledger)
    await rec.start("export invoices")
    invoice_flow(rec)
    rec.on_action("type", "password", value="hunter2hunter2hunter3", intent="enter the password")
    recording = await rec.stop()
    skills_dir = tmp_path / "skills"
    res = draft_skill(recording, name="Export invoices", use="When asked to export last month's invoices as CSV.", skills_dir=skills_dir)
    assert res.state == SkillState.staged and res.ok and res.path == skills_dir / "export-invoices" / "SKILL.md"
    skill = res.skill
    assert skill.slug == "export-invoices" and skill.access == ["browser"]
    assert skill.steps == [
        "Open app.example.com/invoices",
        "Open the filter",
        "Filter by last month",
        "Search for Acme",
        "Export CSV",
        "Enter the password",
    ]
    text = res.path.read_text(encoding="utf-8")
    assert "source: browser-actions" in text and "vault:PASSWORD" in text and "hunter2" not in text
    assert "- password: vault:PASSWORD" in text and "- customer: Acme" in text
    # the same loader the vertical slice uses reads it back
    again = load_skill(res.path)
    assert again.state == SkillState.added and again.skill.steps == skill.steps

    # with a catalog the draft is added
    catalog = Catalog()
    res2 = draft_skill(recording, name="Export invoices", use="u", skills_dir=tmp_path / "s2", catalog=catalog)
    assert res2.state == SkillState.added and catalog.skill("export-invoices") is not None


async def test_draft_states_and_reasons(ledger: Ledger, tmp_path: Path):
    rec, _ = make(ledger)
    await rec.start("empty")
    empty = await rec.stop()
    r = draft_skill(empty, name="Empty", use="u", skills_dir=tmp_path)
    assert r.state == SkillState.nothing and r.reason == Reason.empty and not (tmp_path / "empty").exists()

    await rec.start("flow")
    invoice_flow(rec)
    recording = await rec.stop()
    big = draft_skill(recording, name="Flow", use="u", skills_dir=tmp_path, config=SkillConfig(max_bytes=10))
    assert big.state == SkillState.nothing and big.reason == Reason.too_large

    full = draft_skill(recording, name="Flow", use="u", skills_dir=tmp_path / "f", catalog=Catalog(), config=SkillConfig(max_per_bot=0))
    assert full.state == SkillState.nothing and full.reason == Reason.too_many_skills

    recording.source = "screen-capture"
    uns = draft_skill(recording, name="Flow", use="u", skills_dir=tmp_path / "u")
    assert uns.state == SkillState.nothing and uns.reason == Reason.unsupported


# -- replay ----------------------------------------------------------------------


async def test_replay_runs_the_draft_headless_on_the_mock_provider(ledger: Ledger, tmp_path: Path):
    rec, _ = make(ledger)
    await rec.start("export invoices")
    invoice_flow(rec)
    recording = await rec.stop()
    res = draft_skill(recording, name="Export invoices", use="u", skills_dir=tmp_path)
    driver = FakeDriver()
    provider = ScriptedProvider([
        completion(calls=[("c1", "browser", {"action": "navigate", "target": "https://app.example.com/invoices"})]),
        completion(calls=[("c2", "browser", {"action": "click", "target": "a.export-csv"})]),
        completion("Replayed: invoices exported."),
    ])
    session = await replay(res.skill, provider, driver, ledger, cwd=tmp_path)
    assert session.state == SessionState.done and session.result == "Replayed: invoices exported."
    assert [(a.kind, a.target) for a in driver.ran] == [("navigate", "https://app.example.com/invoices"), ("click", "a.export-csv")]
    assert driver.headed is False
    system = provider.calls[0][-2].content  # the per-message context
    assert "Export invoices" in system and "Open app.example.com/invoices" in system
    assert provider.calls[1][-1].role == "tool" and "ok navigate" in provider.calls[1][-1].content


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="live: needs OPENROUTER_API_KEY")
async def test_replay_live_on_openrouter(ledger: Ledger, tmp_path: Path):
    import src.providers.openrouter  # noqa: F401 - registers the provider
    from src.providers import registry

    rec, _ = make(ledger)
    await rec.start("export invoices")
    invoice_flow(rec)
    recording = await rec.stop()
    res = draft_skill(recording, name="Export invoices", use="u", skills_dir=tmp_path)
    provider = registry.get("openrouter", key_ref="env:OPENROUTER_API_KEY", model=os.environ.get("OPENROUTER_MODEL"))
    driver = FakeDriver()
    session = await replay(res.skill, provider, driver, ledger, cwd=tmp_path, max_steps=20)
    assert session.state == SessionState.done and driver.ran


# -- api -------------------------------------------------------------------------


async def test_recorder_endpoints(vault: Path, tmp_path: Path):
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path, skills_dir=tmp_path / "skills")
    driver = FakeDriver()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog(), recorder_driver=driver)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        assert (await c.get("/recorder/status")).json()["state"] == "idle"
        r = await c.post("/recorder/start", json={"name": "export invoices"})
        assert r.status_code == 200 and r.json()["state"] == "recording" and driver.started
        assert (await c.post("/recorder/start", json={"name": "again"})).status_code == 409
        for kind, target, value, intent in [
            ("navigate", "https://app.example.com/invoices", None, "open the invoices page"),
            ("type", "password", "hunter2hunter2hunter4", "enter the password"),
            ("click", "a.export-csv", None, "export CSV"),
        ]:
            r = await c.post("/recorder/actions", json={"kind": kind, "target": target, "value": value, "intent": intent})
            assert r.status_code == 200
        assert (await c.get("/recorder/status")).json()["actions"] == 3
        r = await c.post("/recorder/stop")
        assert r.status_code == 200 and r.json()["source"] == "browser-actions" and "hunter2" not in r.text
        r = await c.post("/recorder/draft", json={"name": "Export invoices", "use": "When asked to export invoices."})
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "added" and body["slug"] == "export-invoices" and body["path"].endswith("SKILL.md")
        assert any(s["name"] == "Export invoices" and s["kind"] == "skill" for s in (await c.get("/skills")).json())
        assert (await c.post("/recorder/stop")).status_code == 409


async def test_recorder_unavailable_without_a_browser(vault: Path, tmp_path: Path):
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog(), recorder_driver=None, recorder_autodetect=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        r = await c.post("/recorder/start", json={"name": "x"})
        assert r.status_code == 503 and "playwright" in r.json()["detail"].lower()


# -- review fixes ----------------------------------------------------------------


async def test_common_password_fields_and_password_type_are_secrets(ledger: Ledger, tmp_path: Path):
    rec, _ = make(ledger)
    await rec.start("login")
    rec.on_action("navigate", "https://wp.example.com/wp-login.php", intent="open login")
    rec.on_action("type", "pwd", value="correct horse battery", intent="enter the password")
    rec.on_action("type", "pin", value="482915", intent="enter the pin")
    rec.on_action("type", "email", value="hidden-by-type", intent="enter", secret=True)  # <input type=password>
    recording = await rec.stop()
    values = {a.target: a.value for a in recording.actions if a.kind == "type"}
    assert values == {"pwd": "vault:PWD", "pin": "vault:PIN", "email": "vault:EMAIL"}
    res = draft_skill(recording, name="WP login", use="u", skills_dir=tmp_path)
    text = res.path.read_text(encoding="utf-8")
    for leaked in ("correct horse battery", "482915", "hidden-by-type"):
        assert leaked not in text


async def test_field_name_rule_matches_whole_tokens_only(ledger: Ledger):
    rec, _ = make(ledger)
    await rec.start("profile")
    rec.on_action("type", "author", value="Alex Rivera", intent="enter the author")
    rec.on_action("type", "oauth_provider", value="github", intent="choose")
    rec.on_action("type", "api key", value="plain-looking", intent="paste")
    recording = await rec.stop()
    values = {a.target: a.value for a in recording.actions}
    assert values["author"] == "Alex Rivera" and values["oauth_provider"] == "github" and values["api key"] == "vault:API_KEY"
    # a plain name typed into a non-secret field must not poison the global filter
    assert exclusion.find("Alex Rivera wrote this") == []


async def test_targets_intents_and_urls_pass_the_filter(ledger: Ledger, tmp_path: Path):
    rec, _ = make(ledger)
    await rec.start("reset")
    rec.on_action("navigate", "https://app.example.com/reset?token=sk-or-v1-abcdefghijklmnopqrstuvwxyz#access_token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop", intent="open reset")
    rec.on_action("click", "button.reveal", intent="copy key ghp_abcdefghijklmnopqrstuvwxyz0123 shown on screen")
    recording = await rec.stop()
    nav, click = recording.actions
    assert nav.target == "https://app.example.com/reset" and nav.url == "https://app.example.com/reset"
    assert "ghp_" not in click.intent and "[redacted" in click.intent
    text = draft_skill(recording, name="Reset", use="u", skills_dir=tmp_path).path.read_text(encoding="utf-8")
    assert "sk-or-v1" not in text and "ghp_" not in text and "eyJ" not in text


async def test_draft_never_overwrites_a_foreign_skill_and_reports_the_catalog_result(ledger: Ledger, tmp_path: Path):
    skills_dir = tmp_path / "skills"
    foreign = skills_dir / "export-invoices" / "SKILL.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("---\nname: Export invoices\nuse: a hand-written skill\n---\nkeep me\n", encoding="utf-8")
    rec, _ = make(ledger)
    await rec.start("export invoices")
    invoice_flow(rec)
    recording = await rec.stop()
    res = draft_skill(recording, name="Export invoices", use="u", skills_dir=skills_dir)
    assert res.state == SkillState.nothing and res.reason == Reason.duplicate
    assert "keep me" in foreign.read_text(encoding="utf-8")
    # a second draft of a recorder-made skill replaces it, and the catalog result is the truth
    catalog = Catalog()
    first = draft_skill(recording, name="Export CSV", use="u", skills_dir=skills_dir, catalog=catalog)
    assert first.state == SkillState.added
    second = draft_skill(recording, name="Export CSV", use="u2", skills_dir=skills_dir, catalog=catalog)
    assert second.state == SkillState.added and catalog.skill("export-csv").use == "u2"


# -- user skills beside the vault (ZEN_USER_CONTENT) --------------------------------


def _skill_file(folder: Path, slug: str, use: str) -> None:
    (folder / slug).mkdir(parents=True, exist_ok=True)
    (folder / slug / "SKILL.md").write_text(f"---\nname: {slug.upper()}\nuse: {use}\n---\n# {slug}\n", encoding="utf-8")


def test_user_skills_win_over_shipped_on_the_same_slug(monkeypatch, vault: Path, tmp_path: Path):
    from src.harness.main import build_harness
    from src.vault_settings import user_skills_dir

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ZEN_MEMORY_BACKEND", "local")
    shipped = tmp_path / "shipped"
    _skill_file(shipped, "x", "shipped")
    _skill_file(user_skills_dir(vault), "x", "mine")
    _skill_file(user_skills_dir(vault), "y", "also mine")
    h = build_harness(HarnessConfig(vault_root=vault, skills_dir=shipped, bot_prompts_dir=tmp_path / "none", user_content=True))
    assert h.catalog.skill("x").use == "mine"
    assert h.catalog.skill("y") is not None


async def _draft_via_api(cfg: HarnessConfig, tmp_path: Path, catalog: Catalog) -> dict:
    driver = FakeDriver()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(cfg.vault_root), catalog=catalog, recorder_driver=driver)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        await c.post("/recorder/start", json={"name": "export invoices"})
        await c.post("/recorder/actions", json={"kind": "navigate", "target": "https://app.example.com/invoices", "intent": "open the invoices page"})
        await c.post("/recorder/stop")
        r = await c.post("/recorder/draft", json={"name": "Export invoices", "use": "When asked to export invoices."})
        assert r.status_code == 200
        return r.json()


async def test_a_recorded_skill_is_drafted_into_the_user_folder(vault: Path, tmp_path: Path):
    from src.vault_settings import user_skills_dir

    shipped = tmp_path / "shipped"
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path, skills_dir=shipped, user_content=True)
    body = await _draft_via_api(cfg, tmp_path, Catalog())
    assert body["state"] == "added"
    assert (user_skills_dir(vault) / body["slug"] / "SKILL.md").is_file()
    assert not (shipped / body["slug"]).exists()


async def test_a_recorded_skill_never_replaces_a_shipped_skill_of_the_same_slug(vault: Path, tmp_path: Path):
    from src.skills.loader import load_dir
    from src.vault_settings import user_skills_dir

    shipped = tmp_path / "shipped"
    _skill_file(shipped, "export-invoices", "shipped")
    (shipped / "export-invoices" / "SKILL.md").write_text("---\nname: Export invoices\nuse: shipped\n---\n# s\n", encoding="utf-8")
    catalog = Catalog()
    for r in load_dir(shipped):
        catalog.add_skill(r.skill)
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path, skills_dir=shipped, user_content=True)
    body = await _draft_via_api(cfg, tmp_path, catalog)
    assert body["state"] == "added" and body["slug"] == "export-invoices-2"
    assert catalog.skill("export-invoices").use == "shipped"
    assert catalog.skill("export-invoices-2") is not None
    assert not (user_skills_dir(vault) / "export-invoices").exists()
    # a numbered name never lands on a recording already saved: the next one is -3, and -2 is as it was
    second = user_skills_dir(vault) / "export-invoices-2" / "SKILL.md"
    before = second.read_text(encoding="utf-8")
    body = await _draft_via_api(cfg, tmp_path, catalog)
    assert body["state"] == "added" and body["slug"] == "export-invoices-3"
    assert second.read_text(encoding="utf-8") == before


async def test_draft_never_takes_a_shipped_slug_with_user_content_off(vault: Path, tmp_path: Path):
    shipped = tmp_path / "skills" / "export-invoices"
    shipped.mkdir(parents=True)
    (shipped / "SKILL.md").write_text("---\nname: Shipped export\nuse: Export.\n---\nBody\n", encoding="utf-8")
    catalog = Catalog()
    # the shipped skill has the slug the draft's name would make
    shipped_skill = load_skill(shipped).skill
    shipped_skill.slug = "export-invoices"
    catalog.add_skill(shipped_skill)
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path, skills_dir=tmp_path / "skills", user_content=False)
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=catalog, recorder_driver=FakeDriver())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        await c.post("/recorder/start", json={"name": "export invoices"})
        await c.post("/recorder/actions", json={"kind": "navigate", "target": "https://app.example.com/invoices", "value": None, "intent": "open"})
        await c.post("/recorder/stop")
        r = await c.post("/recorder/draft", json={"name": "Export invoices", "use": "When asked to export invoices."})
    body = r.json()
    assert body["slug"] != "export-invoices"
    assert not (user_skills_dir(vault) / "export-invoices").exists()


async def test_a_recorded_skill_never_takes_a_discovered_skills_slug_or_name(vault: Path, tmp_path: Path):
    from src.harness.bot import Bot
    from src.skills.loader import load_dir
    from src.vault_settings import discovered_skills_dir

    disc = discovered_skills_dir(vault)
    (disc / "export-invoices").mkdir(parents=True)
    (disc / "export-invoices" / "SKILL.md").write_text(
        "---\nname: Invoice export\nslug: export-invoices\nuse: discovered\n---\n# d\n", encoding="utf-8")
    (disc / "ledgers").mkdir(parents=True)
    (disc / "ledgers" / "SKILL.md").write_text("---\nname: Export invoices\nslug: ledgers\nuse: discovered too\n---\n# d\n",
                                               encoding="utf-8")
    catalog = Catalog()
    for r in load_dir(disc):
        catalog.add_skill(r.skill)
    found = catalog.skill("export-invoices")
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path, skills_dir=tmp_path / "shipped", user_content=True)
    driver = FakeDriver()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=catalog, recorder_driver=driver)
    bot = Bot("B", "r", "", id="b")
    bot.attach_skill(found)
    h.add_bot(bot)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        await c.post("/recorder/start", json={"name": "export invoices"})
        await c.post("/recorder/actions", json={"kind": "navigate", "target": "https://app.example.com/invoices", "intent": "open"})
        await c.post("/recorder/stop")
        r = await c.post("/recorder/draft", json={"name": "Export invoices", "use": "When asked to export invoices."})
    body = r.json()
    assert body["state"] == "added" and body["slug"] == "export-invoices-2"
    assert catalog.skill("export-invoices") is found and catalog.skill("ledgers") is not None
    assert [s.slug for s in h.bots["b"].skills] == ["export-invoices"]

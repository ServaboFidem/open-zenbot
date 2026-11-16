"""Claude Code and Codex detection: the sign-in and capability probes, the switches, and the two routes.
Every probe runs a fake CLI; nothing here starts the real `claude` or `codex`."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from pathlib import Path

import httpx
import pytest

from src.harness import agent_clis, tool_paths
from src.harness.agent_clis import AGENTS, RUNNABLE, Agents, AgentSettings, load_settings, probe, read_auth, run_env, save_settings
from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.vault_settings import settings_dir

pytestmark = pytest.mark.real_agent_probe  # tests/conftest.py makes probing inert for every other test

SUB = '{"loggedIn": true, "authMethod": "claude.ai"}'
CLAUDE = {"--version": "2.1.0 (Claude Code)", "--help": "Usage: claude --restricted --strict-mcp-config", "auth": SUB}
CODEX = {"--version": "codex-cli 0.5.0", "exec": "Usage: codex exec --sandbox MODE",
         "login": "Logged in using ChatGPT"}


def fake_cli(d: Path, stem: str, answers: dict[str, str]) -> Path:
    """A stand-in program answering by its first argument."""
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        p = d / f"{stem}.bat"
        lines = [f'if "%~1"=="{a}" echo {out}' for a, out in answers.items()]
        p.write_text("@echo off\r\n" + "\r\n".join(lines) + "\r\n", encoding="utf-8")
    else:
        p = d / stem
        cases = "\n".join(f"  {a}) echo '{out}';;" for a, out in answers.items())
        p.write_text(f'#!/bin/sh\ncase "$1" in\n{cases}\nesac\n', encoding="utf-8")
        p.chmod(0o755)
    return p


@pytest.fixture
def only(monkeypatch, tmp_path):
    """Nothing on PATH; each program is found only where `place(key, answers)` puts a fake."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    for key in AGENTS:
        monkeypatch.setitem(tool_paths.PROGRAMS, key, dataclasses.replace(tool_paths.PROGRAMS[key], candidates=lambda: []))

    def place(key: str, answers: dict[str, str]) -> Path:
        exe = fake_cli(tmp_path / f"bin-{key}", AGENTS[key].program.bin, answers)
        monkeypatch.setitem(tool_paths.PROGRAMS, key, dataclasses.replace(tool_paths.PROGRAMS[key], candidates=lambda: [exe]))
        return exe

    return place


def test_run_env_drops_keys_and_vendor_variables():
    base = {"ANTHROPIC_API_KEY": "a", "ANTHROPIC_BASE_URL": "b", "OPENAI_API_KEY": "c", "CLAUDE_CODE_OAUTH_TOKEN": "d",
            "CODEX_FOO": "e", "CODEX_HOME": "/h", "CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin"}
    assert run_env(base) == {"CODEX_HOME": "/h", "CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin"}


@pytest.mark.parametrize("key,out,want", [
    ("claude-code", '{"loggedIn": true, "authMethod": "claude.ai"}', "subscription"),
    ("claude-code", '{"loggedIn": true, "authMethod": "api_key"}', "api_key"),
    ("claude-code", '{"loggedIn": false}', "none"),
    ("claude-code", "not json", "unknown"),
    ("codex", "Logged in using ChatGPT", "subscription"),
    ("codex", "Logged in using an API key - sk-***", "api_key"),
    ("codex", "Not logged in", "none"),
    ("codex", "something else", "unknown"),
    ("claude-code", None, "unknown"),
    ("codex", "garbage", "unknown"),
])
def test_read_auth(key, out, want):
    assert read_auth(key, out) == want


def test_settings_default_off_and_asking(tmp_path: Path):
    assert load_settings(tmp_path / "v") == {k: AgentSettings(False, True, "") for k in AGENTS}


def test_settings_round_trip_keeps_the_other_agent(tmp_path: Path):
    v = tmp_path / "v"
    save_settings(v, "claude-code", AgentSettings(True, False, "sonnet-4.5"))
    save_settings(v, "codex", AgentSettings(True, True, ""))
    got = load_settings(v)
    assert got["claude-code"] == AgentSettings(True, False, "sonnet-4.5")
    assert got["codex"] == AgentSettings(True, True, "")


def test_settings_file_is_in_the_settings_folder_not_the_vault(tmp_path: Path):
    v = tmp_path / "v"
    save_settings(v, "codex", AgentSettings(True))
    assert (settings_dir(v) / "agents.json").is_file()
    assert not list(v.rglob("agents.json"))


def test_save_refuses_a_model_with_a_space(tmp_path: Path):
    with pytest.raises(ValueError):
        save_settings(tmp_path / "v", "codex", AgentSettings(model="gpt 5"))
    assert not (settings_dir(tmp_path / "v") / "agents.json").exists()


def test_malformed_file_reads_as_defaults(tmp_path: Path):
    v = tmp_path / "v"
    f = settings_dir(v) / "agents.json"
    f.parent.mkdir(parents=True)
    f.write_text("{not json", encoding="utf-8")
    assert load_settings(v)["codex"] == AgentSettings()
    f.write_text(json.dumps({"codex": {"enabled": "yes", "ask": 3, "model": "bad model"}, "claude-code": 5}), encoding="utf-8")
    assert load_settings(v) == {k: AgentSettings() for k in AGENTS}


def test_probe_usable(tmp_path: Path, only):
    only("claude-code", CLAUDE)
    r = probe(tmp_path / "v", "claude-code")
    assert (r["usable"], r["reason"], r["auth"], r["confinable"], r["version"]) == (True, "", "subscription", True, "2.1.0")
    assert r["source"] == "found" and r["sign_in"] == "claude" and r["vendor"] == "Anthropic"


def test_probe_too_old_to_confine(tmp_path: Path, only):
    only("claude-code", {**CLAUDE, "--help": "Usage: claude --strict-mcp-config"})
    r = probe(tmp_path / "v", "claude-code")
    assert not r["confinable"] and not r["usable"] and "update" in r["reason"]


def test_probe_not_signed_in(tmp_path: Path, only):
    only("claude-code", {**CLAUDE, "auth": '{"loggedIn": false}'})
    r = probe(tmp_path / "v", "claude-code")
    assert r["auth"] == "none" and not r["usable"] and "`claude`" in r["reason"]


def test_probe_sign_in_state_unknown(tmp_path: Path, only):
    only("claude-code", {**CLAUDE, "auth": "oops"})
    r = probe(tmp_path / "v", "claude-code")
    assert r["auth"] == "unknown" and not r["usable"] and r["reason"]


def test_probe_not_found(tmp_path: Path, only):
    r = probe(tmp_path / "v", "claude-code")
    assert r["path"] is None and not r["usable"] and "not found" in r["reason"]


def test_codex_is_not_runnable_yet(tmp_path: Path, only):
    only("codex", CODEX)
    r = probe(tmp_path / "v", "codex")
    assert r["auth"] == "subscription" and r["confinable"] and not r["runnable"]
    assert not r["usable"] and r["reason"] == RUNNABLE["codex"]


async def test_on_needs_enabled_and_usable(tmp_path: Path, only):
    v = tmp_path / "v"
    only("claude-code", CLAUDE)
    agents = Agents(v)
    await agents.refresh()
    assert agents.on() == []  # usable but off
    save_settings(v, "claude-code", AgentSettings(enabled=True))
    assert agents.on() == ["claude-code"]
    save_settings(v, "codex", AgentSettings(enabled=True))
    assert agents.on() == ["claude-code"]  # codex is on but not usable


def test_run_env_keeps_the_config_names_in_any_case():
    assert run_env({"claude_config_dir": "/c", "Codex_Home": "/h", "codex_foo": "x"}) == {"claude_config_dir": "/c", "Codex_Home": "/h"}


def test_probes_run_with_the_scrubbed_environment(tmp_path: Path, only, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    seen = tmp_path / "seen.txt"
    exe = only("claude-code", CLAUDE)
    body = exe.read_text(encoding="utf-8")
    first, rest = body.split("\n", 1)  # keep the "@echo off" / shebang line first
    record = f'set > "{seen}"' if sys.platform == "win32" else f"env > '{seen}'"
    exe.write_text(f"{first}\n{record}\n{rest}", encoding="utf-8")  # the fake records its environment on every call
    probe(tmp_path / "v", "claude-code")
    names = {l.split("=", 1)[0].upper() for l in seen.read_text(encoding="utf-8", errors="replace").splitlines() if "=" in l}
    assert names and "ANTHROPIC_API_KEY" not in names and "CODEX_HOME" in names


def test_an_api_key_sign_in_is_usable(tmp_path: Path, only):
    only("claude-code", {**CLAUDE, "auth": '{"loggedIn": true, "authMethod": "api_key"}'})
    r = probe(tmp_path / "v", "claude-code")
    assert r["auth"] == "api_key" and r["usable"] and r["reason"] == ""


# -- routes -------------------------------------------------------------------------------------------------

@pytest.fixture
def app_h(tmp_path: Path, only):
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", mcp_config=tmp_path / "mcp.json")
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    return create_app(h), h


async def test_get_agents_lists_both(app_h, only):
    only("claude-code", CLAUDE)
    app, _ = app_h
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        got = (await c.get("/agents")).json()["agents"]
    assert [a["id"] for a in got] == ["claude-code", "codex"]
    assert got[0]["usable"] and not got[1]["usable"]


async def test_put_enables_a_usable_agent(app_h, only):
    only("claude-code", CLAUDE)
    app, h = app_h
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        r = await c.put("/settings/agents/claude-code", json={"enabled": True, "ask": False, "model": "sonnet"})
    assert r.status_code == 200
    body = r.json()
    assert (body["enabled"], body["ask"], body["model"], body["usable"]) == (True, False, "sonnet", True)
    assert load_settings(h.config.vault_root)["claude-code"] == AgentSettings(True, False, "sonnet")
    assert h.agents.on() == ["claude-code"]
    events = [e.data for e in h.ledger.tail(50) if e.data.get("event") == "agent"]
    assert events and events[-1]["agent"] == "claude-code" and events[-1]["enabled"] is True


async def test_put_refuses_enabling_an_unusable_agent_422(app_h, only):
    app, h = app_h
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        r = await c.put("/settings/agents/claude-code", json={"enabled": True})
    assert r.status_code == 422 and "not found" in r.json()["detail"]
    assert not load_settings(h.config.vault_root)["claude-code"].enabled


async def test_put_unknown_agent_404(app_h):
    app, _ = app_h
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        assert (await c.put("/settings/agents/nope", json={"ask": False})).status_code == 404


async def test_put_enable_registers_the_tool_and_disable_removes_it(app_h, only):
    only("claude-code", CLAUDE)
    app, h = app_h
    assert "delegate" not in h.tools.names()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        assert (await c.put("/settings/agents/claude-code", json={"enabled": True})).status_code == 200
        assert "delegate" in h.tools.names()
        spec = next(s for s in h.tools.specs() if s.name == "delegate")
        assert spec.parameters["properties"]["agent"]["enum"] == ["claude-code"]
        assert (await c.put("/settings/agents/claude-code", json={"enabled": False})).status_code == 200
    assert "delegate" not in h.tools.names()


async def test_stop_ends_a_delegated_run(app_h, monkeypatch, tmp_path):
    from src.harness import delegation
    from src.harness.loop import Session
    from src.harness.tools import current_call
    from src.providers.interface import ToolCall

    from .test_delegation import FAKE, FakeRunner, _all_dead, _child_recorded

    _, h = app_h
    record = tmp_path / "record.json"
    real = delegation.run_env
    monkeypatch.setattr(delegation, "run_env",
                        lambda base=None: {**real(base), "FAKE_AGENT_RECORD": str(record), "FAKE_AGENT_MODE": "hang"})
    h.agents.statuses["claude-code"] = {"path": str(FAKE), "auth": "subscription", "usable": True}
    save_settings(h.config.vault_root, "claude-code", AgentSettings(enabled=True, ask=False))
    h.delegator.runner = FakeRunner(h.agents, h.ledger, timeout=60)
    h.delegator.register(h.tools)
    session = Session(bot_id="b1", task="hand it off")
    session.conversation_id = "conv-1"
    h.sessions[session.id] = session
    token = current_call.set(("b1", session.id))
    try:  # the task copies the context as it is now
        running = asyncio.create_task(h.tools.run(ToolCall("c1", "delegate", {"agent": "claude-code", "task": "Wait."})))
    finally:
        current_call.reset(token)
    rec = await _child_recorded(record)
    h.stop(session.id)
    result = await asyncio.wait_for(running, 30)
    assert not result.ok and "Stopped by the user." in result.error
    assert session.stop_requested
    assert await _all_dead([rec["pid"], rec["child"]])
    assert h.delegator.runner.live == {}


async def test_put_bad_model_422(app_h):
    app, h = app_h
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        assert (await c.put("/settings/agents/codex", json={"model": "a b"})).status_code == 422
    assert load_settings(h.config.vault_root)["codex"].model == ""

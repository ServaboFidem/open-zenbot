"""MCP client: servers named in mcp.json connect at start; every tool they
expose registers in the tool runner as `<server>.<tool>` and the server
enters the catalog as an `mcp` entry so the decider can route to it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.harness.loop import allowed_tool_groups
from src.harness.mcp_client import McpRegistry, ServerConfig, load_config
from src.harness.tools import ToolRunner
from src.memory.ledger import Ledger
from src.providers.interface import ToolCall
from src.skills.catalog import Catalog

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
ECHO = ServerConfig(name="echo", command=sys.executable, args=[str(FIXTURE)], use="Echo text and add numbers.")


async def test_registry_connects_a_stdio_server_and_registers_its_tools(tmp_path: Path):
    tools, catalog = ToolRunner(cwd=tmp_path), Catalog()
    reg = McpRegistry([ECHO])
    try:
        await reg.connect(tools, catalog)
        assert {"echo.echo", "echo.add"} <= set(tools.names())
        spec = next(s for s in tools.specs() if s.name == "echo.echo")
        assert "text" in spec.parameters["properties"] and spec.description == "Echo the text back."
        assert [e.ref for e in catalog.mcps()] == ["echo"] and catalog.mcps()[0].use == "Echo text and add numbers."
        assert sorted(catalog.mcps()[0].inputs) == ["add", "echo"]
        r = await tools.run(ToolCall(id="c1", name="echo.echo", arguments={"text": "hi there"}))
        assert r.ok and r.output == "hi there"
        r = await tools.run(ToolCall(id="c2", name="echo.add", arguments={"a": 2, "b": 3}))
        assert r.ok and r.output == "5"
        assert await reg.call("echo", "echo", {"text": "direct"}) == "direct"
        st = reg.status()["echo"]
        assert st["state"] == "connected" and st["tools"] == ["add", "echo"] and st["transport"] == "stdio"
    finally:
        await reg.close()
    assert "echo.echo" not in tools.names()


async def test_registry_reports_a_failed_server_and_keeps_the_rest(tmp_path: Path):
    bad = ServerConfig(name="bad", command="definitely-not-a-command-zen", args=[])
    reg = McpRegistry([bad, ECHO])
    try:
        await reg.connect(ToolRunner(cwd=tmp_path), Catalog())
        st = reg.status()
        assert st["bad"]["state"] == "failed" and st["bad"]["error"]
        assert st["echo"]["state"] == "connected"
    finally:
        await reg.close()


def test_load_config_reads_stdio_and_http_servers(tmp_path: Path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {
        "files": {"command": "npx", "args": ["-y", "some-server"], "env": {"ROOT": "/data"}, "use": "Read local files."},
        "remote": {"url": "http://127.0.0.1:9/mcp", "headers": {"Authorization": "Bearer x"}},
    }}), encoding="utf-8")
    servers = {s.name: s for s in load_config(f)}
    assert servers["files"].transport == "stdio" and servers["files"].args == ["-y", "some-server"] and servers["files"].env == {"ROOT": "/data"}
    assert servers["files"].use == "Read local files."
    assert servers["remote"].transport == "http" and servers["remote"].url == "http://127.0.0.1:9/mcp" and servers["remote"].headers == {"Authorization": "Bearer x"}
    assert load_config(tmp_path / "missing.json") == []
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(tmp_path / "bad.json")


def test_load_config_expands_env_vars_and_drops_unset_headers(tmp_path: Path, monkeypatch):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {
        "exa": {"url": "https://mcp.exa.ai/mcp", "headers": {"x-api-key": "${EXA_API_KEY}", "x-static": "s"}},
        "local": {"command": "srv", "env": {"TOKEN": "${EXA_API_KEY}", "ROOT": "${ZEN_TEST_ROOT}/data"}},
    }}), encoding="utf-8")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("ZEN_TEST_ROOT", "/r")
    servers = {s.name: s for s in load_config(f)}
    assert servers["exa"].headers == {"x-static": "s"}          # unset key: header omitted, keyless access
    assert servers["local"].env == {"ROOT": "/r/data"}           # unset in env: dropped, child inherits nothing
    monkeypatch.setenv("EXA_API_KEY", "k-1")
    servers = {s.name: s for s in load_config(f)}
    assert servers["exa"].headers == {"x-api-key": "k-1", "x-static": "s"}
    assert servers["local"].env["TOKEN"] == "k-1"


def test_load_config_expands_args_and_harness_vars(tmp_path: Path, monkeypatch):
    """`${VAR}` works in args too (the filesystem server takes its roots as
    args); an unset one drops that arg; the harness can hand in its own
    values, which win over the environment."""
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {
        "files": {"command": "npx", "args": ["-y", "server-filesystem", "${ZEN_VAULT_ROOT}", "${ZEN_EXTRA_ROOT}"]},
    }}), encoding="utf-8")
    monkeypatch.delenv("ZEN_EXTRA_ROOT", raising=False)
    monkeypatch.setenv("ZEN_VAULT_ROOT", "/from-env")
    assert load_config(f)[0].args == ["-y", "server-filesystem", "/from-env"]
    assert load_config(f, vars={"ZEN_VAULT_ROOT": "/vault"})[0].args == ["-y", "server-filesystem", "/vault"]


async def test_the_harness_hands_its_vault_root_to_mcp_servers(vault: Path, tmp_path: Path):
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", mcp_config=tmp_path / "mcp.json")
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"echo": {"command": sys.executable, "args": [str(FIXTURE), "${ZEN_VAULT_ROOT}"]}}}), encoding="utf-8")
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    app = create_app(h)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        async with app.router.lifespan_context(app):
            st = (await c.get("/mcps")).json()["echo"]
            assert st["state"] == "connected" and st["target"].endswith(str(vault))
            assert st["use"].startswith("echo: Echo")  # no `use` in this config, so the line the decider scores is built from the tools


def test_tool_specs_filter_by_group(tmp_path: Path):
    tools = ToolRunner(cwd=tmp_path)

    async def fn(**_):
        return ""
    tools.register("echo.echo", "e", {"type": "object", "properties": {}}, fn, group="echo")
    tools.register("other.x", "o", {"type": "object", "properties": {}}, fn, group="other")
    assert {s.name for s in tools.specs()} == {"shell", "echo.echo", "other.x"}
    assert {s.name for s in tools.specs(groups={"echo"})} == {"shell", "echo.echo"}
    assert {s.name for s in tools.specs(groups=set())} == {"shell"}
    assert [s.name for s in tools.specs({"echo"}, builtins=False)] == ["echo.echo"]
    tools.register("scoped", "s", {"type": "object", "properties": {}}, fn, group="echo", offered=lambda b, s: True)
    # a tool with `offered` is a per-caller built-in: left out too
    assert [s.name for s in tools.specs({"echo"}, caller=("b", "s"), builtins=False)] == ["echo.echo"]
    assert "scoped" in {s.name for s in tools.specs({"echo"}, caller=("b", "s"))}


def test_allowed_tool_groups_follow_the_mcp_band():
    assert allowed_tool_groups({}) is None  # no scorer, no band: every tool
    assert allowed_tool_groups({"bands": {"skill": {"winners": ["a"]}}}) is None
    assert allowed_tool_groups({"mcps": ["echo"], "bands": {"mcp": {"winners": ["echo"], "outcome": "act"}}}) == {"echo"}
    assert allowed_tool_groups({"mcps": [], "bands": {"mcp": {"winners": ["echo"], "outcome": "defer"}}}) == set()


async def test_api_lists_mcp_servers_and_health_counts_them(vault: Path, tmp_path: Path):
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", mcp_config=tmp_path / "mcp.json")
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"echo": {"command": sys.executable, "args": [str(FIXTURE)]}}}), encoding="utf-8")
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    app = create_app(h)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        async with app.router.lifespan_context(app):
            r = await c.get("/mcps")
            assert r.status_code == 200 and r.json()["echo"]["state"] == "connected" and r.json()["echo"]["tools"] == ["add", "echo"]
            assert (await c.get("/health")).json()["mcps"] == 1
            assert any(e["ref"] == "echo" and e["kind"] == "mcp" for e in (await c.get("/skills")).json())
    assert "echo.echo" not in h.tools.names()

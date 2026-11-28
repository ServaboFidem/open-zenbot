"""MCP client: servers named in mcp.json connect at start; every tool they
expose registers in the tool runner as `<server>.<tool>` and the server
enters the catalog as an `mcp` entry so the decider can route to it."""

from __future__ import annotations

import asyncio
import json
import sys
import time
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


async def test_a_stdio_server_starts_in_an_empty_folder_not_the_harness_folder(tmp_path: Path, monkeypatch):
    # A server that loads `.env` from its working folder (open-museum-mcp calls dotenv.config()) must not find the
    # harness's own `.env` there, which would hand back every key the scrubbed environment drops.
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-must-not-leak\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    where = ServerConfig(name="cwd", command=sys.executable, args=[str(Path(__file__).parent / "fixtures" / "mcp_cwd_server.py")])
    reg = McpRegistry([where])
    try:
        await reg.connect(ToolRunner(cwd=tmp_path), Catalog())
        seen = json.loads(await reg.call("cwd", "where", {}))
    finally:
        await reg.close()
    assert Path(seen["cwd"]).resolve() != tmp_path.resolve()
    assert seen["entries"] == []


async def test_each_stdio_server_gets_its_own_folder_removed_when_it_stops(tmp_path: Path):
    # A file one server leaves in its folder (a `.env` with a key mapped only to it) must never reach another.
    fixture = str(Path(__file__).parent / "fixtures" / "mcp_cwd_server.py")
    one = ServerConfig(name="one", command=sys.executable, args=[fixture])
    two = ServerConfig(name="two", command=sys.executable, args=[fixture])
    reg = McpRegistry([one, two])
    try:
        await reg.connect(ToolRunner(cwd=tmp_path), Catalog())
        assert await reg.call("one", "leave", {"name": ".env"}) == "ok"
        a = json.loads(await reg.call("one", "where", {}))
        b = json.loads(await reg.call("two", "where", {}))
    finally:
        await reg.close()
    assert a["cwd"] != b["cwd"] and a["entries"] == [".env"] and b["entries"] == []
    assert not Path(a["cwd"]).exists() and not Path(b["cwd"]).exists()


def _slow(name: str, delay: float) -> ServerConfig:
    fixture = str(Path(__file__).parent / "fixtures" / "mcp_cwd_server.py")
    return ServerConfig(name=name, command=sys.executable, args=[fixture], env={"ZEN_TEST_START_DELAY": str(delay)})


async def test_servers_connect_at_once_and_register_in_config_order(tmp_path: Path):
    # One after another, three 3 s starters took over 9 s; at once they take about as long as one. The first is the
    # slowest, so it answers last, and its tools still come first: the tool list is the same on every start.
    reg = McpRegistry([_slow("a", 4.0), _slow("b", 3.0), _slow("c", 3.0)])
    tools, catalog = ToolRunner(cwd=tmp_path), Catalog()
    t = time.monotonic()
    try:
        await reg.connect(tools, catalog)
        took = time.monotonic() - t
        assert {n: st["state"] for n, st in reg.status().items()} == {"a": "connected", "b": "connected", "c": "connected"}
    finally:
        await reg.close()
    assert took < 8.5, took
    assert [e.name for e in catalog.mcps()] == ["a", "b", "c"]


async def test_a_server_reconnected_from_another_task_closes_cleanly(tmp_path: Path, monkeypatch):
    # The MCP clients must be closed by the task that opened them. A reconnect runs in a request's task and the
    # close at shutdown in another; closed there, the stdio client's cancel scope refused to exit.
    from src.harness import mcp_client

    failures: list[str] = []

    class Recording(mcp_client.AsyncExitStack):
        async def aclose(self) -> None:
            try:
                await super().aclose()
            except BaseException as e:
                failures.append(f"{type(e).__name__}: {e}")
                raise

    monkeypatch.setattr(mcp_client, "AsyncExitStack", Recording)
    reg = McpRegistry([_slow("a", 0), _slow("b", 0)])
    try:
        await reg.connect(ToolRunner(cwd=tmp_path), Catalog())
        await asyncio.create_task(reg.reconnect(_slow("b", 0)))  # as from a request handler
        assert json.loads(await reg.call("b", "where", {}))["entries"] == []
    finally:
        await reg.close()
    assert failures == []
    assert {n: st["state"] for n, st in reg.status().items()} == {"a": "closed", "b": "closed"}


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
    # a grouped tool with `offered` is listed to a closed bot when its group is allowed and `offered` accepts the caller
    assert [s.name for s in tools.specs({"echo"}, caller=("b", "s"), builtins=False)] == ["echo.echo", "scoped"]
    assert [s.name for s in tools.specs({"echo"}, builtins=False)] == ["echo.echo"]  # no caller: not offered
    assert [s.name for s in tools.specs({"other"}, caller=("b", "s"), builtins=False)] == ["other.x"]
    tools.register("private", "p", {"type": "object", "properties": {}}, fn, offered=lambda b, s: True)
    assert "private" not in {s.name for s in tools.specs({"echo"}, caller=("b", "s"), builtins=False)}  # no group
    tools.register("picky", "p", {"type": "object", "properties": {}}, fn, group="echo", offered=lambda b, s: b == "x")
    assert "picky" not in {s.name for s in tools.specs({"echo"}, caller=("b", "s"), builtins=False)}
    assert "picky" in {s.name for s in tools.specs({"echo"}, caller=("x", "s"), builtins=False)}
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

"""MCP client: a server whose host requirements are unmet is held back as
`needs_install` (no tools, no catalog entry); the other servers connect."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.harness.mcp_client import McpRegistry, ServerConfig, load_config
from src.harness.tools import ToolRunner
from src.harness.host_deps import Requirement
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from tests.conftest import fake_program

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


def _echo(name: str, requires: list[Requirement] | None = None) -> ServerConfig:
    return ServerConfig(name=name, command=sys.executable, args=[str(FIXTURE)], requires=requires or [])


async def test_unmet_requirement_holds_the_server_back_and_others_connect(tmp_path: Path):
    tools, catalog = ToolRunner(cwd=tmp_path), Catalog()
    reg = McpRegistry([_echo("held", [Requirement(bin="zen-no-such-bin")]), _echo("free")])
    try:
        await reg.connect(tools, catalog)
        st = reg.status()
        assert st["held"]["state"] == "needs_install" and st["held"]["tools"] == []
        assert st["held"]["error"].startswith("zen-no-such-bin: missing")
        assert st["held"]["deps"][0]["problem"] == "missing"
        assert not [n for n in tools.names() if n.startswith("held.")]
        assert [e.ref for e in catalog.mcps()] == ["free"]
        assert st["free"]["state"] == "connected" and st["free"]["deps"] == []
    finally:
        await reg.close()


async def test_met_requirement_connects_and_reports_deps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_program(bindir, "zen-dep")
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    reg = McpRegistry([_echo("ok", [Requirement(bin="zen-dep")])])
    try:
        await reg.connect(ToolRunner(cwd=tmp_path), Catalog())
        st = reg.status()["ok"]
        assert st["state"] == "connected" and st["deps"][0]["ok"] is True
    finally:
        await reg.close()


def test_bad_requires_is_a_config_error(tmp_path: Path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"x": {"command": "foo", "requires": "nope"}}}))
    with pytest.raises(ValueError, match="server 'x'"):
        load_config(p)


async def test_recheck_connects_once_the_binary_appears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    vault, bindir = tmp_path / "vault", tmp_path / "bin"
    vault.mkdir()
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {
        "echo": {"command": sys.executable, "args": [str(FIXTURE)], "requires": [{"bin": "zen-dep"}]},
    }}), encoding="utf-8")
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", mcp_config=tmp_path / "mcp.json")
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    app = create_app(h)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        async with app.router.lifespan_context(app):
            st = (await c.get("/mcps")).json()["echo"]
            assert st["state"] == "needs_install" and st["deps"][0]["bin"] == "zen-dep"
            assert "mcp echo: needs install \u2014 zen-dep: missing" in capsys.readouterr().out

            fake_program(bindir, "zen-dep")
            r = await c.post("/mcps/echo/recheck")
            assert r.status_code == 200, r.text
            st = r.json()["echo"]
            assert st["state"] == "connected" and st["tools"] == ["add", "echo"] and st["deps"][0]["ok"] is True
            assert "echo.echo" in h.tools.names()

            assert (await c.post("/mcps/nope/recheck")).status_code == 404


async def test_probe_uses_the_path_the_child_will_get(tmp_path: Path):
    bindir = tmp_path / "only-child"
    bindir.mkdir()
    fake_program(bindir, "zen-child-dep")
    cfg = ServerConfig(name="p", command=sys.executable, args=[str(FIXTURE)], env={"PATH": str(bindir)},
                       requires=[Requirement(bin="zen-child-dep")])
    reg = McpRegistry([cfg])
    try:
        await reg.connect(ToolRunner(cwd=tmp_path), Catalog())
        assert reg.status()["p"]["state"] == "connected"
    finally:
        await reg.close()

"""MCP server keys from the MCP servers page: the ${VAR}s a server's mcp.json entry names in env, headers
and url, saved per server in vault-settings/mcp_keys.json over .env, and the server reconnected at once.
The page only ever gets back where a key comes from and its last four characters."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.harness.mcp_client import key_vars, load_config
from src.harness.mcp_keys import FILE, McpKeys, public
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.vault_settings import settings_dir

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


def test_key_vars_come_from_env_headers_and_url_not_args_or_harness_vars():
    spec = {"command": "x", "args": ["${ZEN_FS_ROOT}"], "url": "https://h/mcp?k=${URL_KEY}",
            "env": {"A": "${PEXELS_API_KEY}", "ROOT": "${ZEN_VAULT_ROOT}/x"},
            "headers": {"Authorization": "Bearer ${TOOLSMONK_API_KEY}", "x-again": "${PEXELS_API_KEY}"}}
    assert key_vars(spec, {"ZEN_VAULT_ROOT": "/v"}) == ["PEXELS_API_KEY", "TOOLSMONK_API_KEY", "URL_KEY"]
    assert key_vars({"command": "uvx", "args": ["mcp-server-time"]}) == []


def test_a_saved_key_wins_over_the_environment_for_its_server_only(tmp_path: Path, monkeypatch):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {
        "a": {"url": "https://a/mcp", "headers": {"Authorization": "Bearer ${SHARED_KEY}"}},
        "b": {"command": "srv", "env": {"K": "${SHARED_KEY}"}},
    }}), encoding="utf-8")
    monkeypatch.setenv("SHARED_KEY", "from-env")
    servers = {s.name: s for s in load_config(f, saved={"a": {"SHARED_KEY": "saved-a", "NOT_NAMED": "x"}})}
    assert servers["a"].headers == {"Authorization": "Bearer saved-a"} and servers["a"].keys == ["SHARED_KEY"]
    assert servers["b"].env == {"K": "from-env"}
    # the harness's own variables still win over anything saved
    f.write_text(json.dumps({"mcpServers": {"c": {"command": "srv", "env": {"R": "${ZEN_VAULT_ROOT}"}}}}), encoding="utf-8")
    c = load_config(f, vars={"ZEN_VAULT_ROOT": "/vault"}, saved={"c": {"ZEN_VAULT_ROOT": "/evil"}})[0]
    assert c.env == {"R": "/vault"} and c.keys == []


def test_store_round_trips_keeps_blank_saves_and_clears_one_key(tmp_path: Path):
    keys = McpKeys.load(tmp_path)
    assert keys.servers == {}
    keys = keys.with_keys("pexels", {"PEXELS_API_KEY": " pk-123456789 "})
    keys.save(tmp_path)
    assert json.loads((settings_dir(tmp_path) / FILE).read_text(encoding="utf-8")) == {"servers": {"pexels": {"PEXELS_API_KEY": "pk-123456789"}}}
    again = McpKeys.load(tmp_path).with_keys("pexels", {"PEXELS_API_KEY": ""})  # blank: keep the saved one
    assert again.for_server("pexels") == {"PEXELS_API_KEY": "pk-123456789"}
    assert again.without_key("pexels", "PEXELS_API_KEY").servers == {}
    (settings_dir(tmp_path) / FILE).write_text("{broken", encoding="utf-8")
    assert McpKeys.load(tmp_path).servers == {}


def test_public_shows_where_a_key_comes_from_and_only_its_last_four():
    rows = public(["A", "B", "C", "D"], {"A": "saved-key-1234"}, env={"A": "env-key-0000", "B": "env-key-5678", "D": "short"})
    assert rows == [{"name": "A", "source": "saved", "hint": "1234"}, {"name": "B", "source": ".env", "hint": "5678"},
                    {"name": "C", "source": "", "hint": ""}, {"name": "D", "source": ".env", "hint": "set"}]
    assert "saved-key-1234" not in json.dumps(rows)


async def test_saving_a_key_reconnects_the_server_with_it(vault: Path, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ECHO_API_KEY", raising=False)
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", mcp_config=tmp_path / "mcp.json")
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {
        "echo": {"command": sys.executable, "args": [str(FIXTURE)], "env": {"ECHO_API_KEY": "${ECHO_API_KEY}"}},
    }}), encoding="utf-8")
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    app = create_app(h)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        async with app.router.lifespan_context(app):
            st = (await c.get("/mcps")).json()["echo"]
            assert st["keys"] == [{"name": "ECHO_API_KEY", "source": "", "hint": ""}] and "key_vars" not in st

            r = await c.put("/mcps/echo/keys", json={"keys": {"ECHO_API_KEY": "ek-secret-9876"}})
            assert r.status_code == 200, r.text
            st = r.json()["echo"]
            assert st["state"] == "connected" and st["tools"] == ["add", "echo"]
            assert st["keys"] == [{"name": "ECHO_API_KEY", "source": "saved", "hint": "9876"}]
            assert "ek-secret-9876" not in r.text
            assert h.mcps._servers["echo"].config.env == {"ECHO_API_KEY": "ek-secret-9876"}
            assert "echo.echo" in h.tools.names() and [e.ref for e in h.catalog.mcps()].count("echo") == 1

            # a variable the entry does not name, an empty save, and a key with a line break are refused
            assert (await c.put("/mcps/echo/keys", json={"keys": {"OTHER": "x"}})).status_code == 422
            assert (await c.put("/mcps/echo/keys", json={"keys": {"ECHO_API_KEY": "  "}})).status_code == 422
            assert (await c.put("/mcps/echo/keys", json={"keys": {"ECHO_API_KEY": "a\r\nX-Evil: 1"}})).status_code == 422
            assert (await c.put("/mcps/nope/keys", json={"keys": {"ECHO_API_KEY": "x"}})).status_code == 404

            r = await c.delete("/mcps/echo/keys/ECHO_API_KEY")
            assert r.status_code == 200 and r.json()["echo"]["keys"][0]["source"] == ""
            assert h.mcps._servers["echo"].config.env == {} and r.json()["echo"]["state"] == "connected"

    # cleared, the key is nowhere in the vault: not the store, not the ledger
    assert not [p for p in vault.rglob("*") if p.is_file() and "ek-secret-9876" in p.read_text(encoding="utf-8", errors="ignore")]
    assert any(e.kind == "settings" and e.data.get("event") == "mcp_keys" for e in h.ledger.tail(50))


async def test_a_key_saved_before_start_is_used_at_start(vault: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ECHO_API_KEY", "from-env-0000")
    McpKeys().with_keys("echo", {"ECHO_API_KEY": "saved-key-1111"}).save(vault)
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", mcp_config=tmp_path / "mcp.json")
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {
        "echo": {"command": sys.executable, "args": [str(FIXTURE)], "env": {"ECHO_API_KEY": "${ECHO_API_KEY}"}},
    }}), encoding="utf-8")
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    app = create_app(h)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        async with app.router.lifespan_context(app):
            assert (await c.get("/mcps")).json()["echo"]["keys"] == [{"name": "ECHO_API_KEY", "source": "saved", "hint": "1111"}]
            assert h.mcps._servers["echo"].config.env == {"ECHO_API_KEY": "saved-key-1111"}

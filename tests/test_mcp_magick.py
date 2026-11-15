"""Vendor in this mcp: the magick server manipulates images locally.

Upstream: https://github.com/ncipollo/magick-mcp

The magick binary is absent on test hosts, so these tests assert config
shape only and never connect to the real server."""

from __future__ import annotations

import json
from pathlib import Path

from src.harness.mcp_client import key_vars, load_config

REPO_MCP_JSON = Path(__file__).parent.parent / "mcp.json"


def _magick_spec() -> dict:
    data = json.loads(REPO_MCP_JSON.read_text(encoding="utf-8"))
    spec = data["mcpServers"]["magick"]
    assert isinstance(spec, dict)
    return spec


def _magick_config():
    servers = {s.name: s for s in load_config(REPO_MCP_JSON)}
    return servers["magick"]


def test_magick_entry_is_present_in_this_mcp():
    data = json.loads(REPO_MCP_JSON.read_text(encoding="utf-8"))
    assert "magick" in data["mcpServers"]


def test_should_use_magick_mcp_command_with_mcp_arg():
    spec = _magick_spec()
    assert spec["command"] == "magick-mcp"
    assert spec["args"] == ["mcp"]


def test_should_be_keyless_stdio_server_with_no_url():
    cfg = _magick_config()
    assert cfg.transport == "stdio"
    assert cfg.command == "magick-mcp" and cfg.args == ["mcp"]
    assert cfg.url is None


def test_should_manipulate_images_in_use_line():
    use = _magick_spec()["use"].lower()
    assert "manipulate" in use and "images" in use


def test_should_convert_optimize_image_files_in_use_line():
    assert "convert/optimize image files" in _magick_spec()["use"]


def test_should_name_no_keys_for_the_magick_entry():
    spec = _magick_spec()
    assert key_vars(spec) == []
    cfg = _magick_config()
    assert cfg.keys == [] and cfg.env == {} and cfg.headers == {}


def test_should_parse_a_magick_shaped_entry_through_load_config(tmp_path: Path):
    """Echo-fixture pattern: write a tmp mcp.json, read it back via load_config."""
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {
        "magick": {"command": "magick-mcp", "args": ["mcp"], "use": "Manipulate local images."},
    }}), encoding="utf-8")
    servers = {s.name: s for s in load_config(f)}
    assert servers["magick"].transport == "stdio"
    assert servers["magick"].args == ["mcp"]
    assert servers["magick"].use == "Manipulate local images."
    assert servers["magick"].keys == []


def test_magick_declares_its_host_requirements():
    reqs = _magick_spec()["requires"]
    assert [r["bin"] for r in reqs] == ["magick-mcp", "magick"]
    assert reqs[0]["install"] == {"cargo": "magick-mcp"}
    m = reqs[1]
    assert m["min_version"] == "7" and m["old_bins"] == ["convert"]
    assert {"brew", "apt", "winget"} <= set(m["install"])
    assert set(m["manual"]) == {"linux", "darwin", "windows"}

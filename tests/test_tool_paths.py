"""Settings > Programs > ImageMagick: the full path the harness runs magick from, saved in
vault-settings/tools.json, filled in by install-deps, and used before PATH by the magick runner and by the
MCP servers' `requires` probes."""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from src.harness import host_deps, tool_paths
from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.harness.host_deps import Host, Requirement, parse_requires, probe
from src.harness.tool_paths import CLAUDE_CODE, CODEX, IMAGEMAGICK, canonical, find, path_for, save, saved, status, verify
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.vault_settings import settings_dir

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


def fake(d: Path, banner: str = "Version: ImageMagick 7.1.2-34 Q16-HDRI x64", stem: str = "magick") -> Path:
    """A stand-in program (magick unless `stem` says otherwise) that prints a version banner."""
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        p = d / f"{stem}.bat"
        p.write_text(f"@echo off\r\necho {banner}\r\n", encoding="utf-8")
    else:
        p = d / stem
        p.write_text(f"#!/bin/sh\necho '{banner}'\n", encoding="utf-8")
        p.chmod(0o755)
    return p


@pytest.fixture
def no_magick(monkeypatch, tmp_path):
    """Nothing on PATH and no install folders: only what a test sets up is found."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    prog = dataclasses.replace(IMAGEMAGICK, candidates=lambda: [])
    monkeypatch.setitem(tool_paths.PROGRAMS, "imagemagick", prog)
    return prog


def test_the_path_is_saved_beside_image_generation(tmp_path: Path):
    vault = tmp_path / "vault"
    f = settings_dir(vault) / "tools.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"image": {"enabled": True, "model": "m"}}), encoding="utf-8")
    exe = fake(tmp_path / "im")
    save(vault, "imagemagick", str(exe))
    assert saved(vault, "imagemagick") == canonical(str(exe))
    assert json.loads(f.read_text(encoding="utf-8"))["image"] == {"enabled": True, "model": "m"}
    save(vault, "imagemagick", None)
    assert saved(vault, "imagemagick") is None and "image" in json.loads(f.read_text(encoding="utf-8"))
    f.write_text("{broken", encoding="utf-8")
    assert saved(vault, "imagemagick") is None


def test_verify_runs_it_and_wants_imagemagick_7(tmp_path: Path):
    assert verify(IMAGEMAGICK, str(fake(tmp_path / "ok"))) == {"ok": True, "version": "7.1.2-34", "problem": None}
    old = verify(IMAGEMAGICK, str(fake(tmp_path / "old", "Version: ImageMagick 6.9.12-98 Q16")))
    assert old == {"ok": False, "version": "6.9.12-98", "problem": "too_old"}
    assert verify(IMAGEMAGICK, str(fake(tmp_path / "gm", "GraphicsMagick 1.3.42")))["problem"] == "not_program"
    assert verify(IMAGEMAGICK, str(tmp_path / "nope.exe"))["problem"] == "not_found"
    assert verify(IMAGEMAGICK, "magick")["problem"] == "not_absolute"
    assert "6.9.12-98 is too old" in tool_paths.problem_text(IMAGEMAGICK, "too_old", "6.9.12-98")


def test_verify_reads_a_version_printed_before_the_name(tmp_path: Path):
    exe = fake(tmp_path / "c", "2.1.325 (Claude Code)", "claude")
    assert verify(CLAUDE_CODE, str(exe)) == {"ok": True, "version": "2.1.325", "problem": None}


def test_verify_codex_banner(tmp_path: Path):
    exe = fake(tmp_path / "x", "codex-cli 0.163.0", "codex")
    assert verify(CODEX, str(exe)) == {"ok": True, "version": "0.163.0", "problem": None}


def test_verify_refuses_another_program_named_claude(tmp_path: Path):
    assert verify(CLAUDE_CODE, str(fake(tmp_path / "c", "hello", "claude")))["problem"] == "not_program"


def test_path_for_uses_an_install_folder_copy_when_use_found(tmp_path: Path, monkeypatch):
    exe = fake(tmp_path / "npm", "codex-cli 0.163.0", "codex")
    monkeypatch.setattr(tool_paths.shutil, "which", lambda b, path=None: None)
    monkeypatch.setitem(tool_paths.PROGRAMS, "codex", dataclasses.replace(CODEX, candidates=lambda: [exe]))
    vault = tmp_path / "vault"
    assert path_for(vault, "codex") == canonical(str(exe))
    st = status(vault, "codex")
    assert (st["source"], st["path"], st["ok"], st["found"]) == ("found", canonical(str(exe)), True, None)


def test_imagemagick_still_ignores_install_folders(tmp_path: Path, monkeypatch):
    exe = fake(tmp_path / "IM")
    monkeypatch.setattr(tool_paths.shutil, "which", lambda b, path=None: None)
    monkeypatch.setitem(tool_paths.PROGRAMS, "imagemagick", dataclasses.replace(IMAGEMAGICK, candidates=lambda: [exe]))
    assert path_for(tmp_path / "vault", "imagemagick") is None


def test_not_absolute_text_names_this_program():
    assert "magick" not in tool_paths.problem_text(CLAUDE_CODE, "not_absolute")
    assert "magick.exe" in tool_paths.problem_text(IMAGEMAGICK, "not_absolute")


def test_find_tries_path_then_install_folders(tmp_path: Path, no_magick, monkeypatch):
    folder = fake(tmp_path / "Program Files" / "ImageMagick-7.1.2")
    assert find(no_magick) is None
    prog = dataclasses.replace(no_magick, candidates=lambda: [tmp_path / "gone" / "magick.exe", folder])
    assert find(prog) == canonical(str(folder))
    if sys.platform == "win32":
        monkeypatch.setenv("PATHEXT", os.environ.get("PATHEXT", ".EXE") + ";.BAT")
    on_path = fake(tmp_path / "bin")
    assert find(prog, path_env=str(on_path.parent)) == canonical(str(on_path))  # PATH first
    gm = fake(tmp_path / "gm", "GraphicsMagick 1.3.42")
    assert find(prog, path_env=str(gm.parent)) == canonical(str(folder))  # not ImageMagick: the next one


def test_the_harness_runs_the_saved_path_and_falls_back_to_path_only_when_none_is_saved(tmp_path: Path, no_magick,
                                                                                         monkeypatch):
    vault = tmp_path / "vault"
    assert path_for(vault, "imagemagick") is None
    monkeypatch.setattr(tool_paths.shutil, "which", lambda b, path=None: "/usr/bin/magick" if b == "magick" else None)
    assert path_for(vault, "imagemagick") == "/usr/bin/magick"
    exe = fake(tmp_path / "im")
    save(vault, "imagemagick", str(exe))
    assert path_for(vault, "imagemagick") == canonical(str(exe))
    exe.unlink()
    assert path_for(vault, "imagemagick") is None  # gone: no quiet fallback; Settings says why


def test_status_says_where_it_comes_from_and_offers_a_found_copy(tmp_path: Path, no_magick, monkeypatch):
    vault = tmp_path / "vault"
    st = status(vault, "imagemagick")
    assert (st["path"], st["source"], st["ok"], st["found"]) == (None, "", False, None)
    folder = fake(tmp_path / "IM")
    monkeypatch.setitem(tool_paths.PROGRAMS, "imagemagick", dataclasses.replace(no_magick, candidates=lambda: [folder]))
    st = status(vault, "imagemagick")
    assert st["found"] == canonical(str(folder)) and st["saved"] is None
    save(vault, "imagemagick", str(folder))
    st = status(vault, "imagemagick")
    assert (st["source"], st["ok"], st["version"], st["found"]) == ("saved", True, "7.1.2-34", None)
    folder.unlink()
    st = status(vault, "imagemagick")
    assert st["ok"] is False and st["problem"] == "There is no file at that path."


def test_vault_for_install_deps_follows_the_harness_rule(tmp_path: Path):
    env_file = tmp_path / ".env"
    assert tool_paths.vault_root_from_env(env_file, env={}) == Path("~/.zen-agents/vault").expanduser()
    env_file.write_text("ZEN_VAULT=/from/dotenv\n", encoding="utf-8")
    assert tool_paths.vault_root_from_env(env_file, env={}) == Path("/from/dotenv")
    assert tool_paths.vault_root_from_env(env_file, env={"ZEN_VAULT": "/from/shell"}) == Path("/from/shell")


def test_a_requirement_names_its_setting():
    [r] = parse_requires([{"bin": "magick", "setting": "imagemagick"}], "x")
    assert r.setting == "imagemagick"
    with pytest.raises(ValueError, match="must be one of"):
        parse_requires([{"bin": "magick", "setting": "nope"}], "x")
    with pytest.raises(ValueError, match="is for magick, not convert"):
        parse_requires([{"bin": "convert", "setting": "imagemagick"}], "x")


def test_probes_use_the_saved_path_then_install_folders(tmp_path: Path, no_magick, monkeypatch):
    req = Requirement(bin="magick", version_args=["-version"], min_version="7", setting="imagemagick")
    host = Host(os="windows" if sys.platform == "win32" else "linux", path=str(tmp_path / "empty-path"), managers=set())
    assert probe(req, host)["problem"] == "missing"
    exe = fake(tmp_path / "saved")
    host.known = {"magick": str(exe)}
    assert probe(req, host)["ok"] and probe(req, host)["found_path"] == str(exe)
    host.known = {}
    folder = fake(tmp_path / "IM")
    monkeypatch.setitem(tool_paths.PROGRAMS, "imagemagick", dataclasses.replace(no_magick, candidates=lambda: [folder]))
    r = probe(req, host)
    assert r["ok"] and r["found_path"] == canonical(str(folder))  # off PATH is fine: it is run by full path
    plain = Requirement(bin="magick", version_args=["-version"], min_version="7")
    assert probe(plain, host)["problem"] == "missing"  # without a setting, PATH it is


def test_install_deps_records_the_path_it_finds(tmp_path: Path, no_magick, monkeypatch, capsys):
    folder = fake(tmp_path / "IM")
    monkeypatch.setitem(tool_paths.PROGRAMS, "imagemagick", dataclasses.replace(no_magick, candidates=lambda: [folder]))
    monkeypatch.setattr(host_deps, "refresh_windows_path", lambda: None)  # keep the real PATH out of it
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"magick": {"command": "x", "requires": [
        {"bin": "magick", "setting": "imagemagick", "version_args": ["-version"], "min_version": "7"}]}}}), encoding="utf-8")
    vault = tmp_path / "v" / "vault"
    assert host_deps.main(["install", "--config", str(cfg), "--vault", str(vault)]) == 0
    assert saved(vault, "imagemagick") == canonical(str(folder))
    assert "saved" in capsys.readouterr().out
    assert host_deps.main(["install", "--config", str(cfg), "--vault", str(vault)]) == 0  # again: kept, not rewritten
    assert "saved" not in capsys.readouterr().out


async def test_settings_routes_check_save_and_reconnect(vault: Path, tmp_path: Path, no_magick, monkeypatch):
    monkeypatch.setattr("src.harness.api.refresh_windows_path", lambda: None)
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", mcp_config=tmp_path / "mcp.json")
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"echo": {
        "command": sys.executable, "args": [str(FIXTURE)],
        "requires": [{"bin": "magick", "setting": "imagemagick", "version_args": ["-version"], "min_version": "7"}]}}}),
        encoding="utf-8")
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    app = create_app(h)
    exe, gm = fake(tmp_path / "im"), fake(tmp_path / "gm", "GraphicsMagick 1.3.42")
    url = "/settings/tools/programs/imagemagick"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        async with app.router.lifespan_context(app):
            assert h.mcps.status()["echo"]["state"] == "needs_install"
            got = (await c.get(url)).json()
            assert (got["name"], got["path"], got["ok"]) == ("ImageMagick", None, False)

            bad = await c.put(url, json={"path": str(gm)})
            assert bad.status_code == 422 and "does not say ImageMagick" in bad.json()["detail"]
            assert (await c.put(url, json={"path": "magick"})).status_code == 422

            r = await c.put(url, json={"path": f'"{exe}"'})  # pasted with quotes: fine
            assert r.status_code == 200, r.text
            assert (r.json()["source"], r.json()["ok"], r.json()["version"]) == ("saved", True, "7.1.2-34")
            assert h.mcps.status()["echo"]["state"] == "connected"  # its requirement is met by the saved path

            r = await c.delete(url)
            assert r.json()["saved"] is None and h.mcps.status()["echo"]["state"] == "needs_install"
            assert (await c.get("/settings/tools/programs/nope")).status_code == 404
    events = [e.data for e in h.ledger.tail(50) if e.data.get("event") == "tool" and e.data.get("tool") == "imagemagick"]
    assert [e["path"] for e in events] == [canonical(str(exe)), None]

"""Host dependency model: parse `requires`, probe the host, pick a fix."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from src.harness.host_deps import (
    MANAGERS,
    Host,
    Requirement,
    install,
    main,
    merge_path,
    parse_requires,
    parse_version,
    probe,
    refresh_windows_path,
)


def stub(dir: Path, name: str, body: str = "echo 1.0") -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    p = dir / name
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)
    return p


def host(tmp_path, os="linux", managers=(), extra_dirs=()):
    return Host(os=os, path=str(tmp_path / "bin"), managers=set(managers), extra_dirs=list(extra_dirs))


def test_parse_requires_reads_defaults_and_rejects_bad_shapes():
    assert parse_requires(None, "x") == []
    reqs = parse_requires([{"bin": "a"}], "x")
    assert reqs[0].version_args == ["--version"]
    for bad in ("nope", [{}], [{"bin": "a", "install": []}]):
        with pytest.raises(ValueError) as e:
            parse_requires(bad, "x")
        assert str(e.value).startswith("x:")


def test_parse_version_takes_the_first_dotted_number():
    assert parse_version("Version: ImageMagick 7.1.2-18 Q16-HDRI x86_64") == (7, 1, 2)
    assert parse_version("6.9.12-98") == (6, 9, 12)
    assert parse_version("no digits") is None
    assert (10, 0) > (9, 9)


def test_found_without_min_version_is_ok_even_if_version_run_fails(tmp_path):
    stub(tmp_path / "bin", "tool", "exit 3")
    r = probe(Requirement(bin="tool"), host(tmp_path))
    assert r["ok"] is True and r["problem"] is None and r["version"] is None


def test_min_version_too_old_and_ok(tmp_path):
    b = tmp_path / "bin"
    stub(b, "magick", "echo 'Version: ImageMagick 6.9.12-98'")
    r = probe(Requirement(bin="magick", min_version="7"), host(tmp_path))
    assert r["problem"] == "too_old" and r["version"] == "6.9.12" and not r["ok"]
    stub(b, "magick", "echo 'Version: ImageMagick 7.1.2-18'")
    assert probe(Requirement(bin="magick", min_version="7"), host(tmp_path))["ok"] is True


def test_hanging_probe_is_probe_failed(tmp_path):
    stub(tmp_path / "bin", "slow", "sleep 30")
    t = time.monotonic()
    r = probe(Requirement(bin="slow", min_version="1"), host(tmp_path), timeout=0.5)
    assert r["problem"] == "probe_failed"
    assert time.monotonic() - t < 5


def test_not_on_path_names_the_dir(tmp_path):
    d = tmp_path / "cargo" / "bin"
    stub(d, "tool")
    (tmp_path / "bin").mkdir()
    r = probe(Requirement(bin="tool"), host(tmp_path, extra_dirs=[d]))
    assert r["problem"] == "not_on_path" and str(d) in r["fix"]


def test_old_bins_mean_too_old_and_manual_leads(tmp_path):
    stub(tmp_path / "bin", "convert")
    req = Requirement(
        bin="magick", old_bins=["convert"], install={"apt": "imagemagick"}, manual={"linux": "AppImage"}
    )
    r = probe(req, host(tmp_path, managers={"apt"}))
    assert r["problem"] == "too_old" and r["fix"] == "" and r["manual"] == "AppImage"
    assert r["found_path"].endswith("convert")


ORDER = {"brew": "imagemagick", "apt": "imagemagick", "winget": "ImageMagick.ImageMagick", "choco": "imagemagick"}


@pytest.mark.parametrize(
    "os,managers,install,fix,privileged",
    [
        ("darwin", {"brew"}, ORDER, "brew install imagemagick", False),
        ("linux", {"apt"}, ORDER, "sudo apt-get install -y imagemagick", True),
        (
            "windows",
            {"winget", "choco"},
            ORDER,
            "winget install --id ImageMagick.ImageMagick -e --accept-package-agreements --accept-source-agreements",
            False,
        ),
        ("windows", {"choco"}, ORDER, "choco install imagemagick -y (in an admin shell)", True),
        ("linux", set(), ORDER, "", False),
    ],
)
def test_fix_selection_per_os(tmp_path, os, managers, install, fix, privileged):
    (tmp_path / "bin").mkdir()
    req = Requirement(bin="magick", install=install, manual={os: "see docs"})
    r = probe(req, host(tmp_path, os=os, managers=managers))
    assert r["problem"] == "missing"
    assert r["fix"] == fix and r["privileged"] is privileged
    assert r["manual"] == "see docs"


def test_cargo_absent_points_to_rustup(tmp_path):
    (tmp_path / "bin").mkdir()
    r = probe(Requirement(bin="magick-mcp", install={"cargo": "magick-mcp"}), host(tmp_path))
    assert r["fix"].startswith("Install Rust from https://rustup.rs")


def test_manager_table_is_complete():
    assert set(MANAGERS) == {"cargo", "brew", "winget", "scoop", "apt", "dnf", "pacman", "apk", "choco"}


def test_merge_path_appends_and_never_drops():
    assert merge_path("a:b", "b:c", None, sep=":") == "a:b:c"
    assert merge_path("a", None, None, sep=":") == "a"
    assert merge_path("", "x", "y", sep=":") == "x:y"


def test_refresh_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    before = os.environ["PATH"]

    def boom():
        raise AssertionError("must not read the registry")

    refresh_windows_path(boom)
    assert os.environ["PATH"] == before


def _two_servers():
    return {
        "a": [Requirement("toolA", install={"brew": "pa"})],
        "b": [Requirement("toolB", install={"apt": "pb"})],
    }


def test_install_runs_unprivileged_prints_privileged(tmp_path):
    calls, lines = [], []

    def run(argv, **kw):
        assert kw["stdin"] == __import__("subprocess").DEVNULL
        calls.append(list(argv))

    install(_two_servers(), host(tmp_path, managers={"brew", "apt"}), run=run, out=lines.append)
    assert calls == [["brew", "install", "pa"]]
    text = "\n".join(lines)
    assert "sudo apt-get install -y pb" in text
    assert "run yourself:" in text


def test_install_dry_run_executes_nothing(tmp_path):
    lines = []

    def run(argv, **kw):
        raise AssertionError("dry run executed")

    install(_two_servers(), host(tmp_path, managers={"brew", "apt"}), dry_run=True, run=run, out=lines.append)
    assert "would run: brew install pa" in "\n".join(lines)


def test_install_survives_a_failing_or_prompting_installer(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    stub(bindir, "brew", "read x || exit 1")
    monkeypatch.setenv("PATH", str(bindir))
    lines = []
    h = host(tmp_path, managers={"brew"})
    install({"a": [Requirement("toolA", install={"brew": "pa"})]}, h, out=lines.append)
    assert "toolA" in "\n".join(lines[-5:])


def test_cli_check_exit_codes(tmp_path):
    p = tmp_path / "mcp.json"
    spec = {"command": "x", "requires": [{"bin": "definitely-not-a-real-bin-xyz"}]}
    p.write_text(json.dumps({"mcpServers": {"s": spec, "_note": {}}}))
    assert main(["check", "--config", str(p)]) == 1
    del spec["requires"]
    p.write_text(json.dumps({"mcpServers": {"s": spec}}))
    assert main(["check", "--config", str(p)]) == 0
    spec["requires"] = "nope"
    p.write_text(json.dumps({"mcpServers": {"s": spec}}))
    assert main(["install", "--config", str(p)]) == 2


def test_not_on_path_wording_per_os(tmp_path):
    d = tmp_path / "cargo" / "bin"
    stub(d, "tool")
    (tmp_path / "bin").mkdir()
    lin = probe(Requirement(bin="tool"), host(tmp_path, extra_dirs=[d]))
    assert lin["fix"] == f"Add {d} to PATH and restart the harness."
    win = probe(Requirement(bin="tool"), host(tmp_path, os="windows", extra_dirs=[d]))
    assert win["fix"] == f"Add {d} to PATH, then Recheck."


WIN_REQ = Requirement(bin="magick", old_bins=["convert"], install={"winget": "ImageMagick.ImageMagick"})


def test_windows_system_convert_is_ignored(tmp_path, monkeypatch):
    stub(tmp_path / "bin", "convert")
    monkeypatch.setenv("SystemRoot", str(tmp_path / "BIN"))
    r = probe(WIN_REQ, host(tmp_path, os="windows", managers={"winget"}))
    assert r["problem"] == "missing" and r["fix"].startswith("winget install --id ImageMagick.ImageMagick")


def test_windows_convert_elsewhere_is_still_too_old(tmp_path, monkeypatch):
    stub(tmp_path / "bin", "convert")
    monkeypatch.setenv("SystemRoot", str(tmp_path / "elsewhere"))
    r = probe(WIN_REQ, host(tmp_path, os="windows", managers={"winget"}))
    assert r["problem"] == "too_old"


def test_install_resolves_argv0_and_never_runs_the_same_argv_twice(tmp_path):
    brew = stub(tmp_path / "bin", "brew")
    calls = []
    manifest = {
        "a": [Requirement("toolA", install={"brew": "p"})],
        "b": [Requirement("toolA", install={"brew": "p"})],
    }
    install(manifest, host(tmp_path, managers={"brew"}), run=lambda argv, **kw: calls.append(list(argv)), out=lambda s: None)
    assert calls == [[str(brew), "install", "p"]]


def test_cli_unknown_server_exits_2(tmp_path, capsys):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"s": {"command": "x"}}}))
    for cmd in ("check", "install"):
        assert main([cmd, "--server", "nope", "--config", str(p)]) == 2
        assert f"no MCP server named 'nope' in {p}" in capsys.readouterr().err


@pytest.mark.parametrize("doc", [[], {"mcpServers": []}, {"mcpServers": 3}])
def test_cli_non_object_manifest_exits_2(tmp_path, doc):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps(doc))
    assert main(["check", "--config", str(p)]) == 2

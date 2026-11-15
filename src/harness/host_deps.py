"""Host dependencies. An MCP server in `mcp.json` may declare host binaries it
needs; this module probes the host for them and picks an install command.

Config, one entry per binary under the server's `requires` key:

    "requires": [{"bin": "magick", "min_version": "7", "old_bins": ["convert"],
                  "install": {"brew": "imagemagick", "apt": "imagemagick"},
                  "manual": {"linux": "Download the AppImage from imagemagick.org"}}]

`probe` reports one requirement as a dict (`ok`, `problem`, `fix`, `manual`
and more); `check` does a list. Probes run `--version`-style commands with a
10 s timeout and stdin closed. `install` runs only the argv of unprivileged
managers (cargo, brew, winget, scoop) from MANAGERS, with stdin closed and no
timeout. Privileged commands (printed with `sudo ` on Linux), rustup hints,
PATH hints and manual pointers are only printed; nothing here elevates.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import tool_paths


@dataclass(frozen=True)
class Requirement:
    bin: str
    version_args: list[str] | None = field(default_factory=lambda: ["--version"])
    min_version: str | None = None
    install: dict[str, str] = field(default_factory=dict)
    manual: dict[str, str] = field(default_factory=dict)
    old_bins: list[str] = field(default_factory=list)
    setting: str | None = None  # a Harness Tools path setting (tool_paths.PROGRAMS) that install fills in


@dataclass(frozen=True)
class Manager:
    name: str
    detect: str
    argv: tuple[str, ...]
    privileged: bool
    oses: frozenset[str]


def _m(name: str, detect: str, argv: str, privileged: bool, *oses: str) -> Manager:
    return Manager(name, detect, tuple(argv.split()), privileged, frozenset(oses))


_ALL = ("linux", "darwin", "windows")
MANAGERS: dict[str, Manager] = {
    m.name: m
    for m in (
        _m("cargo", "cargo", "cargo install --locked {pkg}", False, *_ALL),
        _m("brew", "brew", "brew install {pkg}", False, "darwin", "linux"),
        _m(
            "winget",
            "winget",
            "winget install --id {pkg} -e --accept-package-agreements --accept-source-agreements",
            False,
            "windows",
        ),
        _m("scoop", "scoop", "scoop install {pkg}", False, "windows"),
        _m("apt", "apt-get", "apt-get install -y {pkg}", True, "linux"),
        _m("dnf", "dnf", "dnf install -y {pkg}", True, "linux"),
        _m("pacman", "pacman", "pacman -S --noconfirm {pkg}", True, "linux"),
        _m("apk", "apk", "apk add {pkg}", True, "linux"),
        _m("choco", "choco", "choco install {pkg} -y", True, "windows"),
    )
}


@dataclass
class Host:
    os: str
    path: str
    managers: set[str]
    extra_dirs: list[Path] = field(default_factory=list)
    known: dict[str, str] = field(default_factory=dict)  # bin -> the full path saved in Settings, tried before PATH


def current_host(path: str | None = None) -> Host:
    path = path if path is not None else os.environ.get("PATH", "")
    plat = {"darwin": "darwin", "win32": "windows"}.get(sys.platform, "linux")
    cargo_home = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
    return Host(
        os=plat,
        path=path,
        managers={n for n, m in MANAGERS.items() if shutil.which(m.detect, path=path)},
        extra_dirs=[cargo_home / "bin", Path("/opt/homebrew/bin"), Path("/home/linuxbrew/.linuxbrew/bin")],
    )


def _bad(where: str, why: str) -> ValueError:
    return ValueError(f"{where}: {why}")


def _str_map(item: dict, key: str, where: str) -> dict[str, str]:
    v = item.get(key, {})
    if not isinstance(v, dict) or not all(isinstance(k, str) and isinstance(x, str) for k, x in v.items()):
        raise _bad(where, f"`{key}` must be an object of strings")
    return dict(v)


def parse_requires(raw: Any, where: str) -> list[Requirement]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _bad(where, "`requires` must be a list")
    out: list[Requirement] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise _bad(where, f"requires[{i}] must be an object")
        b = item.get("bin")
        if not isinstance(b, str) or not b:
            raise _bad(where, f"requires[{i}] needs a `bin` string")
        va = item.get("version_args", ["--version"])
        if va is not None and not (isinstance(va, list) and all(isinstance(a, str) for a in va)):
            raise _bad(where, f"requires[{i}] `version_args` must be a list of strings or null")
        mv = item.get("min_version")
        if mv is not None and not isinstance(mv, str):
            raise _bad(where, f"requires[{i}] `min_version` must be a string")
        ob = item.get("old_bins", [])
        if not (isinstance(ob, list) and all(isinstance(a, str) for a in ob)):
            raise _bad(where, f"requires[{i}] `old_bins` must be a list of strings")
        st = item.get("setting")
        if st is not None and st not in tool_paths.PROGRAMS:
            raise _bad(where, f"requires[{i}] `setting` must be one of {sorted(tool_paths.PROGRAMS)}")
        if st is not None and tool_paths.PROGRAMS[st].bin != b:
            raise _bad(where, f"requires[{i}] `setting` {st!r} is for {tool_paths.PROGRAMS[st].bin}, not {b}")
        out.append(
            Requirement(
                bin=b,
                version_args=None if va is None else list(va),
                min_version=mv,
                install=_str_map(item, "install", f"{where}: requires[{i}]"),
                manual=_str_map(item, "manual", f"{where}: requires[{i}]"),
                old_bins=list(ob),
                setting=st,
            )
        )
    return out


def parse_version(text: str) -> tuple[int, ...] | None:
    m = re.search(r"\d+(?:\.\d+)*", text)
    return tuple(int(p) for p in m.group().split(".")) if m else None


def _run_version(path: str, args: list[str], timeout: float) -> str | None:
    try:
        r = subprocess.run(
            [path, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout or "") + "\n" + (r.stderr or "")


def _fix(req: Requirement, host: Host) -> tuple[str, bool]:
    rustup = ""
    for name, pkg in req.install.items():
        m = MANAGERS.get(name)
        if m is None or host.os not in m.oses:
            continue
        if name not in host.managers:
            if name == "cargo" and not rustup:
                rustup = f"Install Rust from https://rustup.rs, then: cargo install --locked {pkg}"
            continue
        cmd = " ".join(a.replace("{pkg}", pkg) for a in m.argv)
        if m.privileged and host.os == "linux":
            cmd = "sudo " + cmd
        elif m.privileged and name == "choco":
            cmd += " (in an admin shell)"
        return cmd, m.privileged
    return rustup, False


def _in_system_dir(p: str) -> bool:
    """True when `p` lies under the Windows system directory (its `convert.exe` is not ImageMagick)."""
    def norm(x: str) -> str:
        return os.path.normpath(x).replace("\\", "/").lower().rstrip("/")

    root = norm(os.environ.get("SystemRoot") or r"C:\Windows")
    return norm(p).startswith(root + "/")


def probe(req: Requirement, host: Host, timeout: float = 10.0) -> dict:
    res: dict = {
        "bin": req.bin,
        "ok": False,
        "found_path": None,
        "version": None,
        "min_version": req.min_version,
        "problem": None,
        "fix": "",
        "privileged": False,
        "manual": req.manual.get(host.os, ""),
    }
    saved = host.known.get(req.bin)
    found = saved if saved and Path(saved).is_file() else shutil.which(req.bin, path=host.path)
    if not found and req.setting:  # the harness runs it by full path, so an installed copy off PATH will do
        found = tool_paths.find(tool_paths.PROGRAMS[req.setting], path_env=host.path)
    if found:
        res["found_path"] = found
        if req.version_args is not None:
            out = _run_version(found, req.version_args, timeout)
            v = parse_version(out) if out is not None else None
            res["version"] = ".".join(map(str, v)) if v else None
        else:
            v = None
        if req.min_version is None:
            res["ok"] = True
            return res
        want = parse_version(req.min_version)
        if v is None or want is None:
            res["problem"] = "probe_failed"
            return res
        if v >= want:
            res["ok"] = True
            return res
        res["problem"] = "too_old"
        res["fix"], res["privileged"] = _fix(req, host)
        return res
    for d in host.extra_dirs:
        if shutil.which(req.bin, path=str(d)):
            res["problem"] = "not_on_path"
            res["fix"] = (
                f"Add {d} to PATH, then Recheck." if host.os == "windows" else f"Add {d} to PATH and restart the harness."
            )
            return res
    for old in req.old_bins:
        p = shutil.which(old, path=host.path)
        if p and not (host.os == "windows" and _in_system_dir(p)):
            res["problem"] = "too_old"
            res["found_path"] = p
            return res
    res["problem"] = "missing"
    res["fix"], res["privileged"] = _fix(req, host)
    return res


def check(requires: list[Requirement], host: Host | None = None) -> list[dict]:
    host = host or current_host()
    return [probe(r, host) for r in requires]


def merge_path(current: str, machine: str | None, user: str | None, sep: str = os.pathsep) -> str:
    """Current entries first, then any machine and user entries not already there."""
    win = sep == ";"
    key = (lambda e: e.lower()) if win else (lambda e: e)
    out = [e for e in current.split(sep) if e]
    seen = {key(e) for e in out}
    for scope in (machine, user):
        for e in (scope or "").split(sep):
            if e and key(e) not in seen:
                seen.add(key(e))
                out.append(e)
    return sep.join(out)


def _read_registry_path() -> tuple[str | None, str | None]:
    import winreg

    def read(root: Any, sub: str) -> str | None:
        try:
            with winreg.OpenKey(root, sub) as k:
                value, kind = winreg.QueryValueEx(k, "Path")
                return winreg.ExpandEnvironmentStrings(value) if kind == winreg.REG_EXPAND_SZ else value
        except OSError:
            return None

    machine = read(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    user = read(winreg.HKEY_CURRENT_USER, "Environment")
    return machine, user


def refresh_windows_path(read: Callable[[], tuple[str | None, str | None]] = _read_registry_path) -> None:
    """Pick up PATH changes made by an installer since this process started."""
    if sys.platform != "win32":
        return
    machine, user = read()
    os.environ["PATH"] = merge_path(os.environ.get("PATH", ""), machine, user, os.pathsep)


def load_manifest(path: Path) -> dict[str, list[Requirement]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers", {})
    return {
        name: parse_requires(spec.get("requires"), f"{path}: server {name!r}")
        for name, spec in servers.items()
        if not name.startswith("_") and isinstance(spec, dict)
    }


def _runnable(req: Requirement, host: Host, res: dict) -> list[str] | None:
    """Argv of the unprivileged manager `_fix` chose, or None if the fix is not runnable."""
    if res["ok"] or res["problem"] not in ("missing", "too_old") or res["privileged"] or not res["fix"]:
        return None
    for name, pkg in req.install.items():
        m = MANAGERS.get(name)
        if m is None or host.os not in m.oses or name not in host.managers:
            continue
        if m.privileged:
            return None
        return [a.replace("{pkg}", pkg) for a in m.argv]
    return None


def install(
    manifest: dict[str, list[Requirement]],
    host: Host,
    dry_run: bool = False,
    run: Callable = subprocess.run,
    out: Callable[[str], None] = print,
    rehost: Callable[[], Host] | None = None,
    record: Callable[[str, str], None] | None = None,
) -> None:
    """Run unprivileged fixes, then report whatever is still unmet. Never raises on a failed installer.
    A met requirement that names a `setting` has its full path passed to `record(setting, path)`."""
    ran: set[tuple[str, ...]] = set()
    for server, reqs in manifest.items():
        for req in reqs:
            argv = _runnable(req, host, probe(req, host))
            if argv is None or tuple(argv) in ran:
                continue
            ran.add(tuple(argv))
            cmd = " ".join(argv)
            if dry_run:
                out(f"would run: {cmd}")
                continue
            out(f"== {server}: {cmd}")
            try:
                run([shutil.which(argv[0], path=host.path) or argv[0], *argv[1:]], stdin=subprocess.DEVNULL)
            except OSError as e:
                out(f"   could not run: {e}")
    if not dry_run:
        host = rehost() if rehost else host
    unmet = False
    recorded: set[str] = set()
    for server, reqs in manifest.items():
        for req in reqs:
            res = probe(req, host)
            if res["ok"]:
                if req.setting and record and res["found_path"] and req.setting not in recorded and not dry_run:
                    recorded.add(req.setting)
                    record(req.setting, res["found_path"])
                continue
            if not unmet:
                out("Still unmet:")
                unmet = True
            out(f"  {server}: {req.bin} ({res['problem']})")
            if res["fix"]:
                out(f"    {'run yourself: ' if res['privileged'] else ''}{res['fix']}")
            if res["manual"]:
                out(f"    or: {res['manual']}")
    if not unmet:
        out("All host dependencies are met.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="host_deps")
    ap.add_argument("command", choices=["check", "install"])
    ap.add_argument("--server")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[2] / "mcp.json")
    ap.add_argument("--vault", type=Path, help="the harness vault whose Settings get the paths found (default: ZEN_VAULT)")
    args = ap.parse_args(argv)
    vault = (args.vault.expanduser() if args.vault
             else tool_paths.vault_root_from_env(Path(__file__).resolve().parents[2] / ".env"))
    known = {p.bin: s for k, p in tool_paths.PROGRAMS.items() if (s := tool_paths.saved(vault, k))}

    def host_now() -> Host:
        h = current_host()
        h.known = dict(known)
        return h

    def record(setting: str, path: str) -> None:
        prog = tool_paths.PROGRAMS[setting]
        where = f"Settings > Harness Tools > {prog.name}"
        path = tool_paths.canonical(path)
        if tool_paths.saved(vault, setting) == path:
            print(f"{prog.name}: {path} ({where})")
            return
        tool_paths.save(vault, setting, path)
        known[prog.bin] = path
        print(f"{prog.name}: saved {path} to {where} ({tool_paths.settings_dir(vault) / tool_paths.FILE})")
    try:
        manifest = load_manifest(args.config)
    except (ValueError, OSError, AttributeError, TypeError) as e:
        print(f"host_deps: {e}", file=sys.stderr)
        return 2
    if args.server:
        if args.server not in manifest:
            print(f"no MCP server named {args.server!r} in {args.config}", file=sys.stderr)
            return 2
        manifest = {k: v for k, v in manifest.items() if k == args.server}
    if args.command == "install":
        def rehost() -> Host:
            refresh_windows_path()
            return host_now()

        install(manifest, host_now(), dry_run=args.dry_run, rehost=rehost, record=record)
        return 0
    host = host_now()
    bad = False
    for server, reqs in manifest.items():
        for req in reqs:
            res = probe(req, host)
            bad = bad or not res["ok"]
            state = "ok" if res["ok"] else res["problem"]
            print(f"{server}: {req.bin} {state}" + (f" -> {res['fix']}" if res["fix"] else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

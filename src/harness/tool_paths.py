"""Host programs the harness runs itself, by full path: Settings > Harness Tools. Today, ImageMagick.

The path is saved in `vault-settings/tools.json` under the program's key (`{"imagemagick": {"path": ...}}`),
beside image generation's section and out of the bots' reach (a bot that could rewrite it could make the
harness run any program). `scripts/install-deps` fills it in: an `mcp.json` `requires` entry that names
`"setting": "imagemagick"` gets the full path of the binary it found (`host_deps.install`). The harness runs
the saved path; with none saved it falls back to PATH, and the Settings page says which it is using."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.vault_settings import settings_dir

FILE = "tools.json"  # shared with image generation (src/generation/images.py); each tool keeps its own key


@dataclass(frozen=True)
class Program:
    key: str            # its section in tools.json, and in the API path
    bin: str            # the executable's name on PATH (`requires` "bin")
    name: str
    min_version: tuple[int, ...]
    banner: str         # what its version output must say, so another program by the same name is refused
    version_args: tuple[str, ...]
    candidates: Callable[[], list[Path]]  # where installers put it, newest first, for when PATH does not have it


def _imagemagick_candidates() -> list[Path]:
    if sys.platform == "win32":
        roots = {os.environ.get(v) for v in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")} - {None, ""}
        found = [Path(p) for r in roots for p in glob.glob(str(Path(r) / "ImageMagick-*" / "magick.exe"))]

        def version(p: Path) -> tuple[int, ...]:
            m = re.search(r"ImageMagick-(\d+(?:\.\d+)*)", str(p))
            return tuple(int(x) for x in m.group(1).split(".")) if m else ()

        return sorted(found, key=version, reverse=True)
    home = Path.home()
    return [Path("/opt/homebrew/bin/magick"), Path("/usr/local/bin/magick"), Path("/usr/bin/magick"),
            home / ".local" / "bin" / "magick", home / "bin" / "magick"]


IMAGEMAGICK = Program(key="imagemagick", bin="magick", name="ImageMagick", min_version=(7,), banner="ImageMagick",
                      version_args=("-version",), candidates=_imagemagick_candidates)
PROGRAMS: dict[str, Program] = {p.key: p for p in (IMAGEMAGICK,)}


# -- the saved setting --------------------------------------------------------------------------------------

def _read(vault_root: Path) -> dict[str, Any]:
    try:
        data = json.loads((settings_dir(vault_root) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def saved(vault_root: Path, key: str) -> str | None:
    """The saved full path, or None."""
    entry = _read(vault_root).get(key)
    p = entry.get("path") if isinstance(entry, dict) else None
    return p.strip() if isinstance(p, str) and p.strip() else None


def canonical(path: str) -> str:
    """The path as the file system spells it on Windows (`magick.EXE` from PATHEXT becomes `magick.exe`).
    Elsewhere it is kept as given: Homebrew's /opt/homebrew/bin/magick is a link that outlives upgrades."""
    return os.path.realpath(path) if sys.platform == "win32" else path


def save(vault_root: Path, key: str, path: str | None) -> None:
    """Save the full path (None clears it), keeping the other tools' sections."""
    target = settings_dir(vault_root) / FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    data = _read(vault_root)
    if path:
        data[key] = {"path": canonical(path)}
    else:
        data.pop(key, None)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(target)


# -- checking a path ----------------------------------------------------------------------------------------

PROBLEMS = {
    "not_absolute": "Give the full path, like C:\\Program Files\\ImageMagick-7.1.2-Q16-HDRI\\magick.exe or /usr/bin/magick.",
    "not_found": "There is no file at that path.",
    "not_program": "That file does not run as {name}: its version output does not say {banner}.",
    "too_old": "{name} {version} is too old; the harness needs {min} or newer.",
}


def problem_text(prog: Program, problem: str, version: str | None = None) -> str:
    return PROBLEMS[problem].format(name=prog.name, banner=prog.banner, version=version or "",
                                    min=".".join(map(str, prog.min_version)))


def verify(prog: Program, path: str, timeout: float = 10.0) -> dict[str, Any]:
    """Run the program's version command: {"ok", "version", "problem"}. `problem` is a PROBLEMS key."""
    from .host_deps import _run_version, parse_version

    p = Path(path)
    if not p.is_absolute():
        return {"ok": False, "version": None, "problem": "not_absolute"}
    if not p.is_file():
        return {"ok": False, "version": None, "problem": "not_found"}
    out = _run_version(str(p), list(prog.version_args), timeout)
    if out is None or prog.banner not in out:
        return {"ok": False, "version": None, "problem": "not_program"}
    m = re.search(re.escape(prog.banner) + r"\s+(\d+(?:\.\d+)*(?:-\d+)?)", out)
    version = m.group(1) if m else None
    v = parse_version(version or "")
    if v is None or v < prog.min_version:
        return {"ok": False, "version": version, "problem": "too_old"}
    return {"ok": True, "version": version, "problem": None}


def find(prog: Program, path_env: str | None = None) -> str | None:
    """The first working copy: on PATH, then where installers put it."""
    first = shutil.which(prog.bin, path=path_env)
    for c in [*([Path(first)] if first else []), *prog.candidates()]:
        if c.is_file() and verify(prog, str(c))["ok"]:
            return canonical(str(c))
    return None


def path_for(vault_root: Path, key: str) -> str | None:
    """What the harness runs: the saved path when that file exists, else the program on PATH. No version
    check: this runs on every call. A saved path that is gone does not fall back; Settings shows why."""
    prog = PROGRAMS[key]
    s = saved(vault_root, key)
    if s:
        return s if Path(s).is_file() else None
    return shutil.which(prog.bin)


def status(vault_root: Path, key: str) -> dict[str, Any]:
    """For Settings: the saved path, what the harness runs and where it came from, and a found copy to offer."""
    prog = PROGRAMS[key]
    s = saved(vault_root, key)
    on_path = shutil.which(prog.bin)
    path, source = (s, "saved") if s else (on_path, "PATH") if on_path else (None, "")
    v = verify(prog, path) if path else {"ok": False, "version": None, "problem": None}
    found = find(prog)
    return {"id": key, "name": prog.name, "bin": prog.bin, "saved": s, "path": path, "source": source,
            "ok": v["ok"], "version": v["version"],
            "problem": problem_text(prog, v["problem"], v["version"]) if v["problem"] else None,
            "found": found if found and found != s else None,
            "min_version": ".".join(map(str, prog.min_version))}


# -- install-deps: which vault ------------------------------------------------------------------------------

def vault_root_from_env(env_file: Path, env: Mapping[str, str] = os.environ) -> Path:
    """The vault the harness would use: ZEN_VAULT from the shell, else from the repo's .env, else the default
    (HarnessConfig's rule, without starting the harness)."""
    raw = env.get("ZEN_VAULT")
    if not raw and env_file.is_file():
        from dotenv import dotenv_values

        raw = dotenv_values(env_file).get("ZEN_VAULT")
    return Path(raw or "~/.zen-agents/vault").expanduser()

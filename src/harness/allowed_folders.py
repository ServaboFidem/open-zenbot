"""Folders the `files` MCP server may reach, added on Settings > Allowed Folders, each Full Access (read
and write) or Read-Only. Kept in `<vault>-settings/allowed_folders.json`, beside the vault where bots cannot
rewrite it (src.vault_settings):

    {"folders": [{"path": "D:\\\\Projects\\\\site", "access": "full"},
                 {"path": "/home/me/reference", "access": "read_only"}]}

(A bare path string, the format before access modes, loads as Full Access.) `mcp.json` hands them to the
server as two list variables, each a whole arg: `${ZEN_ALLOWED_FOLDERS}` (Full Access, one arg per folder)
and `${ZEN_READ_ONLY_FOLDER_ARGS}` (`--read-only-dir <path>` pairs; server-filesystem 2.3.0+ refuses every
write inside those). Only folders that still exist are passed: the server will not start on a missing one.
The vault and `ZEN_FS_ROOT` are reachable as before, with Full Access, and are not stored here.

A folder must be absolute, exist, and not be too broad: a drive or filesystem root, the home folder or
anything above it, or an operating system folder (or anything inside one) is refused, and so is the
harness's settings, ledger or music folder, anything inside them, or anything holding them. How folders may nest
is `AllowedFolders.place`."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.vault_settings import settings_dir

FILE = "allowed_folders.json"
VAR = "ZEN_ALLOWED_FOLDERS"  # Full Access folders, one arg each
READ_ONLY_VAR = "ZEN_READ_ONLY_FOLDER_ARGS"  # Read-Only folders, as `--read-only-dir <path>` pairs
VARS = (VAR, READ_ONLY_VAR)


class FolderRefused(ValueError):
    """A folder that cannot be allowed; the message is for the user."""


def _system_folders() -> list[Path]:
    if sys.platform == "win32":
        names = [os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows",
                 os.environ.get("ProgramFiles") or r"C:\Program Files",
                 os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
                 os.environ.get("ProgramData") or r"C:\ProgramData"]
    else:
        names = ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/opt", "/proc", "/root", "/run",
                 "/sbin", "/sys", "/usr", "/var", "/System", "/Library", "/Applications", "/private"]
    return [Path(n) for n in names if n]


def _norm(p: Path) -> Path:
    try:
        return p.resolve()
    except OSError:
        return p


def private_folders(vault_root: Path) -> list[Path]:
    """What the harness keeps from bots: its settings (keys, and this very list), its ledger and its music data."""
    from src.memory.ledger import ledger_path_for
    from src.music.settings import music_dir

    return [settings_dir(vault_root), ledger_path_for(vault_root), music_dir(vault_root)]


def refusal(path: Path, home: Path | None = None, system: list[Path] | None = None,
            private: list[Path] | None = None) -> str | None:
    """Why this folder cannot be allowed, or None. `path` is absolute and resolved."""
    home = _norm(home if home is not None else Path.home())
    system = [_norm(s) for s in (system if system is not None else _system_folders())]
    if path.parent == path:
        return f"{path} is a whole drive; choose a folder inside it"
    if path == home or home.is_relative_to(path):
        return f"{path} holds your whole home folder; choose a folder inside it"
    for s in system:
        if path == s or path.is_relative_to(s):
            return f"{path} is a system folder; choose a folder of your own"
    for p in (_norm(x) for x in private or []):
        if path == p or path.is_relative_to(p) or p.is_relative_to(path):
            return f"{path} holds the harness's own {p.name} folder (keys and records bots must not reach); choose another folder"
    return None


def check(raw: str, home: Path | None = None, system: list[Path] | None = None,
          private: list[Path] | None = None) -> Path:
    """A path the user typed or picked -> the folder to store; raises FolderRefused with the reason."""
    text = (raw or "").strip().strip('"').strip()
    if not text:
        raise FolderRefused("enter a folder")
    p = Path(text).expanduser()
    if not p.is_absolute():
        raise FolderRefused(f"{text} is not a full path; give the whole path, like {Path.home() / 'Documents'}")
    try:
        p = p.resolve(strict=True)
    except OSError:
        raise FolderRefused(f"{text} does not exist") from None
    if not p.is_dir():
        raise FolderRefused(f"{p} is a file, not a folder")
    why = refusal(p, home, system, private)
    if why:
        raise FolderRefused(why)
    return p


class FolderConflict(FolderRefused):
    """The folder is fine on its own but clashes with one already allowed (HTTP 409)."""


FULL, READ_ONLY = "full", "read_only"
ACCESS = (FULL, READ_ONLY)
LABEL = {FULL: "Full Access", READ_ONLY: "Read-Only"}


@dataclass(frozen=True)
class Folder:
    path: str
    access: str = FULL


@dataclass
class AllowedFolders:
    """The folders the user added, each Full Access or Read-Only. The server refuses every write inside a
    Read-Only folder, whatever else is allowed, so the one nesting that adds anything is a Read-Only folder
    inside a Full Access one (the vault and ZEN_FS_ROOT count as Full Access): a protected corner of a
    writable folder. Every other nesting is refused by `place` with the reason."""

    folders: list[Folder] = field(default_factory=list)

    @classmethod
    def load(cls, vault_root: Path) -> "AllowedFolders":
        try:
            raw = json.loads((settings_dir(vault_root) / FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        items = raw.get("folders") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return cls()
        out: list[Folder] = []
        for f in items:
            if isinstance(f, str):  # before access modes: a bare path, which was read-write
                f = {"path": f, "access": FULL}
            if not isinstance(f, dict) or not isinstance(f.get("path"), str):
                continue
            path, access = f["path"].strip(), f.get("access", FULL)
            if path and Path(path).is_absolute() and access in ACCESS and all(Path(x.path) != Path(path) for x in out):
                out.append(Folder(path, access))
        return cls(folders=out)

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"folders": [{"path": x.path, "access": x.access} for x in self.folders]}, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def get(self, raw: str) -> Folder | None:
        target = Path(raw)
        return next((f for f in self.folders if Path(f.path) == target), None)

    def place(self, p: Path, access: str, fixed: list[Path] | None = None) -> "AllowedFolders":
        """Add `p` with `access`, or raise FolderConflict saying why it clashes. `fixed` are the folders the
        server always gets with Full Access (the vault, ZEN_FS_ROOT). Inside `p`, folders with the same
        access go (it covers them); a Read-Only folder stays inside a new Full Access one."""
        if access not in ACCESS:
            raise FolderRefused(f"access is {' or '.join(ACCESS)}")
        if self.get(str(p)):
            raise FolderConflict(f"{p} is already allowed")
        outer = [(Path(f.path), f.access) for f in self.folders] + [(_norm(Path(x)), FULL) for x in fixed or []]
        around = [(o, a) for o, a in outer if p != o and p.is_relative_to(o)]
        if around:
            o, a = max(around, key=lambda x: len(x[0].parts))  # the nearest one
            if a == READ_ONLY:
                raise FolderConflict(f"{p} is inside {o}, which is Read-Only, so nothing in it can be written"
                                     + ("" if access == READ_ONLY else f"; make {o} Full Access first"))
            if access == FULL:
                raise FolderConflict(f"{p} is already inside {o}, which has Full Access")
        inside = [f for f in self.folders if Path(f.path) != p and Path(f.path).is_relative_to(p)]
        if access == READ_ONLY and (writable := [f.path for f in inside if f.access == FULL]):
            raise FolderConflict(f"{p} holds {', '.join(writable)}, which has Full Access; Read-Only would stop "
                                 "writes there too. Make it Read-Only or remove it first")
        kept = [f for f in self.folders if f not in inside or f.access != access]
        return AllowedFolders(folders=[*kept, Folder(str(p), access)])

    def with_access(self, raw: str, access: str, fixed: list[Path] | None = None) -> "AllowedFolders":
        """Switch one folder between Full Access and Read-Only, under the same rules as adding it."""
        cur = self.get(raw)
        if cur is None:
            raise FolderRefused(f"{raw} is not an allowed folder")
        if cur.access == access:
            return self
        rest = AllowedFolders(folders=[f for f in self.folders if f != cur])
        placed = rest.place(Path(cur.path), access, fixed)
        order = [f.path for f in self.folders]  # keep the row where it was
        return AllowedFolders(folders=sorted(placed.folders, key=lambda f: order.index(f.path) if f.path in order else len(order)))

    def without(self, raw: str) -> "AllowedFolders":
        target = Path(raw)
        return AllowedFolders(folders=[f for f in self.folders if Path(f.path) != target])

    def existing(self, access: str) -> list[str]:
        """What the server gets, by access: the folders still on disk (it will not start on a missing one)."""
        return [f.path for f in self.folders if f.access == access and Path(f.path).is_dir()]

    def server_args(self) -> dict[str, list[str]]:
        """The harness's list variables for mcp.json: Full Access folders as plain args, Read-Only ones as
        `--read-only-dir <path>` pairs (server-filesystem 2.3.0+; options go before its `--`)."""
        ro = [a for p in self.existing(READ_ONLY) for a in ("--read-only-dir", p)]
        return {VAR: self.existing(FULL), READ_ONLY_VAR: ro}


def full_access_roots(vault_root: Path, env: Mapping[str, str] | None = None) -> list[Path]:
    """What the files server always gets with Full Access: the vault, and ZEN_FS_ROOT when set."""
    fs_root = (os.environ if env is None else env).get("ZEN_FS_ROOT")
    return [Path(vault_root), *([Path(fs_root).expanduser()] if fs_root else [])]


@dataclass
class Folders:
    """Where bots may reach, the same folders the `files` server gets: read fresh wherever a check runs, so a
    change on Settings > Allowed Folders applies at once. Paths are compared resolved (symlinks followed)."""

    full: list[Path] = field(default_factory=list)       # writable: the vault, ZEN_FS_ROOT, Full Access folders
    read_only: list[Path] = field(default_factory=list)  # Read-Only folders: readable, never written
    private: list[Path] = field(default_factory=list)    # never reached: the harness's settings, ledger and music

    def readable(self, p: Path) -> bool:
        """Inside a Full Access or Read-Only folder, and not inside the harness's own folders."""
        p = _norm(Path(p))
        return not _within(p, self.private) and _within(p, [*self.full, *self.read_only])

    def writable(self, p: Path) -> bool:
        """Inside a Full Access folder, and not inside a Read-Only one or the harness's own folders."""
        p = _norm(Path(p))
        return not _within(p, [*self.private, *self.read_only]) and _within(p, self.full)


def _within(p: Path, roots: list[Path]) -> bool:
    return any(p == r or p.is_relative_to(r) for r in (_norm(Path(x)) for x in roots))


def reachable(vault_root: Path, env: Mapping[str, str] | None = None) -> Folders:
    """The folders bots may reach now: the vault, ZEN_FS_ROOT and the user's Allowed Folders by access, with
    the harness's settings, ledger and music folders kept out."""
    s = AllowedFolders.load(vault_root)
    return Folders(full=[*full_access_roots(vault_root, env), *map(Path, s.existing(FULL))],
                   read_only=[Path(p) for p in s.existing(READ_ONLY)],
                   private=private_folders(vault_root))


def public(saved: AllowedFolders, vault_root: Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What Settings > Allowed Folders shows: the fixed folders, then the ones the user added."""
    fs_root = (os.environ if env is None else env).get("ZEN_FS_ROOT")
    fixed = [{"path": str(vault_root), "source": "vault", "access": FULL}]
    if fs_root:
        fixed.append({"path": fs_root, "source": ".env", "access": FULL})
    return {"fixed": fixed, "folders": [{"path": f.path, "exists": Path(f.path).is_dir(), "access": f.access} for f in saved.folders]}

"""Music discovery settings, the Discogs token and the music data folder.

    <vault>-settings/music.json        MusicSettings (no secrets)
    <vault>-settings/music_keys.json   the Discogs token (owner-only)
    <vault>-music/                     music.db, atlas/, covers/ (beside the vault, private to bots)"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.harness.mcp_keys import hint
from src.memory.exclusion import exclusion
from src.vault_settings import settings_dir

SETTINGS_FILE = "music.json"
KEYS_FILE = "music_keys.json"
SUFFIX = "-music"


def music_dir(vault_root: Path) -> Path:
    vault_root = Path(vault_root)
    return vault_root.parent / f"{vault_root.name}{SUFFIX}"


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _num(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class MusicSettings:
    enabled: bool = False
    username: str = ""
    focus: list[str] = field(default_factory=list)
    count: int = 4
    era_mix: float = 0.5  # 0 = all classics, 1 = all new
    obscurity: float = 0.7  # 0 = well known allowed, 1 = deepest cuts
    notify: bool = True

    @classmethod
    def load(cls, vault_root: Path) -> "MusicSettings":
        raw = _read(settings_dir(vault_root) / SETTINGS_FILE)
        d = cls()
        focus = raw.get("focus")
        return cls(
            enabled=raw["enabled"] if isinstance(raw.get("enabled"), bool) else d.enabled,
            username=raw["username"].strip() if isinstance(raw.get("username"), str) else d.username,
            focus=[s.strip() for s in focus if isinstance(s, str) and s.strip()] if isinstance(focus, list) else [],
            count=int(_clamp(_num(raw.get("count"), d.count), 1, 10)),
            era_mix=_clamp(_num(raw.get("era_mix"), d.era_mix), 0.0, 1.0),
            obscurity=_clamp(_num(raw.get("obscurity"), d.obscurity), 0.0, 1.0),
            notify=raw["notify"] if isinstance(raw.get("notify"), bool) else d.notify,
        )

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.public(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def public(self) -> dict[str, Any]:
        """Safe for the GUI: settings only, never the token."""
        return {"enabled": self.enabled, "username": self.username, "focus": list(self.focus), "count": self.count,
                "era_mix": self.era_mix, "obscurity": self.obscurity, "notify": self.notify}


@dataclass
class MusicKeys:
    token: str = ""

    def __post_init__(self) -> None:
        # the ledger redacts registered values: a token that reaches a tool output or a turn is masked there
        exclusion.register_secret_value(self.token)

    @classmethod
    def load(cls, vault_root: Path) -> "MusicKeys":
        token = _read(settings_dir(vault_root) / KEYS_FILE).get("token")
        return cls(token=token.strip() if isinstance(token, str) else "")

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / KEYS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner only: it holds a secret
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"token": self.token}, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def hint(self) -> str:
        return hint(self.token)

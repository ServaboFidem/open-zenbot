"""Sonos settings in sonos.json, in the settings folder beside the vault (never in it: a bot can write the vault).

No secret: whether ZenBot looks for speakers, the highest volume it may set, and the addresses of the speakers it
last found (tried first at the next look, since a direct request gets through where a multicast may not)."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.vault_settings import settings_dir

SETTINGS_FILE = "sonos.json"
DEFAULT_MAX_VOLUME = 60
BAD_VOLUME = "The highest volume is a whole number from 0 to 100."


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _is_volume(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100


def check_volume(value: object) -> int:
    """The volume as given, or ValueError(BAD_VOLUME) for anything but a whole number from 0 to 100."""
    if not _is_volume(value):
        raise ValueError(BAD_VOLUME)
    return value  # type: ignore[return-value]


def _is_address(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


@dataclass
class SonosSettings:
    enabled: bool = True
    max_volume: int = DEFAULT_MAX_VOLUME
    known: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, vault_root: Path) -> "SonosSettings":
        raw = _read(settings_dir(vault_root) / SETTINGS_FILE)
        enabled = raw.get("enabled")
        volume = raw.get("max_volume")
        known = raw.get("known")
        return cls(enabled=enabled if isinstance(enabled, bool) else True,
                   max_volume=volume if _is_volume(volume) else DEFAULT_MAX_VOLUME,
                   known=[a for a in known if _is_address(a)] if isinstance(known, list) else [])

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self.public(), "known": list(self.known)}, indent=2), encoding="utf-8")
        tmp.replace(path)

    def public(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "max_volume": self.max_volume}

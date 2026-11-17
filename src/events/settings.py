"""Calendar settings, the Google keys and the calendar data folder.

    <vault>-settings/calendar.json        CalendarSettings (no secrets)
    <vault>-settings/calendar_keys.json   the Google client and refresh token (owner-only)
    <vault>-calendar/                     calendar.db (beside the vault, private to bots)"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.harness.mcp_keys import hint
from src.memory.exclusion import exclusion
from src.vault_settings import settings_dir

SETTINGS_FILE = "calendar.json"
KEYS_FILE = "calendar_keys.json"
SUFFIX = "-calendar"


def calendar_dir(vault_root: Path) -> Path:
    vault_root = Path(vault_root)
    return vault_root.parent / f"{vault_root.name}{SUFFIX}"


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


@dataclass
class CalendarSettings:
    push: bool = False
    destination: str = ""
    account: str = ""
    scopes: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, vault_root: Path) -> "CalendarSettings":
        raw = _read(settings_dir(vault_root) / SETTINGS_FILE)
        scopes = raw.get("scopes")
        return cls(
            push=raw["push"] if isinstance(raw.get("push"), bool) else False,
            destination=_text(raw, "destination"),
            account=_text(raw, "account"),
            scopes=[s.strip() for s in scopes if isinstance(s, str) and s.strip()] if isinstance(scopes, list) else [],
        )

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.public(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def public(self) -> dict[str, Any]:
        """Safe for the GUI: settings only, never a key."""
        return {"push": self.push, "destination": self.destination, "account": self.account,
                "scopes": list(self.scopes)}


@dataclass
class CalendarKeys:
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""

    def __post_init__(self) -> None:
        # the ledger redacts registered values: a secret that reaches a tool output or a turn is masked there
        exclusion.register_secret_value(self.client_secret)
        exclusion.register_secret_value(self.refresh_token)

    @classmethod
    def load(cls, vault_root: Path) -> "CalendarKeys":
        raw = _read(settings_dir(vault_root) / KEYS_FILE)
        return cls(client_id=_text(raw, "client_id"), client_secret=_text(raw, "client_secret"),
                   refresh_token=_text(raw, "refresh_token"))

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / KEYS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner only: it holds secrets
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"client_id": self.client_id, "client_secret": self.client_secret,
                       "refresh_token": self.refresh_token}, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def public(self) -> dict[str, Any]:
        """Safe for the GUI: whether keys are set, a hint of the client id, never a secret or token."""
        return {"credentials": bool(self.client_id and self.client_secret),
                "client_id_hint": hint(self.client_id), "connected": bool(self.refresh_token)}

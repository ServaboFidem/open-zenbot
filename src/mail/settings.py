"""AgentMail settings: which inbox is ZenBot's (agentmail.json) and the API key (agentmail_keys.json, owner only).

Both live in the settings folder beside the vault, never in it: a bot can write the vault."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.harness.mcp_keys import hint
from src.memory.exclusion import exclusion
from src.vault_settings import settings_dir

SETTINGS_FILE = "agentmail.json"
KEYS_FILE = "agentmail_keys.json"

BAD_KEY = "An AgentMail key is plain text with no spaces, at most 200 characters."
BAD_NAME = ("A name is 1 to 64 lowercase letters, digits, dots, dashes or underscores, "
            "starting with a letter or digit.")
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def check_key(value: object) -> str:
    """The key trimmed, or ValueError(BAD_KEY). Any prefix is taken: AgentMail decides whether a key is good."""
    if not isinstance(value, str):
        raise ValueError(BAD_KEY)
    key = value.strip()
    if not key or len(key) > 200 or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
        raise ValueError(BAD_KEY)
    return key


def check_username(value: object) -> str:
    """The inbox name trimmed and lower-cased, or ValueError(BAD_NAME)."""
    if not isinstance(value, str):
        raise ValueError(BAD_NAME)
    name = value.strip().lower()
    if not _NAME.fullmatch(name):
        raise ValueError(BAD_NAME)
    return name


@dataclass
class MailSettings:
    """ZenBot's chosen inbox; both empty when none is chosen. No secret."""

    inbox_id: str = ""
    email: str = ""

    @classmethod
    def load(cls, vault_root: Path) -> "MailSettings":
        raw = _read(settings_dir(vault_root) / SETTINGS_FILE)
        return cls(inbox_id=_text(raw, "inbox_id"), email=_text(raw, "email"))

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.public(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def public(self) -> dict[str, Any]:
        return {"inbox_id": self.inbox_id, "email": self.email}


@dataclass
class MailKeys:
    """The AgentMail API key, in <vault>-settings/agentmail_keys.json (owner only)."""

    api_key: str = field(default="", repr=False)  # not in a repr: telemetry sends frame locals

    def __post_init__(self) -> None:
        exclusion.register_secret_value(self.api_key)  # the ledger masks it if it reaches a tool output

    @classmethod
    def load(cls, vault_root: Path) -> "MailKeys":
        return cls(api_key=_text(_read(settings_dir(vault_root) / KEYS_FILE), "api_key"))

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / KEYS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner only: it holds a secret
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"api_key": self.api_key}, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def public(self) -> dict[str, Any]:
        """Safe for the GUI: whether a key is set and its last four characters, never the key."""
        return {"key_set": bool(self.api_key), "key_hint": hint(self.api_key)}

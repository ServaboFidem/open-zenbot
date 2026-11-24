"""Telnyx settings: the sending number (telnyx.json), the API key (telnyx_keys.json, owner only) and the texts file
(telnyx_texts.json: the owner's phone and the schedule).

All three live in the settings folder beside the vault, never in it: a bot can write the vault. The texts file is
written only by the desktop app (src/electron/telnyx-texts.cjs), never by a harness route: the harness API has no
sign-in, so a route that saved the recipient would let a bot point ZenBot's texts at any number. Here it is only
read, and a file that fails any check reads as "texts off"."""

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

SETTINGS_FILE = "telnyx.json"
KEYS_FILE = "telnyx_keys.json"
TEXTS_FILE = "telnyx_texts.json"
LEADS = (10, 15, 30, 60, 120)
PHONE = re.compile(r"\+[1-9]\d{7,14}")
_EVENING = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")

BAD_KEY = "A Telnyx key is plain text with no spaces, at most 200 characters."


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _text(raw: Any, key: str) -> str:
    value = raw.get(key) if isinstance(raw, dict) else None
    return value.strip() if isinstance(value, str) else ""


def check_key(value: object) -> str:
    """The key trimmed, or ValueError(BAD_KEY). Telnyx decides whether a key is good."""
    if not isinstance(value, str):
        raise ValueError(BAD_KEY)
    key = value.strip()
    if not key or len(key) > 200 or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
        raise ValueError(BAD_KEY)
    return key


def last4(phone: str) -> str:
    """The last four digits of a phone number, the most any answer or ledger entry shows."""
    return "".join(c for c in phone if c.isdigit())[-4:]


@dataclass
class TelnyxNumber:
    """The number ZenBot texts from; all empty when none is chosen. No secret."""

    id: str = ""
    phone: str = ""
    profile_id: str = ""

    @classmethod
    def load(cls, vault_root: Path) -> "TelnyxNumber":
        raw = _read(settings_dir(vault_root) / SETTINGS_FILE)
        return cls(id=_text(raw, "id"), phone=_text(raw, "phone"), profile_id=_text(raw, "profile_id"))

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.public(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "phone": self.phone, "profile_id": self.profile_id}


@dataclass
class TelnyxKeys:
    """The Telnyx API key, in <vault>-settings/telnyx_keys.json (owner only)."""

    api_key: str = field(default="", repr=False)  # not in a repr: telemetry sends frame locals

    def __post_init__(self) -> None:
        exclusion.register_secret_value(self.api_key)  # the ledger masks it if it reaches a tool output

    @classmethod
    def load(cls, vault_root: Path) -> "TelnyxKeys":
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


@dataclass(frozen=True)
class TextsConfig:
    """The owner's phone (E.164, or empty) and when to text it."""

    phone: str
    lead_minutes: int
    evening: str
    before: bool
    rundown: bool

    @property
    def on(self) -> bool:
        return bool(self.phone) and (self.before or self.rundown)


def read_texts(vault_root: Path) -> TextsConfig | None:
    """The texts file as the desktop app saved it, or None when it is missing or any field fails its check. The same
    rules as the desktop app's (telnyx-texts.cjs); a missing field takes its default, a present one must pass."""
    raw = _read(settings_dir(vault_root) / TEXTS_FILE)
    if not isinstance(raw, dict):
        return None
    phone, lead = raw.get("phone", ""), raw.get("lead_minutes", 30)
    evening, before, rundown = raw.get("evening", "20:00"), raw.get("before", False), raw.get("rundown", False)
    if not isinstance(phone, str) or (phone and not PHONE.fullmatch(phone)):
        return None
    if type(lead) is not int or lead not in LEADS:  # noqa: E721 - True is an int, and is no lead time
        return None
    if not isinstance(evening, str) or not _EVENING.fullmatch(evening):
        return None
    if type(before) is not bool or type(rundown) is not bool:  # noqa: E721
        return None
    return TextsConfig(phone, lead, evening, before, rundown)

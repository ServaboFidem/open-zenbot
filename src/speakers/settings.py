"""Wi-Fi speakers settings in wifi_speakers.json, in the settings folder beside the vault (never in it: a bot can write
the vault).

No secret: a switch per speaker type (AirPlay, Chromecast, DLNA; all off until the owner turns one on, since each
listens on the home network), the one default speaker's key, and the speakers each type last found, so the lists and
the default's name show before the first look ends. Names come from whatever is on the network, so they are cleaned
and capped here and shown as plain text everywhere."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.vault_settings import settings_dir

SETTINGS_FILE = "wifi_speakers.json"
PROTOCOLS = ("airplay", "chromecast", "dlna")
NAME_CAP = 120
KNOWN_CAP = 64
KEY_CAP = 300
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def clean(text: object, cap: int = NAME_CAP) -> str:
    """A name or model as a device announced it: control characters removed, trimmed, at most `cap` characters."""
    if not isinstance(text, str):
        return ""
    return _CONTROL.sub("", text).strip()[:cap].strip()


@dataclass(frozen=True)
class Speaker:
    """One speaker one protocol found. `key` is `<protocol>:<id>` with the id its library plays to later (pyatv's
    identifier, the Cast UUID, the DLNA UDN). `address` never leaves the harness."""

    key: str
    protocol: str
    name: str
    model: str
    address: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: object) -> "Speaker | None":
        if not isinstance(d, dict):
            return None
        key, protocol = d.get("key"), d.get("protocol")
        if protocol not in PROTOCOLS or not isinstance(key, str) or not key.startswith(f"{protocol}:") or len(key) > KEY_CAP:
            return None
        address = d.get("address")
        return cls(key=clean(key, KEY_CAP), protocol=protocol, name=clean(d.get("name")), model=clean(d.get("model")),
                   address=address if isinstance(address, str) and len(address) <= 64 else "")


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


@dataclass
class SpeakerSettings:
    airplay: bool = False
    chromecast: bool = False
    dlna: bool = False
    default: str | None = None
    known: dict[str, list[Speaker]] = field(default_factory=dict)

    @classmethod
    def load(cls, vault_root: Path) -> "SpeakerSettings":
        raw = _read(settings_dir(vault_root) / SETTINGS_FILE)
        switches = {p: raw.get(p) is True for p in PROTOCOLS}
        default = raw.get("default")
        known: dict[str, list[Speaker]] = {}
        raw_known = raw.get("known")
        if isinstance(raw_known, dict):
            for p in PROTOCOLS:
                entries = raw_known.get(p)
                if isinstance(entries, list):
                    found = [s for s in (Speaker.from_dict(e) for e in entries) if s is not None and s.protocol == p]
                    # the default first, so the cap never drops the entry that keeps its name
                    known[p] = ([s for s in found if s.key == default][:1] + [s for s in found if s.key != default])[:KNOWN_CAP]
        return cls(**switches, default=default if isinstance(default, str) and 0 < len(default) <= KEY_CAP else None,
                   known=known)

    def save(self, vault_root: Path) -> None:
        d = settings_dir(vault_root)
        d.mkdir(parents=True, exist_ok=True)
        path = d / SETTINGS_FILE
        tmp = path.with_suffix(".json.tmp")
        body = {**self.switches(), "default": self.default,
                "known": {p: [s.to_dict() for s in v[:KNOWN_CAP]] for p, v in self.known.items()}}
        tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
        tmp.replace(path)

    def on(self, protocol: str) -> bool:
        return bool(getattr(self, protocol, False)) if protocol in PROTOCOLS else False

    def switches(self) -> dict[str, bool]:
        return {p: self.on(p) for p in PROTOCOLS}

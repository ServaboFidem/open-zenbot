"""The spoken briefing's three files, all in `vault-settings/` (never the vault): what the owner set
(`briefings.json`), what happened (`briefing_state.json`) and the jokes told (`briefing_jokes.json`).
Each is written tmp-then-replace. A broken file reads as empty; only `briefings.json` carries an `error`."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.harness.scheduler import Schedule, next_run
from src.vault_settings import settings_dir

log = logging.getLogger(__name__)

KINDS = ("morning",)
MAX_SLOTS = 7
MAX_SPEAKERS = 32
JOKES_CAP = 500
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass
class Slot:
    days: list[int]
    time: str


@dataclass
class Briefing:
    kind: str
    enabled: bool
    slots: list[Slot]
    speakers: list[str] | None
    volume: int
    id: str = field(default_factory=lambda: secrets.token_hex(4))


def check(b: Briefing, ceiling: int) -> None:
    """Raise a ValueError whose message is the sentence the form shows."""
    if b.kind not in KINDS:
        raise ValueError("Only the morning briefing exists.")
    if not b.slots:
        raise ValueError("Add at least one day and time.")
    if len(b.slots) > MAX_SLOTS:
        raise ValueError(f"Use at most {MAX_SLOTS} lines.")
    seen: set[int] = set()
    for s in b.slots:
        if not s.days:
            raise ValueError("Pick at least one day for each line.")
        if any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in s.days):
            raise ValueError("Days must be Monday to Sunday.")
        if not isinstance(s.time, str) or not TIME_RE.match(s.time):
            raise ValueError("Enter each time as HH:MM, from 00:00 to 23:59.")
        if seen & set(s.days):
            raise ValueError("Each day can have only one time.")
        seen |= set(s.days)
    if isinstance(b.volume, bool) or not isinstance(b.volume, int) or not 1 <= b.volume <= ceiling:
        raise ValueError(f"Pick a volume from 1 to {ceiling}.")
    if b.speakers is not None and not 1 <= len(b.speakers) <= MAX_SPEAKERS:
        raise ValueError(f"Pick from 1 to {MAX_SPEAKERS} speakers, or use every speaker.")


def next_slot(b: Briefing, after: datetime) -> datetime:
    """The earliest next time over the lines, worked out as routines do."""
    return min(next_run(Schedule("weekly", list(s.days), s.time), after) for s in b.slots)


def _write(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _briefing(d: Any) -> Briefing:
    sp = d.get("speakers")
    if sp is not None and not (isinstance(sp, list) and all(isinstance(x, str) for x in sp)):
        raise ValueError("speakers")
    return Briefing(kind=str(d["kind"]), enabled=bool(d["enabled"]),
                    slots=[Slot(days=[int(x) for x in s["days"]], time=str(s["time"])) for s in d["slots"]],
                    speakers=sp, volume=int(d["volume"]), id=str(d.get("id") or secrets.token_hex(4)))


class BriefingStore:
    FILE = "briefings.json"

    def __init__(self) -> None:
        self.items: dict[str, Briefing] = {}
        self.error: str = ""

    @classmethod
    def load(cls, vault: Path) -> "BriefingStore":
        store = cls()
        path = settings_dir(vault) / cls.FILE
        if not path.exists():
            return store
        try:
            doc = _read(path)
            for d in doc["briefings"]:
                b = _briefing(d)
                store.items[b.kind] = b
        except Exception as e:  # noqa: BLE001 - a broken or hostile file reads as none
            store.items = {}
            store.error = f"{cls.FILE} could not be read ({type(e).__name__})."
            log.warning("briefings: %s", type(e).__name__)
        return store

    def save(self, vault: Path) -> None:
        _write(settings_dir(vault) / self.FILE, {"briefings": [asdict(b) for b in self.items.values()]})

    def put(self, b: Briefing) -> None:
        self.items[b.kind] = b

    def delete(self, kind: str) -> bool:
        return self.items.pop(kind, None) is not None


class BriefingState:
    FILE = "briefing_state.json"

    def __init__(self) -> None:
        self._d: dict[str, dict[str, Any]] = {}

    @classmethod
    def load(cls, vault: Path) -> "BriefingState":
        st = cls()
        try:
            doc = _read(settings_dir(vault) / cls.FILE)
            if isinstance(doc, dict):
                st._d = {k: v for k, v in doc.items() if isinstance(v, dict)}
        except Exception as e:  # noqa: BLE001 - missing or broken reads as empty
            if not isinstance(e, FileNotFoundError):
                log.warning("briefing state: %s", type(e).__name__)
        return st

    def save(self, vault: Path) -> None:
        _write(settings_dir(vault) / self.FILE, self._d)

    def claimed(self, kind: str) -> str | None:
        v = self._d.get(kind, {}).get("claimed")
        return v if isinstance(v, str) else None

    def claim(self, kind: str, slot_iso: str) -> None:
        self._d.setdefault(kind, {})["claimed"] = slot_iso

    def last_run(self, kind: str) -> dict[str, Any] | None:
        return self._d.get(kind, {}).get("last_run")

    def last_skip(self, kind: str) -> dict[str, Any] | None:
        return self._d.get(kind, {}).get("last_skip")

    def record_run(self, kind: str, run: dict[str, Any]) -> None:
        self._d.setdefault(kind, {})["last_run"] = run

    def record_skip(self, kind: str, skip: dict[str, Any]) -> None:
        self._d.setdefault(kind, {})["last_skip"] = skip


def normalise(joke: str) -> str:
    return "".join(c for c in joke.casefold() if c.isalnum())


class ToldJokes:
    FILE = "briefing_jokes.json"

    def __init__(self) -> None:
        self._items: list[dict[str, str]] = []  # newest first

    @classmethod
    def load(cls, vault: Path) -> "ToldJokes":
        j = cls()
        try:
            doc = _read(settings_dir(vault) / cls.FILE)
            j._items = [{"text": x["text"], "day": str(x.get("day", ""))} for x in doc["jokes"]
                        if isinstance(x, dict) and isinstance(x.get("text"), str)][:JOKES_CAP]
        except Exception as e:  # noqa: BLE001 - missing or broken reads as empty
            if not isinstance(e, FileNotFoundError):
                log.warning("briefing jokes: %s", type(e).__name__)
        return j

    def save(self, vault: Path) -> None:
        _write(settings_dir(vault) / self.FILE, {"jokes": self._items})

    def recent(self, n: int = 100) -> list[str]:
        return [x["text"] for x in self._items[:n]]

    def seen(self, joke: str) -> bool:
        k = normalise(joke)
        return any(normalise(x["text"]) == k for x in self._items)

    def add(self, joke: str, day: str) -> None:
        self._items.insert(0, {"text": joke, "day": day})
        del self._items[JOKES_CAP:]

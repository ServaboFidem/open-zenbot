"""The spoken briefing's three files, all in `vault-settings/` (never the vault): what the owner set
(`briefings.json`), what happened (`briefing_state.json`) and the jokes told (`briefing_jokes.json`).
Each is written tmp-then-replace. A broken file reads as empty; only `briefings.json` carries an `error`."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
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
JOKE_MAX = 500
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
    if not isinstance(b.slots, list) or not b.slots:
        raise ValueError("Add at least one day and time.")
    if len(b.slots) > MAX_SLOTS:
        raise ValueError(f"Use at most {MAX_SLOTS} lines.")
    seen: set[int] = set()
    for s in b.slots:
        if not isinstance(s, Slot) or not isinstance(s.days, list) or not s.days:
            raise ValueError("Pick at least one day for each line.")
        if any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in s.days):
            raise ValueError("Days must be Monday to Sunday.")
        if not isinstance(s.time, str) or not TIME_RE.match(s.time):
            raise ValueError("Enter each time as HH:MM, from 00:00 to 23:59.")
        if seen & set(s.days) or len(set(s.days)) != len(s.days):
            raise ValueError("Each day can have only one time.")
        seen |= set(s.days)
    if isinstance(b.volume, bool) or not isinstance(b.volume, int) or not 1 <= b.volume <= ceiling:
        raise ValueError(f"Pick a volume from 1 to {ceiling}.")
    if b.speakers is not None:
        sp = b.speakers
        if (not isinstance(sp, list) or not 1 <= len(sp) <= MAX_SPEAKERS
                or any(not isinstance(x, str) or not x.strip() for x in sp)):
            raise ValueError(f"Pick from 1 to {MAX_SPEAKERS} speakers, or use every speaker.")
        if len({x.casefold() for x in sp}) != len(sp):
            raise ValueError("Each speaker can be listed only once.")
    if not isinstance(b.enabled, bool):
        raise ValueError("Enabled must be on or off.")


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
    """Strict: a wrong type is refused, never coerced. The ceiling is unknown here, so volume is held to 1..100."""
    if not isinstance(d, dict) or not isinstance(d.get("slots"), list):
        raise ValueError("shape")
    slots = []
    for s in d["slots"]:
        if not isinstance(s, dict):
            raise ValueError("slot")
        slots.append(Slot(days=s.get("days"), time=s.get("time")))
    ident = d.get("id")
    if not isinstance(ident, str) or not ident:
        raise ValueError("id")
    b = Briefing(kind=d.get("kind"), enabled=d.get("enabled"), slots=slots,
                 speakers=d.get("speakers"), volume=d.get("volume"), id=ident)
    check(b, 100)
    return b


class BriefingStore:
    FILE = "briefings.json"

    def __init__(self) -> None:
        self.items: dict[str, Briefing] = {}
        self.error: str = ""
        self.read_only = False  # the file could not be read at all: never save over it
        self._bad: list[Any] = []  # entries the load could not read, written back as they were

    @classmethod
    def load(cls, vault: Path) -> "BriefingStore":
        """Entry by entry: a bad one is skipped and kept in the file. A file that cannot be read at all is copied
        to `briefings.json.bad-<time>` and the store goes read-only, so a save never wipes it."""
        store = cls()
        path = settings_dir(vault) / cls.FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("briefings"), list):
                raise ValueError("not a briefings file")
        except FileNotFoundError:
            return store
        except (OSError, ValueError) as e:
            store.read_only = True
            backup = path.with_name(f"{cls.FILE}.bad-{datetime.now().strftime('%Y%m%dT%H%M%S')}")
            try:
                shutil.copy2(path, backup)
                kept = f"; a copy is at {backup.name}"
            except OSError:
                kept = ""
            store.error = (f"{cls.FILE} could not be read ({type(e).__name__}){kept}. "
                           "Briefings will not be saved until it is fixed or removed.")
            log.error("briefings: unreadable file: %s", type(e).__name__)
            return store
        for item in raw["briefings"]:
            try:
                b = _briefing(item)
                if b.kind in store.items:
                    raise ValueError("duplicate kind")
                store.items[b.kind] = b
            except (TypeError, KeyError, ValueError, AttributeError) as e:
                store._bad.append(item)
                store.error = "A briefing in briefings.json could not be read and was skipped."
                log.warning("briefings: skipped an entry: %s", type(e).__name__)
        return store

    def save(self, vault: Path) -> None:
        if self.read_only:
            return  # the unreadable file stays as it is
        _write(settings_dir(vault) / self.FILE,
               {"briefings": [asdict(b) for b in self.items.values()] + self._bad})

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
        v = self._d.get(kind, {}).get("last_run")
        return v if isinstance(v, dict) else None

    def last_skip(self, kind: str) -> dict[str, Any] | None:
        v = self._d.get(kind, {}).get("last_skip")
        return v if isinstance(v, dict) else None

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
        k = normalise(joke[:JOKE_MAX])
        return bool(k) and any(normalise(x["text"]) == k for x in self._items)

    def add(self, joke: str, day: str) -> None:
        if not normalise(joke):
            return
        self._items.insert(0, {"text": joke[:JOKE_MAX], "day": day})
        del self._items[JOKES_CAP:]

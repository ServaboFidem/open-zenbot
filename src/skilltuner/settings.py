"""SkillTuner's switch (`vault-settings/skilltuner.json`) and its state (`<vault>-ledger/skilltuner.json`).

Dates are local `YYYY-MM-DD` strings passed in by the caller; nothing here reads the clock.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.vault_settings import settings_dir

DAILY_REQUESTS = 40
RESEARCH_AFTER_DAYS = 30
HOWS = ("suggested", "approved", "rejected")


def _replace_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


@dataclass
class TunerSettings:
    """SkillTuner's switch; no secret."""
    enabled: bool = False

    @staticmethod
    def _path(vault_root: Path) -> Path:
        return settings_dir(vault_root) / "skilltuner.json"

    @classmethod
    def load(cls, vault_root: Path) -> TunerSettings:
        try:
            d = json.loads(cls._path(vault_root).read_text(encoding="utf-8"))
            if not isinstance(d["enabled"], bool):
                raise TypeError("enabled must be true or false")
            return cls(d["enabled"])
        except (OSError, ValueError, KeyError, TypeError):
            return cls()

    def save(self, vault_root: Path) -> None:
        _replace_file(self._path(vault_root), asdict(self))


@dataclass
class Need:
    """Something a skill was missing, in words a search can use."""
    words: str
    topic: str
    seen: int = 1
    searched_on: str = ""
    outcome: str = ""


def _need_of(v: Any) -> Need:
    if not isinstance(v, dict):
        raise TypeError("a need is not an object")
    n = Need(**v)
    if not (isinstance(n.words, str) and isinstance(n.topic, str) and _whole(n.seen)
            and isinstance(n.searched_on, str) and isinstance(n.outcome, str)):
        raise TypeError("a need holds a value of the wrong type")
    return n


def _counts_of(v: Any) -> dict[str, int]:
    if not isinstance(v, dict) or not all(isinstance(k, str) and _whole(c) for k, c in v.items()):
        raise TypeError("counts are not day to number")
    return dict(v)


class TunerState:
    """Read marks, needs, the daily budget and known source slugs; tmp-then-replace on every change."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._marks: dict[str, int] = {}
        self._needs: dict[str, Need] = {}
        self._spent: dict[str, int] = {}
        self._converted: dict[str, int] = {}
        self._known: dict[str, str] = {}
        self._paused_until = ""
        self._last_pass_at = ""
        self._last_error = ""
        self._load()

    def _load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        try:
            d = json.loads(text)
            marks = _counts_of(d["marks"])
            needs = {k: _need_of(v) for k, v in d["needs"].items()}
            spent, conv = _counts_of(d["spent"]), _counts_of(d["converted"])
            known = d["known"]
            if not isinstance(known, dict) or not all(isinstance(k, str) and v in HOWS for k, v in known.items()):
                raise TypeError("known is malformed")
            paused, at, err = d["paused_until"], d["last_pass_at"], d["last_error"]
            if not all(isinstance(x, str) for x in (paused, at, err)):
                raise TypeError("a text value has the wrong type")
        except (ValueError, KeyError, TypeError, AttributeError):
            try:
                self.path.replace(self.path.with_name(self.path.name + ".bad"))
            except OSError:
                pass  # start empty all the same; the next save replaces it
            return
        self._marks, self._needs, self._spent, self._converted = marks, needs, spent, conv
        self._known, self._paused_until, self._last_pass_at, self._last_error = known, paused, at, err

    def _save(self) -> None:  # caller holds the lock
        _replace_file(self.path, {
            "marks": self._marks, "needs": {k: asdict(n) for k, n in self._needs.items()},
            "spent": self._spent, "converted": self._converted, "known": self._known,
            "paused_until": self._paused_until, "last_pass_at": self._last_pass_at,
            "last_error": self._last_error})

    def read(self, conv_id: str) -> int:
        with self._lock:
            return self._marks.get(conv_id, 0)

    def set_read(self, conv_id: str, n: int) -> None:
        with self._lock:
            self._marks[conv_id] = n
            self._save()

    def add_need(self, topic: str, words: str) -> None:
        with self._lock:
            key = words.casefold()
            if key in self._needs:
                self._needs[key].seen += 1
            else:
                self._needs[key] = Need(words=words, topic=topic)
            self._save()

    def due_needs(self, today: str, limit: int) -> list[Need]:
        now = date.fromisoformat(today)
        due: list[Need] = []
        with self._lock:
            for n in self._needs.values():
                try:
                    fresh = bool(n.searched_on) and (now - date.fromisoformat(n.searched_on)).days < RESEARCH_AFTER_DAYS
                except ValueError:
                    fresh = False
                if not fresh:
                    due.append(Need(**asdict(n)))
        due.sort(key=lambda n: -n.seen)  # stable: ties keep the order they were added
        return due[:max(limit, 0)]

    def mark_searched(self, words: str, today: str, outcome: str) -> None:
        with self._lock:
            n = self._needs.get(words.casefold())
            if n is not None:
                n.searched_on, n.outcome = today, outcome
                self._save()

    def searched(self, today: str) -> int:
        with self._lock:
            return sum(1 for n in self._needs.values() if n.searched_on == today)

    def spend(self, today: str) -> bool:
        with self._lock:
            if self._spent.get(today, 0) >= DAILY_REQUESTS:
                return False
            self._spent = {today: self._spent.get(today, 0) + 1}  # older days are not needed
            self._save()
            return True

    def spent(self, today: str) -> int:
        with self._lock:
            return self._spent.get(today, 0)

    def converted(self, today: str) -> int:
        with self._lock:
            return self._converted.get(today, 0)

    def add_converted(self, today: str) -> None:
        with self._lock:
            self._converted = {today: self._converted.get(today, 0) + 1}
            self._save()

    def pause_until(self, day: str) -> None:
        with self._lock:
            self._paused_until = day
            self._save()

    def paused(self, today: str) -> bool:
        with self._lock:
            return bool(self._paused_until) and today < self._paused_until

    @property
    def known(self) -> set[str]:
        with self._lock:
            return set(self._known)

    def remember(self, slug: str, how: str) -> None:
        if how not in HOWS:
            raise ValueError(f"how must be one of {', '.join(HOWS)}")
        with self._lock:
            self._known[slug] = how
            self._save()

    @property
    def last_pass_at(self) -> str:
        return self._last_pass_at

    @last_pass_at.setter
    def last_pass_at(self, v: str) -> None:
        with self._lock:
            self._last_pass_at = v
            self._save()

    @property
    def last_error(self) -> str:
        return self._last_error

    @last_error.setter
    def last_error(self, v: str) -> None:
        with self._lock:
            self._last_error = v
            self._save()

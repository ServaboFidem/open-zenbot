"""SkillTuner's switch (`vault-settings/skilltuner.json`) and its state (`<vault>-ledger/skilltuner.json`).

Dates are local `YYYY-MM-DD` strings passed in by the caller; nothing here reads the clock.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.skilltuner.convert import LICENCES
from src.skilltuner.repos import RepoEntry, parse_address, parse_index_path
from src.vault_settings import settings_dir

DAILY_REQUESTS = 40
RESEARCH_AFTER_DAYS = 30
HOWS = ("suggested", "approved", "rejected")
GH_API_PER_DAY = 30
GH_FILES_PER_DAY = 400
GH_LIMITS = {"api": GH_API_PER_DAY, "file": GH_FILES_PER_DAY}
NAMES_CAP = 1000


def _replace_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _repo_of(v: Any) -> RepoEntry:
    if not isinstance(v, dict):
        raise TypeError("a repository is not an object")
    r = RepoEntry(**v)
    if not all(isinstance(x, str) for x in asdict(r).values()):
        raise TypeError("a repository holds a value of the wrong type")
    if re.fullmatch(r"[0-9a-f]{16}", r.id) is None:
        raise ValueError("bad id")
    if r.licence and r.licence.strip().casefold() not in LICENCES:
        raise ValueError("licence not accepted")
    if (r.owner, r.repo, r.ref, r.folder) != parse_address(r.address()):
        raise ValueError("bad address")
    parse_index_path(r.index)
    return r


@dataclass
class TunerSettings:
    """SkillTuner's switch and the GitHub repositories it may read; no secret."""
    enabled: bool = False
    repos: list[RepoEntry] = field(default_factory=list)

    @staticmethod
    def _path(vault_root: Path) -> Path:
        return settings_dir(vault_root) / "skilltuner.json"

    @classmethod
    def load(cls, vault_root: Path) -> TunerSettings:
        try:
            d = json.loads(cls._path(vault_root).read_text(encoding="utf-8"))
            if not isinstance(d["enabled"], bool):
                raise TypeError("enabled must be true or false")
            repos: list[RepoEntry] = []
            for v in d.get("repos", []) if isinstance(d.get("repos", []), list) else []:
                try:
                    repos.append(_repo_of(v))
                except (TypeError, ValueError):
                    pass  # one bad entry never costs the switch or the others
            return cls(d["enabled"], repos)
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
    github_on: str = ""
    github_outcome: str = ""


def _need_of(v: Any) -> Need:
    if not isinstance(v, dict):
        raise TypeError("a need is not an object")
    n = Need(**v)
    if not (isinstance(n.words, str) and isinstance(n.topic, str) and _whole(n.seen)
            and isinstance(n.searched_on, str) and isinstance(n.outcome, str)
            and isinstance(n.github_on, str) and isinstance(n.github_outcome, str)):
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
        self._searches: dict[str, int] = {}  # searches sent, per local date
        self._batches: dict[str, int] = {}  # batches of messages read, per local date
        self._known: dict[str, str] = {}
        self._paused_until = ""
        self._source_pauses: dict[str, str] = {}
        self._github_spent: dict[str, dict[str, int]] = {}  # per local date: api and file counts
        self._names: list[str] = []  # rejected skill names, case-folded, oldest first
        self._last_pass_at = ""
        self._last_error = ""
        self._load()

    def _load(self) -> None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        try:
            d = json.loads(raw.decode("utf-8"))  # a UnicodeDecodeError is a ValueError: kept aside like bad JSON
            marks = _counts_of(d["marks"])
            needs = {k: _need_of(v) for k, v in d["needs"].items()}
            spent, conv = _counts_of(d["spent"]), _counts_of(d["converted"])
            searches, read_batches = _counts_of(d.get("searches", {})), _counts_of(d.get("batches", {}))  # later fields
            pauses = d.get("source_pauses", {})
            if not isinstance(pauses, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                       for k, v in pauses.items()):
                raise TypeError("source pauses are malformed")
            gspent = d.get("github_spent", {})
            if not isinstance(gspent, dict) or not all(
                    isinstance(k, str) and isinstance(v, dict) and set(v) <= set(GH_LIMITS)
                    and all(_whole(c) for c in v.values()) for k, v in gspent.items()):
                raise TypeError("github counts are malformed")
            names = d.get("rejected_names", [])
            if not isinstance(names, list) or not all(isinstance(x, str) for x in names):
                raise TypeError("rejected names are malformed")
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
        self._searches, self._batches = searches, read_batches
        self._source_pauses, self._github_spent = dict(pauses), {k: dict(v) for k, v in gspent.items()}
        self._names = list(names)[-NAMES_CAP:]
        self._known, self._paused_until, self._last_pass_at, self._last_error = known, paused, at, err

    def _save(self) -> None:  # caller holds the lock
        _replace_file(self.path, {
            "marks": self._marks, "needs": {k: asdict(n) for k, n in self._needs.items()},
            "spent": self._spent, "converted": self._converted, "searches": self._searches,
            "batches": self._batches, "known": self._known,
            "source_pauses": self._source_pauses, "github_spent": self._github_spent,
            "rejected_names": self._names,
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

    def drop_need(self, words: str) -> None:
        with self._lock:
            if self._needs.pop(words.casefold(), None) is not None:
                self._save()

    @staticmethod
    def _stale(on: str, outcome: str, now: date) -> bool:
        """Due again: never searched, or searched RESEARCH_AFTER_DAYS ago (an `undecided` one the next date)."""
        try:
            age = (now - date.fromisoformat(on)).days if on else None
        except ValueError:
            age = None
        return age is None or age >= (1 if outcome == "undecided" else RESEARCH_AFTER_DAYS)

    def _due_for(self, n: Need, now: date) -> list[str]:
        out = []
        if self._stale(n.searched_on, n.outcome, now):
            out.append("casemark")
        if self._stale(n.github_on, n.github_outcome, now):
            out.append("github")
        return out

    def due_for(self, need: Need, today: str) -> list[str]:
        """Which of "casemark" and "github" this need is due for."""
        return self._due_for(need, date.fromisoformat(today))

    def due_needs(self, today: str, limit: int, sources: tuple[str, ...] = ("casemark",)) -> list[Need]:
        """Needs due for any of `sources` (not searched there in the last RESEARCH_AFTER_DAYS), most seen first; a
        need the decider could not judge (`undecided`) is due again from the next local date."""
        now = date.fromisoformat(today)
        due: list[Need] = []
        with self._lock:
            for n in self._needs.values():
                if set(self._due_for(n, now)) & set(sources):
                    due.append(Need(**asdict(n)))
        due.sort(key=lambda n: -n.seen)  # stable: ties keep the order they were added
        return due[:max(limit, 0)]

    def mark_searched(self, words: str, today: str, outcome: str, source: str = "casemark") -> None:
        with self._lock:
            n = self._needs.get(words.casefold())
            if n is not None:
                if source == "github":
                    n.github_on, n.github_outcome = today, outcome
                else:
                    n.searched_on, n.outcome = today, outcome
                self._save()

    def reset_github(self) -> None:
        """Make every need due for GitHub again; CaseMark's marks stay."""
        with self._lock:
            for n in self._needs.values():
                n.github_on = n.github_outcome = ""
            self._save()

    def searched(self, today: str) -> int:
        """Searches sent on `today` (giving a need back does not lower it)."""
        with self._lock:
            return self._searches.get(today, 0)

    def add_search(self, today: str) -> None:
        with self._lock:
            self._searches = {today: self._searches.get(today, 0) + 1}
            self._save()

    def batches_read(self, today: str) -> int:
        with self._lock:
            return self._batches.get(today, 0)

    def add_batch(self, today: str) -> None:
        with self._lock:
            self._batches = {today: self._batches.get(today, 0) + 1}
            self._save()

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

    def pause_source(self, source: str, day: str) -> None:
        with self._lock:
            self._source_pauses[source] = day
            self._save()

    def source_paused(self, source: str, today: str) -> bool:
        with self._lock:
            until = self._source_pauses.get(source, "")
            return bool(until) and today < until

    def spend_github(self, today: str, kind: str) -> bool:
        if kind not in GH_LIMITS:
            raise ValueError(f"kind must be one of {', '.join(GH_LIMITS)}")
        with self._lock:
            now = {k: 0 for k in GH_LIMITS} | self._github_spent.get(today, {})
            if now[kind] >= GH_LIMITS[kind]:
                return False
            now[kind] += 1
            self._github_spent = {today: now}  # older days are not needed
            self._save()
            return True

    def github_spent(self, today: str) -> dict[str, int]:
        with self._lock:
            return {k: 0 for k in GH_LIMITS} | self._github_spent.get(today, {})

    @property
    def rejected_names(self) -> set[str]:
        with self._lock:
            return set(self._names)

    def remember_name(self, name: str) -> None:
        key = name.strip().casefold()
        if not key:
            return
        with self._lock:
            if key in self._names:
                self._names.remove(key)
            self._names.append(key)
            del self._names[:-NAMES_CAP]
            self._save()

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

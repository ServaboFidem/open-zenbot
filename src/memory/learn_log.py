"""What learning from the web remembers about itself.

`LearnLog` keeps, per URL, the md5 of the page text last learned, the ids
of the facts written from it as they land (so a changed page replaces them
and an unchanged, finished one is skipped), the ids left over from an
earlier version still to forget (`stale`), a `done` flag set once every
write has landed (a crash in the middle leaves it clear, so the page is
learned again), the names a page taught memory (so a product learned from
a company's site can anchor a later crawl of its own site), the
(organization, host) pairs whose site fact is written with that fact's id,
and the (host, name) pairs the user denied so they are never asked about
again. `HoldStore` keeps the loose discoveries waiting in Approvals, with
the request's fields and the writes to make if approved, so a restart
loses none of them. Both are small JSON files under the vault, written
whole through a temp file under a lock, like the sync log and the
registries.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .registry import _read, _write

__all__ = ["HoldStore", "LearnEntry", "LearnLog", "LEARN_SCHEMA"]

LEARN_SCHEMA = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s: str) -> str:
    return s.strip().lower()


@dataclass
class LearnEntry:
    url: str
    md5: str
    fact_ids: list[str] = field(default_factory=list)  # written by the current learn of this page, as they land
    approved_fact_ids: list[str] = field(default_factory=list)  # written from approved holds; kept across re-learns
    names: list[str] = field(default_factory=list)  # the names the page taught (matched names, products, approved holds)
    at: str = field(default_factory=_now)
    stale: list[str] = field(default_factory=list)  # ids from an earlier version of the page, still to forget
    done: bool = False  # every write of the current version landed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LearnEntry":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


class LearnLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        data = _read(self.path)
        self._entries: dict[str, LearnEntry] = {u: LearnEntry.from_dict(e) for u, e in data.get("entries", {}).items()}
        sites = data.get("sites", {})
        self._sites: dict[str, str] = dict(sites) if isinstance(sites, dict) else {k: "" for k in sites}  # schema 1 kept a list
        self._denied: set[str] = set(data.get("denied", []))

    def _save(self) -> None:
        _write(self.path, {"schema": LEARN_SCHEMA, "entries": {u: e.to_dict() for u, e in self._entries.items()},
                           "sites": dict(sorted(self._sites.items())), "denied": sorted(self._denied)})

    # -- pages --------------------------------------------------------------

    def get(self, url: str) -> LearnEntry | None:
        with self._lock:
            return self._entries.get(url)

    def put(self, entry: LearnEntry) -> None:
        with self._lock:
            self._entries[entry.url] = entry
            self._save()

    def add_fact_id(self, url: str, md5: str, fact_id: str) -> None:
        """A write landed. It belongs to the page's current version when the md5
        still matches; a late write from an earlier version is stale at once."""
        with self._lock:
            e = self._entries.get(url)
            if e is None:
                return
            (e.fact_ids if e.md5 == md5 else e.stale).append(fact_id)
            self._save()

    def take_stale(self, url: str) -> list[str]:
        with self._lock:
            e = self._entries.get(url)
            if e is None or not e.stale:
                return []
            out, e.stale = list(e.stale), []
            self._save()
            return out

    def mark_done(self, url: str, md5: str) -> None:
        with self._lock:
            e = self._entries.get(url)
            if e is None or e.md5 != md5:
                return
            e.done = True
            self._save()

    def add_approved(self, url: str, fact_ids: list[str], name: str = "") -> None:
        with self._lock:
            e = self._entries.get(url)
            if e is None:
                e = self._entries[url] = LearnEntry(url, "")
            e.approved_fact_ids = list(dict.fromkeys([*e.approved_fact_ids, *fact_ids]))
            if name.strip() and _norm(name) not in {_norm(n) for n in e.names}:
                e.names.append(name.strip())
            self._save()

    def learned(self) -> set[str]:
        """Every name any learned page taught, normalized."""
        with self._lock:
            return {_norm(n) for e in self._entries.values() for n in e.names if _norm(n)}

    # -- sites --------------------------------------------------------------

    @staticmethod
    def _site_key(org: str, host: str) -> str:
        return f"{_norm(org)}|{_norm(host)}"

    def site_recorded(self, org: str, host: str) -> bool:
        with self._lock:
            return self._site_key(org, host) in self._sites

    def record_site(self, org: str, host: str) -> None:
        with self._lock:
            self._sites.setdefault(self._site_key(org, host), "")
            self._save()

    def set_site_fact(self, org: str, host: str, fact_id: str) -> None:
        with self._lock:
            self._sites[self._site_key(org, host)] = fact_id
            self._save()

    def site_fact_id(self, org: str, host: str) -> str:
        with self._lock:
            return self._sites.get(self._site_key(org, host), "")

    def clear_site(self, org: str, host: str) -> None:
        with self._lock:
            if self._sites.pop(self._site_key(org, host), None) is not None:
                self._save()

    # -- denials ------------------------------------------------------------

    def is_denied(self, host: str, name: str) -> bool:
        with self._lock:
            return f"{_norm(host)}|{_norm(name)}" in self._denied

    def deny(self, host: str, name: str) -> None:
        with self._lock:
            self._denied.add(f"{_norm(host)}|{_norm(name)}")
            self._save()


class HoldStore:
    """Held learn payloads by approval id: the request's fields (so the
    approval can be rebuilt after a restart) and the writes to make."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._holds: dict[str, dict[str, Any]] = dict(_read(self.path).get("holds", {}))

    def _save(self) -> None:
        _write(self.path, {"schema": LEARN_SCHEMA, "holds": self._holds})

    def put(self, request: dict[str, Any], payload: dict[str, Any]) -> None:
        with self._lock:
            self._holds[request["id"]] = {"request": request, "payload": payload}
            self._save()

    def get(self, id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        with self._lock:
            h = self._holds.get(id)
            return None if h is None else (h["request"], h["payload"])

    def pop(self, id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        with self._lock:
            h = self._holds.pop(id, None)
            if h is None:
                return None
            self._save()
            return h["request"], h["payload"]

    def pending(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        with self._lock:
            return [(h["request"], h["payload"]) for h in self._holds.values()]

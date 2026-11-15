"""Global registries: who the owner is, who they know, and which organizations are theirs.

Persisted as small JSON files (people.json, orgs.json). Only ``shared`` writes feed them.
"""

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from .stores import slugify

Tier = Literal["close", "known"]
OrgRelation = Literal["owns", "runs", "works_at"]


@dataclass
class PersonEntry:
    name: str
    aliases: list[str] = field(default_factory=list)
    relation: str = ""
    tier: Tier = "known"
    is_owner: bool = False


@dataclass
class OrgEntry:
    slug: str
    name: str
    relation: OrgRelation
    aliases: list[str] = field(default_factory=list)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        path.replace(path.with_name(path.name + ".bad"))  # keep the damaged file recoverable
        return {}


def _clean(value: str, what: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{what} must not be empty")
    return value


class PeopleRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._people: dict[str, PersonEntry] = {
            e["name"]: PersonEntry(**e) for e in _read(self.path).get("people", [])
        }

    def _save(self) -> None:
        _write(self.path, {"people": [asdict(e) for e in self._people.values()]})

    def _find(self, name: str) -> PersonEntry | None:
        n = name.strip().lower()
        for e in self._people.values():
            if n == e.name.lower() or n in (a.lower() for a in e.aliases):
                return e
        return None

    def owner(self) -> PersonEntry | None:
        with self._lock:
            return next((e for e in self._people.values() if e.is_owner), None)

    def set_owner(self, name: str, aliases: list[str]) -> PersonEntry:
        name = _clean(name, "name")
        aliases = [_clean(a, "alias") for a in aliases]
        with self._lock:
            for e in self._people.values():
                e.is_owner = False
            e = self._find(name) or self._people.setdefault(name, PersonEntry(name))
            e.aliases = list(aliases)
            e.is_owner = True
            e.tier = "close"
            self._save()
            return e

    def rename_owner(self, name: str) -> PersonEntry | None:
        """Give the owner a new name (a full name for the one word an older
        install stored), keeping the old one as an alias. None, with nothing
        changed, when there is no owner or another person holds the name."""
        name = _clean(name, "name")
        with self._lock:
            owner = next((e for e in self._people.values() if e.is_owner), None)
            other = self._find(name)
            if owner is None or (other is not None and other is not owner):
                return None
            del self._people[owner.name]
            old, owner.name = owner.name, name
            if old.lower() != name.lower() and old.lower() not in (a.lower() for a in owner.aliases):
                owner.aliases.append(old)
            owner.aliases = [a for a in owner.aliases if a.lower() != name.lower()]
            self._people[name] = owner
            self._save()
            return owner

    def add(self, name: str, *, relation: str = "", tier: Tier = "known") -> PersonEntry:
        name = _clean(name, "name")
        with self._lock:
            e = self._find(name)
            if e is None:
                e = self._people[name] = PersonEntry(name, relation=relation, tier=tier)
            else:
                e.relation = relation or e.relation
                if tier == "close":
                    e.tier = "close"
            self._save()
            return e

    def add_alias(self, name: str, alias: str) -> None:
        alias = _clean(alias, "alias")
        with self._lock:
            e = self._find(name)
            if e and alias.lower() not in (a.lower() for a in [e.name, *e.aliases]):
                e.aliases.append(alias)
                self._save()

    def promote(self, name: str) -> None:
        with self._lock:
            e = self._find(name)
            if e and e.tier != "close":
                e.tier = "close"
                self._save()

    def find(self, name: str) -> PersonEntry | None:
        with self._lock:
            return self._find(name)

    def all(self) -> list[PersonEntry]:
        with self._lock:
            return list(self._people.values())

    def for_text(self, text: str) -> list[PersonEntry]:
        """Every close person, plus known people whose name or alias appears in ``text``."""
        out = []
        for e in self.all():
            if e.tier == "close" or any(
                re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text, re.IGNORECASE) for n in [e.name, *e.aliases]
            ):
                out.append(e)
        return out


class OrgRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._orgs: dict[str, OrgEntry] = {e["slug"]: OrgEntry(**e) for e in _read(self.path).get("orgs", [])}

    def _save(self) -> None:
        _write(self.path, {"orgs": [asdict(e) for e in self._orgs.values()]})

    def add(self, name: str, relation: OrgRelation, aliases=()) -> OrgEntry:
        with self._lock:
            slug = slugify(name)
            e = self._orgs.get(slug)
            if e is None:
                e = self._orgs[slug] = OrgEntry(slug, name, relation, list(aliases))
            else:
                e.relation = relation
                e.aliases = list(dict.fromkeys([*e.aliases, *aliases]))
            self._save()
            return e

    def get(self, slug: str) -> OrgEntry | None:
        with self._lock:
            return self._orgs.get(slug)

    def all(self) -> list[OrgEntry]:
        with self._lock:
            return list(self._orgs.values())

    def find(self, name: str) -> OrgEntry | None:
        n = name.strip().lower()
        with self._lock:
            for e in self._orgs.values():
                if n == e.name.lower() or n in (a.lower() for a in e.aliases):
                    return e
        return None

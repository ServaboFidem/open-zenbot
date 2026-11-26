"""Skill folders, index files and frontmatter of a GitHub repository (pure: no network).

Index files and frontmatter are text written by strangers. Nothing here yields a path that is not a listed skill
folder, and every text is cleaned and cut.
"""
from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass

import yaml

from .casemark import _clean
from .repos import SEGMENT

INDEX_NAMES = ("skills_index.json", "skills-index.json", "skill_index.json", "skill-index.json")
NAME_MAX = 120
DESCRIPTION_MAX = 1000
CATEGORY_MAX = 80
LICENCE_MAX = 100
SKILL_FILE = "SKILL.md"


@dataclass
class Listed:
    path: str
    name: str
    description: str
    category: str
    has_extra: bool = False


def _plain(path: str) -> bool:
    return bool(path) and all(re.fullmatch(SEGMENT, p) and p not in (".", "..") for p in path.split("/"))


def _inside(path: str, folder: str) -> bool:
    return not folder or path.startswith(folder + "/")


def skill_folders(blobs: list[tuple[str, int]], folder: str) -> list[str]:
    """Each directory holding a SKILL.md inside `folder`; a folder inside another skill folder is dropped."""
    dirs = set()
    for path, _size in blobs:
        d, _, base = path.rpartition("/")
        if base == SKILL_FILE and d and _inside(d, folder) and _plain(d):
            dirs.add(d)
    return sorted(d for d in dirs if not any(d.startswith(o + "/") for o in dirs))


def find_index(blobs: list[tuple[str, int]], folder: str, named: str) -> str | None:
    paths = {p for p, _ in blobs}
    if named and named in paths:
        return named
    found = []
    for p in paths:
        d, _, base = p.rpartition("/")
        if base in INDEX_NAMES and (d == "" or d == folder or _inside(d, folder)):
            found.append(p)
    return min(found, key=lambda p: (len(p), p)) if found else None


def _resolve(raw: object, index_dir: str, folders: set[str]) -> str | None:
    if not isinstance(raw, str) or not raw or "\\" in raw or "://" in raw or raw.startswith("/"):
        return None
    parts = raw.rstrip("/").split("/")
    lead = 0
    while lead < len(parts) and parts[lead] == "..":
        lead += 1
    rest = parts[lead:]
    if not rest or any(x in ("", ".", "..") for x in rest):
        return None
    tail = "/".join(rest)
    for base in (index_dir, ""):
        p = posixpath.normpath(posixpath.join(base, "/".join(parts)))
        if p in folders:
            return p
    if lead and tail in folders:
        return tail
    return None


def _line(value: object, limit: int) -> str:
    return " ".join(_clean(value, limit).split())


def parse_index(raw: bytes, index_path: str, folders: set[str]) -> tuple[list[Listed], int]:
    """The skills an index lists that are skill folders in `folders`, and how many entries were skipped."""
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as e:
        raise ValueError("The index is not JSON.") from e
    if isinstance(data, dict):
        data = data.get("skills")
    if not isinstance(data, list):
        raise ValueError("The index holds no list of skills.")
    index_dir = posixpath.dirname(index_path)
    listed: list[Listed] = []
    seen: set[str] = set()
    skipped = 0
    for entry in data:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        named = entry.get("name") if isinstance(entry.get("name"), str) else entry.get("id")
        where = entry.get("path") if isinstance(entry.get("path"), str) else entry.get("source")
        path = _resolve(where, index_dir, folders)
        name = _line(named, NAME_MAX).strip()
        description = _line(entry.get("description"), DESCRIPTION_MAX).strip()
        if not path or not name or not description or path in seen:
            skipped += 1
            continue
        seen.add(path)
        listed.append(Listed(path, name, description, _line(entry.get("category"), CATEGORY_MAX).strip()))
    return listed, skipped


class _NoAliases(yaml.SafeLoader):
    """safe_load that refuses anchors and aliases, which a small text can use to expand without bound."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.YAMLError("alias")
        return super().compose_node(parent, index)


def frontmatter(text: str) -> dict[str, str]:
    """`name`, `description` and `license` from a SKILL.md's frontmatter, when plain strings; else `{}`."""
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return {}
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        return {}
    try:
        meta = yaml.load("\n".join(lines[1:end]), Loader=_NoAliases)  # noqa: S506 (a SafeLoader subclass)
    except (yaml.YAMLError, RecursionError):
        return {}
    if not isinstance(meta, dict):
        return {}
    out = {}
    for key, limit in (("name", NAME_MAX), ("description", DESCRIPTION_MAX), ("license", LICENCE_MAX)):
        value = meta.get(key)
        if isinstance(value, str):
            value = _line(value, limit).strip()
            if value:
                out[key] = value
    return out


def extras(blobs: list[tuple[str, int]], folder: str) -> bool:
    """True when the skill folder holds a file other than SKILL.md, LICENSE* and references/*."""
    prefix = folder + "/"
    for path, _size in blobs:
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        if rest == SKILL_FILE or rest.startswith("LICENSE") or rest.startswith("references/"):
            continue
        return True
    return False

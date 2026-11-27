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


def _parents(path: str):
    """Each folder above `path`, nearest first."""
    d = path.rpartition("/")[0]
    while d:
        yield d
        d = d.rpartition("/")[0]


def skill_folders(blobs: list[tuple[str, int]], folder: str) -> list[str]:
    """Each directory holding a SKILL.md inside `folder`; a folder inside another skill folder is dropped."""
    dirs = set()
    for path, _size in blobs:
        d, _, base = path.rpartition("/")
        if base == SKILL_FILE and d and (d == folder or _inside(d, folder)) and _plain(d):
            dirs.add(d)
    return sorted(d for d in dirs if not any(a in dirs for a in _parents(d)))


def find_index(blobs: list[tuple[str, int]], folder: str, named: str) -> str | None:
    """The index to read: the one the owner named, else the shortest in or above `folder`. Only a path whose every
    segment is plain is taken, so nothing here can name a file the client would refuse."""
    paths = {p for p, _ in blobs if _plain(p)}
    if named and named in paths:
        return named
    found = []
    for p in paths:
        d, _, base = p.rpartition("/")
        if base in INDEX_NAMES and (d == "" or d == folder or _inside(d, folder) or _inside(folder, d)):
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


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """(the frontmatter's text, the body after it) when `text` opens with a `---` block, else None."""
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        return None
    return "\n".join(lines[1:end]), "\n".join(lines[end + 1:])


def frontmatter(text: str) -> dict[str, str]:
    """`name`, `description` and `license` from a SKILL.md's frontmatter, when plain strings; else `{}`."""
    parts = split_frontmatter(text)
    if parts is None:
        return {}
    try:
        meta = yaml.load(parts[0], Loader=_NoAliases)  # noqa: S506 (a SafeLoader subclass)
    except (yaml.YAMLError, RecursionError):
        return {}
    if not isinstance(meta, dict):
        return {}
    out = {}
    for key, limit in (("name", NAME_MAX), ("description", DESCRIPTION_MAX), ("license", LICENCE_MAX)):
        value = meta.get(key)
        if isinstance(value, str):
            if key == "license":  # checked whole: a cut could leave only an accepted id ("MIT", then the rest)
                value = _line(value, len(value))
                if len(value) > limit:
                    value = value[:limit - 1] + "…"  # no accepted id ends so: an overlong licence is refused
            else:
                value = _line(value, limit).strip()
            if value:
                out[key] = value
    return out


LICENCE_PREFIXES = ("license", "copying", "notice")


def _extra(rest: str) -> bool:
    """Whether a file inside a skill folder (its path below the folder) is more than the skill, its licence files and
    its references."""
    first = rest.split("/", 1)[0].casefold()
    return not (rest == SKILL_FILE or first.startswith(LICENCE_PREFIXES) or first == "references")


def extras_by_folder(blobs: list[tuple[str, int]], folders: set[str]) -> dict[str, bool]:
    """For each skill folder, whether it holds a file other than SKILL.md, LICENSE*, COPYING*, NOTICE* and
    references/*: every blob is looked at once, by the skill folder above it."""
    out = {f: False for f in folders}
    for path, _size in blobs:
        for d in _parents(path):
            if d in out:
                if not out[d] and _extra(path[len(d) + 1:]):
                    out[d] = True
                break
    return out


def extras(blobs: list[tuple[str, int]], folder: str) -> bool:
    return extras_by_folder(blobs, {folder})[folder]

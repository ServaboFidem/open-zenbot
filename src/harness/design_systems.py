"""Design systems: a folder with `DESIGN.md` (and optionally `tokens.css`) that a conversation can pick; the harness
adds it to the context of every task in that conversation. Two neutral starters ship in the repo; the user's own live
in `<vault>/design-systems/`, which bots can write, so everything read from there is untrusted text for a prompt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_CHARS = 40_000  # DESIGN.md and tokens.css together
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
DESIGN_FILE, TOKENS_FILE = "DESIGN.md", "tokens.css"
SUMMARY_CHARS = 140


class DesignSystemError(ValueError):
    pass


@dataclass
class DesignSystem:
    slug: str
    name: str
    source: str  # "shipped" or "yours"
    summary: str
    design: str
    tokens: str


def _roots(shipped_dir: Path, vault_root: Path) -> list[tuple[str, Path]]:
    return [("shipped", Path(shipped_dir)), ("yours", Path(vault_root) / "design-systems")]


def _inside(child: Path, parent: Path) -> bool:
    """`child`, resolved, sits directly inside `parent`, resolved."""
    try:
        return child.resolve().parent == parent.resolve()
    except (OSError, RuntimeError):
        return False


def _read(folder: Path, name: str, *, required: bool) -> str:
    path = folder / name
    if not path.exists() and not path.is_symlink():
        if required:
            raise DesignSystemError(f"{folder.name} has no {name}")
        return ""
    if not _inside(path, folder) or not path.resolve().is_file():
        raise DesignSystemError(f"{name} of {folder.name} resolves outside its own folder or is not a file")
    try:
        return path.resolve().read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise DesignSystemError(f"{name} of {folder.name} is not valid UTF-8") from None
    except OSError as e:
        raise DesignSystemError(f"{name} of {folder.name} cannot be read: {e.strerror or e}") from None


def _find(root: Path, slug: str) -> Path | None:
    """The folder for `slug` inside `root`, or None when there is no folder there."""
    folder = root / slug
    return folder if folder.is_dir() else None


def _name_of(slug: str, design: str) -> str:
    for line in design.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return slug


def _summary_of(design: str) -> str:
    for line in design.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s[:SUMMARY_CHARS]
    return ""


def _open(root: Path, source: str, slug: str) -> DesignSystem:
    folder = _find(root, slug)
    if folder is None:
        raise KeyError(slug)
    if not _inside(folder, root):
        raise DesignSystemError(f"{slug} resolves outside its own folder")
    folder = folder.resolve()
    design = _read(folder, DESIGN_FILE, required=True)
    tokens = _read(folder, TOKENS_FILE, required=False)
    return DesignSystem(slug, _name_of(slug, design), source, _summary_of(design), design, tokens)


def available(shipped_dir: Path, vault_root: Path) -> list[dict[str, str]]:
    """Every design system the user can pick, sorted by name. Theirs replace a shipped one of the same slug.
    One that is unsafe or unreadable is left out; one that is too large is listed (picking it says why)."""
    found: dict[str, DesignSystem] = {}
    for source, root in _roots(shipped_dir, vault_root):
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not SLUG.fullmatch(entry.name):
                continue
            try:
                found[entry.name] = _open(root, source, entry.name)
            except (DesignSystemError, KeyError, OSError):
                found.pop(entry.name, None)  # a broken folder of yours still replaces the shipped one, as `load` does
    rows = [{"slug": d.slug, "name": d.name, "source": d.source, "summary": d.summary} for d in found.values()]
    return sorted(rows, key=lambda r: (r["name"].lower(), r["slug"]))


def load(shipped_dir: Path, vault_root: Path, slug: str) -> DesignSystem:
    """KeyError when there is none; DesignSystemError with the reason when it is too large or unsafe."""
    if not SLUG.fullmatch(slug):
        raise KeyError(slug)
    for source, root in reversed(_roots(shipped_dir, vault_root)):  # yours first
        if not root.is_dir():
            continue
        try:
            ds = _open(root, source, slug)
        except KeyError:
            continue
        size = len(ds.design) + len(ds.tokens)
        if size > MAX_CHARS:
            raise DesignSystemError(f"{ds.name} is {size:,} characters; the limit is {MAX_CHARS:,}")
        return ds
    raise KeyError(slug)


def block(ds: DesignSystem) -> str:
    parts = [f"Design system: {ds.name}. This is authoritative: use these tokens; do not invent colours, type or spacing.",
             ds.design.strip()]
    if ds.tokens.strip():
        fence = "```"
        while any(line.startswith(fence) for line in ds.tokens.splitlines()):
            fence += "`"  # a longer fence than any line in the tokens, so they cannot close it early
        parts.append(f"{fence}css\n{ds.tokens.strip()}\n{fence}")
    return "\n\n".join(parts)

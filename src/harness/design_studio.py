"""Design studio: each attached design is one fixed revision, a numbered copy kept beside the file."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from uuid import uuid4

from src.harness import design_images

_SUFFIXES = {".html", ".htm"}
_COPY = re.compile(r"^(\d+)\.(.+\.html?)$", re.IGNORECASE)  # `<n>.<the design file's own name>`


class SnapshotRefused(Exception):
    """A design whose fixed copy cannot be kept. The message is for the bot."""


def _designs(vault_root: Path) -> Path:
    return vault_root.resolve() / "designs"


_SLUG_MAX = 40
_FILLER = {"a", "an", "the", "i", "im", "id", "we", "my", "our", "me", "to", "of", "for", "and", "or", "is", "it", "that", "this",
           "want", "need", "would", "like", "please", "can", "could", "you", "help", "design", "create", "make", "build",
           "new", "going", "thinking", "with", "in", "on"}


def slug_of(text: str) -> str:
    """A short kebab-case folder name from a brief's first line: its telling words, at most 40 characters."""
    line = (text.strip().splitlines() or [""])[0].lower().replace("'", "").replace("\u2019", "")
    words = [w for w in re.findall(r"[a-z0-9]+", line)]
    kept = [w for w in words if w not in _FILLER] or words
    slug = ""
    for w in kept:
        if len(slug) + len(w) + 1 > _SLUG_MAX:
            break
        slug = f"{slug}-{w}" if slug else w
    return slug or (kept[0][:_SLUG_MAX] if kept else "design")


def fresh_folder(vault_root: Path, brief: str) -> str:
    """Make a new, empty `<vault>/designs/<slug>/` for one conversation's design and return the slug. A name
    already there (another conversation's design, or anything else) is never reused: the next free
    `<slug>-2`, `<slug>-3`... is made instead, so a new design cannot write over an old one. Raises
    SnapshotRefused when `designs` is a link or not a folder, and OSError when the folder cannot be made."""
    designs = vault_root / "designs"
    if designs.is_symlink() or (designs.exists() and not designs.is_dir()):
        raise SnapshotRefused("designs is a link or not a folder")
    designs.mkdir(exist_ok=True)
    base = slug_of(brief)
    for n in range(1, 1000):
        slug = base if n == 1 else f"{base}-{n}"
        try:
            (designs / slug).mkdir()  # exclusive: fails on anything already at the name, a link included
            return slug
        except FileExistsError:
            continue
    raise SnapshotRefused(f"no free folder name for designs/{base}")


def folder_block(slug: str) -> str:
    """What a studio bot reads about where this conversation's design lives."""
    return (f"Design folder for this conversation: designs/{slug}/ (it already exists; do not create it).\n"
            f"Write the design to designs/{slug}/index.html and attach that path. Use no other folder under designs/: "
            "the others hold other conversations' designs.")


def _fixed(p: Path) -> bool:
    """A regular file under one name only: a link (symbolic or hard) to the live design would change with it."""
    try:
        st = p.lstat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1


def snapshot(vault_root: Path, target: Path) -> Path | None:
    """Copy `<vault>/designs/<slug>/<name>` to `.versions/<n>.<name>` (the file's own name, extension and
    case kept) and return the copy. The newest copy with the same bytes is reused, unless it is a link.
    Any other path (compared resolved, so `..` and symlinks cannot pass a file from elsewhere off as a
    design) gives None. A design whose copy cannot be kept raises SnapshotRefused: the caller must not
    attach the live file in its place, which a later edit would change."""
    try:
        real = target.resolve(strict=True)
        parts = real.relative_to(_designs(vault_root)).parts
    except (OSError, ValueError):
        return None
    if len(parts) != 2 or parts[0].startswith(".") or real.suffix.lower() not in _SUFFIXES or not real.is_file():
        return None
    # a symlinked .versions would send the copy elsewhere; refuse before any read, mkdir or write
    vdir = _versions(real, parts[0])
    name = real.name
    have = _copies(vdir, name)
    data = design_images.embed(real.read_bytes(), real.parent)[0]  # the copy is fixed: its pictures go inside it
    n = 0
    if have:
        n, newest = max(have)  # a number in use stays reserved, whatever sits there
        if _fixed(newest) and newest.read_bytes() == data:
            return newest
    vdir.mkdir(exist_ok=True)
    copy = vdir / f"{n + 1}.{name}"
    try:
        with open(copy, "xb") as out:  # exclusive: never truncates or follows what is already at the name
            out.write(data)
    except FileExistsError:
        raise SnapshotRefused(f"designs/{parts[0]}/.versions/{copy.name} is already there; attach again") from None
    return copy


def _copies(vdir: Path, name: str) -> list[tuple[int, Path]]:
    have: list[tuple[int, Path]] = []
    if vdir.is_dir():
        for p in vdir.iterdir():
            m = _COPY.match(p.name)
            if m and m.group(2) == name:
                have.append((int(m.group(1)), p))
    return have


def _versions(real: Path, slug: str) -> Path:
    """The design's `.versions` folder, refused when it is a link or not a folder (as `snapshot` refuses it)."""
    vdir = real.parent / ".versions"
    if vdir.is_symlink() or (vdir.exists() and vdir.resolve() != vdir):
        raise SnapshotRefused(f"designs/{slug}/.versions is a link or not a folder; remove it and attach again")
    return vdir


def next_number(vault_root: Path, live: Path) -> int:
    """The number the next new numbered copy of this design file takes. Raises SnapshotRefused for a path
    that is not a design or a linked `.versions`."""
    real = _live(vault_root, live)
    if real is None:
        raise SnapshotRefused("not a design file")
    have = _copies(_versions(real, real.parent.name), real.name)
    return max(have)[0] + 1 if have else 1


def keep_before(vault_root: Path, live: Path, n: int, source: bytes) -> Path:
    """Keep the design file's source as it was before a helper's change: `.versions/before-<n>.<name>`,
    where `<n>` is the number of the copy the change is about to make. Never overwrites or follows what is
    at the name. A kept source left with no copy `<n>` (the copy was never made) is stale and is replaced:
    written beside it, then renamed over it, which replaces a link rather than writing through it. Raises
    SnapshotRefused (the message names no disk path) or OSError."""
    real = _live(vault_root, live)
    if real is None:
        raise SnapshotRefused("not a design file")
    vdir = _versions(real, real.parent.name)
    vdir.mkdir(exist_ok=True)
    kept = vdir / f"before-{n}.{real.name}"
    try:
        with open(kept, "xb") as out:  # exclusive, as the numbered copies are
            out.write(source)
        return kept
    except FileExistsError:
        if any(k == n for k, _ in _copies(vdir, real.name)):
            raise SnapshotRefused(f"designs/{real.parent.name}/.versions/{kept.name} is already there") from None
    tmp = vdir / f".{kept.name}.{uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "xb") as out:
            out.write(source)
        os.replace(tmp, kept)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return kept


def before_of(vault_root: Path, copy: Path) -> Path | None:
    """The source kept before the helper's change that made this numbered copy, when there is one."""
    rev = revision_of(vault_root, copy)
    if rev is None:
        return None
    vdir = _designs(vault_root) / rev[0] / ".versions"
    if vdir.is_symlink() or not vdir.is_dir():
        return None
    kept = vdir / f"before-{rev[1]}.{rev[2]}"
    return kept if _fixed(kept) else None


def _live(vault_root: Path, live: Path) -> Path | None:
    """The design file `live` names, resolved, when it is one `snapshot` would copy; else None."""
    try:
        real = live.resolve(strict=True)
        parts = real.relative_to(_designs(vault_root)).parts
    except (OSError, ValueError):
        return None
    if len(parts) != 2 or parts[0].startswith(".") or real.suffix.lower() not in _SUFFIXES or not real.is_file():
        return None
    return real


def is_current(vault_root: Path, live: Path, copy: Path) -> bool:
    """True when the copy holds exactly what `snapshot` would write for the live file now (its pictures
    embedded). False when either cannot be read."""
    real = _live(vault_root, live)
    if real is None:
        return False
    try:
        return design_images.embed(real.read_bytes(), real.parent)[0] == copy.read_bytes()
    except OSError:
        return False


def embed_notes(vault_root: Path, live: Path) -> list[str]:
    """What a bot should fix before the next attach: `images/x.png: <reason>` for each picture left as
    written, and one line when the copy with its pictures is over BRIDGE_MAX_BYTES (it could not be commented on)."""
    real = _live(vault_root, live)
    if real is None:
        return []
    try:
        data, left = design_images.embed(real.read_bytes(), real.parent)
    except OSError:
        return []
    notes = [f"{src}: {why}" for src, why in left]
    if len(data) > BRIDGE_MAX_BYTES:
        notes.append("the design is too large to comment on (over 5 MB with its pictures)")
    return notes


def revision_of(vault_root: Path, path: Path) -> tuple[str, int, str] | None:
    """`(slug, n, "<the design file's name>")` for a path that is a numbered copy, else None."""
    try:
        parts = path.resolve().relative_to(_designs(vault_root)).parts
    except (OSError, ValueError):
        return None
    if len(parts) != 3 or parts[1] != ".versions" or parts[0].startswith("."):
        return None
    m = _COPY.match(parts[2])
    return (parts[0], int(m.group(1)), m.group(2)) if m else None


def _flat(s: str) -> str:
    # comment text reaches a prompt: no newline may start a forged numbered line or the closing rule
    return " ".join(s.split())


def comment_block(file: str, version: int, comments: list[dict[str, str]]) -> str:
    """The text a bot reads for the user's comments on one design revision. `file` is vault-relative with
    forward slashes: the block reaches the ledger and the session API, so it carries no path on disk."""
    lines = [f"Comments on the design ({file}, version {version}):"]
    for i, c in enumerate(comments, 1):
        text = _flat(c.get("text", ""))
        lines.append(f"{i}. [{_flat(c.get('element_id', '')) or _flat(c['selector'])}] <{_flat(c['tag'])}>"
                     + (f' "{text}"' if text else "") + f": {_flat(c['note'])}")
    lines.append("Change only these elements. Leave the rest of the file as it is unless a change requires it, "
                 "and say so if it does.")
    return "\n".join(lines)


# the studio page is framed with the preview policy; the app picks elements through this one script
BRIDGE_MAX_BYTES = 5 * 1024 * 1024
_BRIDGE = (b"<script data-zen-bridge>" + (Path(__file__).with_name("design_bridge.js")).read_bytes().strip()
           + b"</script>")
_BODY_CLOSE = re.compile(rb"</body\s*>", re.IGNORECASE)


def with_bridge(html: bytes) -> bytes:
    """Insert the bridge before the last `</body>` (appended when there is none), on the raw bytes."""
    last = None
    for last in _BODY_CLOSE.finditer(html):
        pass
    at = last.start() if last else len(html)
    return html[:at] + _BRIDGE + html[at:]

"""Design studio: each attached design is one fixed revision, a numbered copy kept beside the file."""
from __future__ import annotations

import re
import stat
from pathlib import Path

_SUFFIXES = {".html", ".htm"}
_COPY = re.compile(r"^(\d+)\.(.+\.html?)$", re.IGNORECASE)  # `<n>.<the design file's own name>`


class SnapshotRefused(Exception):
    """A design whose fixed copy cannot be kept. The message is for the bot."""


def _designs(vault_root: Path) -> Path:
    return vault_root.resolve() / "designs"


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
    vdir = real.parent / ".versions"
    # a symlinked .versions would send the copy elsewhere; refuse before any read, mkdir or write
    if vdir.is_symlink() or (vdir.exists() and vdir.resolve() != vdir):
        raise SnapshotRefused(f"designs/{parts[0]}/.versions is a link or not a folder; remove it and attach again")
    name = real.name
    have: list[tuple[int, Path]] = []
    if vdir.is_dir():
        for p in vdir.iterdir():
            m = _COPY.match(p.name)
            if m and m.group(2) == name:
                have.append((int(m.group(1)), p))
    data = real.read_bytes()
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

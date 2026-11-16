"""Design studio: each attached design is one fixed revision, a numbered copy kept beside the file."""
from __future__ import annotations

import re
from pathlib import Path

_SUFFIXES = {".html", ".htm"}
_COPY = re.compile(r"^(\d+)\.(.+\.html)$")


def _designs(vault_root: Path) -> Path:
    return vault_root.resolve() / "designs"


def snapshot(vault_root: Path, target: Path) -> Path | None:
    """Copy `<vault>/designs/<slug>/<stem>.html` to `.versions/<n>.<stem>.html` and return the copy.
    The newest copy with the same bytes is reused. Any other path (compared resolved, so `..` and
    symlinks cannot pass a file from elsewhere off as a design) gives None."""
    try:
        real = target.resolve(strict=True)
        parts = real.relative_to(_designs(vault_root)).parts
    except (OSError, ValueError):
        return None
    if len(parts) != 2 or parts[0].startswith(".") or real.suffix.lower() not in _SUFFIXES or not real.is_file():
        return None
    vdir = real.parent / ".versions"
    name = f"{real.stem}.html"
    have: list[tuple[int, Path]] = []
    if vdir.is_dir():
        for p in vdir.iterdir():
            m = _COPY.match(p.name)
            if m and m.group(2) == name:
                have.append((int(m.group(1)), p))
    data = real.read_bytes()
    if have:
        n, newest = max(have)
        if newest.read_bytes() == data:
            return newest
    else:
        n = 0
    vdir.mkdir(exist_ok=True)
    copy = vdir / f"{n + 1}.{name}"
    copy.write_bytes(data)
    return copy


def revision_of(vault_root: Path, path: Path) -> tuple[str, int, str] | None:
    """`(slug, n, "<stem>.html")` for a path that is a numbered copy, else None."""
    try:
        parts = path.resolve().relative_to(_designs(vault_root)).parts
    except (OSError, ValueError):
        return None
    if len(parts) != 3 or parts[1] != ".versions" or parts[0].startswith("."):
        return None
    m = _COPY.match(parts[2])
    return (parts[0], int(m.group(1)), m.group(2)) if m else None

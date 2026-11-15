"""Text normalisation for matching artist and title strings across services."""

from __future__ import annotations

import re
import unicodedata

_TRAILING_NUM = re.compile(r"\s*\(\d+\)\s*$")
_TRAILING_THE = re.compile(r"^(.*?),\s*the\s*$", re.IGNORECASE)


def norm(s: str) -> str:
    """Lowercase ASCII-folded text with ``&`` as ``and`` and punctuation as single spaces.

    Accents fold to their base letter; characters with no ASCII form (kana, Cyrillic...)
    are kept as they are.
    """
    out = []
    for ch in s.replace("&", " and "):
        if ch.isascii():
            out.append(ch)
            continue
        base = unicodedata.normalize("NFKD", ch)
        ascii_base = "".join(c for c in base if c.isascii())
        if ascii_base:
            out.append(ascii_base)
        elif ch.isalnum():
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(" ")
    text = "".join(out).lower()
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def display_name(s: str) -> str:
    """An artist name to show and to search with: Discogs' ``(3)`` suffix dropped, ``Beatles, The`` as ``The Beatles``."""
    s = _TRAILING_NUM.sub("", s).strip()
    m = _TRAILING_THE.match(s)
    return f"The {m.group(1).strip()}" if m else s


def artist_name(a: object) -> str:
    """A stored release's artist as a name: the scan keeps ``{"id", "name"}`` objects, older rows plain strings."""
    if isinstance(a, dict):
        return str(a.get("name") or "")
    return a if isinstance(a, str) else ""


def artist_key(s: str) -> str:
    """Matching key for an artist name: Discogs ``(2)`` suffix and ``, The`` suffix handled."""
    s = _TRAILING_NUM.sub("", s)
    m = _TRAILING_THE.match(s)
    if m:
        s = "The " + m.group(1)
    return norm(s)


def titles_match(found: str, wanted: str) -> bool:
    """The same album title, allowing one to extend the other ("Album" and "Album (Deluxe Edition)")."""
    a, b = norm(found), norm(wanted)
    return bool(a) and bool(b) and (a == b or a.startswith(b) or b.startswith(a))

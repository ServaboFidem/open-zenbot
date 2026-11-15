"""YouTube Music lookups (unauthenticated ``ytmusicapi``).

Every method returns ``None`` / ``[]`` on any failure, including a broken install:
``ytmusicapi`` is imported only when the default factory runs. Calls are sync; callers
use ``asyncio.to_thread``.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from src.music.textnorm import artist_key, titles_match

_SUFFIX = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_PLAYS = re.compile(r"^\s*([\d.,]+)\s*([KMB])?\b", re.I)


def parse_plays(text) -> int | None:
    """``"2.4M"`` -> 2400000, ``"1,234"`` -> 1234, anything else -> None."""
    if not isinstance(text, str):
        return None
    m = _PLAYS.match(text)
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return round(n * _SUFFIX.get((m.group(2) or "").upper(), 1))


def _same_album(hit: dict, artist: str, title: str) -> bool:
    """A search hit is this album only when one of its artists and its title match: search ranks loosely."""
    names = [a.get("name", "") for a in hit.get("artists") or [] if isinstance(a, dict)]
    if isinstance(hit.get("artist"), str):
        names.append(hit["artist"])
    keys = {artist_key(n) for n in names if n} | ({artist_key(" ".join(names))} if names else set())
    return artist_key(artist) in keys and titles_match(str(hit.get("title") or ""), title)


def _default_factory():
    from ytmusicapi import YTMusic
    return YTMusic()


class YTMusicLookup:
    def __init__(self, factory=_default_factory):
        self._factory = factory
        self._yt = None

    def _api(self):
        if self._yt is None:
            self._yt = self._factory()
        return self._yt

    def search_url(self, artist: str, title: str) -> str:
        return f"https://music.youtube.com/search?q={quote(f'{artist} {title}')}"

    def album(self, artist: str, title: str) -> dict | None:
        try:
            yt = self._api()
            hits = yt.search(f"{artist} {title}", filter="albums", limit=5)
            browse_id = next((h["browseId"] for h in hits if h.get("browseId") and _same_album(h, artist, title)), None)
            if not browse_id:
                return None
            tracks = yt.get_album(browse_id).get("tracks") or []
            return {"browse_id": browse_id,
                    "url": f"https://music.youtube.com/browse/{browse_id}",
                    "tracks": [{"title": t.get("title", ""), "plays": parse_plays(t.get("views"))}
                               for t in tracks]}
        except Exception:
            return None

    def related_artists(self, artist: str) -> list[str]:
        try:
            yt = self._api()
            hits = yt.search(artist, filter="artists", limit=1)
            channel = next((h["browseId"] for h in hits if h.get("browseId")), None)
            if not channel:
                return []
            related = (yt.get_artist(channel).get("related") or {}).get("results") or []
            return [r["title"] for r in related if r.get("title")]
        except Exception:
            return []

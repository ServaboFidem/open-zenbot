"""MusicBrainz client: Discogs artist id to MBID, and an artist's release groups.

MusicBrainz asks for at most one request a second. Failures are soft (``None`` / ``[]``):
MusicBrainz only enriches, so a miss must not stop a scan or a digest.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from .discogs import USER_AGENT

API = "https://musicbrainz.org/ws/2"
MIN_GAP = 1.0


class MusicBrainz:
    def __init__(self, client: httpx.AsyncClient | None = None, sleep=asyncio.sleep,
                 clock=time.monotonic):
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict) -> dict | None:
        if self._last is not None:
            wait = MIN_GAP - (self._clock() - self._last)
            if wait > 0:
                await self._sleep(wait)
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        try:
            resp = await self._client.get(f"{API}{path}", params={**params, "fmt": "json"},
                                          headers={"User-Agent": USER_AGENT})
            self._last = self._clock()
            if resp.status_code != 200:
                return None
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            self._last = self._clock()
            return None
        return data if isinstance(data, dict) else None

    async def artist_by_discogs(self, discogs_artist_id: int) -> str | None:
        data = await self._get("/url", {"resource": f"https://www.discogs.com/artist/{discogs_artist_id}",
                                        "inc": "artist-rels"})
        for rel in (data or {}).get("relations", []):
            mbid = (rel.get("artist") or {}).get("id")
            if mbid:
                return str(mbid)
        return None

    async def release_groups(self, mbid: str) -> list[dict]:
        data = await self._get("/release-group", {"artist": mbid, "type": "album", "limit": 100})
        return [{"title": rg.get("title", ""),
                 "first_release_date": rg.get("first-release-date", ""),
                 "primary_type": rg.get("primary-type", "")}
                for rg in (data or {}).get("release-groups", [])]

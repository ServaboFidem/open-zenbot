"""Discogs client: collection, search, lookups and the wantlist.

Discogs allows 25 requests a minute without a token and 60 with one. The client keeps a
sliding window of its own requests, waits out a reported ``X-Discogs-Ratelimit-Remaining``
of 0, and backs off on 429. Failures raise ``DiscogsError`` with a ``kind`` the callers
branch on.
"""

from __future__ import annotations

import asyncio
import time
import tomllib
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

API = "https://api.discogs.com"
WINDOW = 60.0
BACKOFF = (2, 4, 8)
PER_PAGE = 100


def _version() -> str:
    try:
        data = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text("utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, ValueError):
        return "0.0.1"


USER_AGENT = f"zen-agents/{_version()} +https://github.com/"


class DiscogsError(Exception):
    """kind: not_found, private, token_invalid, rate_limited or network."""

    def __init__(self, kind: str, message: str = ""):
        super().__init__(message or kind)
        self.kind = kind


_STATUS_KINDS = {401: "token_invalid", 403: "private", 404: "not_found"}


class Discogs:
    def __init__(self, username: str, token: str = "", client: httpx.AsyncClient | None = None,
                 clock=time.monotonic, sleep=asyncio.sleep):
        self.username = username
        self.token = token
        self._client = client
        self._owns_client = client is None
        self._clock = clock
        self._sleep = sleep
        self._limit = 60 if token else 25
        self._sent: deque[float] = deque()
        self._resume_at = 0.0

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    def _need_token(self) -> None:
        if not self.token:
            raise DiscogsError("token_invalid", "this call needs a Discogs token")

    async def _throttle(self) -> None:
        now = self._clock()
        if self._resume_at > now:
            await self._sleep(self._resume_at - now)
            self._resume_at = 0.0
            now = self._clock()
        while self._sent and self._sent[0] <= now - WINDOW:
            self._sent.popleft()
        if len(self._sent) >= self._limit:
            await self._sleep(max(self._sent[0] + WINDOW - now, 0.0))
            self._sent.popleft()
        self._sent.append(self._clock())

    async def _request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        headers = {"User-Agent": USER_AGENT}
        if self.token:
            headers["Authorization"] = f"Discogs token={self.token}"
        for attempt in range(len(BACKOFF) + 1):
            await self._throttle()
            try:
                resp = await self._http().request(method, API + path, params=params, headers=headers)
            except httpx.HTTPError as e:
                raise DiscogsError("network", str(e)) from e
            if resp.headers.get("X-Discogs-Ratelimit-Remaining") == "0":
                self._resume_at = self._clock() + WINDOW
            if resp.status_code == 429:
                if attempt == len(BACKOFF):
                    raise DiscogsError("rate_limited", "Discogs rate limit")
                await self._sleep(BACKOFF[attempt])
                continue
            if resp.status_code in _STATUS_KINDS:
                raise DiscogsError(_STATUS_KINDS[resp.status_code], f"{method} {path}: {resp.status_code}")
            if resp.status_code >= 400:
                raise DiscogsError("network", f"{method} {path}: {resp.status_code}")
            return resp
        raise DiscogsError("rate_limited", "Discogs rate limit")  # unreachable

    async def _json(self, path: str, params: dict | None = None) -> dict:
        try:
            return self._as_dict((await self._request("GET", path, params)).json())
        except ValueError as e:
            raise DiscogsError("network", f"{path}: bad JSON") from e

    @staticmethod
    def _as_dict(data) -> dict:
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return data

    async def collection_pages(self) -> AsyncIterator[tuple[int, int, list[dict]]]:
        """Yield ``(page, pages, releases)`` for the whole collection (folder 0)."""
        page = 1
        while True:
            data = await self._json(f"/users/{self.username}/collection/folders/0/releases",
                                    {"page": page, "per_page": PER_PAGE})
            pages = int(data.get("pagination", {}).get("pages", 1) or 1)
            yield page, pages, list(data.get("releases", []))
            if page >= pages:
                return
            page += 1

    async def collection_size(self) -> int:
        """How many releases the collection holds; raises ``not_found`` / ``private`` for a bad or closed one."""
        data = await self._json(f"/users/{self.username}/collection/folders/0/releases", {"per_page": 1})
        return int(data.get("pagination", {}).get("items", 0) or 0)

    async def identity(self) -> str:
        self._need_token()
        return str((await self._json("/oauth/identity")).get("username", ""))

    async def search_masters(self, artist: str, title: str) -> list[dict]:
        self._need_token()
        data = await self._json("/database/search",
                                {"type": "master", "artist": artist, "release_title": title})
        return list(data.get("results", []))

    async def search_artists(self, name: str) -> list[dict]:
        self._need_token()
        data = await self._json("/database/search", {"type": "artist", "q": name})
        return list(data.get("results", []))

    async def artist_masters(self, discogs_artist_id: int, newest: bool = False) -> list[dict]:
        """One page of an artist's masters, oldest first (newest first with ``newest``).

        Rows are ``id, title, year, role`` and ``have`` when listed.
        """
        data = await self._json(f"/artists/{discogs_artist_id}/releases",
                                {"sort": "year", "sort_order": "desc" if newest else "asc",
                                 "per_page": PER_PAGE})
        out = []
        for r in data.get("releases", []):
            if r.get("type") != "master" or not r.get("id"):
                continue
            row = {"id": int(r["id"]), "title": r.get("title", ""), "year": int(r.get("year") or 0),
                   "role": r.get("role", "")}
            stats = r.get("stats") or {}
            have = (stats.get("community") or {}).get("in_collection", stats.get("have"))
            if have is not None:
                row["have"] = int(have)
            out.append(row)
        return out

    async def master(self, id: int) -> dict:
        return await self._json(f"/masters/{id}")

    async def release(self, id: int) -> dict:
        return await self._json(f"/releases/{id}")

    async def wantlist_ids(self) -> set[int]:
        """Release IDs and, where the entry has one, master IDs of every wantlist entry."""
        self._need_token()
        ids: set[int] = set()
        page = 1
        while True:
            data = await self._json(f"/users/{self.username}/wants", {"page": page, "per_page": PER_PAGE})
            for want in data.get("wants", []):
                info = want.get("basic_information") or {}
                for key in (want.get("id"), info.get("id"), info.get("master_id")):
                    if key:
                        ids.add(int(key))
            if page >= int(data.get("pagination", {}).get("pages", 1) or 1):
                return ids
            page += 1

    async def want(self, release_id: int) -> None:
        self._need_token()
        await self._request("PUT", f"/users/{self.username}/wants/{release_id}")

    async def unwant(self, release_id: int) -> None:
        self._need_token()
        await self._request("DELETE", f"/users/{self.username}/wants/{release_id}")

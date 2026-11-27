"""The GitHub client: reads public repositories through GitHub's API and raw file host.

It knows two hosts and nothing else. Every request address is built here from parts checked against
`repos.OWNER`, `repos.REPO` and `repos.SEGMENT` (a sha must be 40 lowercase hex characters); no address is ever
taken from an answer.

- `https://api.github.com/repos/<owner>/<repo>`, `/commits/<ref>` and `/git/trees/<sha>[?recursive=1]`;
- `https://raw.githubusercontent.com/<owner>/<repo>/<sha>/<path>`;
- one request at a time, `GAP_S` apart, the whole request bounded by `TIMEOUT_S`, no redirect followed (any 3xx is
  `source_changed`), no compression, every answer capped.

Errors are `SourceError(kind)` with `status` set to the HTTP status (0 when there was none). Kinds: `network`,
`refused`, `source_changed`, `not_found`, `provider`, `budget`, `too_large`.
Nothing here logs; an exception's text is never kept."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from src.events.eventbrite import SourceError, _body
from src.skilltuner.repos import OWNER, REPO, SEGMENT

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
GAP_S = 1.0
TIMEOUT_S = 30.0
API_LIMIT = 1_000_000
TREE_LIMIT = 20_000_000
SHA = re.compile(r"[0-9a-f]{40}")
API_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "OpenZenBot", "Accept-Encoding": "identity"}
RAW_HEADERS = {"User-Agent": "OpenZenBot", "Accept-Encoding": "identity"}


@dataclass
class RepoInfo:
    default_branch: str
    licence: str


@dataclass
class Tree:
    blobs: list[tuple[str, int]]  # (path, size)
    trees: list[tuple[str, str]]  # (path, sha)
    truncated: bool


def new_client() -> httpx.AsyncClient:
    """The client's own: no proxy or certificate setting from the environment, no redirect followed."""
    return httpx.AsyncClient(trust_env=False, follow_redirects=False)


def _fail(kind: str, status: int = 0) -> SourceError:
    error = SourceError(kind)
    error.status = status  # type: ignore[attr-defined]
    return error


def _owner_repo(owner: str, repo: str) -> None:
    if (not isinstance(owner, str) or re.fullmatch(OWNER, owner) is None or not isinstance(repo, str)
            or re.fullmatch(REPO, repo) is None or repo in (".", "..")):
        raise ValueError("not a repository")


def _segment(s: object) -> bool:
    return isinstance(s, str) and re.fullmatch(SEGMENT, s) is not None and s not in (".", "..")


def _sha(s: object) -> bool:
    return isinstance(s, str) and SHA.fullmatch(s) is not None


class GitHub:
    def __init__(self, http: Callable[[], httpx.AsyncClient],
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 on_send: Callable[[str], bool] = lambda kind: True, timeout_s: float = TIMEOUT_S):
        self._http = http
        self._sleep = sleep
        self._on_send = on_send
        self._timeout_s = timeout_s
        self._lock = asyncio.Lock()
        self._sent = 0

    async def _get(self, url: str, kind: str, limit: int) -> bytes:
        headers = API_HEADERS if kind == "api" else RAW_HEADERS
        async with self._lock:
            if not self._on_send(kind):
                raise _fail("budget")
            if self._sent:
                await self._sleep(GAP_S)
            self._sent += 1
            try:
                async with asyncio.timeout(self._timeout_s):  # the whole request, send and body
                    async with self._http().stream("GET", url, headers=headers, follow_redirects=False,
                                                   timeout=TIMEOUT_S) as response:
                        status = response.status_code
                        if status == 404:
                            raise _fail("not_found", status)
                        if status in (403, 429):
                            raise _fail("refused", status)
                        if 300 <= status < 400:
                            raise _fail("source_changed", status)  # a redirect is never followed
                        if status != 200:
                            raise _fail("provider", status)
                        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                            raise _fail("source_changed", status)
                        if kind == "api":
                            ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
                            if ctype != "application/json":
                                raise _fail("source_changed", status)
                        body = await _body(response, limit)
            except (httpx.HTTPError, TimeoutError):
                raise _fail("network") from None
        if body is None:
            raise _fail("too_large", 200)
        return body

    async def _json(self, url: str, limit: int = API_LIMIT) -> dict:
        body = await self._get(url, "api", limit)
        try:
            answer = json.loads(body.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            raise _fail("source_changed", 200) from None
        if not isinstance(answer, dict):
            raise _fail("source_changed", 200)
        return answer

    async def repo(self, owner: str, repo: str) -> RepoInfo:
        _owner_repo(owner, repo)
        answer = await self._json(f"{API}/repos/{owner}/{repo}")
        if answer.get("private") is not False:
            raise _fail("not_found", 200)  # public repositories only; a missing flag is not trusted
        branch = answer.get("default_branch")
        if not _segment(branch):
            raise _fail("source_changed", 200)
        lic = answer.get("license")
        spdx = lic.get("spdx_id") if isinstance(lic, dict) else None
        return RepoInfo(branch, spdx if isinstance(spdx, str) and 0 < len(spdx.strip()) <= 100 else "")

    async def commit(self, owner: str, repo: str, ref: str) -> str:
        _owner_repo(owner, repo)
        if not _segment(ref):
            raise ValueError("not a ref")
        answer = await self._json(f"{API}/repos/{owner}/{repo}/commits/{ref}")
        sha = answer.get("sha")
        if not _sha(sha):
            raise _fail("source_changed", 200)
        return sha

    async def tree(self, owner: str, repo: str, sha: str, recursive: bool = True) -> Tree:
        _owner_repo(owner, repo)
        if not _sha(sha):
            raise ValueError("not a sha")
        query = "?recursive=1" if recursive else ""
        answer = await self._json(f"{API}/repos/{owner}/{repo}/git/trees/{sha}{query}", TREE_LIMIT)
        rows = answer.get("tree")
        if not isinstance(rows, list):
            raise _fail("source_changed", 200)
        blobs: list[tuple[str, int]] = []
        trees: list[tuple[str, str]] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                continue
            size = row.get("size")
            if row.get("type") == "blob" and isinstance(size, int) and not isinstance(size, bool):
                blobs.append((row["path"], size))
            elif row.get("type") == "tree" and _sha(row.get("sha")):
                trees.append((row["path"], row["sha"]))
        return Tree(blobs, trees, answer.get("truncated") is True)

    async def raw(self, owner: str, repo: str, sha: str, path: str, limit: int) -> bytes:
        _owner_repo(owner, repo)
        if not _sha(sha):
            raise ValueError("not a sha")
        if not isinstance(path, str) or not path or not all(_segment(s) for s in path.split("/")):
            raise ValueError("not a path")
        return await self._get(f"{RAW}/{owner}/{repo}/{sha}/{path}", "file", limit)

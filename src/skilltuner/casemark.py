"""The CaseMark connector: searches and reads skills from agentskills.finance's MCP endpoint.

It knows that endpoint and nothing else. Everything in an answer is untrusted text, so these rules hold:

- one endpoint: requests go only to `MCP_URL`, one at a time, `GAP_S` apart;
- nothing read from an answer is ever fetched: a bundled file is asked for through the same endpoint, by a slug
  built here from a checked root and a path matching `FILE_PATH`;
- no redirect is followed: any 3xx is `source_changed`;
- answers are size-capped and every text is cleaned and cut.

Errors carry a kind (`SourceError.kind`): `network`, `refused`, `source_changed`, `not_found`, `provider`, `budget`.
Nothing here logs; an exception's text is never kept."""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from src.events.eventbrite import SourceError as _SourceError
from src.events.eventbrite import _body

MCP_URL = "https://skills.case.dev/api/mcp"
GAP_S = 1.0
TIMEOUT_S = 30.0
ANSWER_LIMIT = 1_000_000
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
FILE_PATH = re.compile(r"^references/[A-Za-z0-9._-]{1,100}\.(md|txt)$")

MAX_RESULTS = 5
NAME_MAX = 120
SUMMARY_MAX = 1_000
BODY_MAX = 200_000
SHORT_MAX = 200  # author, licence, version, one tag
TAGS_MAX = 20

_DROPPED = ("Cc", "Zl", "Zp", "Cs")
SourceError = _SourceError  # the one error class of every connector; kinds here add `not_found` and `budget`


@dataclass
class Found:
    slug: str
    name: str
    summary: str
    tags: list[str]
    score: float


@dataclass
class Downloaded:
    slug: str
    name: str
    summary: str
    body: str
    tags: list[str]
    version: str
    author: str
    license: str
    files: list[tuple[str, str]]  # (path, file slug) of each bundled text file under references/


def _clean(value: object, limit: int) -> str:
    """Text with control characters (but not newline or tab) and U+2028/U+2029 taken out, cut to `limit`."""
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c in "\n\t" or unicodedata.category(c) not in _DROPPED)[:limit]


def _tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [t for t in (_clean(x, SHORT_MAX).strip() for x in value[:TAGS_MAX] if isinstance(x, str)) if t]


def _score(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


class CaseMark:
    def __init__(self, http: Callable[[], httpx.AsyncClient],
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 on_send: Callable[[], bool] = lambda: True):
        self._http = http
        self._sleep = sleep
        self._on_send = on_send
        self._lock = asyncio.Lock()
        self._sent = 0

    async def _call(self, tool: str, arguments: dict[str, str]) -> dict:
        """The inner JSON object a tool call answered, or SourceError."""
        async with self._lock:
            if not self._on_send():
                raise SourceError("budget")
            if self._sent:
                await self._sleep(GAP_S)
            self._sent += 1
            payload = {"jsonrpc": "2.0", "id": self._sent, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}}
            headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
            try:
                async with self._http().stream("POST", MCP_URL, json=payload, headers=headers,
                                               follow_redirects=False, timeout=TIMEOUT_S) as response:
                    status = response.status_code
                    if status == 429:
                        raise SourceError("refused")
                    if 300 <= status < 400:
                        raise SourceError("source_changed")  # a redirect is never followed
                    if status != 200:
                        raise SourceError("provider")
                    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if kind != "application/json":
                        raise SourceError("source_changed")
                    body = await _body(response, ANSWER_LIMIT)
            except httpx.HTTPError:
                raise SourceError("network") from None
        if body is None:
            raise SourceError("provider")
        return self._inner(body)

    @staticmethod
    def _inner(body: bytes) -> dict:
        try:
            answer = json.loads(body.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            raise SourceError("source_changed") from None
        if not isinstance(answer, dict):
            raise SourceError("source_changed")
        if "error" in answer and "result" not in answer:
            raise SourceError("provider")
        result = answer.get("result")
        content = result.get("content") if isinstance(result, dict) else None
        first = content[0] if isinstance(content, list) and content else None
        text = first.get("text") if isinstance(first, dict) else None
        if not isinstance(text, str):
            raise SourceError("source_changed")
        try:
            inner = json.loads(text)
        except (ValueError, RecursionError):
            raise SourceError("source_changed") from None
        if not isinstance(inner, dict):
            raise SourceError("source_changed")
        if inner.get("error") is True:
            raise SourceError("not_found" if inner.get("statusCode") == 404 else "provider")
        return inner

    async def search(self, words: str) -> list[Found]:
        """At most the first five usable results for `words`."""
        inner = await self._call("resolve_skill", {"query": words})
        rows = inner.get("results")
        if not isinstance(rows, list):
            raise SourceError("source_changed")
        found: list[Found] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("slug"), str) or not SLUG.match(row["slug"]):
                continue
            found.append(Found(row["slug"], _clean(row.get("name"), NAME_MAX), _clean(row.get("summary"), SUMMARY_MAX),
                               _tags(row.get("tags")), _score(row.get("score"))))
            if len(found) == MAX_RESULTS:
                break
        return found

    async def read(self, slug: str) -> Downloaded:
        if not isinstance(slug, str) or not SLUG.match(slug):
            raise ValueError("not a skill slug")
        inner = await self._call("read_skill", {"slug": slug})
        if inner.get("slug") != slug or not isinstance(inner.get("content"), str):
            raise SourceError("source_changed")
        files: list[tuple[str, str]] = []
        bundle = inner.get("bundle")
        listed = bundle.get("files") if isinstance(bundle, dict) else None
        for item in listed if isinstance(listed, list) else []:
            if not isinstance(item, dict):
                continue
            path, file_slug, kind = item.get("path"), item.get("slug"), item.get("content_type")
            if (isinstance(path, str) and FILE_PATH.match(path) and isinstance(file_slug, str)
                    and file_slug == f"{slug}--file--{path}" and isinstance(kind, str) and kind.startswith("text/")):
                files.append((path, file_slug))
        version = inner.get("version")
        return Downloaded(
            slug, _clean(inner.get("name"), NAME_MAX), _clean(inner.get("summary"), SUMMARY_MAX),
            _clean(inner["content"], BODY_MAX), _tags(inner.get("tags")),
            _clean(str(version) if isinstance(version, (int, str)) and not isinstance(version, bool) else "", SHORT_MAX),
            _clean(inner.get("author_name"), SHORT_MAX), _clean(inner.get("license"), SHORT_MAX), files)

    async def read_file(self, root: str, path: str) -> str:
        """The text of one bundled file of `root`; `path` must be a references/ text file."""
        if not isinstance(root, str) or not SLUG.match(root) or not isinstance(path, str) or not FILE_PATH.match(path):
            raise ValueError("not a bundled file")
        inner = await self._call("read_skill", {"slug": f"{root}--file--{path}"})
        if not isinstance(inner.get("content"), str):
            raise SourceError("source_changed")
        return _clean(inner["content"], BODY_MAX)

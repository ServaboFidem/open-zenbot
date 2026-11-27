import json
from pathlib import Path

import httpx
import pytest

from src.skilltuner.casemark import CaseMark

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"casemark_{name}.json").read_text(encoding="utf-8"))


def rpc(inner) -> dict:
    """A JSON-RPC answer carrying `inner` as the text of its one content item."""
    text = inner if isinstance(inner, str) else json.dumps(inner)
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}


class FakeCaseMark:
    """An httpx.MockTransport standing in for CaseMark's MCP endpoint; answers by `params.name`."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.search_answer: object = fixture("search")
        self.read_answer: object = fixture("read")
        self.file_answer: object = fixture("ref")
        self.status = 200
        self.headers: dict[str, str] = {"content-type": "application/json"}
        self.raw: bytes | None = None
        self.raises: Exception | None = None
        self.stream = None  # an async generator function: the body, sent with no Content-Length
        self.sleeps: list[float] = []
        self.allow = True
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises:
            raise self.raises
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw, headers=self.headers)
        if self.stream is not None:
            return httpx.Response(self.status, content=self.stream(), headers=self.headers)
        params = json.loads(request.content)["params"]
        if params["name"] == "resolve_skill":
            inner = self.search_answer
        elif "--file--" in params["arguments"].get("slug", ""):
            inner = self.file_answer
        else:
            inner = self.read_answer
        return httpx.Response(self.status, json=rpc(inner), headers=self.headers)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def reader(self) -> CaseMark:
        return CaseMark(lambda: self.client, sleep=self.sleep, on_send=lambda: self.allow)


@pytest.fixture
def fake_casemark():
    return FakeCaseMark()


GH_SHA = "6dcb09b5b57875f334f61aebed695e2e4193db5e"


class FakeGitHub:
    """An httpx.MockTransport standing in for api.github.com and raw.githubusercontent.com, routed by URL."""

    def __init__(self):
        from src.skilltuner.github import GitHub
        self._make = GitHub
        self.repos: dict[str, object] = {"o/r": json.loads((FIXTURES / "github_repo.json").read_text("utf-8"))}
        self.commits: dict[str, object] = {"o/r/main": json.loads((FIXTURES / "github_commit.json").read_text("utf-8"))}
        self.trees: dict[str, object] = {f"o/r/{GH_SHA}": json.loads((FIXTURES / "github_tree.json").read_text("utf-8"))}
        self.files: dict[str, bytes] = {}  # "o/r/<sha>/<path>" -> bytes
        self.status = 200
        self.headers: dict[str, str] = {}
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.allow = True
        self.kinds: list[str] = []
        self.raises: Exception | None = None
        self.stream = None  # an async generator function: a raw body sent with no Content-Length
        self.hold = None  # an asyncio.Event a test may set to stall the tree answer
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises:
            raise self.raises
        url = request.url
        path = url.path
        if self.status != 200:
            return httpx.Response(self.status, headers=self.headers)
        if url.host == "raw.githubusercontent.com":
            if self.stream is not None:
                return httpx.Response(200, content=self.stream(), headers=self.headers)
            body = self.files.get(path.lstrip("/"))
            return httpx.Response(200 if body is not None else 404, content=body or b"", headers=self.headers)
        headers = {"content-type": "application/json; charset=utf-8", **self.headers}
        parts = path.strip("/").split("/")
        if parts[0] == "repos" and len(parts) == 3:
            answer = self.repos.get("/".join(parts[1:]))
        elif parts[0] == "repos" and len(parts) == 6 and parts[3] == "git" and parts[4] == "trees":
            if self.hold is not None:
                await self.hold.wait()
            answer = self.trees.get(f"{parts[1]}/{parts[2]}/{parts[5]}")
        elif parts[0] == "repos" and len(parts) == 5 and parts[3] == "commits":
            answer = self.commits.get(f"{parts[1]}/{parts[2]}/{parts[4]}")
        else:
            answer = None
        if answer is None:
            return httpx.Response(404, json={"message": "Not Found"}, headers=headers)
        return httpx.Response(200, json=answer, headers=headers)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def _on_send(self, kind: str) -> bool:
        self.kinds.append(kind)
        return self.allow

    def reader(self):
        return self._make(lambda: self.client, sleep=self.sleep, on_send=self._on_send)


@pytest.fixture
def fake_github():
    return FakeGitHub()

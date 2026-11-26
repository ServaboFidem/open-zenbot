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

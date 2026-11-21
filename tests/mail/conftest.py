"""A stand-in for AgentMail's REST API, on httpx.MockTransport. Every address and message is made up."""

from __future__ import annotations

import json
from urllib.parse import unquote

import httpx
import pytest

KEY = "am_testkey00001"


def inbox(name: str, **over) -> dict:
    made = {"inbox_id": f"{name}@agentmail.to", "email": f"{name}@agentmail.to", "pod_id": "pod-1",
            "display_name": "ZenBot", "created_at": "2026-11-20T10:00:00Z", "updated_at": "2026-11-20T10:00:00Z"}
    made.update(over)
    return made


def message(mid: str, **over) -> dict:
    made = {"message_id": mid, "thread_id": f"t-{mid}", "timestamp": "2026-11-21T09:30:00Z",
            "from": "Ann Lee <ann@example.com>", "to": ["zen@agentmail.to"], "cc": [], "subject": f"Hello {mid}",
            "preview": f"Preview of {mid}", "labels": ["received"],
            "attachments": [{"attachment_id": "a1", "filename": "notes.txt", "size": 10}],
            "text": f"Full text of {mid}\n\n> quoted", "extracted_text": f"Full text of {mid}",
            "html": "<b>x</b>", "extracted_html": "<b>x</b>"}
    made.update(over)
    return made


BODY_FIELDS = ("text", "html", "extracted_text", "extracted_html", "reply_to")


def _error(status: int, code: str, text: str) -> httpx.Response:
    return httpx.Response(status, json={"name": "Error", "code": code, "message": text})


class FakeAgentMail:
    """AgentMail as far as the client uses it. ``fail`` maps ``"METHOD /path"`` (the path after /v0, decoded) to a
    status number, an ``httpx.Response`` or an exception to raise. ``requests`` is everything the client sent."""

    def __init__(self):
        self.key = KEY
        self.inboxes: list[dict] = [inbox("zen")]
        self.messages: dict[str, list[dict]] = {"zen@agentmail.to": [message("m1"), message("m2")]}
        self.next_page_token: str | None = "t2"
        self.fail: dict[str, object] = {}
        self.requests: list[httpx.Request] = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raw = request.url.raw_path.split(b"?", 1)[0].decode("ascii")
        if request.url.host != "api.agentmail.to" or not raw.startswith("/v0/"):
            return _error(404, "not_found", "No such route")
        parts = [unquote(p) for p in raw[len("/v0/"):].split("/")]
        name = f"{request.method} /{'/'.join(parts)}"
        if name in self.fail:
            got = self.fail[name]
            if isinstance(got, BaseException):
                raise got
            return got if isinstance(got, httpx.Response) else httpx.Response(int(got), json={"name": "Error"})
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return _error(401, "unknown_api_key", "Unknown API key")
        if parts[0] != "inboxes":
            return _error(404, "not_found", "No such route")
        if len(parts) == 1 and request.method == "GET":
            return httpx.Response(200, json={"count": len(self.inboxes), "limit": 100, "next_page_token": None,
                                             "inboxes": self.inboxes})
        if len(parts) == 1 and request.method == "POST":
            return self._create(json.loads(request.content or b"{}"))
        box = next((i for i in self.inboxes if i["inbox_id"] == parts[1]), None)
        if box is None:
            return _error(404, "not_found", "Inbox not found")
        if len(parts) == 2:
            return httpx.Response(200, json=box)
        held = self.messages.get(box["inbox_id"], [])
        if len(parts) == 3 and parts[2] == "messages":
            if request.url.params.get("page_token"):
                return httpx.Response(200, json={"count": 0, "limit": 50, "next_page_token": None, "messages": []})
            listed = [{k: v for k, v in m.items() if k not in BODY_FIELDS} for m in held]
            return httpx.Response(200, json={"count": len(listed), "limit": 50,
                                             "next_page_token": self.next_page_token, "messages": listed})
        if len(parts) == 4 and parts[2] == "messages":
            found = next((m for m in held if m["message_id"] == parts[3]), None)
            return httpx.Response(200, json=found) if found else _error(404, "not_found", "Message not found")
        return _error(404, "not_found", "No such route")

    def _create(self, body: dict) -> httpx.Response:
        same = next((i for i in self.inboxes if body.get("client_id") and i.get("client_id") == body["client_id"]), None)
        if same:
            return httpx.Response(200, json=same)
        made = inbox(body["username"], display_name=body.get("display_name"), client_id=body.get("client_id"))
        if any(i["inbox_id"] == made["inbox_id"] for i in self.inboxes):
            return _error(409, "already_exists", "Inbox already exists")
        self.inboxes.insert(0, made)
        return httpx.Response(200, json=made)


@pytest.fixture
async def fake_mail():
    site = FakeAgentMail()
    yield site
    await site.client.aclose()

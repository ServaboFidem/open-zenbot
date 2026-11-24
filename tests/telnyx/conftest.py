"""A stand-in for the parts of Telnyx's REST API the client uses, on httpx.MockTransport. Every number is made up."""

from __future__ import annotations

import json
from urllib.parse import unquote

import httpx
import pytest

KEY = "KEY0TELNYX0TEST1"


def number(nid: str, phone: str, profile: str) -> dict:
    return {"id": nid, "phone_number": phone, "messaging_profile_id": profile, "record_type": "messaging_phone_number",
            "type": "long-code", "country_code": "US"}


def _error(status: int, detail: str = "error") -> httpx.Response:
    return httpx.Response(status, json={"errors": [{"code": str(status), "title": "Error", "detail": detail}]})


class FakeTelnyx:
    """Telnyx as far as the client uses it. ``fail`` maps ``"METHOD /path"`` (the path after /v2, decoded) to a status
    number, an ``httpx.Response`` or an exception to raise. ``sent`` is every text accepted; ``requests`` everything
    the client sent."""

    def __init__(self):
        self.key = KEY
        self.profiles: list[dict] = [{"id": "p1", "name": "ZenBot"}]
        self.numbers: dict[str, list[dict]] = {"p1": [number("n1", "+15550001111", "p1")]}
        self.sent: list[dict] = []
        self.fail: dict[str, object] = {}
        self.requests: list[httpx.Request] = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raw = request.url.raw_path.split(b"?", 1)[0].decode("ascii")
        if request.url.host != "api.telnyx.com" or not raw.startswith("/v2/"):
            return _error(404)
        parts = [unquote(p) for p in raw[len("/v2/"):].split("/")]
        name = f"{request.method} /{'/'.join(parts)}"
        if name in self.fail:
            got = self.fail[name]
            if isinstance(got, BaseException):
                raise got
            return got if isinstance(got, httpx.Response) else _error(int(got))
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return _error(401, "Authentication failed")
        if name == "POST /messages":
            body = json.loads(request.content)
            self.sent.append(body)
            return httpx.Response(200, json={"data": {"id": f"msg-{len(self.sent)}", "to": [{"phone_number": body["to"]}],
                                                      "from": {"phone_number": body["from"]}, "text": body["text"]}})
        if name == "GET /messaging_profiles":
            return httpx.Response(200, json={"data": self.profiles, "meta": {"total_pages": 1}})
        if len(parts) == 3 and parts[0] == "messaging_profiles" and parts[2] == "phone_numbers":
            return httpx.Response(200, json={"data": self.numbers.get(parts[1], []), "meta": {"total_pages": 1}})
        return _error(404)


@pytest.fixture
async def fake_telnyx():
    site = FakeTelnyx()
    yield site
    await site.client.aclose()

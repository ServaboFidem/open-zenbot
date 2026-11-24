"""The Telnyx connector: sending one text and listing the numbers that can send them.

It knows Telnyx and nothing else:

- one host: requests go only to https://api.telnyx.com/v2/..., every path part percent-quoted as one part;
- no redirect is followed: any 3xx is `provider`;
- the key is only ever put in the request's Authorization header.

Errors carry a kind (`TelnyxError.kind`); Telnyx's own error text is never kept (it can echo a phone number), the
transport error is dropped (`from None`), and nothing here logs."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from src.events.eventbrite import _body
from src.telnyx.settings import PHONE

BASE = "https://api.telnyx.com/v2"
TIMEOUT_S = 30.0
ANSWER_LIMIT = 1_000_000
PAGE_SIZE = 250
MAX_PROFILES = 20
MAX_NUMBERS = 500


class TelnyxError(Exception):
    """A failed Telnyx request. ``kind``: network, key, refused, invalid, provider. ``before_request`` is true only
    when the request certainly never left (no connection was made), so a send may be tried again."""

    def __init__(self, kind: str, *, status: int = 0, before_request: bool = False):
        super().__init__(kind)
        self.kind = kind
        self.status = status
        self.before_request = before_request


def _kind(status: int) -> str:
    if status in (401, 403):
        return "key"
    if status == 429:
        return "refused"
    if status in (400, 422):
        return "invalid"
    return "provider"


def _id(value: object) -> str:
    return value if isinstance(value, str) and value and value.isprintable() and len(value) <= 200 else ""


class Telnyx:
    """One key's view of Telnyx, through the harness's one HTTP client."""

    def __init__(self, http: httpx.AsyncClient, key: str):
        self._http = http
        self._key = key

    def __repr__(self) -> str:  # never the key
        return "Telnyx()"

    async def _call(self, method: str, parts: tuple[str, ...], *, params: dict | None = None,
                    body: dict | None = None) -> Any:
        url = BASE + "/" + "/".join(quote(p, safe="") for p in parts)
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        try:
            async with self._http.stream(method, url, params=params, json=body, timeout=TIMEOUT_S,
                                         follow_redirects=False, headers=headers) as response:
                status = response.status_code
                data = await _body(response, ANSWER_LIMIT)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise TelnyxError("network", before_request=True) from None
        except httpx.HTTPError:
            raise TelnyxError("network") from None
        if not 200 <= status < 300:
            raise TelnyxError(_kind(status), status=status)
        try:
            payload = json.loads(data) if data is not None else None
        except (ValueError, RecursionError):  # deep nesting raises RecursionError, which is no ValueError
            payload = None
        if not isinstance(payload, dict):
            raise TelnyxError("provider", status=status)
        return payload.get("data")

    async def send(self, from_: str, to: str, text: str) -> str:
        """Send one text; answers Telnyx's message id."""
        row = await self._call("POST", ("messages",), body={"from": from_, "to": to, "text": text})
        message_id = _id(row.get("id")) if isinstance(row, dict) else ""
        if not message_id:
            raise TelnyxError("provider")
        return message_id

    async def _list(self, parts: tuple[str, ...]) -> list:
        rows = await self._call("GET", parts, params={"page[size]": PAGE_SIZE})
        if not isinstance(rows, list):
            raise TelnyxError("provider")
        return rows

    async def numbers(self) -> list[dict]:
        """The account's numbers that can send texts: those on a messaging profile, read profile by profile."""
        found: dict[str, dict] = {}
        for profile in (await self._list(("messaging_profiles",)))[:MAX_PROFILES]:
            profile_id = _id(profile.get("id")) if isinstance(profile, dict) else ""
            if not profile_id:
                continue
            for row in await self._list(("messaging_profiles", profile_id, "phone_numbers")):
                if not isinstance(row, dict):
                    continue
                number_id, phone = _id(row.get("id")), row.get("phone_number")
                if number_id and isinstance(phone, str) and PHONE.fullmatch(phone):
                    found.setdefault(number_id, {"id": number_id, "phone": phone, "profile_id": profile_id})
        return sorted(found.values(), key=lambda n: n["phone"])[:MAX_NUMBERS]

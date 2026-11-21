"""The AgentMail connector: ZenBot's inbox through AgentMail's REST API, read-only but for creating an inbox.

It knows AgentMail and nothing else. Everything in an answer is untrusted text (mail is written by strangers), so
three rules hold:

- one host: requests go only to https://api.agentmail.to/v0/..., every path part percent-quoted as one part;
- nothing read from an answer is ever fetched (a link in a mail is text, never followed);
- no redirect is followed: any 3xx is `provider`.

The key is only ever put in the request's Authorization header. Errors carry a kind (`MailError.kind`); an
exception's text and the transport error are never kept (`from None`), and nothing here logs."""

from __future__ import annotations

import json
import unicodedata
from typing import Any
from urllib.parse import quote

import httpx

from src.events.eventbrite import _body

BASE = "https://api.agentmail.to/v0"
TIMEOUT_S = 30.0
PAGE_LIMIT = 5_000_000
LINE_MAX = 320
SUBJECT_MAX = 300
PREVIEW_MAX = 500
TEXT_MAX = 200_000
PAGE_SIZE = 50
TOKEN_MAX = 1000
ADDRESSES_MAX = 50
ATTACHMENTS_MAX = 100
DETAIL_MAX = 200
_TAKEN_WORDS = ("taken", "exists", "already", "in use")
_BREAKS = {" ", " "}


class MailError(Exception):
    """A failed AgentMail request. ``kind``: network, key, permission, not_found, taken, refused, invalid,
    provider. ``code`` and ``detail`` are AgentMail's own (cleaned); neither ever holds the key."""

    def __init__(self, kind: str, code: str = "", detail: str = ""):
        super().__init__(kind)
        self.kind = kind
        self.code = code
        self.detail = detail


def clean_line(value: object, limit: int) -> str:
    """One line of text: control characters and line separators become a space; trimmed; cut to `limit`."""
    if not isinstance(value, str):
        return ""
    out = "".join(" " if ch in _BREAKS or unicodedata.category(ch) == "Cc" else ch for ch in value)
    return out.strip()[:limit].strip()


def clean_text(value: object, limit: int) -> str:
    """A body: line ends and line separators become \\n, tabs stay, every other control character goes; cut."""
    if not isinstance(value, str):
        return ""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in value:
        if ch in _BREAKS:
            out.append("\n")
        elif ch in "\n\t" or unicodedata.category(ch) != "Cc":
            out.append(ch)
    return "".join(out)[:limit]


def _addresses(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [a for a in (clean_line(v, LINE_MAX) for v in value[:ADDRESSES_MAX]) if a]


def _attachments(value: object) -> list[dict]:
    return [a for a in value[:ATTACHMENTS_MAX] if isinstance(a, dict)] if isinstance(value, list) else []


def _inbox(row: object) -> dict | None:
    if not isinstance(row, dict):
        return None
    inbox_id = clean_line(row.get("inbox_id"), LINE_MAX)
    if not inbox_id:
        return None
    return {"inbox_id": inbox_id, "email": clean_line(row.get("email"), LINE_MAX),
            "display_name": clean_line(row.get("display_name"), LINE_MAX),
            "deactivated": row.get("status") == "deactivated"}


def _item(row: object) -> dict | None:
    """A list row: no body. None when it is not an object or has no id."""
    if not isinstance(row, dict):
        return None
    message_id = clean_line(row.get("message_id"), LINE_MAX)
    if not message_id:
        return None
    return {"message_id": message_id, "from": clean_line(row.get("from"), LINE_MAX),
            "to": _addresses(row.get("to")), "subject": clean_line(row.get("subject"), SUBJECT_MAX),
            "preview": clean_line(row.get("preview"), PREVIEW_MAX),
            "timestamp": clean_line(row.get("timestamp"), 64),
            "attachments": len(_attachments(row.get("attachments")))}


def _kind(status: int, code: str, detail: str, creating: bool) -> str:
    if status == 401:
        return "key"
    if status == 403:
        return "permission" if code == "missing_permission" else "key"
    if status == 404:
        return "not_found"
    if status == 429:
        return "refused"
    if creating and status in (400, 409, 422):
        said = f"{code} {detail}".casefold()
        if status == 409 or any(w in said for w in _TAKEN_WORDS):
            return "taken"
        return "invalid"
    return "provider"


class AgentMail:
    """One key's view of AgentMail, through the harness's one HTTP client."""

    def __init__(self, http: httpx.AsyncClient, key: str):
        self._http = http
        self._key = key

    def __repr__(self) -> str:  # never the key
        return "AgentMail()"

    async def _call(self, method: str, parts: tuple[str, ...], *, params: dict | None = None,
                    body: dict | None = None, creating: bool = False) -> Any:
        url = BASE + "/" + "/".join(quote(p, safe="") for p in parts)
        try:
            async with self._http.stream(method, url, params=params, json=body, timeout=TIMEOUT_S,
                                         follow_redirects=False,
                                         headers={"Authorization": f"Bearer {self._key}",
                                                  "Accept": "application/json"}) as response:
                status = response.status_code
                data = await _body(response, PAGE_LIMIT)
        except httpx.HTTPError:
            raise MailError("network") from None
        if data is None:
            raise MailError("provider")
        try:
            payload = json.loads(data)
        except ValueError:
            payload = None
        if 200 <= status < 300:
            if payload is None:
                raise MailError("provider")
            return payload
        found = payload if isinstance(payload, dict) else {}
        code = clean_line(found.get("code"), 100)
        detail = clean_line(found.get("message"), DETAIL_MAX)
        raise MailError(_kind(status, code, detail, creating), code, detail)

    async def list_inboxes(self, limit: int = 100) -> list[dict]:
        payload = await self._call("GET", ("inboxes",), params={"limit": limit})
        rows = payload.get("inboxes") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise MailError("provider")
        return [i for i in map(_inbox, rows) if i]

    @staticmethod
    def _one_inbox(payload: Any) -> dict:
        found = _inbox(payload)
        if found is None:
            raise MailError("provider")
        return found

    async def get_inbox(self, inbox_id: str) -> dict:
        return self._one_inbox(await self._call("GET", ("inboxes", inbox_id)))

    async def create_inbox(self, username: str) -> dict:
        body = {"username": username, "display_name": "ZenBot", "client_id": "openzenbot-" + username}
        return self._one_inbox(await self._call("POST", ("inboxes",), body=body, creating=True))

    async def list_messages(self, inbox_id: str, page_token: str | None = None, limit: int = PAGE_SIZE) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if page_token:
            params["page_token"] = page_token
        payload = await self._call("GET", ("inboxes", inbox_id, "messages"), params=params)
        rows = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise MailError("provider")
        token = clean_line(payload.get("next_page_token"), TOKEN_MAX)
        return {"messages": [m for m in map(_item, rows) if m], "next_page_token": token or None}

    async def get_message(self, inbox_id: str, message_id: str) -> dict:
        row = await self._call("GET", ("inboxes", inbox_id, "messages", message_id))
        item = _item(row)
        if item is None:
            raise MailError("provider")
        text = row.get("extracted_text")
        if not isinstance(text, str) or not text:
            text = row.get("text")
        names = [n for n in (clean_line(a.get("filename"), LINE_MAX) for a in _attachments(row.get("attachments")))
                 if n]
        return {"message_id": item["message_id"], "from": item["from"], "to": item["to"],
                "cc": _addresses(row.get("cc")), "subject": item["subject"], "timestamp": item["timestamp"],
                "text": clean_text(text, TEXT_MAX), "attachments": names}

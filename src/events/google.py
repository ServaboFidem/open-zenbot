"""Google Calendar over httpx: OAuth (PKCE) and the calendar and event calls.

Every call goes through the ``httpx.AsyncClient`` handed in, so tests pass a ``MockTransport``.
Failures raise ``GoogleError`` with a ``kind`` the callers branch on: auth, forbidden, missing,
conflict, gone, rate, network or provider."""

from __future__ import annotations

import base64
import hashlib
import math
import secrets
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx

from src.events.settings import CalendarKeys

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/calendar/v3"
READ_SCOPES = [
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
]
WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
EXPIRY_MARGIN = 60.0
PAGE = 250

_STATUS_KINDS = {401: "auth", 403: "forbidden", 404: "missing", 409: "conflict", 410: "gone", 429: "rate"}
_RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}


class GoogleError(Exception):
    """kind: auth, forbidden, missing, conflict, gone, rate, network or provider."""

    def __init__(self, kind: str, message: str = "", status: int = 0):
        super().__init__(message or kind)
        self.kind = kind
        self.status = status


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def pkce() -> tuple[str, str]:
    verifier = _b64(secrets.token_bytes(48))
    return verifier, _b64(hashlib.sha256(verifier.encode()).digest())


def auth_url(client_id: str, redirect_uri: str, scopes: list[str], state: str, challenge: str) -> str:
    query = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(scopes), "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true",
    })
    return f"{AUTH_URL}?{query}"


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _lifetime(value: Any) -> float:
    """``expires_in`` as seconds; anything that is not a finite, positive number counts as already expired.
    It never raises: the caller's frame holds the client secret and the tokens."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    return seconds if math.isfinite(seconds) and seconds > 0 else 0.0


def _failure(resp: httpx.Response) -> GoogleError:
    status = resp.status_code
    body = _json(resp)
    error = body.get("error")
    if error == "invalid_grant":
        return GoogleError("auth", "Google no longer accepts this sign-in", status)
    reasons: set[str] = set()
    message = ""
    if isinstance(error, dict):
        message = str(error.get("message") or "")
        reasons = {str(e.get("reason")) for e in error.get("errors") or [] if isinstance(e, dict)}
    kind = "rate" if status == 403 and reasons & _RATE_REASONS else _STATUS_KINDS.get(status, "provider")
    return GoogleError(kind, message or f"Google answered {status}", status)


class Google:
    def __init__(self, http: httpx.AsyncClient, keys: Callable[[], CalendarKeys], clock=time.monotonic):
        self._http = http
        self._keys = keys
        self._clock = clock
        self._access = ""
        self._expires_at = 0.0

    async def _send(self, method: str, url: str, **kw: Any) -> httpx.Response:
        try:
            return await self._http.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise GoogleError("network", f"Could not reach Google ({type(e).__name__})") from e

    async def _grant(self, form: dict[str, str]) -> dict[str, Any]:
        keys = self._keys()
        data = {"client_id": keys.client_id, "client_secret": keys.client_secret, **form}
        resp = await self._send("POST", TOKEN_URL, data=data)
        if resp.status_code != 200:
            raise _failure(resp)
        out = _json(resp)
        if not out.get("access_token"):
            raise GoogleError("provider", "Google sent no access token", resp.status_code)
        self._access = str(out["access_token"])
        self._expires_at = self._clock() + _lifetime(out.get("expires_in"))
        return out

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        return await self._grant({"grant_type": "authorization_code", "code": code,
                                  "code_verifier": verifier, "redirect_uri": redirect_uri})

    async def token(self) -> str:
        if self._access and self._clock() < self._expires_at - EXPIRY_MARGIN:
            return self._access
        refresh = self._keys().refresh_token
        if not refresh:
            raise GoogleError("auth", "Google is not connected")
        await self._grant({"grant_type": "refresh_token", "refresh_token": refresh})
        return self._access

    def forget(self) -> None:
        self._access = ""
        self._expires_at = 0.0

    async def revoke(self, token: str) -> None:
        try:
            await self._http.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            pass

    async def _call(self, method: str, path: str, *, params: dict[str, str] | None = None,
                    body: dict | None = None, ok_gone: bool = False, access: str | None = None) -> dict[str, Any]:
        """``access``: use exactly this access token. Without one, a 401 (the cached token was revoked or
        expired early) is retried once with a newly issued token before it counts as ``auth``."""
        for last in (False, True):
            bearer = access or await self.token()
            resp = await self._send(method, f"{API}{path}", params=params, json=body,
                                    headers={"Authorization": f"Bearer {bearer}"})
            if resp.status_code != 401:
                break
            self.forget()
            if access or last:
                break
        if resp.status_code == 410 and ok_gone:
            return {}
        if resp.status_code >= 300:
            raise _failure(resp)
        return _json(resp)

    async def calendar_list(self, access: str | None = None) -> list[dict]:
        """``access``: list with exactly this access token (never a cached or refreshed one)."""
        items: list[dict] = []
        page = ""
        while True:
            params = {"maxResults": str(PAGE), **({"pageToken": page} if page else {})}
            body = await self._call("GET", "/users/me/calendarList", params=params, access=access)
            items += body.get("items") or []
            page = body.get("nextPageToken") or ""
            if not page:
                return items

    async def list_events(self, calendar_id: str, *, sync_token: str | None = None, time_min: str | None = None,
                          time_max: str | None = None) -> tuple[list[dict], str]:
        base = {"singleEvents": "true", "showDeleted": "true", "maxResults": str(PAGE)}
        if sync_token:
            base["syncToken"] = sync_token
        else:
            if time_min:
                base["timeMin"] = time_min
            if time_max:
                base["timeMax"] = time_max
        items: list[dict] = []
        page = ""
        while True:
            params = {**base, **({"pageToken": page} if page else {})}
            body = await self._call("GET", f"/calendars/{_quote(calendar_id)}/events", params=params)
            items += body.get("items") or []
            page = body.get("nextPageToken") or ""
            if not page:
                return items, str(body.get("nextSyncToken") or "")

    def _event_path(self, calendar_id: str, event_id: str) -> str:
        return f"/calendars/{_quote(calendar_id)}/events/{_quote(event_id)}"

    async def get_event(self, calendar_id: str, event_id: str) -> dict:
        return await self._call("GET", self._event_path(calendar_id, event_id))

    async def insert_event(self, calendar_id: str, body: dict) -> dict:
        return await self._call("POST", f"/calendars/{_quote(calendar_id)}/events", body=body)

    async def patch_event(self, calendar_id: str, event_id: str, body: dict) -> dict:
        return await self._call("PATCH", self._event_path(calendar_id, event_id), body=body)

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        await self._call("DELETE", self._event_path(calendar_id, event_id), ok_gone=True)

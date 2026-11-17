import json
import urllib.parse

import httpx
import pytest

from src.events.google import API, Google, TOKEN_URL
from src.events.settings import CalendarKeys
from src.events.store import CalendarStore


@pytest.fixture
def store(tmp_path):
    s = CalendarStore(tmp_path / "calendar.db")
    yield s
    s.close()


class FakeGoogle:
    """A small model of Google for tests: pass ``fake.handler`` to ``httpx.MockTransport``.

    Models:
    - the token endpoint (POST TOKEN_URL): ``authorization_code`` and ``refresh_token`` grants. The answer
      carries ``access_token`` (a new one each time), ``expires_in`` 3600, ``scope`` (the ``scope`` attribute)
      and, on a code exchange only, ``refresh_token``. ``refresh_ok = False`` answers 400 ``invalid_grant``.
    - the revoke endpoint (always 200).
    - ``GET users/me/calendarList`` (``calendars``, a list of calendar-list entries; one page).
    - ``calendars/{id}/events``: list, get, insert, patch, delete. ``events[calendar_id][event_id]`` holds the
      items. List pages by ``page_size`` (default 250) with ``pageToken``, supports ``syncToken`` (only items
      changed since that token; 410 for a token it never issued) and answers ``nextSyncToken`` on the last page.
      ``timeMin``/``timeMax`` are recorded but not applied. Insert answers 409 when the id exists (a cancelled
      one too); patch 404 when absent; delete marks ``status: "cancelled"`` (410 when already cancelled; 404
      when absent). Every write gives the item a new ``etag``. Seed items with ``put`` so sync sees them.
    - ``requests``: every request, in order. ``fail``: path fragment -> status, answered before anything else
      (``fail_reason`` is the error reason inside the body, e.g. ``rateLimitExceeded``).
    - ``access_tokens``: how many access tokens it issued.
    """

    def __init__(self):
        self.calendars: list[dict] = []
        self.events: dict[str, dict[str, dict]] = {}
        self.requests: list[httpx.Request] = []
        self.fail: dict[str, int] = {}
        self.fail_reason = ""
        self.refresh_ok = True
        self.scope = ""
        self.page_size = 250
        self.access_tokens = 0
        self._clock = 0
        self._changed: dict[tuple[str, str], int] = {}
        self._issued: set[str] = set()

    def put(self, calendar_id: str, item: dict) -> dict:
        """Seed or replace an item as a write: new etag, counted as changed for sync."""
        self._clock += 1
        item = {**item, "etag": f'"{self._clock}"'}
        self.events.setdefault(calendar_id, {})[item["id"]] = item
        self._changed[(calendar_id, item["id"])] = self._clock
        return item

    def token_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).split("?")[0] == TOKEN_URL]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for fragment, status in self.fail.items():
            if fragment in url:
                return self._error(status, self.fail_reason)
        if url.split("?")[0] == TOKEN_URL:
            return self._token(request)
        if "/revoke" in url:
            return httpx.Response(200, json={})
        path = urllib.parse.unquote(request.url.path.removeprefix(urllib.parse.urlsplit(API).path))
        if path == "/users/me/calendarList":
            return httpx.Response(200, json={"items": self.calendars})
        parts = path.split("/")
        if len(parts) >= 4 and parts[1] == "calendars" and parts[3] == "events":
            return self._events(request, parts[2], parts[4] if len(parts) > 4 else None)
        return self._error(404)

    def _error(self, status: int, reason: str = "") -> httpx.Response:
        errors = [{"reason": reason}] if reason else []
        return httpx.Response(status, json={"error": {"code": status, "message": "fake", "errors": errors}})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        if form.get("grant_type") == "refresh_token" and not self.refresh_ok:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.access_tokens += 1
        body = {"access_token": f"access-{self.access_tokens}", "expires_in": 3600, "scope": self.scope,
                "token_type": "Bearer"}
        if form.get("grant_type") == "authorization_code":
            body["refresh_token"] = "refresh-1"
        return httpx.Response(200, json=body)

    def _events(self, request: httpx.Request, cal: str, event_id: str | None) -> httpx.Response:
        items = self.events.setdefault(cal, {})
        method = request.method
        if event_id is None:
            if method == "GET":
                return self._list(request, cal)
            if method == "POST":
                body = json.loads(request.content)
                if body.get("id") in items:
                    return self._error(409)
                return httpx.Response(200, json=self.put(cal, {"status": "confirmed", **body}))
            return self._error(405)
        if method == "GET":
            return httpx.Response(200, json=items[event_id]) if event_id in items else self._error(404)
        if event_id not in items:
            return self._error(404)
        if method == "PATCH":
            return httpx.Response(200, json=self.put(cal, {**items[event_id], **json.loads(request.content)}))
        if method == "DELETE":
            if items[event_id].get("status") == "cancelled":
                return self._error(410)
            self.put(cal, {**items[event_id], "status": "cancelled"})
            return httpx.Response(204)
        return self._error(405)

    def _list(self, request: httpx.Request, cal: str) -> httpx.Response:
        params = request.url.params
        rows = list(self.events.get(cal, {}).values())
        token = params.get("syncToken")
        if token:
            if token not in self._issued:
                return self._error(410)
            since = int(token.removeprefix("sync-"))
            rows = [r for r in rows if self._changed.get((cal, r["id"]), 0) > since]
        start = int(params.get("pageToken") or 0)
        page = rows[start:start + self.page_size]
        body: dict = {"items": page}
        if start + self.page_size < len(rows):
            body["nextPageToken"] = str(start + self.page_size)
        else:
            body["nextSyncToken"] = f"sync-{self._clock}"
            self._issued.add(body["nextSyncToken"])
        return httpx.Response(200, json=body)


@pytest.fixture
def fake():
    return FakeGoogle()


@pytest.fixture
async def google(fake):
    keys = CalendarKeys(client_id="cid", client_secret="secret", refresh_token="refresh")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
        yield Google(http, lambda: keys)


@pytest.fixture
def clock():
    class Clock:
        now = 1000.0

        def __call__(self):
            return self.now

    return Clock()


@pytest.fixture
async def conn_factory(tmp_path, store, fake, clock):
    """Builds Connections over one vault: a second call is a restart (new object, same files)."""
    from src.events.connect import Connection
    from src.memory.ledger import Ledger

    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    ledger = Ledger(vault)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    made = []

    def build():
        holder = []
        google = Google(http, lambda: holder[0].keys, clock)
        c = Connection(vault, store, google, ledger, port=8765, clock=clock)
        holder.append(c)
        made.append(c)
        return c

    build.ledger = ledger
    yield build
    await http.aclose()


@pytest.fixture
def conn(conn_factory):
    c = conn_factory()
    c.set_credentials("cid", "secret")
    return c


SYNC_NOW = "2026-11-17T12:00:00Z"


@pytest.fixture
def sync(store, fake, conn, conn_factory):
    """A connected Sync with ``now`` fixed at SYNC_NOW. Google has calendars ``work`` (owner, primary) and
    ``home`` (reader); both are in the store and only ``work`` is selected."""
    from datetime import datetime, timezone

    from src.events.convert import calendar_row
    from src.events.sync import Sync

    conn.keys.refresh_token = "refresh"
    fake.calendars = [{"id": "work", "summary": "Work", "accessRole": "owner", "primary": True},
                      {"id": "home", "summary": "Home", "accessRole": "reader"}]
    store.upsert_google([calendar_row(c) for c in fake.calendars])
    store.set_calendar("work", selected=True)
    now = datetime.strptime(SYNC_NOW, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return Sync(store, conn.google, conn, conn_factory.ledger, now=lambda: now)


def state_of(url: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]

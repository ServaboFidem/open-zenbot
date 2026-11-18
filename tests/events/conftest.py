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
      one too) and 400 for a ``start`` or ``end`` holding a null; patch 404 when absent or cancelled
      (``patch_cancelled = True`` instead patches a cancelled item and answers it, still cancelled). A patch
      merges ``start`` and ``end`` key by key as Google does (a null removes the key) and answers 400 when the
      result holds both ``date`` and ``dateTime``; delete marks ``status: "cancelled"`` (410 when already cancelled; 404
      when absent). Every write gives the item a new ``etag``. Seed items with ``put`` so sync sees them.
    - ``requests``: every request, in order. ``fail``: path fragment -> status, answered before anything else
      (``fail_reason`` is the error reason inside the body, e.g. ``rateLimitExceeded``).
      ``fail_once``: the same, for the next matching request only.
    - ``access_tokens``: how many access tokens it issued. ``expires_in``: what a token answer carries.
    - ``on_request``: a callable run with each request before it is answered (e.g. a local edit made while a
      push waits on Google).
    """

    def __init__(self):
        self.calendars: list[dict] = []
        self.events: dict[str, dict[str, dict]] = {}
        self.requests: list[httpx.Request] = []
        self.fail: dict[str, int] = {}
        self.fail_once: dict[str, int] = {}
        self.fail_reason = ""
        self.expires_in: object = 3600
        self.refresh_ok = True
        self.scope = ""
        self.page_size = 250
        self.access_tokens = 0
        self.calendars_by_token: dict[str, list[dict]] = {}  # bearer token -> the list that token sees
        self.omit_expiry = False  # a code-exchange answer without expires_in
        self.patch_cancelled = False  # patching a cancelled item answers 200 (still cancelled), not 404
        self.on_request = None
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
        if self.on_request is not None:
            self.on_request(request)
        url = str(request.url)
        for fragment, status in self.fail.items():
            if fragment in url:
                return self._error(status, self.fail_reason)
        for fragment in list(self.fail_once):
            if fragment in url:
                return self._error(self.fail_once.pop(fragment), self.fail_reason)
        if url.split("?")[0] == TOKEN_URL:
            return self._token(request)
        if "/revoke" in url:
            return httpx.Response(200, json={})
        path = urllib.parse.unquote(request.url.path.removeprefix(urllib.parse.urlsplit(API).path))
        if path == "/users/me/calendarList":
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            return httpx.Response(200, json={"items": self.calendars_by_token.get(bearer, self.calendars)})
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
        body = {"access_token": f"access-{self.access_tokens}", "expires_in": self.expires_in, "scope": self.scope,
                "token_type": "Bearer"}
        if form.get("grant_type") == "authorization_code":
            body["refresh_token"] = "refresh-1"
            if self.omit_expiry:
                del body["expires_in"]
        return httpx.Response(200, json=body)

    def _events(self, request: httpx.Request, cal: str, event_id: str | None) -> httpx.Response:
        items = self.events.setdefault(cal, {})
        method = request.method
        if event_id is None:
            if method == "GET":
                return self._list(request, cal)
            if method == "POST":
                body = json.loads(request.content)
                if any(v is None for k in ("start", "end") for v in (body.get(k) or {}).values()):
                    return self._error(400)
                if body.get("id") in items:
                    return self._error(409)
                return httpx.Response(200, json=self.put(cal, {"status": "confirmed", **body}))
            return self._error(405)
        if method == "GET":
            return httpx.Response(200, json=items[event_id]) if event_id in items else self._error(404)
        if event_id not in items:
            return self._error(404)
        if method == "PATCH":
            if items[event_id].get("status") == "cancelled" and not self.patch_cancelled:
                return self._error(404)
            merged = {**items[event_id], **json.loads(request.content)}
            for key in ("start", "end"):
                both = {**(items[event_id].get(key) or {}), **(merged.get(key) or {})}
                merged[key] = {k: v for k, v in both.items() if v is not None}
                if "date" in merged[key] and "dateTime" in merged[key]:
                    return self._error(400)
            return httpx.Response(200, json=self.put(cal, merged))
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


@pytest.fixture
def push_on(sync, store, fake, conn):
    """``sync`` with push switched on: the write scope granted and ``dest`` (owner, selected) as the
    destination. ``dest`` is in ``fake.calendars`` too, so ``refresh_calendars`` keeps it."""
    from src.events.convert import calendar_row
    from src.events.google import READ_SCOPES, WRITE_SCOPE

    fake.calendars.append({"id": "dest", "summary": "Dest", "accessRole": "owner"})
    store.upsert_google([calendar_row(c) for c in fake.calendars])
    store.set_calendar("dest", selected=True)
    conn.settings.scopes = [*READ_SCOPES, WRITE_SCOPE]
    conn.settings.push = True
    conn.settings.destination = "dest"
    return conn


def state_of(url: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]


# ---- Eventbrite (src/events/eventbrite.py) --------------------------------------------------------------

_row_ids = iter(range(5_000_000_000_000, 6_000_000_000_000))


def row(**over) -> dict:
    """One listing row as Eventbrite's page carries it, valid until a keyword changes it. ``image`` is the
    picture's link; anything else replaces the field of that name."""
    event_id = str(next(_row_ids))
    image = over.pop("image", "https://img.evbuc.com/https%3A%2F%2Fcdn.evbuc.com%2Fimages%2F1%2Foriginal.jpg?s=1")
    made = {"id": event_id, "name": "Made-up Event", "summary": "A made-up summary.", "start_date": "2026-12-05",
            "start_time": "18:00", "end_date": "2026-12-05", "end_time": "20:00", "timezone": "America/New_York",
            "url": f"https://www.eventbrite.com/e/made-up-event-tickets-{event_id}", "is_cancelled": None,
            "is_online_event": False,
            "primary_venue": {"name": "Example Hall",
                              "address": {"city": "Exampleton", "region": "OH",
                                          "localized_address_display": "1 Example Street, Exampleton, OH"}},
            "image": {"original": {"url": image}},
            "tags": [{"prefix": "EventbriteCategory", "tag": "EventbriteCategory/103", "display_name": "Music"},
                     {"prefix": "EventbriteFormat", "tag": "EventbriteFormat/6",
                      "display_name": "Concert or Performance"}]}
    made.update(over)
    return made


def page_with(rows: list, pages: int = 1) -> str:
    data = {"search_data": {"events": {"results": rows, "pagination": {"page_count": pages}}}}
    return f"<html><script>_ = {{}};\nwindow.__SERVER_DATA__ = {json.dumps(data)};\n</script></html>"


class FakeSite:
    """A model of Eventbrite's listing pages for tests. ``this_pages`` and ``next_pages`` are the page counts
    each month offers; every page holds two events of its own and one (``SHARED_ID``) that is on every page.
    ``fail`` maps ``(month, page)`` ("this" or "next") to a status number, an ``httpx.Response`` or an
    exception to raise. ``images`` maps a picture link to the response for it (404 when absent).
    ``requests`` is every request in order and ``sleeps`` every wait the connector asked for.
    ``eventbrite()`` builds the connector over it."""

    SHARED_ID = "7000000000001"

    def __init__(self):
        self.this_pages = 3
        self.next_pages = 2
        self.fail: dict = {}
        self.images: dict[str, httpx.Response] = {}
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def eventbrite(self):
        from src.events.eventbrite import Eventbrite

        return Eventbrite(lambda: self.client, sleep=self.sleep)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "img.evbuc.com":
            outcome = self.images.get(str(request.url), httpx.Response(404))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        month = "next" if "events--next-month" in request.url.path else "this"
        page = int(request.url.params.get("page", "1"))
        if (month, page) in self.fail:
            outcome = self.fail[(month, page)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome if isinstance(outcome, httpx.Response) else httpx.Response(outcome, text="no")
        total = self.next_pages if month == "next" else self.this_pages
        if page > total:
            return httpx.Response(404)
        base = (2 if month == "next" else 1) * 1_000_000_000_000 + page * 10
        rows = [row(id=str(base + 1)), row(id=str(base + 2)), row(id=self.SHARED_ID)]
        return httpx.Response(200, text=page_with(rows, total))


@pytest.fixture
async def fake_site():
    site = FakeSite()
    yield site
    await site.client.aclose()


# ---- FindLocal (src/events/findlocal.py) ---------------------------------------------------------------

def fl_row(**over) -> dict:
    """A full, valid FindLocal API row."""
    base = {
        "id": "0b1c2d3e-0000-4000-8000-000000000001", "title": "Jazz at the Lodge", "description": "An evening of jazz.",
        "event_date": "2026-11-21", "start_time": "19:00", "end_time": "21:00", "end_date": None, "category": "music",
        "status": "scheduled", "attendance": "in_person", "detail_page_url": "https://venue.example/shows/1",
        "ticket_page_url": None, "venue_name": "The Lodge", "venue_address": "1 High St, Columbus, OH", "venue_tba": 0,
        "city": "Columbus", "region": "Short North", "is_deleted": 0, "duplicate_of": None, "is_real_event": 1,
    }
    return {**base, **over}


class FakeFindLocal:
    """A model of FindLocal's events endpoint for tests. ``rows`` is everything it holds; a call returns the page
    asked for. ``fail`` maps a page number to a status number, an ``httpx.Response`` or an exception to raise;
    ``total`` replaces ``meta.total`` when not None. ``requests`` and ``sleeps`` are what the reader did."""

    def __init__(self):
        self.rows: list[dict] = []
        self.fail: dict = {}
        self.total: object | None = None
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def reader(self):
        from src.events.findlocal import FindLocal

        return FindLocal(lambda: self.client, sleep=self.sleep)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = int(request.url.params.get("page", "1"))
        limit = int(request.url.params.get("limit", "100"))
        if page in self.fail:
            outcome = self.fail[page]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome if isinstance(outcome, httpx.Response) else httpx.Response(outcome, text="no")
        total = len(self.rows) if self.total is None else self.total
        body = {"data": self.rows[(page - 1) * limit: page * limit], "meta": {"total": total, "page": page}}
        return httpx.Response(200, json=body)


@pytest.fixture
async def fake_findlocal():
    site = FakeFindLocal()
    yield site
    await site.client.aclose()


# ---- Discovery (src/events/discovery.py) ---------------------------------------------------------------

class RecordingLedger:
    """Keeps what Discovery appends, as dicts of the entry's data (``event`` and the counts)."""

    def __init__(self):
        self.entries: list[dict] = []

    def append(self, kind, bot_id, session_id, **data):
        self.entries.append({"_kind": str(kind), **data})


@pytest.fixture
def discovery(tmp_path, fake_site, fake_findlocal, clock):
    """A Discovery over a fresh vault, switched on for ``eventbrite-city``, reading ``fake_site`` and
    ``fake_findlocal``. ``today`` is 2026-11-18; ``now`` is 09:00:00Z that day plus whatever the ``clock``
    fixture has advanced. The picture fetches' clock is ``clock`` and their waits go to ``fake_site.sleeps``."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from src.events.discover_settings import DiscoverSettings
    from src.events.discovery import Discovery

    vault = tmp_path / "vault"
    vault.mkdir()
    DiscoverSettings(enabled=True, place="oh--exampleton").save(vault)
    base = datetime(2026, 11, 18, 9, 0, 0, tzinfo=timezone.utc)
    start = clock.now
    calendar = SimpleNamespace(http=lambda: fake_site.client)
    d = Discovery(SimpleNamespace(vault_root=vault), RecordingLedger(), calendar,
                  today=lambda: "2026-11-18",
                  now=lambda: (base + timedelta(seconds=clock.now - start)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    d.source = fake_site.eventbrite()
    d.findlocal = fake_findlocal.reader()
    d.sleep = fake_site.sleep   # the wait between picture fetches is recorded, never slept
    d.monotonic = clock
    d.bot_lookup = lambda bot_id: SimpleNamespace(id=bot_id, closed=True)   # the scout is a closed bot
    yield d
    d.store.close()

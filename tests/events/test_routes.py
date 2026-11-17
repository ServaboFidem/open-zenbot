"""CalendarService and the /calendar routes, offline: Google is the FakeGoogle over an httpx.MockTransport."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import pytest

from src.events.google import READ_SCOPES, WRITE_SCOPE
from src.events.service import CalendarService
from src.events.settings import CalendarKeys
from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.memory.exclusion import exclusion
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from tests.conftest import ScriptedProvider
from tests.events.conftest import state_of

EV = {"title": "Dentist", "location": "Main St", "description": "", "all_day": False,
      "start": "2026-11-20T09:00:00Z", "end": "2026-11-20T10:00:00Z", "tz": "Europe/London"}
SECRET = "client-secret-0123456789"
ME = "me@example.com"
READ_ONLY = "This event is read-only here."
LOCKED = "This calendar holds events pushed from here; it stays selected while push is on."
NEEDS_WRITE = "Allow OpenZenBot to add events to Google first."


NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)  # the service's sync clock in these tests


def soon(days: int = 1, hours: int = 0) -> str:
    """An instant ``days`` and ``hours`` after NOW, inside the pull window."""
    return (NOW + timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def item(id: str, title: str = "Standup", **extra) -> dict:
    return {"id": id, "status": "confirmed", "summary": title, "start": {"dateTime": soon(1), "timeZone": "UTC"},
            "end": {"dateTime": soon(1, 1), "timeZone": "UTC"}, **extra}


@pytest.fixture
async def http(fake):
    """The fake's client; ``fake.gate`` (an asyncio.Event) holds every Calendar API request until it is set
    (sign-in and revoke requests go through)."""
    fake.gate = None

    async def handler(request: httpx.Request) -> httpx.Response:
        if fake.gate is not None and request.url.path.startswith("/calendar/v3/"):
            await fake.gate.wait()
        return fake.handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        yield c


@pytest.fixture
def cfg(vault, tmp_path):
    c = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path)
    c.validate()
    return c


@pytest.fixture
async def service(cfg, http):
    s = CalendarService(cfg, Ledger(cfg.vault_root), client=http)
    s.syncer._now = lambda: NOW  # the pull window follows NOW, not the real clock
    yield s
    await s.close()


@pytest.fixture
async def client(cfg, service, fake):
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=service.ledger, catalog=Catalog(), calendar=service)
    app = create_app(h)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        yield c
    if fake.gate is not None:
        fake.gate.set()  # a test that failed while holding Google must not hold the teardown
    await service.settle()


def calendar_list_requests(fake) -> int:
    return sum(1 for r in fake.requests if r.url.path.endswith("/users/me/calendarList"))


async def connect(client, service, fake, *, write=False, calendars=None):
    """Sets credentials (when not set) and signs in as ME; ``ME`` (owner, primary) and ``home`` (reader)."""
    if (await client.get("/calendar/status")).json()["google"]["state"] == "not_set_up":
        r = await client.put("/calendar/google/credentials", json={"client_id": "cid-123456789", "client_secret": SECRET})
        assert r.status_code == 200
    fake.calendars = calendars or [{"id": ME, "summary": "Me", "accessRole": "owner", "primary": True},
                                   {"id": "home", "summary": "Home", "accessRole": "reader"}]
    fake.scope = " ".join(READ_SCOPES + ([WRITE_SCOPE] if write else []))
    url = (await client.post("/calendar/google/connect", json={"write": write})).json()["url"]
    r = await client.get("/calendar/google/callback", params={"state": state_of(url), "code": "the-code"})
    assert r.status_code == 200, r.text
    await service.settle()


async def select(client, service, cid=ME):
    r = await client.put("/calendar/calendars/" + quote(cid, safe=""), json={"selected": True})
    assert r.status_code == 200, r.text
    await service.settle()


async def push_on(client, service, fake, destination=ME):
    await connect(client, service, fake, write=True)
    r = await client.put("/calendar/push", json={"on": True, "destination": destination})
    assert r.status_code == 200, r.text
    await service.settle()


async def events(client):
    r = await client.get("/calendar/events", params={"start": "2026-11-01", "end": "2027-12-05"})
    assert r.status_code == 200, r.text
    return r.json()


# the calendar alone -----------------------------------------------------------


async def test_the_calendar_works_with_no_google(client, fake):
    assert (await client.get("/calendar/status")).json()["google"]["state"] == "not_set_up"
    e = (await client.post("/calendar/events", json=EV)).json()
    assert e["editable"] is True and e["calendar_name"] == "OpenZenBot" and "etag" not in e
    assert [x["id"] for x in (await client.get("/calendar/events", params={"start": "2026-11-01", "end": "2026-12-01"})).json()] == [e["id"]]
    assert (await client.post("/calendar/sync")).status_code == 200
    assert fake.requests == []  # nothing reaches Google until it is connected


async def test_status_shape_with_no_google(client):
    body = (await client.get("/calendar/status")).json()
    assert body == {
        "google": {"state": "not_set_up", "credentials": False, "client_id_hint": "", "account": "", "can_write": False,
                   "redirect_uri": "http://127.0.0.1:8765/calendar/google/callback"},
        "push": {"on": False, "destination": ""},
        "sync": {"running": False, "last": "", "error": "", "lost_edits": 0},
        "counts": {"local": 0, "pulled": 0, "pending": 0}}


async def test_bad_event_is_422_with_the_sentence(client):
    r = await client.post("/calendar/events", json={**EV, "title": "  "})
    assert r.status_code == 422 and r.json()["detail"] == "An event needs a title."
    r = await client.post("/calendar/events", json={**EV, "all_day": "yes"})
    assert r.status_code == 422 and r.json()["detail"] == "All day must be true or false."
    r = await client.post("/calendar/events", json={**EV, "end": EV["start"]})
    assert r.status_code == 422 and r.json()["detail"] == "The event must end after it starts."
    e = (await client.post("/calendar/events", json=EV)).json()
    r = await client.put(f"/calendar/events/{e['id']}", json={**EV, "title": ""})
    assert r.status_code == 422 and r.json()["detail"] == "An event needs a title."


async def test_edit_and_delete_an_event_made_here(client):
    e = (await client.post("/calendar/events", json=EV)).json()
    r = await client.put(f"/calendar/events/{e['id']}", json={**EV, "title": "Dentist, moved"})
    assert r.status_code == 200 and r.json()["title"] == "Dentist, moved" and r.json()["editable"] is True
    assert (await client.delete(f"/calendar/events/{e['id']}")).json() == {"ok": True}
    assert await events(client) == []


async def test_a_missing_event_is_404(client, service):
    assert (await client.put("/calendar/events/nope", json=EV)).status_code == 404
    assert (await client.delete("/calendar/events/nope")).status_code == 404


async def test_a_deleted_event_is_404(client, service):
    e = (await client.post("/calendar/events", json=EV)).json()
    service.store.mark_pushed(e["id"], google_calendar=ME, google_id="g1", etag="", link="")
    service.store.delete(e["id"])  # a tombstone: kept for push
    assert service.store.event(e["id"])["deleted"] is True
    assert (await client.put(f"/calendar/events/{e['id']}", json=EV)).status_code == 404
    assert (await client.delete(f"/calendar/events/{e['id']}")).status_code == 404


@pytest.mark.parametrize("params", [
    {"start": "2026-11-01"},
    {"start": "2026-11-01", "end": "2026-11-01T00:00:00Z"},
    {"start": "2026-11-1", "end": "2026-12-01"},
    {"start": "2026-02-30", "end": "2026-03-01"},
    {"start": "2026-12-01", "end": "2026-11-01"},
    {"start": "2026-01-01", "end": "2027-02-06"},  # 401 days
])
async def test_an_events_range_is_two_day_keys_at_most_400_days_apart(client, params):
    assert (await client.get("/calendar/events", params=params)).status_code == 422


async def test_an_events_range_of_400_days_is_fine(client):
    assert (await client.get("/calendar/events", params={"start": "2026-01-01", "end": "2027-02-05"})).status_code == 200


async def test_the_local_calendar_is_listed_without_a_sync_token(client):
    cals = (await client.get("/calendar/calendars")).json()
    assert [c["id"] for c in cals] == ["local"]
    assert "sync_token" not in cals[0] and cals[0]["locked"] is False and cals[0]["name"] == "OpenZenBot"


async def test_a_missing_calendar_is_404(client):
    assert (await client.put("/calendar/calendars/nope", json={"visible": False})).status_code == 404


async def test_hiding_a_calendar(client):
    r = await client.put("/calendar/calendars/local", json={"visible": False})
    assert r.status_code == 200 and r.json()["visible"] is False and "sync_token" not in r.json()


# credentials and sign-in ------------------------------------------------------


async def test_credentials_need_both_values(client):
    r = await client.put("/calendar/google/credentials", json={"client_id": "cid-123456789", "client_secret": " "})
    assert r.status_code == 422 and r.json()["detail"] == "Both the client id and the client secret are needed."


async def test_credentials_set_and_cleared(client):
    r = await client.put("/calendar/google/credentials", json={"client_id": "cid-123456789", "client_secret": SECRET})
    g = r.json()["google"]
    assert g["state"] == "ready" and g["credentials"] is True and g["client_id_hint"] == "6789"
    assert SECRET not in r.text
    g = (await client.delete("/calendar/google/credentials")).json()["google"]
    assert g["state"] == "not_set_up" and g["credentials"] is False


async def test_connect_needs_a_client(client):
    r = await client.post("/calendar/google/connect", json={})
    assert r.status_code == 422 and r.json()["detail"] == "Set up the Google client first."


async def test_connect_callback_then_status_is_connected(client, service, fake):
    await connect(client, service, fake)
    body = (await client.get("/calendar/status")).json()
    assert body["google"]["state"] == "connected" and body["google"]["account"] == ME
    assert body["google"]["can_write"] is False
    assert body["sync"]["last"] and body["sync"]["error"] == ""  # the callback started a sync
    cals = {c["id"]: c for c in (await client.get("/calendar/calendars")).json()}
    assert set(cals) == {"local", ME, "home"} and cals[ME]["writable"] is True and cals["home"]["writable"] is False


async def test_the_callback_page_on_success(client, service, fake):
    await client.put("/calendar/google/credentials", json={"client_id": "cid-123456789", "client_secret": SECRET})
    fake.calendars = [{"id": ME, "summary": "Me", "accessRole": "owner", "primary": True}]
    fake.scope = " ".join(READ_SCOPES)
    url = (await client.post("/calendar/google/connect", json={"write": False})).json()["url"]
    r = await client.get("/calendar/google/callback", params={"state": state_of(url), "code": "the-code"})
    assert r.status_code == 200
    assert r.text == '<!doctype html><meta charset="utf-8"><title>OpenZenBot</title><p>Connected. You can close this tab.</p>'
    assert r.headers["content-security-policy"] == "default-src 'none'"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert r.headers["content-type"].startswith("text/html")


async def test_callback_reflects_nothing_and_is_closed(client):
    r = await client.get("/calendar/google/callback", params={"state": "<script>x</script>", "code": "c"})
    assert r.status_code == 400 and "<script>" not in r.text and r.headers["content-security-policy"] == "default-src 'none'"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert "This sign-in link is no longer valid." in r.text
    r = await client.get("/calendar/google/callback")
    assert r.status_code == 400 and "no longer valid" in r.text


async def test_a_cancelled_sign_in_reflects_nothing(client, service, fake):
    await client.put("/calendar/google/credentials", json={"client_id": "cid-123456789", "client_secret": SECRET})
    url = (await client.post("/calendar/google/connect", json={})).json()["url"]
    r = await client.get("/calendar/google/callback", params={"state": state_of(url), "error": "<b>access_denied</b>"})
    assert r.status_code == 400 and "<b>" not in r.text and "access_denied" not in r.text
    assert "cancelled" in r.text


async def test_no_secret_in_any_response(client, service, fake):
    fake.put(ME, item("g1"))
    await connect(client, service, fake, write=True)
    await select(client, service)
    await client.post("/calendar/events", json=EV)
    texts = [(await client.get(p)).text for p in ("/calendar/status", "/calendar/calendars")]
    texts.append((await client.get("/calendar/events", params={"start": "2026-01-01", "end": "2027-01-01"})).text)
    texts.append((await client.post("/calendar/sync")).text)
    texts.append((await client.put("/calendar/push", json={"on": True, "destination": ME})).text)
    issued = [f"access-{n}" for n in range(1, fake.access_tokens + 1)]
    assert fake.access_tokens >= 1 and len(json.loads(texts[2])) == 2
    for text in texts:
        for secret in [SECRET, "refresh-1", *issued]:
            assert secret not in text


async def test_a_saved_secret_is_masked_once_the_service_is_built(cfg, http):
    CalendarKeys(client_id="cid-123456789", client_secret=SECRET, refresh_token="saved-refresh-token").save(cfg.vault_root)
    exclusion.clear_values()  # a fresh process: nothing registered yet
    s = CalendarService(cfg, Ledger(cfg.vault_root), client=http)
    try:
        text, reasons = exclusion.redact(f"token saved-refresh-token and {SECRET}")
        assert "saved-refresh-token" not in text and SECRET not in text and "registered-secret" in reasons
    finally:
        await s.close()


async def test_disconnect_keeps_events_made_here(client, service, fake):
    fake.put(ME, item("g1"))
    mine = (await client.post("/calendar/events", json=EV)).json()
    await connect(client, service, fake)
    await select(client, service)
    assert len(await events(client)) == 2
    body = (await client.post("/calendar/google/disconnect")).json()
    assert body["google"]["state"] == "ready" and body["google"]["account"] == ME  # who was last connected
    assert body["push"]["on"] is False
    assert [e["id"] for e in await events(client)] == [mine["id"]]
    assert [c["id"] for c in (await client.get("/calendar/calendars")).json()] == ["local"]


# pulled events ----------------------------------------------------------------


async def test_a_pulled_event_is_read_only_until_push_is_on(client, service, fake):
    fake.put(ME, item("g1"))
    await connect(client, service, fake, write=True)
    await select(client, service)
    [e] = await events(client)
    assert e["origin"] == "google" and e["editable"] is False and e["calendar_name"] == "Me" and "etag" not in e
    r = await client.put(f"/calendar/events/{e['id']}", json={**EV, "title": "Moved"})
    assert r.status_code == 403 and r.json()["detail"] == READ_ONLY
    r = await client.delete(f"/calendar/events/{e['id']}")
    assert r.status_code == 403 and r.json()["detail"] == READ_ONLY
    assert (await client.put("/calendar/push", json={"on": True, "destination": ME})).status_code == 200
    await service.settle()
    [e] = await events(client)
    assert e["editable"] is True
    fake.gate = asyncio.Event()  # hold the push the edit starts, to see the row as the route left it
    r = await client.put(f"/calendar/events/{e['id']}", json={**EV, "title": "Moved"})
    assert r.status_code == 200
    moved = r.json()
    assert moved["title"] == "Moved" and moved["origin"] == "google" and moved["calendar_id"] == ME and moved["dirty"]
    fake.gate.set()
    await service.settle()
    assert fake.events[ME]["g1"]["summary"] == "Moved"


async def test_a_pulled_event_on_a_read_only_calendar_stays_read_only(client, service, fake):
    fake.put("home", item("h1"))
    await push_on(client, service, fake)
    await select(client, service, "home")
    [e] = await events(client)
    assert e["calendar_id"] == "home" and e["editable"] is False
    assert (await client.put(f"/calendar/events/{e['id']}", json=EV)).status_code == 403


async def test_an_occurrence_is_always_read_only(client, service, fake):
    fake.put(ME, item("g1_20261118", recurringEventId="g1"))
    await push_on(client, service, fake)
    await select(client, service)
    [e] = await events(client)
    assert e["series_id"] == "g1" and e["editable"] is False
    r = await client.put(f"/calendar/events/{e['id']}", json=EV)
    assert r.status_code == 403 and r.json()["detail"] == READ_ONLY
    assert (await client.delete(f"/calendar/events/{e['id']}")).status_code == 403


async def test_unselecting_a_calendar_drops_its_pulled_events(client, service, fake):
    fake.put(ME, item("g1"))
    await connect(client, service, fake)
    await select(client, service)
    assert len(await events(client)) == 1
    r = await client.put("/calendar/calendars/" + quote(ME, safe=""), json={"selected": False})
    assert r.status_code == 200 and r.json()["selected"] is False
    await service.settle()
    assert await events(client) == []


async def test_a_calendar_id_with_hash_and_at_works(client, service, fake):
    cid = "en.usa#holiday@group.v.calendar.google.com"
    await connect(client, service, fake, calendars=[{"id": ME, "summary": "Me", "accessRole": "owner", "primary": True},
                                                    {"id": cid, "summary": "Holidays", "accessRole": "reader"}])
    r = await client.put("/calendar/calendars/" + quote(cid, safe=""), json={"selected": True})
    assert r.status_code == 200 and r.json()["id"] == cid


# push -------------------------------------------------------------------------


async def test_push_on_needs_write_access_and_a_destination(client, service, fake):
    await connect(client, service, fake)
    r = await client.put("/calendar/push", json={"on": True, "destination": ME})
    assert r.status_code == 409 and r.json()["detail"] == NEEDS_WRITE
    await connect(client, service, fake, write=True)  # the same account again, now with write access
    assert (await client.put("/calendar/push", json={"on": True, "destination": "home"})).status_code == 422
    assert (await client.put("/calendar/push", json={"on": True})).status_code == 422
    assert (await client.put("/calendar/push", json={"on": True, "destination": "local"})).status_code == 422
    r = await client.put("/calendar/push", json={"on": True, "destination": ME})
    assert r.status_code == 200 and r.json()["push"] == {"on": True, "destination": ME}
    cal = next(c for c in (await client.get("/calendar/calendars")).json() if c["id"] == ME)
    assert cal["selected"] is True and cal["locked"] is True
    r = await client.put("/calendar/push", json={"on": False})
    assert r.status_code == 200 and r.json()["push"]["on"] is False


async def test_push_preview(client, service, fake):
    await connect(client, service, fake, write=True)
    await client.post("/calendar/events", json=EV)
    r = await client.get("/calendar/push/preview", params={"destination": ME})
    assert r.status_code == 200 and r.json() == {"add": 1, "update": 0, "remove": 0}
    assert (await client.get("/calendar/push/preview", params={"destination": "home"})).status_code == 422
    assert (await client.get("/calendar/push/preview", params={"destination": "nope"})).status_code == 422


async def test_a_locked_calendar_cannot_be_unselected(client, service, fake):
    cals = [{"id": ME, "summary": "Me", "accessRole": "owner", "primary": True},
            {"id": "other", "summary": "Other", "accessRole": "writer"}]
    await connect(client, service, fake, write=True, calendars=cals)
    await client.put("/calendar/push", json={"on": True, "destination": ME})
    await service.settle()
    r = await client.put("/calendar/calendars/" + quote(ME, safe=""), json={"selected": False})
    assert r.status_code == 409 and r.json()["detail"] == LOCKED
    await client.post("/calendar/events", json=EV)
    await service.settle()
    assert ME in service.store.pushed_calendars()
    await client.put("/calendar/push", json={"on": True, "destination": "other"})
    await service.settle()
    r = await client.put("/calendar/calendars/" + quote(ME, safe=""), json={"selected": False})
    assert r.status_code == 409 and r.json()["detail"] == LOCKED  # it holds a pushed event
    cal = next(c for c in (await client.get("/calendar/calendars")).json() if c["id"] == ME)
    assert cal["locked"] is True and cal["selected"] is True
    assert (await client.put("/calendar/calendars/" + quote(ME, safe=""), json={"visible": False})).status_code == 200
    await client.put("/calendar/push", json={"on": False})
    r = await client.put("/calendar/calendars/" + quote(ME, safe=""), json={"selected": False})
    assert r.status_code == 200 and r.json()["locked"] is False


async def test_create_with_push_on_reaches_google_without_a_sync_call(client, service, fake):
    await push_on(client, service, fake)
    e = (await client.post("/calendar/events", json=EV)).json()
    await service.settle()
    sent = [i for i in fake.events.get(ME, {}).values() if i["summary"] == "Dentist"]
    assert len(sent) == 1
    assert service.store.event(e["id"])["google_id"] == sent[0]["id"]


async def test_no_kick_without_push(client, service, fake):
    await connect(client, service, fake, write=True)
    before = len(fake.requests)
    await client.post("/calendar/events", json=EV)
    await service.settle()
    assert len(fake.requests) == before


# runs -------------------------------------------------------------------------


async def test_two_sync_requests_share_one_run(client, service, fake):
    await connect(client, service, fake)
    before = calendar_list_requests(fake)
    fake.gate = asyncio.Event()
    first = asyncio.ensure_future(client.post("/calendar/sync"))
    second = asyncio.ensure_future(client.post("/calendar/sync"))
    for _ in range(20):
        await asyncio.sleep(0)
    assert (await client.get("/calendar/status")).json()["sync"]["running"] is True
    fake.gate.set()
    a, b = await asyncio.gather(first, second)
    assert a.status_code == b.status_code == 200
    assert calendar_list_requests(fake) - before == 1
    assert a.json()["sync"]["running"] is False


async def test_a_kick_during_a_run_queues_one_more_run(client, service, fake):
    await push_on(client, service, fake)
    before = calendar_list_requests(fake)
    fake.gate = asyncio.Event()
    running = asyncio.ensure_future(client.post("/calendar/sync"))
    for _ in range(20):
        await asyncio.sleep(0)
    for _ in range(3):  # three changes made while the run waits on Google
        assert (await client.post("/calendar/events", json=EV)).status_code == 200
    fake.gate.set()
    await running
    await service.settle()
    assert calendar_list_requests(fake) - before == 2  # the run, then exactly one more
    assert len([i for i in fake.events[ME].values() if i["summary"] == "Dentist"]) == 3
    assert service.store.counts()["pending"] == 0


async def test_a_failed_sync_keeps_events_and_last_sync(client, service, fake):
    fake.put(ME, item("g1"))
    await connect(client, service, fake)
    await select(client, service)
    last = (await client.get("/calendar/status")).json()["sync"]["last"]
    assert last and len(await events(client)) == 1
    fake.fail["calendarList"] = 500
    body = (await client.post("/calendar/sync")).json()
    assert body["sync"]["error"] == "provider" and body["sync"]["last"] == last and body["sync"]["running"] is False
    assert len(await events(client)) == 1
    del fake.fail["calendarList"]
    body = (await client.post("/calendar/sync")).json()
    assert body["sync"]["error"] == "" and body["google"]["state"] == "connected"


async def test_a_refused_sign_in_shows_needs_reconnect(client, service, fake):
    await connect(client, service, fake)
    service.google.forget()
    fake.refresh_ok = False
    body = (await client.post("/calendar/sync")).json()
    assert body["sync"]["error"] == "auth" and body["google"]["state"] == "needs_reconnect"


async def test_lost_edits_show_in_status(client, service):
    service.store.set_meta("lost_edits", "2")
    assert (await client.get("/calendar/status")).json()["sync"]["lost_edits"] == 2


async def test_disconnect_waits_for_the_run_in_flight(client, service, fake):
    fake.put(ME, item("g1"))
    await connect(client, service, fake)
    await select(client, service)
    fake.gate = asyncio.Event()
    running = asyncio.ensure_future(client.post("/calendar/sync"))
    for _ in range(20):
        await asyncio.sleep(0)
    leaving = asyncio.ensure_future(client.post("/calendar/google/disconnect"))
    for _ in range(20):
        await asyncio.sleep(0)
    assert not leaving.done()
    fake.gate.set()
    await running
    assert (await leaving).json()["google"]["state"] == "ready"
    assert await events(client) == []  # the run could not bring pulled events back after the disconnect


# lifecycle --------------------------------------------------------------------


async def test_the_loop_syncs_only_when_connected(service, fake, client):
    service.every = 0.01
    loop = service.spawn(service.start())
    for _ in range(5):
        await asyncio.sleep(0.02)
    assert fake.requests == []  # not connected: no request
    loop.cancel()
    await connect(client, service, fake)
    before = calendar_list_requests(fake)
    loop = service.spawn(service.start())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if calendar_list_requests(fake) - before >= 2:
            break
    assert calendar_list_requests(fake) - before >= 2
    loop.cancel()


async def test_close_cancels_the_loop_and_closes_the_store(cfg, http, fake):
    s = CalendarService(cfg, Ledger(cfg.vault_root), client=http)
    loop = s.spawn(s.start())
    await asyncio.sleep(0)
    await s.close()
    assert loop.cancelled()
    assert not http.is_closed  # not its own client: the caller closes it


async def test_its_own_client_is_made_lazily_and_closed(cfg):
    s = CalendarService(cfg, Ledger(cfg.vault_root))
    assert s.client is None
    made = s.http()
    assert s.http() is made and made.timeout.read == 30
    await s.close()
    assert made.is_closed and s.client is None


async def test_the_harness_builds_and_serves_the_calendar(cfg):
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(cfg.vault_root), catalog=Catalog())
    assert isinstance(h.calendar, CalendarService) and h.calendar.client is None
    app = create_app(h)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        assert (await c.get("/calendar/status")).json()["google"]["state"] == "not_set_up"
    await h.calendar.close()

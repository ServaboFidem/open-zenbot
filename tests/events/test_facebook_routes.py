"""The Facebook helper intake routes: valid reports keep pool rows, bad shapes
are refused with a reason, and the status carries the last read, the error
and readiness. Fixtures mirror src/helper/helper.test.mjs so the Python
validation agrees with the helper payload schema."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.events import facebook_routes as fr
from src.events.discover_store import DiscoverStore

EVENT_URL = "https://www.facebook.com/events/"
DASHBOARD = "https://www.facebook.com/events/"
INVITES = "https://www.facebook.com/events/invites/"
BIRTHDAYS = "https://www.facebook.com/birthdays/"

GOOD_EVENT = {"kind": "event", "id": "123456789", "title": "Neighborhood cleanup",
              "start_date": "2026-11-29", "start_time": "10:00",
              "url": "https://www.facebook.com/events/123456789/", "venue": ""}
GOOD_INVITE = {"kind": "invite", "id": "987654321", "title": "Housewarming",
               "start_date": "2026-12-05", "start_time": "",
               "url": "https://www.facebook.com/events/987654321/", "inviter": "Sam Rivera"}
GOOD_BIRTHDAY = {"kind": "birthday", "name": "Sam Rivera",
                 "profile_url": "https://www.facebook.com/profile.php?id=100042",
                 "date_text": "November 21"}


def report(corner, url, items, **over):
    made = {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
            "corner": corner, "url": url, "items": items}
    made.update(over)
    return made


class Ledger:
    def __init__(self):
        self.entries: list[dict] = []

    def append(self, kind, bot_id, session_id, **data):
        self.entries.append({"kind": str(kind), **data})


@pytest.fixture
def store(tmp_path):
    s = DiscoverStore(tmp_path / "discover.db")
    yield s
    s.close()


@pytest.fixture
def ledger():
    return Ledger()


@pytest.fixture
def discovery(store, ledger):
    return SimpleNamespace(store=store, ledger=ledger,
                           today=lambda: "2026-11-20",
                           now=lambda: "2026-11-20T12:00:00Z")


@pytest.fixture
async def client(discovery):
    app = FastAPI()
    app.include_router(fr.facebook_router(discovery))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://localhost") as c:
        yield c


def facebook_posts(discovery) -> list[str]:
    router = fr.facebook_router(discovery)
    return sorted(path for r in router.routes for path in [r.path]
                  for m in (getattr(r, "methods", None) or ()) if m == "POST")


# ---- intake: valid reports keep pool rows ------------------------------------


async def test_event_report_keeps_a_pool_row(client, discovery):
    r = await client.post("/intake/facebook",
                          json=report("events_dashboard", DASHBOARD, [GOOD_EVENT]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kept"] == 1 and body["dropped"] == []
    row = discovery.store.pool_row("fb-123456789")
    assert row is not None and row["source"] == "facebook"
    assert (row["name"], row["start_date"], row["start_time"]) == (
        "Neighborhood cleanup", "2026-11-29", "10:00")
    assert row["url"] == "https://www.facebook.com/events/123456789/"


async def test_invite_report_maps_as_an_event_row(client, discovery):
    r = await client.post("/intake/facebook",
                          json=report("invites", INVITES, [GOOD_INVITE]))
    assert r.status_code == 200, r.text
    assert r.json()["kept"] == 1
    row = discovery.store.pool_row("fb-987654321")
    assert row is not None and row["source"] == "facebook" and row["name"] == "Housewarming"


async def test_birthday_report_keeps_the_next_occurrence(client, discovery):
    r = await client.post("/intake/facebook",
                          json=report("birthdays", BIRTHDAYS, [GOOD_BIRTHDAY]))
    assert r.status_code == 200, r.text
    assert r.json()["kept"] == 1
    rows = [discovery.store.pool_row(e) for e in discovery.store.pool_ids("facebook-birthday")]
    assert len(rows) == 1 and rows[0]["start_date"] == "2026-11-21"
    assert rows[0]["name"] == "Sam Rivera" and rows[0]["format"] == "birthday"


async def test_an_empty_observation_keeps_nothing_but_reads(client, discovery):
    r = await client.post("/intake/facebook", json=report("birthdays", BIRTHDAYS, []))
    assert r.status_code == 200 and r.json()["kept"] == 0
    assert (await client.get("/intake/facebook/status")).json()["last_read"] != ""


async def test_a_repeat_report_drops_as_duplicate(client):
    body = report("events_dashboard", DASHBOARD, [GOOD_EVENT])
    assert (await client.post("/intake/facebook", json=body)).json()["kept"] == 1
    again = (await client.post("/intake/facebook", json=body)).json()
    assert again["kept"] == 0 and [d["kind"] for d in again["dropped"]] == ["duplicate"]
    assert again["dropped"][0]["id"] == "fb-123456789"


# ---- intake: bad shapes are refused with a reason ----------------------------


@pytest.mark.parametrize("body", [
    {"helper": "other", "observed_at": "2026-11-20T12:00:00.000Z",
     "corner": "birthdays", "url": BIRTHDAYS, "items": [GOOD_BIRTHDAY]},
    {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
     "corner": "news_feed", "url": BIRTHDAYS, "items": []},
    {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
     "corner": "birthdays", "url": DASHBOARD, "items": [GOOD_BIRTHDAY]},
    {"helper": "fb-events-v1", "observed_at": "sometime",
     "corner": "birthdays", "url": BIRTHDAYS, "items": [GOOD_BIRTHDAY]},
    {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
     "corner": "birthdays", "url": BIRTHDAYS, "items": [GOOD_BIRTHDAY], "extra": 1},
    {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
     "corner": "invites", "url": INVITES, "items": [GOOD_EVENT]},
    {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
     "corner": "events_dashboard", "url": DASHBOARD,
     "items": [{**GOOD_EVENT, "id": "abc"}]},
    {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
     "corner": "events_dashboard", "url": DASHBOARD,
     "items": [{**GOOD_EVENT, "start_date": "2026-02-30"}]},
    {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
     "corner": "events_dashboard", "url": DASHBOARD,
     "items": [{**GOOD_EVENT, "title": "t" * 301}]},
    {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
     "corner": "birthdays", "url": BIRTHDAYS, "items": [{**GOOD_BIRTHDAY, "name": ""}]},
    {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
     "corner": "birthdays", "url": BIRTHDAYS,
     "items": [{**GOOD_BIRTHDAY, "extra": 1}]},
])
async def test_bad_shapes_are_422_with_a_reason_and_keep_nothing(client, discovery, body):
    r = await client.post("/intake/facebook", json=body)
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], str) and r.json()["detail"]
    assert discovery.store.pool_size("facebook") == 0
    assert discovery.store.pool_size("facebook-birthday") == 0


async def test_too_many_items_is_refused(client):
    items = [{**GOOD_EVENT, "id": str(100000 + i),
              "url": f"https://www.facebook.com/events/{100000 + i}/"} for i in range(201)]
    r = await client.post("/intake/facebook",
                          json=report("events_dashboard", DASHBOARD, items))
    assert r.status_code == 422 and "too many" in r.json()["detail"]


async def test_a_refusal_names_no_listing_text(client):
    body = report("events_dashboard", DASHBOARD, [{**GOOD_EVENT, "id": "abc"}])
    r = await client.post("/intake/facebook", json=body)
    assert r.status_code == 422
    assert "Neighborhood cleanup" not in r.text and "123456789" not in r.text


# ---- payload mismatch: schema-valid rows the mapper cannot use --------------


async def test_a_birthday_label_with_no_month_and_day_drops(client, discovery):
    item = {**GOOD_BIRTHDAY, "date_text": "next Tuesday"}
    r = await client.post("/intake/facebook", json=report("birthdays", BIRTHDAYS, [item]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kept"] == 0 and [d["kind"] for d in body["dropped"]] == ["bad_birthday"]
    assert discovery.store.pool_size("facebook-birthday") == 0


async def test_a_non_event_facebook_link_keeps_the_row_without_the_link(client, discovery):
    item = {**GOOD_EVENT, "url": "https://www.facebook.com/profile.php?id=1"}
    r = await client.post("/intake/facebook", json=report("events_dashboard", DASHBOARD, [item]))
    assert r.status_code == 200, r.text
    assert r.json()["kept"] == 1
    assert discovery.store.pool_row("fb-123456789")["url"] == ""


@pytest.mark.parametrize("label, want", [
    ("November 21", (11, 21)), ("Nov 5", (11, 5)), ("5 Dec", (12, 5)),
    ("21 november, 2027", (11, 21)), ("sometime soon", None), ("", None),
    ("13 Smarch", None), (None, None),
])
def test_date_text_month_and_day_parsing(label, want):
    assert fr.parse_date_text(label) == want


# ---- status: last read, error and ready --------------------------------------


async def test_status_before_any_report(client):
    body = (await client.get("/intake/facebook/status")).json()
    assert body == {"last_read": "", "error": "", "ready": True,
                    "pool": {"facebook": 0, "facebook-birthday": 0}}


async def test_status_after_a_good_report(client):
    await client.post("/intake/facebook", json=report("birthdays", BIRTHDAYS, [GOOD_BIRTHDAY]))
    body = (await client.get("/intake/facebook/status")).json()
    assert body["last_read"] == "2026-11-20T12:00:00Z" and body["error"] == ""
    assert body["ready"] is True
    assert body["pool"] == {"facebook": 0, "facebook-birthday": 1}


async def test_a_refusal_sets_the_error_and_keeps_the_last_read(client):
    await client.post("/intake/facebook", json=report("birthdays", BIRTHDAYS, [GOOD_BIRTHDAY]))
    bad = report("events_dashboard", DASHBOARD, [{**GOOD_EVENT, "id": "abc"}])
    assert (await client.post("/intake/facebook", json=bad)).status_code == 422
    body = (await client.get("/intake/facebook/status")).json()
    assert body["error"] != "" and body["last_read"] == "2026-11-20T12:00:00Z"
    good = report("events_dashboard", DASHBOARD, [GOOD_EVENT])
    assert (await client.post("/intake/facebook", json=good)).status_code == 200
    assert (await client.get("/intake/facebook/status")).json()["error"] == ""


# ---- refresh: the on-demand refresh beside the helper's rhythm --------------


async def test_refresh_prunes_past_rows_and_answers_the_status(client, discovery):
    discovery.store.upsert_pool([{"id": "fb-1", "source": "facebook", "name": "Past",
                                  "start_date": "2026-11-01", "start_time": "",
                                  "end_date": "2026-11-01", "end_time": "", "timezone": "",
                                  "venue": "", "address": "", "city": "", "region": "",
                                  "image_url": "", "url": "", "categories": [],
                                  "category_names": [], "format": ""},
                                 {"id": "fb-2", "source": "facebook", "name": "Future",
                                  "start_date": "2026-11-29", "start_time": "",
                                  "end_date": "2026-11-29", "end_time": "", "timezone": "",
                                  "venue": "", "address": "", "city": "", "region": "",
                                  "image_url": "", "url": "", "categories": [],
                                  "category_names": [], "format": ""}], "2026-11-20T12:00:00Z")
    r = await client.post("/intake/facebook/refresh", json={})
    assert r.status_code == 200, r.text
    assert r.json()["ready"] is True
    assert discovery.store.pool_row("fb-1") is None
    assert discovery.store.pool_row("fb-2") is not None


# ---- harness rules: the POST-body rule, no paths, no listing text -----------


async def test_every_post_needs_a_json_body(client, discovery):
    assert {"/intake/facebook", "/intake/facebook/refresh"} <= set(facebook_posts(discovery))
    foreign = {"Origin": "https://evil.example"}
    full = json.dumps(report("events_dashboard", DASHBOARD, [GOOD_EVENT])).encode()
    for path in ("/intake/facebook", "/intake/facebook/refresh"):
        for body, headers in ((b"", foreign), (b"{}", foreign),
                              (full, {**foreign, "Content-Type": "text/plain"}),
                              (full, {**foreign, "Content-Type": "application/x-www-form-urlencoded"})):
            r = await client.post(path, content=body, headers=headers)
            assert r.status_code == 422, (path, body, headers, r.status_code)
    assert discovery.store.pool_size("facebook") == 0
    assert (await client.get("/intake/facebook/status")).json()["last_read"] == ""


async def test_no_response_carries_a_disk_path(client, tmp_path):
    await client.post("/intake/facebook", json=report("birthdays", BIRTHDAYS, [GOOD_BIRTHDAY]))
    bodies = [(await client.post("/intake/facebook",
                                 json=report("events_dashboard", DASHBOARD, [GOOD_EVENT]))).text,
              (await client.get("/intake/facebook/status")).text,
              (await client.post("/intake/facebook/refresh", json={})).text]
    root = str(tmp_path)
    for body in bodies:
        assert root not in body and "discover-images" not in body and "discover.db" not in body


async def test_the_ledger_carries_counts_never_listing_text(client, discovery):
    await client.post("/intake/facebook", json=report("birthdays", BIRTHDAYS, [GOOD_BIRTHDAY]))
    await client.post("/intake/facebook",
                      json=report("birthdays", BIRTHDAYS,
                                  [{**GOOD_BIRTHDAY, "name": "Pat Lee", "date_text": "someday"}]))
    assert len(discovery.ledger.entries) == 2
    text = json.dumps(discovery.ledger.entries)
    assert "Sam Rivera" not in text and "Pat Lee" not in text and "November 21" not in text
    assert discovery.ledger.entries[0]["event"] == "facebook_intake"
    assert (discovery.ledger.entries[0]["kept"], discovery.ledger.entries[0]["dropped"]) == (1, 0)
    assert (discovery.ledger.entries[1]["kept"], discovery.ledger.entries[1]["dropped"]) == (0, 1)


# ---- payload agreement with src/helper/ --------------------------------------


def _helper_src(name: str) -> str:
    return (Path(__file__).resolve().parent.parent.parent / "src" / "helper" / name).read_text()


def test_helper_and_harness_agree_on_the_contract():
    schema = _helper_src("schema.js")
    assert fr.HELPER_ID in schema and str(fr.MAX_ITEMS) in schema
    corners = _helper_src("corners.js")
    assert sorted(fr.CORNER_IDS) == ["birthdays", "events_dashboard", "invites"]
    for corner in fr.CORNERS:
        for path in corner["paths"]:
            assert f'"{path}"' in corners, path
    sender = _helper_src("sender.js")
    assert "/intake/facebook" in sender
    assert fr.validate_report(report("events_dashboard", DASHBOARD, [GOOD_EVENT])) is not None
    assert fr.validate_report(report("invites", INVITES, [GOOD_INVITE])) is not None
    assert fr.validate_report(report("birthdays", BIRTHDAYS, [GOOD_BIRTHDAY])) is not None


# ---- lists: what the Facebook tab shows --------------------------------------


class People:
    """A stand-in registry: the named people are close friends."""

    def __init__(self, *close):
        self.close = {n.casefold() for n in close}

    def find(self, name):
        if name.casefold() not in self.close:
            return None
        return SimpleNamespace(is_owner=False, tier="close")


class Calendar:
    def __init__(self, events):
        self.store = SimpleNamespace(event=lambda id: events.get(id))


async def lists_client(discovery, harness=None):
    app = FastAPI()
    app.include_router(fr.facebook_router(discovery, harness))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def fill(client):
    for corner, url, item in (("events_dashboard", DASHBOARD, GOOD_EVENT), ("invites", INVITES, GOOD_INVITE),
                              ("birthdays", BIRTHDAYS, GOOD_BIRTHDAY)):
        r = await client.post("/intake/facebook", json=report(corner, url, [item]))
        assert r.status_code == 200, r.text


async def test_lists_are_empty_before_any_report(client):
    r = await client.get("/intake/facebook/lists")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["invites"], body["approvals"], body["birthdays"]) == ([], [], [])
    assert body["status"] == (await client.get("/intake/facebook/status")).json()


async def test_lists_answer_the_pool_rows_the_helper_read(client):
    await fill(client)
    body = (await client.get("/intake/facebook/lists")).json()
    assert [e["id"] for e in body["invites"]] == ["fb-123456789", "fb-987654321"]  # date order
    first = body["invites"][0]
    assert set(first) == set(fr.ROW) | {"state"}
    assert (first["name"], first["start_date"], first["start_time"], first["state"]) == (
        "Neighborhood cleanup", "2026-11-29", "10:00", "")
    assert first["url"] == "https://www.facebook.com/events/123456789/"
    assert body["approvals"] == []
    assert body["birthdays"] == [{"id": "fb-bday-100042", "name": "Sam Rivera", "date": "2026-11-21",
                                  "start_date": "2026-11-21", "daysUntil": 1}]
    assert body["status"]["ready"] is True and body["status"]["last_read"] == "2026-11-20T12:00:00Z"


async def test_lists_keep_to_the_facebook_sources_and_to_rows_not_past(client, discovery):
    await fill(client)
    base = {"start_time": "", "end_date": "", "end_time": "", "url": ""}
    discovery.store.upsert_pool([
        {**base, "id": "111", "source": "eventbrite", "name": "Elsewhere", "start_date": "2026-11-30"},
        {**base, "id": "fb-1", "source": "facebook", "name": "Gone by", "start_date": "2026-11-19"},
        {**base, "id": "fb-bday-far", "source": "facebook-birthday", "name": "Far Off",
         "start_date": "2026-11-28", "end_date": "2026-11-28"},
        {**base, "id": "fb-bday-week", "source": "facebook-birthday", "name": "Week Out",
         "start_date": "2026-11-27", "end_date": "2026-11-27"}], "2026-11-20T12:00:00Z")
    body = (await client.get("/intake/facebook/lists")).json()
    assert [e["id"] for e in body["invites"]] == ["fb-123456789", "fb-987654321"]
    assert [(b["id"], b["daysUntil"]) for b in body["birthdays"]] == [("fb-bday-100042", 1), ("fb-bday-week", 7)]


async def test_an_invite_from_a_close_friend_waits_for_approval(discovery, ledger):
    harness = SimpleNamespace(profile=SimpleNamespace(people=People("Sam Rivera")))
    async with await lists_client(discovery, harness) as c:
        await fill(c)
        r = await c.get("/intake/facebook/lists")
    body = r.json()
    assert [e["id"] for e in body["invites"]] == ["fb-123456789"]
    assert [(e["id"], e["why"]) for e in body["approvals"]] == [("fb-987654321", fr.WHY["personal"])]
    # the inviter is read for the friend check and goes nowhere else
    assert "Sam Rivera" not in json.dumps(body["approvals"]) and "inviter" not in r.text
    assert "Sam Rivera" not in json.dumps(ledger.entries)
    assert "inviter" not in discovery.store.pool_row("fb-987654321")


async def test_an_invite_from_anyone_else_stays_an_invite(discovery):
    for harness in (SimpleNamespace(profile=SimpleNamespace(people=People("Pat Lee"))),
                    SimpleNamespace(profile=None), None):
        async with await lists_client(discovery, harness) as c:
            await fill(c)
            body = (await c.get("/intake/facebook/lists")).json()
        assert body["approvals"] == [] and len(body["invites"]) == 2


async def test_a_repeat_invite_keeps_its_inviter_up_to_date(client, discovery):
    await fill(client)
    again = {**GOOD_INVITE, "inviter": "Pat Lee"}
    r = await client.post("/intake/facebook", json=report("invites", INVITES, [again]))
    assert r.json()["kept"] == 0  # the row is a duplicate, the inviter is still read
    assert discovery.store.fb_marks()["fb-987654321"]["inviter"] == "Pat Lee"
    # an event that is no Facebook pool row gets no mark
    other = {**GOOD_INVITE, "id": "555", "title": "", "url": "https://www.facebook.com/events/555/"}
    assert (await client.post("/intake/facebook", json=report("invites", INVITES, [other]))).status_code == 422
    assert set(discovery.store.fb_marks()) == {"fb-987654321"}


# ---- dismiss: a Facebook row leaves the tab's lists ---------------------------


async def test_dismiss_hides_a_row_and_changes_nothing_else(discovery):
    harness = SimpleNamespace(profile=SimpleNamespace(people=People("Sam Rivera")))
    async with await lists_client(discovery, harness) as c:
        await fill(c)
        r = await c.post("/intake/facebook/events/fb-987654321/dismiss", json={})
        assert r.status_code == 200 and r.json() == {"id": "fb-987654321", "state": "dismissed"}
        body = (await c.get("/intake/facebook/lists")).json()
        assert body["approvals"] == [] and [e["id"] for e in body["invites"]] == ["fb-123456789"]
        assert discovery.store.pool_row("fb-987654321") is not None
        # a later report of the same invite does not bring it back
        await c.post("/intake/facebook", json=report("invites", INVITES, [GOOD_INVITE]))
        assert (await c.get("/intake/facebook/lists")).json()["approvals"] == []


async def test_dismiss_is_only_for_a_facebook_event_row(client, discovery):
    await fill(client)
    discovery.store.upsert_pool([{"id": "111", "source": "eventbrite", "name": "Elsewhere",
                                  "start_date": "2026-11-30"}], "2026-11-20T12:00:00Z")
    for event_id in ("111", "fb-bday-100042", "fb-0", "nope"):
        r = await client.post(f"/intake/facebook/events/{event_id}/dismiss", json={})
        assert r.status_code == 404 and r.json()["detail"] == "No such event.", event_id
    assert discovery.store.fb_marks() == {"fb-987654321": {"inviter": "Sam Rivera", "dismissed": False}}


async def test_refresh_forgets_the_marks_of_rows_that_left_the_pool(client, discovery):
    await fill(client)
    discovery.today = lambda: "2026-12-10"
    app = FastAPI()
    app.include_router(fr.facebook_router(discovery))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        assert (await c.post("/intake/facebook/refresh", json={})).status_code == 200
    assert discovery.store.fb_marks() == {}


# ---- the machine's time zone ---------------------------------------------------


async def test_a_report_with_a_zone_puts_it_on_the_facebook_event_rows(client, discovery):
    await fill(client)  # read with no zone
    discovery.store.upsert_pool([{"id": "111", "source": "eventbrite", "name": "Elsewhere",
                                  "start_date": "2026-11-30", "timezone": "Europe/Dublin"}], "2026-11-20T12:00:00Z")
    assert discovery.store.pool_row("fb-123456789")["timezone"] == ""
    new = {**GOOD_EVENT, "id": "42", "title": "Late show", "url": "https://www.facebook.com/events/42/"}
    r = await client.post("/intake/facebook", json=report("events_dashboard", DASHBOARD, [new],
                                                          timezone="America/New_York"))
    assert r.status_code == 200, r.text
    for event_id in ("fb-42", "fb-123456789", "fb-987654321"):  # the new row and the ones read before
        assert discovery.store.pool_row(event_id)["timezone"] == "America/New_York", event_id
    assert discovery.store.pool_row("111")["timezone"] == "Europe/Dublin"
    assert discovery.store.pool_row("fb-bday-100042")["timezone"] == ""
    # a report with none changes no zone
    await client.post("/intake/facebook", json=report("events_dashboard", DASHBOARD, []))
    assert discovery.store.pool_row("fb-42")["timezone"] == "America/New_York"


@pytest.mark.parametrize("zone", ["", "not a zone", "x" * 65, 5, ["UTC"], "America/New_York\n"])
async def test_a_zone_of_the_wrong_shape_is_refused(client, discovery, zone):
    r = await client.post("/intake/facebook", json=report("events_dashboard", DASHBOARD, [GOOD_EVENT], timezone=zone))
    assert r.status_code == 422 and "timezone" in r.json()["detail"]
    assert discovery.store.pool_size("facebook") == 0


def test_the_helper_sends_the_browsers_zone():
    schema, background = _helper_src("schema.js"), _helper_src("background.js")
    assert '"timezone"' in schema and "resolvedOptions().timeZone" in schema
    assert "timezone: zone" in background and "machineZone()" in background


async def test_a_row_with_a_live_calendar_event_is_added(client, discovery):
    await fill(client)
    discovery.store.mark_event_added("fb-987654321", "cal-1", "2026-11-20T12:00:00Z")
    discovery.store.mark_event_added("fb-123456789", "cal-2", "2026-11-20T12:00:00Z")
    discovery.calendar = Calendar({"cal-1": {"id": "cal-1", "deleted": 0}, "cal-2": {"id": "cal-2", "deleted": 1}})
    body = (await client.get("/intake/facebook/lists")).json()
    assert {e["id"]: e["state"] for e in body["invites"]} == {"fb-987654321": "added", "fb-123456789": ""}


async def test_lists_write_nothing_and_carry_no_disk_path(client, discovery, ledger, tmp_path):
    await fill(client)
    before = (len(ledger.entries), discovery.store.pool_size(), discovery.store.meta(fr.FB_LAST_READ))
    text = (await client.get("/intake/facebook/lists")).text
    assert (len(ledger.entries), discovery.store.pool_size(), discovery.store.meta(fr.FB_LAST_READ)) == before
    assert str(tmp_path) not in text and "discover.db" not in text and "etag" not in text


async def test_the_lists_route_is_a_get(discovery):
    assert facebook_posts(discovery) == ["/intake/facebook", "/intake/facebook/events/{event_id}/dismiss",
                                         "/intake/facebook/refresh"]
    router = fr.facebook_router(discovery)
    assert [sorted(r.methods) for r in router.routes if r.path == "/intake/facebook/lists"] == [["GET"]]

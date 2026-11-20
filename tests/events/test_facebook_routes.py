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

"""The shared calendar-add helper: one lock, one builder, one live event per pool event."""

import asyncio

import httpx
import pytest
from fastapi import HTTPException

from src.events import calendar_add, discover_routes
from src.events.service import CalendarService
from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from tests.conftest import ScriptedProvider

TIMES = {"start": "2026-11-22T15:00:00Z", "end": "2026-11-22T21:00:00Z"}


def listing(id, **over):
    made = {"id": id, "name": f"Event {id}", "summary": "A summary.", "start_date": "2026-11-22",
            "start_time": "10:00", "end_date": "2026-11-22", "end_time": "16:00", "timezone": "America/New_York",
            "venue": "Example Hall", "address": "1 Example Street", "city": "Columbus", "region": "OH",
            "image_url": "", "url": f"https://www.eventbrite.com/e/x-{id}", "format": "Concert",
            "categories": ["EventbriteCategory/103"], "category_names": ["Music"]}
    made.update(over)
    return made


class Body:
    def __init__(self, start: str, end: str) -> None:
        self.start = start
        self.end = end


@pytest.fixture
def cfg(vault, tmp_path):
    c = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path)
    c.validate()
    return c


@pytest.fixture
async def service(cfg):
    s = CalendarService(cfg, Ledger(cfg.vault_root))
    yield s
    await s.close()


@pytest.fixture
def harness(cfg, service):
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=service.ledger, catalog=Catalog(), calendar=service)
    h.discover.today = lambda: "2026-11-18"
    return h


@pytest.fixture
async def client(harness):
    app = create_app(harness)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        yield c


def make_card(harness, id, **over) -> dict:
    harness.discover.store.save_digest([(listing(id, **over), "why")], kind="weekly", note="", session_id=f"s{id}",
                                       created_at="2026-11-17T00:00:00Z")
    return next(
        c for d in harness.discover.store.digests(None, 50) for c in d["cards"] if c["event_id"] == id)


def test_both_paths_import_one_lock_and_one_builder():
    assert discover_routes.add_lock is calendar_add.add_lock
    assert discover_routes.make_event is calendar_add.make_event
    assert discover_routes.listing_times is calendar_add.listing_times


def test_builder_refuses_past_listings(harness):
    past = listing("9300000000001", start_date="2026-11-01", end_date="2026-11-01")
    with pytest.raises(HTTPException) as e:
        calendar_add.make_event(harness.discover, past, Body(TIMES["start"], TIMES["end"]))
    assert (e.value.status_code, e.value.detail) == (409, calendar_add.PAST)


def test_builder_refuses_times_that_are_not_the_listings(harness):
    row = listing("9300000000002")
    with pytest.raises(HTTPException) as e:
        calendar_add.make_event(harness.discover, row, Body("2026-11-23T15:00:01Z", "2026-11-23T21:00:00Z"))
    assert e.value.status_code == 422
    zoneless = listing("9300000000003", timezone="")
    with pytest.raises(HTTPException) as e:
        calendar_add.make_event(harness.discover, zoneless, Body(TIMES["start"], TIMES["end"]))
    assert (e.value.status_code, e.value.detail) == (422, calendar_add.NO_ZONE)


async def test_builder_makes_one_event_directly(harness):
    row = listing("9300000000004")
    made = calendar_add.make_event(harness.discover, row, Body(TIMES["start"], TIMES["end"]))
    assert (made["title"], made["tz"]) == (row["name"], "America/New_York")
    assert (made["start"], made["end"]) == (TIMES["start"], TIMES["end"])


async def test_card_add_then_pool_add_share_one_live_event(client, harness):
    harness.discover.store.upsert_pool([listing("9400000000001")], "2026-11-18T09:00:00Z")
    card = make_card(harness, "9400000000001")
    first = (await client.post(f"/discover/cards/{card['id']}/add", json=TIMES)).json()["event"]
    second = (await client.post("/discover/events/9400000000001/add", json=TIMES)).json()["calendar_event"]
    assert first["id"] == second["id"]
    r = await client.get("/calendar/events", params={"start": "2026-11-01", "end": "2027-12-05"})
    assert [e["id"] for e in r.json()] == [first["id"]]


async def test_dismissed_card_still_shares_the_live_event(client, harness):
    harness.discover.store.upsert_pool([listing("9400000000002")], "2026-11-18T09:00:00Z")
    card = make_card(harness, "9400000000002")
    assert (await client.post(f"/discover/cards/{card['id']}/dismiss", json={})).status_code == 200
    first = (await client.post("/discover/events/9400000000002/add", json=TIMES)).json()["calendar_event"]
    again = (await client.post(f"/discover/cards/{card['id']}/add", json=TIMES)).json()["event"]
    assert again["id"] == first["id"]
    r = await client.get("/calendar/events", params={"start": "2026-11-01", "end": "2027-12-05"})
    assert [e["id"] for e in r.json()] == [first["id"]]


async def test_concurrent_card_adds_make_one_event(client, harness):
    card = make_card(harness, "9400000000003")
    a, b = await asyncio.gather(*(client.post(f"/discover/cards/{card['id']}/add", json=TIMES) for _ in range(2)))
    assert a.status_code == b.status_code == 200
    assert a.json()["event"]["id"] == b.json()["event"]["id"]
    r = await client.get("/calendar/events", params={"start": "2026-11-01", "end": "2027-12-05"})
    assert len(r.json()) == 1

"""The Facebook intake router mounts beside the calendar and discovery routers.

Intake posts land (a good report keeps its rows) and status reads land, while
the existing routers stay where they were.
"""

import httpx
import pytest

from src.events.service import CalendarService
from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from tests.conftest import ScriptedProvider

EVENT_REPORT = {
    "helper": "fb-events-v1",
    "observed_at": "2026-11-20T12:00:00+00:00",
    "corner": "events_dashboard",
    "url": "https://www.facebook.com/events/",
    "items": [
        {
            "kind": "event",
            "id": "123456",
            "title": "Barn Dance",
            "start_date": "2026-11-25",
            "start_time": "19:00",
            "url": "https://www.facebook.com/events/123456",
            "venue": "Grange Hall",
        },
    ],
}


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
    h = Harness(
        config=cfg,
        provider=ScriptedProvider(),
        ledger=service.ledger,
        catalog=Catalog(),
        calendar=service,
    )
    h.discover.today = lambda: "2026-11-18"
    return h


@pytest.fixture
async def client(harness):
    app = create_app(harness)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as c:
        yield c


async def test_facebook_routes_are_mounted(client):
    # FastAPI 0.163 keeps included routers lazy in app.routes, so the mount
    # is proven through the served OpenAPI table, beside the existing routers.
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/intake/facebook" in paths
    assert "/intake/facebook/status" in paths
    assert "/intake/facebook/refresh" in paths
    assert "/discover/status" in paths and "/calendar/status" in paths


async def test_intake_post_lands(client):
    r = await client.post("/intake/facebook", json=EVENT_REPORT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kept"] == 1 and body["dropped"] == []
    assert body["pool"]["facebook"] == 1


async def test_status_read_lands(client):
    r = await client.get("/intake/facebook/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert set(body["pool"]) == {"facebook", "facebook-birthday"}


async def test_existing_routers_stay_put(client):
    r = await client.get("/discover/status")
    assert r.status_code == 200
    r = await client.get("/calendar/status")
    assert r.status_code == 200


async def test_lists_show_a_report_and_an_add_through_the_pool_route(client, harness):
    # the tab's whole path on the real app: the helper's report with the machine's zone, the lists, Add
    # through the existing pool route, Dismiss through the Facebook one
    zoned = {**EVENT_REPORT, "timezone": "America/New_York"}
    r = await client.post("/intake/facebook", json=zoned)
    assert r.status_code == 200, r.text
    body = (await client.get("/intake/facebook/lists")).json()
    assert [(e["id"], e["state"], e["timezone"]) for e in body["invites"]] == [("fb-123456", "", "America/New_York")]
    assert body["approvals"] == [] and body["birthdays"] == []
    times = {"start": "2026-11-26T00:00:00Z", "end": "2026-11-26T01:00:00Z"}  # 19:00 in New York
    first = await client.post("/discover/events/fb-123456/add", json=times)
    assert first.status_code == 200, first.text
    assert first.json()["calendar_event"]["tz"] == "America/New_York"
    again = await client.post("/discover/events/fb-123456/add", json=times)
    assert again.json()["calendar_event"]["id"] == first.json()["calendar_event"]["id"]
    body = (await client.get("/intake/facebook/lists")).json()
    assert [(e["id"], e["state"]) for e in body["invites"]] == [("fb-123456", "added")]
    r = await client.post("/intake/facebook/events/fb-123456/dismiss", json={})
    assert r.status_code == 200, r.text
    assert (await client.get("/intake/facebook/lists")).json()["invites"] == []


async def test_a_timed_row_read_with_no_zone_cannot_be_added(client):
    assert (await client.post("/intake/facebook", json=EVENT_REPORT)).status_code == 200
    r = await client.post("/discover/events/fb-123456/add",
                          json={"start": "2026-11-26T00:00:00Z", "end": "2026-11-26T01:00:00Z"})
    assert r.status_code == 422 and "time zone" in r.json()["detail"]

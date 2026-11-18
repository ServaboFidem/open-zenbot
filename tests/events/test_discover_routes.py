"""The /discover routes, offline. The calendar is the real CalendarService with no Google connected."""

import asyncio
import json

import httpx
import pytest

from src.events.discover_settings import BAD_URL
from src.events.service import CalendarService
from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.providers.interface import UnboundProvider
from tests.conftest import ScriptedProvider

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40
TIMES = {"start": "2026-11-22T15:00:00Z", "end": "2026-11-22T21:00:00Z"}
ADD = "/discover/cards/{}/add"
SET_UP = {"enabled": True, "source_url": "https://www.eventbrite.com/d/oh--columbus/events/", "city": "Columbus"}


def listing(id, **over):
    made = {"id": id, "name": f"Event {id}", "summary": "A summary.", "start_date": "2026-11-22",
            "start_time": "10:00", "end_date": "2026-11-22", "end_time": "16:00", "timezone": "America/New_York",
            "venue": "Example Hall", "address": "1 Example Street", "city": "Columbus", "region": "OH",
            "image_url": "", "url": f"https://www.eventbrite.com/e/x-{id}", "format": "Concert",
            "categories": ["EventbriteCategory/103"], "category_names": ["Music"]}
    made.update(over)
    return made


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


@pytest.fixture
def card(harness):
    return make_card(harness, "9000000000001")


@pytest.fixture
def all_day_card(harness):
    return make_card(harness, "9000000000002", start_time="", end_time="", start_date="2026-11-22",
                     end_date="2026-11-24")


@pytest.fixture
def no_end_card(harness):
    return make_card(harness, "9000000000003", end_date="", end_time="")


@pytest.fixture
def past_card(harness):
    return make_card(harness, "9000000000004", start_date="2026-11-01", end_date="2026-11-01")


async def calendar_events(client):
    r = await client.get("/calendar/events", params={"start": "2026-11-01", "end": "2027-12-05"})
    assert r.status_code == 200
    return r.json()


async def test_status_with_nothing_set_up(client):
    r = await client.get("/discover/status")
    assert r.status_code == 200
    s = r.json()
    assert s["set_up"] is False and s["routine"] is None and s["running"] == ""
    assert s["refresh"] == {"last": "", "error": "", "pool": 0}
    assert s["settings"]["enabled"] is False
    assert {"tag": "EventbriteCategory/103", "name": "Music"} in s["categories"]


async def test_settings_save_and_a_bad_url_is_422_with_the_sentence(client, harness):
    r = await client.put("/discover/settings", json=SET_UP)
    assert r.status_code == 200, r.text
    assert r.json()["set_up"] is True and r.json()["settings"]["place"] == "oh--columbus"
    assert harness.discover.settings.place == "oh--columbus"
    assert r.json()["routine"] is not None
    bad = await client.put("/discover/settings", json={"enabled": True, "source_url": "https://example.com/x"})
    assert bad.status_code == 422 and bad.json()["detail"] == BAD_URL
    assert harness.discover.settings.place == "oh--columbus"


async def test_run_refusals_and_start(client, harness):
    r = await client.post("/discover/run", json={})
    assert (r.status_code, r.json()["detail"]) == (409, "Set up event discovery first.")
    await client.put("/discover/settings", json={**SET_UP, "interests": "jazz"})
    r = await client.post("/discover/run", json={})
    assert r.status_code == 409 and "does not exist" in r.json()["detail"]
    harness.add_bot(Bot(name="Scout", role="Events", id="event-scout"))
    harness.provider = UnboundProvider()
    r = await client.post("/discover/run", json={})
    assert (r.status_code, r.json()["detail"]) == (503, "no provider is bound")
    harness.provider = ScriptedProvider()
    seen = {}

    class S:
        id = "sess1"
        state = None

    def fake(bot, task, **kw):
        seen.update(bot=bot.id, task=task, **kw)
        return S()

    harness.start_task = fake
    harness.discover.running_run = lambda sessions: "busy"
    r = await client.post("/discover/run", json={})
    assert (r.status_code, r.json()["detail"]) == (409, "The event scout is already looking.")
    harness.discover.running_run = lambda sessions: ""
    r = await client.post("/discover/run", json={})
    assert r.json() == {"session_id": "sess1"}
    assert seen["bot"] == "event-scout" and seen["pinned_skills"] == ["events-digest"]
    assert seen["in_conversation"] is False and seen["recall_query"] == "jazz"
    assert seen["task"] == ("On-demand picks: find local events for the user. "
                            "Save them with events_save_digest using kind \"on_demand\".")
    assert "sess1" in harness.discover.on_demand_sessions


async def test_digests_carry_an_image_url_not_has_image(client, harness, card):
    harness.discover.store.set_image(card["id"], True)
    [d] = (await client.get("/discover/digests")).json()
    [c] = d["cards"]
    assert c["image"] == f"/discover/images/{card['id']}" and "has_image" not in c
    got = (await client.get(f"/discover/digests/{d['id']}")).json()
    assert got["cards"][0]["image"] == c["image"]
    assert (await client.get("/discover/digests/nope")).status_code == 404


async def test_add_makes_one_calendar_event_from_the_snapshot(client, card):
    r = await client.post(ADD.format(card["id"]), json=TIMES)
    assert r.status_code == 200, r.text
    ev = r.json()["event"]
    assert (ev["title"], ev["tz"], ev["all_day"]) == (card["name"], "America/New_York", False)
    assert (ev["start"], ev["end"]) == (TIMES["start"], TIMES["end"])
    assert ev["location"] == f'{card["venue"]}, {card["address"]}'
    assert ev["description"] == f'{card["summary"]}\n\n{card["url"]}'
    assert r.json()["card"]["state"] == "added" and r.json()["card"]["calendar_event_id"] == ev["id"]
    assert "etag" not in ev and len(await calendar_events(client)) == 1


async def test_a_card_without_a_summary_or_venue_uses_what_it_has(client, harness):
    c = make_card(harness, "9000000000010", summary="", venue="", address="2 Side Street")
    ev = (await client.post(ADD.format(c["id"]), json=TIMES)).json()["event"]
    assert ev["location"] == "2 Side Street" and ev["description"] == c["url"]


async def test_adding_twice_makes_one_event(client, card):
    a, b = await asyncio.gather(*(client.post(ADD.format(card["id"]), json=TIMES) for _ in range(2)))
    assert a.status_code == b.status_code == 200
    assert a.json()["event"]["id"] == b.json()["event"]["id"] and len(await calendar_events(client)) == 1


@pytest.mark.parametrize("start, end", [
    ("2026-11-23T15:00:01Z", "2026-11-23T21:00:00Z"),      # the start is more than 14 hours from the listing's
    ("2026-11-22T15:00:00Z", "2026-11-23T21:00:00Z"),      # the end is
    ("2026-11-22", "2026-11-23"), ("garbage", "2026-11-22T21:00:00Z"),
    ("2026-11-22T21:00:00Z", "2026-11-22T15:00:00Z"),      # the end is before the start
    ("2026-11-22T15:00:00Z", "2026-11-22T15:00:00Z"),      # the end is not after the start
])
async def test_add_refuses_times_that_are_not_the_listings(client, card, start, end):
    assert (await client.post(ADD.format(card["id"]), json={"start": start, "end": end})).status_code == 422
    assert await calendar_events(client) == []
    assert (await client.get("/discover/digests")).json()[0]["cards"][0]["state"] == "new"


async def test_add_accepts_the_edge_of_fourteen_hours(client, card):
    r = await client.post(ADD.format(card["id"]), json={"start": "2026-11-22T00:00:00Z", "end": "2026-11-22T06:00:00Z"})
    assert r.status_code == 200, r.text


async def test_add_all_day_takes_exactly_the_listings_dates(client, all_day_card):
    url = ADD.format(all_day_card["id"])
    for start, end in (("2026-11-22", "2026-11-24"), ("2026-11-23", "2026-11-25"), ("2026-11-22T00:00:00Z", "2026-11-25")):
        assert (await client.post(url, json={"start": start, "end": end})).status_code == 422
    assert await calendar_events(client) == []
    r = await client.post(url, json={"start": "2026-11-22", "end": "2026-11-25"})   # 22nd to the 24th, end exclusive
    assert r.status_code == 200, r.text
    ev = r.json()["event"]
    assert (ev["all_day"], ev["start"], ev["end"]) == (True, "2026-11-22", "2026-11-25")


async def test_add_with_no_listed_end_takes_one_hour(client, no_end_card):
    url = ADD.format(no_end_card["id"])
    for end in ("2026-11-22T15:30:00Z", "2026-11-22T17:00:00Z"):
        assert (await client.post(url, json={"start": "2026-11-22T15:00:00Z", "end": end})).status_code == 422
    assert await calendar_events(client) == []
    r = await client.post(url, json={"start": "2026-11-22T15:00:00Z", "end": "2026-11-22T16:00:00Z"})
    assert r.status_code == 200 and r.json()["event"]["end"] == "2026-11-22T16:00:00Z"


async def test_a_listing_with_no_zone_is_refused_with_the_stores_sentence(client, harness):
    c = make_card(harness, "9000000000011", timezone="")
    r = await client.post(ADD.format(c["id"]), json=TIMES)
    assert r.status_code == 422 and r.json()["detail"] == "A timed event needs a time zone."
    assert await calendar_events(client) == []


async def test_add_after_the_calendar_event_was_deleted_makes_it_again(client, card):
    first = (await client.post(ADD.format(card["id"]), json=TIMES)).json()["event"]
    assert (await client.delete(f"/calendar/events/{first['id']}")).status_code == 200
    r = await client.post(ADD.format(card["id"]), json=TIMES)
    assert r.status_code == 200, r.text
    second = r.json()["event"]
    assert second["id"] != first["id"] and r.json()["card"]["calendar_event_id"] == second["id"]
    assert [e["id"] for e in await calendar_events(client)] == [second["id"]]
    # and the live one is answered again, not copied
    again = await client.post(ADD.format(card["id"]), json=TIMES)
    assert again.json()["event"]["id"] == second["id"] and len(await calendar_events(client)) == 1


async def test_add_of_a_passed_event_is_409_with_a_sentence(client, past_card):
    r = await client.post(ADD.format(past_card["id"]), json={"start": "2026-11-01T15:00:00Z", "end": "2026-11-01T21:00:00Z"})
    assert (r.status_code, r.json()["detail"]) == (409, "This event has already happened.")
    assert await calendar_events(client) == []


async def test_dismiss_then_add_is_allowed_and_unknown_cards_are_404(client, card):
    r = await client.post(f"/discover/cards/{card['id']}/dismiss", json={})
    assert r.status_code == 200 and r.json()["state"] == "dismissed" and "has_image" not in r.json()
    r = await client.post(ADD.format(card["id"]), json=TIMES)
    assert r.status_code == 200 and r.json()["card"]["state"] == "added"
    assert (await client.post("/discover/cards/nope/dismiss", json={})).status_code == 404
    assert (await client.post(ADD.format("nope"), json=TIMES)).status_code == 404


async def test_an_add_asks_for_a_sync_like_any_calendar_change(client, card, service):
    kicks = []
    service.kick = lambda: kicks.append(1)
    await client.post(ADD.format(card["id"]), json=TIMES)
    assert kicks == [1]
    await client.post(ADD.format(card["id"]), json=TIMES)    # the second answers the same event: nothing new to send
    assert kicks == [1]
    await client.post(ADD.format(card["id"]), json={"start": "garbage", "end": "x"})
    assert kicks == [1]


async def test_images_route(client, harness, card):
    other = make_card(harness, "9000000000012")
    assert (await client.get(f"/discover/images/{card['id']}")).status_code == 404
    harness.discover.images_dir.mkdir(parents=True, exist_ok=True)
    (harness.discover.images_dir / card["id"]).write_bytes(PNG)
    harness.discover.store.set_image(card["id"], True)
    r = await client.get(f"/discover/images/{card['id']}")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["content-type"] == "image/png"
    assert (await client.get(f"/discover/images/{other['id']}")).status_code == 404
    assert (await client.get("/discover/images/..%2F..%2Fsecret")).status_code == 404


def discover_posts(harness) -> list[str]:
    from src.events.discover_routes import discover_router

    router = discover_router(harness.discover, harness)
    return sorted(r.path for r in router.routes if "POST" in (getattr(r, "methods", None) or ()))


async def test_no_discover_post_can_be_sent_as_a_simple_cross_site_request(client, harness, card, service):
    paths = discover_posts(harness)
    assert {"/discover/run", "/discover/cards/{card_id}/dismiss", "/discover/cards/{card_id}/add"} <= set(paths)
    foreign = {"Origin": "https://evil.example"}
    full = json.dumps({**TIMES}).encode()
    for path in paths:
        url = path.replace("{card_id}", card["id"])
        for body in (b"", b"{}", full):
            for headers in (foreign, {**foreign, "Content-Type": "text/plain"},
                            {**foreign, "Content-Type": "application/x-www-form-urlencoded"}):
                r = await client.post(url, content=body, headers=headers)
                assert r.status_code == 422, (path, body, headers, r.status_code)
    assert await calendar_events(client) == []
    assert (await client.get("/discover/digests")).json()[0]["cards"][0]["state"] == "new"
    assert (await client.put("/discover/settings", content=b"{}", headers={"Content-Type": "text/plain"})).status_code == 422


async def test_no_response_carries_a_disk_path(client, harness, card, cfg):
    harness.discover.images_dir.mkdir(parents=True, exist_ok=True)
    (harness.discover.images_dir / card["id"]).write_bytes(PNG)
    harness.discover.store.set_image(card["id"], True)
    await client.put("/discover/settings", json=SET_UP)
    bodies = [(await client.get("/discover/status")).text, (await client.get("/discover/digests")).text,
              (await client.post(ADD.format(card["id"]), json=TIMES)).text,
              (await client.post(f"/discover/cards/{card['id']}/dismiss", json={})).text]
    root = str(cfg.vault_root)
    for body in bodies:
        assert root not in body and root.replace("\\", "/") not in body and root.replace("\\", "\\\\") not in body
        assert "discover-images" not in body and "calendar.db" not in body

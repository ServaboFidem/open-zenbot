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
    h.discover.now = lambda: "2026-11-18T12:00:00Z"    # the clock too, or a listing's end passes in real time
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
    harness.add_bot(Bot(name="Scout", role="Events", id="event-scout", mcps=["events"], closed=True))
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


async def test_run_refuses_a_scout_that_is_not_closed(client, harness):
    await client.put("/discover/settings", json=SET_UP)
    harness.add_bot(Bot(name="Scout", role="Events", id="event-scout"))
    r = await client.post("/discover/run", json={})
    assert (r.status_code, r.json()["detail"]) == (409, "The event scout must be a closed bot.")


async def test_the_harness_looks_the_scout_up_for_the_tools(harness):
    assert harness.discover.scout_is_closed() is False          # no such bot
    harness.add_bot(Bot(name="Scout", role="Events", id="event-scout"))
    assert harness.discover.scout_is_closed() is False          # an open one
    harness.add_bot(Bot(name="Scout", role="Events", id="event-scout", mcps=["events"], closed=True))
    assert harness.discover.scout_is_closed() is True


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


async def test_a_timed_listing_with_no_zone_is_refused(client, harness):
    c = make_card(harness, "9000000000011", timezone="")
    r = await client.post(ADD.format(c["id"]), json=TIMES)
    assert r.status_code == 422 and r.json()["detail"] == "This listing has no usable time zone."
    assert await calendar_events(client) == [] and harness.discover.store.card(c["id"])["state"] == "new"


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
    assert {"/discover/run", "/discover/cards/{card_id}/dismiss", "/discover/cards/{card_id}/add",
            "/discover/events/{event_id}/add", "/discover/refresh"} <= set(paths)
    harness.discover.store.upsert_pool([listing("9100000000001")], "2026-11-18T09:00:00Z")
    foreign = {"Origin": "https://evil.example"}
    full = json.dumps({**TIMES}).encode()
    for path in paths:
        url = path.replace("{card_id}", card["id"]).replace("{event_id}", "9100000000001")
        for body in (b"", b"{}", full):
            for headers in (foreign, {**foreign, "Content-Type": "text/plain"},
                            {**foreign, "Content-Type": "application/x-www-form-urlencoded"}):
                r = await client.post(url, content=body, headers=headers)
                assert r.status_code == 422, (path, body, headers, r.status_code)
    assert await calendar_events(client) == [] and harness.discover.store.added_event("9100000000001") == ""
    assert harness.discover.refresh_task is None
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


KEY = "flk_secret_1234567890"


async def test_status_lists_the_cities_and_no_key(client):
    fl = (await client.get("/discover/status")).json()["findlocal"]
    assert len(fl["cities"]) == 134 and "columbus" in fl["cities"] and fl["key_set"] is False
    r = await client.put("/discover/findlocal/key", json={"key": KEY})
    assert r.status_code == 200
    assert KEY not in r.text and KEY not in (await client.get("/discover/status")).text
    fl = r.json()["findlocal"]
    assert fl["key_set"] is True and fl["key_hint"] == KEY[-4:]


async def test_put_key_saves_and_delete_forgets(client, harness):
    await client.put("/discover/findlocal/key", json={"key": f"  {KEY}  "})
    assert harness.discover.keys.findlocal == KEY
    r = await client.delete("/discover/findlocal/key")
    assert r.status_code == 200 and r.json()["findlocal"]["key_set"] is False
    assert harness.discover.keys.findlocal == ""


@pytest.mark.parametrize("kwargs", [{"json": {"key": "has space"}}, {"json": {"key": ""}}, {"json": {}},
                                    {"json": {"key": 5}}, {}])
async def test_a_bad_key_is_422(client, harness, kwargs):
    r = await client.put("/discover/findlocal/key", **kwargs)
    assert r.status_code == 422
    assert harness.discover.keys.findlocal == ""
    if kwargs.get("json", {}).get("key") == "has space":
        assert r.json()["detail"] == "A FindLocal key is plain text with no spaces, at most 200 characters."


async def test_settings_take_a_corrected_zone_and_keep_it_when_none_is_sent(client):
    body = {**SET_UP, "findlocal_city": "new-york", "timezone": "America/Los_Angeles"}   # the computer's zone
    assert (await client.put("/discover/settings", json=body)).status_code == 200
    r = await client.put("/discover/settings", json={**body, "timezone": "America/New_York"})   # the city's
    assert r.status_code == 200 and r.json()["findlocal"]["timezone"] == "America/New_York"
    r = await client.put("/discover/settings", json={**SET_UP, "findlocal_city": "new-york"})
    assert r.status_code == 200 and r.json()["findlocal"]["timezone"] == "America/New_York"


async def test_a_key_that_cannot_be_written_is_answered_not_raised(client, harness, monkeypatch):
    """An exception leaving the route would carry the key, in its frames, to the error tracker."""
    from src.events.discover_settings import DiscoverKeys

    def full_disk(self, vault_root):
        raise OSError(f"no space left writing {self.findlocal}")

    monkeypatch.setattr(DiscoverKeys, "save", full_disk)
    r = await client.put("/discover/findlocal/key", json={"key": KEY})
    assert (r.status_code, r.json()) == (500, {"detail": "The key could not be saved."})
    assert KEY not in r.text and harness.discover.keys.findlocal == ""
    r = await client.delete("/discover/findlocal/key")
    assert (r.status_code, r.json()) == (500, {"detail": "The key could not be saved."})


async def test_an_unknown_findlocal_city_is_422(client):
    r = await client.put("/discover/settings", json={**SET_UP, "findlocal_city": "atlantis", "timezone": "UTC"})
    assert r.status_code == 422 and r.json()["detail"] == "Pick a FindLocal city from the list."


async def test_a_findlocal_card_says_its_source(client, harness):
    harness.discover.store.save_digest([(listing("9000000000009", source="findlocal"), "why"),
                                        (listing("9000000000010"), "why")], kind="weekly", note="", session_id="s9",
                                       created_at="2026-11-17T00:00:00Z")
    cards = (await client.get("/discover/digests")).json()[0]["cards"]
    assert {c["event_id"]: c["source"] for c in cards} == {"9000000000009": "findlocal",
                                                          "9000000000010": "eventbrite"}


# ---- the pool: list, one event, add, refresh ----------------------------------------------------------

EVENTS = "/discover/events"
EADD = "/discover/events/{}/add"
SLIM = {"id", "name", "start_date", "start_time", "end_date", "end_time", "venue", "city", "category_names",
        "source", "state"}
FULL = SLIM | {"summary", "address", "region", "timezone", "url", "categories", "image"}


@pytest.fixture
async def active(client, harness, fake_site, fake_findlocal):
    """Discovery set up, both sources read through the fakes (so a request would be seen)."""
    assert (await client.put("/discover/settings", json=SET_UP)).status_code == 200
    harness.discover.source = fake_site.eventbrite()
    harness.discover.findlocal = fake_findlocal.reader()
    harness.discover.sleep = fake_site.sleep   # the gap between picture fetches is recorded, never slept
    return harness.discover


def pool(harness, *rows):
    harness.discover.store.upsert_pool(list(rows), "2026-11-18T09:00:00Z")


async def states(client) -> dict:
    return {e["id"]: e["state"] for e in (await client.get(EVENTS)).json()["events"]}


async def test_events_is_empty_when_discovery_is_not_active(client, harness):
    pool(harness, listing("9100000000001"))
    assert (await client.get(EVENTS)).json() == {"events": [], "total": 0, "read_at": ""}


async def test_events_are_slim_in_date_order_with_the_read_time(client, harness, active):
    pool(harness, listing("9100000000002", start_date="2026-11-25"), listing("9100000000001"),
         listing("9100000000003", start_date="2026-11-10"))
    harness.discover.store.set_meta("last_refresh", "2026-11-18T08:00:00Z")
    body = (await client.get(EVENTS)).json()
    assert body["total"] == 2 and body["read_at"] == "2026-11-18T08:00:00Z"
    assert [e["id"] for e in body["events"]] == ["9100000000001", "9100000000002"]
    for e in body["events"]:
        assert set(e) == SLIM
    assert body["events"][0]["category_names"] == ["Music"] and body["events"][0]["state"] == ""


async def test_events_state_follows_cards_and_added_rows(client, harness, active, service):
    ids = [f"92000000000{i:02d}" for i in range(6)]
    pool(harness, *(listing(i) for i in ids))
    store = harness.discover.store
    make_card(harness, ids[1])                                        # a new card: recommended
    store.set_state(make_card(harness, ids[2])["id"], "dismissed")   # a dismissed card: nothing
    live = service.store.add({"title": "x", "start": "2026-11-22", "end": "2026-11-23", "all_day": True, "tz": ""})
    gone = service.store.add({"title": "y", "start": "2026-11-22", "end": "2026-11-23", "all_day": True, "tz": ""})
    store.mark_added(make_card(harness, ids[3])["id"], live["id"])     # an added card: added
    store.mark_event_added(ids[4], live["id"], "t")                    # an added row: added
    store.mark_event_added(ids[5], gone["id"], "t")
    assert (await client.delete(f"/calendar/events/{gone['id']}")).status_code == 200   # its event deleted: nothing
    assert await states(client) == {ids[0]: "", ids[1]: "recommended", ids[2]: "", ids[3]: "added",
                                    ids[4]: "added", ids[5]: ""}
    for i, want in ((ids[1], "recommended"), (ids[3], "added"), (ids[5], "")):
        assert (await client.get(f"{EVENTS}/{i}")).json()["state"] == want


async def test_opening_the_list_or_an_event_reads_no_source(client, harness, active, fake_site, fake_findlocal):
    called = []

    async def no_refresh(*a, **k):
        called.append(1)

    harness.discover.refresh = no_refresh
    pool(harness, listing("9100000000001"))
    assert (await client.get(EVENTS)).status_code == 200
    assert (await client.get(f"{EVENTS}/9100000000001")).status_code == 200
    assert called == [] and fake_site.requests == [] and fake_findlocal.requests == []


async def test_one_event_is_the_full_row_and_its_picture_address(client, harness, active):
    pool(harness, listing("9100000000001", image_url="https://img.evbuc.com/x.jpg"), listing("9100000000002"),
         listing("fl-0b1c2d3e-0000-4000-8000-000000000001", source="findlocal", image_url="https://x/y.jpg"))
    a = (await client.get(f"{EVENTS}/9100000000001")).json()
    assert set(a) == FULL and a["image"] == "/discover/events/9100000000001/image"
    assert (a["summary"], a["address"], a["region"], a["timezone"]) == (
        "A summary.", "1 Example Street", "OH", "America/New_York")
    assert a["categories"] == ["EventbriteCategory/103"] and a["url"].startswith("https://www.eventbrite.com/e/")
    assert (await client.get(f"{EVENTS}/9100000000002")).json()["image"] is None
    fl = (await client.get(f"{EVENTS}/fl-0b1c2d3e-0000-4000-8000-000000000001")).json()
    assert fl["image"] is None and fl["source"] == "findlocal"
    r = await client.get(f"{EVENTS}/nope")
    assert (r.status_code, r.json()["detail"]) == (404, "No such event.")


async def test_add_from_the_pool_makes_one_event_like_a_card(client, harness, active):
    pool(harness, listing("9100000000001"))
    r = await client.post(EADD.format("9100000000001"), json=TIMES)
    assert r.status_code == 200, r.text
    ev, row = r.json()["calendar_event"], r.json()["event"]
    assert (ev["title"], ev["tz"], ev["all_day"]) == ("Event 9100000000001", "America/New_York", False)
    assert ev["location"] == "Example Hall, 1 Example Street"
    assert ev["description"] == "A summary.\n\nhttps://www.eventbrite.com/e/x-9100000000001"
    assert set(row) == FULL and row["state"] == "added" and "etag" not in ev
    assert harness.discover.store.added_event("9100000000001") == ev["id"]
    assert len(await calendar_events(client)) == 1
    assert (await states(client))["9100000000001"] == "added"


async def test_add_of_an_event_on_a_card_marks_the_card(client, harness, active):
    pool(harness, listing("9100000000001"))
    card = make_card(harness, "9100000000001")
    r = await client.post(EADD.format("9100000000001"), json=TIMES)
    assert r.status_code == 200, r.text
    got = harness.discover.store.card(card["id"])
    assert got["state"] == "added" and got["calendar_event_id"] == r.json()["calendar_event"]["id"]
    assert harness.discover.store.added_event("9100000000001") == ""
    assert r.json()["event"]["state"] == "added"


async def test_add_checks_against_the_pool_row_not_the_cards_snapshot(client, harness, active):
    make_card(harness, "9100000000001", start_time="03:00", end_time="05:00", name="Old name")
    pool(harness, listing("9100000000001"))   # the row now says 10:00 to 16:00, as TIMES does
    r = await client.post(EADD.format("9100000000001"), json=TIMES)
    assert r.status_code == 200, r.text
    assert r.json()["calendar_event"]["title"] == "Event 9100000000001"


async def test_two_adds_at_once_make_one_event(client, harness, active):
    pool(harness, listing("9100000000001"))
    a, b = await asyncio.gather(*(client.post(EADD.format("9100000000001"), json=TIMES) for _ in range(2)))
    assert a.status_code == b.status_code == 200
    assert a.json()["calendar_event"]["id"] == b.json()["calendar_event"]["id"]
    assert len(await calendar_events(client)) == 1


async def test_add_again_after_the_event_was_deleted_makes_a_new_one(client, harness, active, service):
    kicks = []
    service.kick = lambda: kicks.append(1)
    pool(harness, listing("9100000000001"))
    first = (await client.post(EADD.format("9100000000001"), json=TIMES)).json()["calendar_event"]
    again = (await client.post(EADD.format("9100000000001"), json=TIMES)).json()["calendar_event"]
    assert again["id"] == first["id"] and kicks == [1]
    assert (await client.delete(f"/calendar/events/{first['id']}")).status_code == 200
    kicks.clear()                                            # the delete asked for its own
    second = (await client.post(EADD.format("9100000000001"), json=TIMES)).json()["calendar_event"]
    assert second["id"] != first["id"] and kicks == [1]
    assert harness.discover.store.added_event("9100000000001") == second["id"]
    assert [e["id"] for e in await calendar_events(client)] == [second["id"]]


async def test_add_refusals(client, harness, active):
    pool(harness, listing("9100000000001"), listing("9100000000002", timezone=""),
         listing("9100000000003", start_date="2026-11-01", end_date="2026-11-01"))
    r = await client.post(EADD.format("nope"), json=TIMES)
    assert (r.status_code, r.json()["detail"]) == (404, "No such event.")
    r = await client.post(EADD.format("9100000000003"), json={"start": "2026-11-01T15:00:00Z",
                                                                "end": "2026-11-01T21:00:00Z"})
    assert (r.status_code, r.json()["detail"]) == (409, "This event has already happened.")
    r = await client.post(EADD.format("9100000000001"), json={"start": "garbage", "end": "2026-11-22T21:00:00Z"})
    assert r.status_code == 422
    r = await client.post(EADD.format("9100000000002"), json=TIMES)
    assert (r.status_code, r.json()["detail"]) == (422, "This listing has no usable time zone.")
    assert await calendar_events(client) == []
    assert harness.discover.store.added_event("9100000000001") == ""


async def test_a_timed_listing_that_ended_earlier_today_is_409(client, harness, active):
    pool(harness, listing("9100000000001"))                       # 22 Nov, 10:00 to 16:00 New York: ends 21:00Z
    harness.discover.today = lambda: "2026-11-22"
    harness.discover.now = lambda: "2026-11-22T21:00:00Z"         # it has just ended
    r = await client.post(EADD.format("9100000000001"), json=TIMES)
    assert (r.status_code, r.json()["detail"]) == (409, "This event has already happened.")
    card = make_card(harness, "9100000000001")
    r = await client.post(ADD.format(card["id"]), json=TIMES)
    assert (r.status_code, r.json()["detail"]) == (409, "This event has already happened.")
    assert await calendar_events(client) == []
    harness.discover.now = lambda: "2026-11-22T20:59:59Z"         # still going: it may be added
    assert (await client.post(EADD.format("9100000000001"), json=TIMES)).status_code == 200


async def test_an_all_day_listing_today_may_still_be_added(client, harness, active):
    pool(harness, listing("9100000000001", start_time="", end_time="", end_date="2026-11-22"))
    harness.discover.today = lambda: "2026-11-22"
    harness.discover.now = lambda: "2026-11-22T23:30:00Z"
    r = await client.post(EADD.format("9100000000001"), json={"start": "2026-11-22", "end": "2026-11-23"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("on_card", [False, True])
async def test_an_add_whose_link_cannot_be_written_leaves_no_event_and_a_retry_makes_one(client, harness, active,
                                                                                        on_card):
    """The calendar event is made first; if marking it then fails, it is taken back, so a retry makes one."""
    store = harness.discover.store
    pool(harness, listing("9100000000001"))
    card = make_card(harness, "9100000000001") if on_card else None
    path = ADD.format(card["id"]) if on_card else EADD.format("9100000000001")
    real = (store.mark_added, store.mark_event_added)

    def broken(*a, **k):
        raise RuntimeError("database is locked")

    store.mark_added = store.mark_event_added = broken
    r = await client.post(path, json=TIMES)
    assert (r.status_code, r.json()["detail"]) == (500, "The event could not be added. Try again.")
    assert await calendar_events(client) == []
    store.mark_added, store.mark_event_added = real
    assert (await client.post(path, json=TIMES)).status_code == 200
    assert len(await calendar_events(client)) == 1


async def test_a_picture_removed_before_it_is_read_is_404(client, harness, active, fake_site, card):
    pool(harness, listing("9100000000001", image_url=PIC))
    fake_site.images[PIC] = httpx.Response(200, content=PNG)
    assert (await client.get(IMG.format("9100000000001"))).status_code == 200
    kept = harness.discover.pool_images_dir / "9100000000001"

    async def swept(event_id):          # the route got the path, then a refresh's sweep removed the file
        kept.unlink()
        return kept

    harness.discover.pool_image = swept
    r = await client.get(IMG.format("9100000000001"))
    assert (r.status_code, r.json()["detail"]) == (404, "No picture.")
    gone = harness.discover.images_dir / card["id"]
    harness.discover.image_path = lambda card_id: gone      # a card's picture that is no longer there
    assert (await client.get(f"/discover/images/{card['id']}")).status_code == 404


async def test_refresh_starts_one_background_refresh(client, harness, active):
    gate, runs = asyncio.Event(), []

    async def slow(*a, **k):
        runs.append((a, k))
        await gate.wait()

    harness.discover.refresh = slow
    assert (await client.post("/discover/refresh")).status_code == 422
    r = await client.post("/discover/refresh", json={})
    assert r.status_code == 200 and r.json()["refreshing"] is True and r.json()["set_up"] is True
    task = harness.discover.refresh_task
    await asyncio.sleep(0)
    assert (await client.post("/discover/refresh", json={})).json()["refreshing"] is True
    assert harness.discover.refresh_task is task
    gate.set()
    await task
    assert runs == [((), {})]
    assert (await client.get("/discover/status")).json()["refreshing"] is False


async def test_refresh_needs_discovery_set_up(client):
    r = await client.post("/discover/refresh", json={})
    assert (r.status_code, r.json()["detail"]) == (409, "Set up event discovery first.")
    assert (await client.get("/discover/status")).json()["refreshing"] is False


async def test_no_pool_response_carries_a_hidden_field(client, harness, active):
    pool(harness, listing("9100000000001", image_url="https://img.evbuc.com/x.jpg"))
    bodies = [(await client.get(EVENTS)).text, (await client.get(f"{EVENTS}/9100000000001")).text,
              (await client.post(EADD.format("9100000000001"), json=TIMES)).text]
    for body in bodies:
        for word in ("image_url", "seen_at", "format", "img.evbuc.com", "discover-images"):
            assert word not in body


# ---- a pool event's picture ----------------------------------------------------------------------------

PIC = "https://img.evbuc.com/x.jpg"
IMG = EVENTS + "/{}/image"


async def test_a_pool_picture_is_fetched_on_the_first_ask_and_kept(client, harness, active, fake_site):
    pool(harness, listing("9100000000001", image_url=PIC))
    fake_site.images[PIC] = httpx.Response(200, content=PNG)
    r = await client.get(IMG.format("9100000000001"))
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["content-type"] == "image/png"
    assert [str(q.url) for q in fake_site.requests] == [PIC]
    again = await client.get(IMG.format("9100000000001"))
    assert again.status_code == 200 and again.content == PNG and len(fake_site.requests) == 1


async def test_two_asks_at_once_fetch_the_picture_once(client, harness, active, fake_site):
    pool(harness, listing("9100000000001", image_url=PIC))
    fake_site.images[PIC] = httpx.Response(200, content=PNG)
    a, b = await asyncio.gather(client.get(IMG.format("9100000000001")), client.get(IMG.format("9100000000001")))
    assert a.status_code == b.status_code == 200 and len(fake_site.requests) == 1


@pytest.mark.parametrize("event_id", ["..%2Fx", "..", "fl-0b1c2d3e-0000-4000-8000-000000000001", "12a", "1" * 31,
                                      "9100000000002", "9100000000003", "9999"])
async def test_no_picture_is_404_with_no_request_and_no_file(client, harness, active, fake_site, event_id):
    pool(harness, listing("12a", image_url=PIC), listing("1" * 31, image_url=PIC), listing("9100000000002"),
         listing("9100000000003", source="findlocal", image_url=PIC),
         listing("fl-0b1c2d3e-0000-4000-8000-000000000001", source="findlocal", image_url=PIC))
    fake_site.images[PIC] = httpx.Response(200, content=PNG)
    r = await client.get(IMG.format(event_id))
    assert r.status_code == 404
    if event_id not in ("..%2Fx", ".."):            # those never reach the route
        assert r.json() == {"detail": "No picture."}
    assert fake_site.requests == [] and not harness.discover.pool_images_dir.exists()


@pytest.mark.parametrize("answer", [httpx.Response(404), httpx.Response(200, content=b"not a picture"),
                                    httpx.Response(200, content=PNG + b"0" * 2_000_000)])
async def test_a_failed_fetch_is_404_keeps_nothing_and_is_tried_again(client, harness, active, fake_site, answer):
    pool(harness, listing("9100000000001", image_url=PIC))
    fake_site.images[PIC] = answer
    r = await client.get(IMG.format("9100000000001"))
    assert r.status_code == 404 and r.json() == {"detail": "No picture."}
    folder = harness.discover.pool_images_dir
    assert not folder.exists() or list(folder.iterdir()) == []
    fake_site.images[PIC] = httpx.Response(200, content=PNG)
    assert (await client.get(IMG.format("9100000000001"))).content == PNG
    assert len(fake_site.requests) == 2 and {q.url.host for q in fake_site.requests} == {"img.evbuc.com"}


async def test_a_picture_answer_carries_no_disk_path(client, harness, active, fake_site):
    pool(harness, listing("9100000000001", image_url=PIC))
    fake_site.images[PIC] = httpx.Response(404)
    r = await client.get(IMG.format("9100000000001"))
    assert "discover-images" not in r.text and str(harness.discover.images_dir) not in r.text
    assert not any("discover-images" in v for v in r.headers.values())


# ---- one event through the two add routes ---------------------------------------------------------------

async def test_a_dismissed_card_added_from_the_pool_is_not_added_again_through_the_card(client, harness, active):
    pool(harness, listing("9100000000001"))
    card = make_card(harness, "9100000000001")
    assert (await client.post(f"/discover/cards/{card['id']}/dismiss", json={})).status_code == 200
    first = (await client.post(EADD.format("9100000000001"), json=TIMES)).json()["calendar_event"]
    r = await client.post(ADD.format(card["id"]), json=TIMES)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"card", "event"} and r.json()["event"]["id"] == first["id"]
    assert r.json()["card"]["state"] == "added" and r.json()["card"]["calendar_event_id"] == first["id"]
    assert [e["id"] for e in await calendar_events(client)] == [first["id"]]
    # once that calendar event is deleted, an add makes a new one
    assert (await client.delete(f"/calendar/events/{first['id']}")).status_code == 200
    again = await client.post(ADD.format(card["id"]), json=TIMES)
    second = again.json()["event"]
    assert again.status_code == 200 and second["id"] != first["id"]
    assert again.json()["card"]["calendar_event_id"] == second["id"]
    assert [e["id"] for e in await calendar_events(client)] == [second["id"]]


async def test_a_card_added_then_dismissed_is_not_added_again_from_the_pool(client, harness, active):
    pool(harness, listing("9100000000001"))
    card = make_card(harness, "9100000000001")
    first = (await client.post(ADD.format(card["id"]), json=TIMES)).json()["event"]
    assert (await client.post(f"/discover/cards/{card['id']}/dismiss", json={})).status_code == 200
    r = await client.post(EADD.format("9100000000001"), json=TIMES)
    assert r.status_code == 200, r.text
    assert r.json()["calendar_event"]["id"] == first["id"] and r.json()["event"]["state"] == "added"
    assert [e["id"] for e in await calendar_events(client)] == [first["id"]]
    assert (await states(client))["9100000000001"] == "added"
    # once that calendar event is deleted, an add makes a new one
    assert (await client.delete(f"/calendar/events/{first['id']}")).status_code == 200
    assert (await states(client))["9100000000001"] == ""
    again = await client.post(EADD.format("9100000000001"), json=TIMES)
    second = again.json()["calendar_event"]
    assert again.status_code == 200 and second["id"] != first["id"]
    assert [e["id"] for e in await calendar_events(client)] == [second["id"]]

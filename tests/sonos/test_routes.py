"""The /sonos routes through the real app, the speakers being FakeSpeakers. No network."""

import threading

import httpx
import pytest

from src.events.service import CalendarService
from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.harness.loop import Session
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.sonos.devices import Favorite
from src.sonos.routes import sonos_router
from src.sonos.service import SonosService
from src.sonos.settings import BAD_VOLUME, SonosSettings
from tests.conftest import ScriptedProvider
from tests.sonos.conftest import room


@pytest.fixture
def cfg(vault, tmp_path):
    c = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path)
    c.validate()
    return c


@pytest.fixture
async def calendar(cfg):
    s = CalendarService(cfg, Ledger(cfg.vault_root))
    yield s
    await s.close()


@pytest.fixture
def speakers(fake_speakers):
    fake_speakers.found = [room("Kitchen", "192.0.2.10"), room("Living Room", "192.0.2.11"),
                           room("Patio", "192.0.2.12", coord="192.0.2.11")]
    return fake_speakers


@pytest.fixture
async def harness(cfg, calendar, speakers):
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=calendar.ledger, catalog=Catalog(),
                calendar=calendar, sonos=SonosService(cfg.vault_root, calendar.ledger, speakers=speakers))
    yield h
    await h.sonos.close()


@pytest.fixture
async def client(harness):
    app = create_app(harness)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        yield c


async def test_status_after_a_look(client, harness):
    await harness.sonos.look()
    r = await client.get("/sonos/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["max_volume"] == 60 and body["error"] == ""
    patio = next(x for x in body["rooms"] if x["name"] == "Patio")
    assert set(patio) == {"name", "group", "model", "state", "title"} and patio["group"] == "Living Room"


async def test_settings_validates_and_saves(client, cfg):
    r = await client.put("/sonos/settings", json={"enabled": True, "max_volume": 101})
    assert r.status_code == 422 and r.json()["detail"] == BAD_VOLUME
    r = await client.put("/sonos/settings", json={"enabled": False, "max_volume": 40})
    assert r.status_code == 200 and r.json()["enabled"] is False
    s = SonosSettings.load(cfg.vault_root)
    assert (s.enabled, s.max_volume) == (False, 40)


async def test_discover_scans(client, harness, speakers):
    r = await client.post("/sonos/discover", json={})
    assert r.status_code == 200
    await harness.sonos.settle()
    assert speakers.discovers[-1][1] is True
    speakers.gate = threading.Event()
    assert (await client.post("/sonos/discover", json={})).status_code == 200
    r = await client.post("/sonos/discover", json={})
    assert r.status_code == 409 and r.json()["detail"] == "Already looking for speakers."
    speakers.gate.set()
    await harness.sonos.settle()
    await client.put("/sonos/settings", json={"enabled": False, "max_volume": 60})
    r = await client.post("/sonos/discover", json={})
    assert r.status_code == 409 and r.json()["detail"] == "Sonos is off."


async def test_rooms_and_favorites(client, harness, speakers):
    speakers.favs = [Favorite("Morning Jazz", "favorite"), Favorite("Dinner", "playlist")]
    await harness.sonos.look()
    r = await client.get("/sonos/rooms")
    assert r.status_code == 200
    body = r.json()
    assert body["max_volume"] == 60 and [x["name"] for x in body["rooms"]] == ["Kitchen", "Living Room", "Patio"]
    assert body["rooms"][2]["group"] == "Living Room" and body["rooms"][2]["volume"] == 20
    r = await client.get("/sonos/favorites")
    assert r.status_code == 200
    assert r.json() == {"favorites": [{"title": "Dinner", "kind": "playlist"},
                                      {"title": "Morning Jazz", "kind": "favorite"}]}


async def test_play_control_and_volume(client, harness, speakers):
    speakers.favs = [Favorite("Dinner", "playlist")]
    await harness.sonos.look()
    r = await client.post("/sonos/play", json={"rooms": ["Kitchen", "Living Room"], "name": "Dinner"})
    assert r.status_code == 200 and r.json() == {"message": "Playing Dinner in Kitchen and Living Room."}
    assert speakers.calls[-1] == ("play", "192.0.2.10", "Dinner", "playlist")
    r = await client.post("/sonos/control", json={"room": "Kitchen", "action": "pause"})
    assert r.status_code == 200 and r.json() == {"message": "Done: pause in Kitchen."}
    r = await client.post("/sonos/volume", json={"room": "Kitchen", "level": 35})
    assert r.status_code == 200 and speakers.volumes["192.0.2.10"] == 35


async def test_panel_refusals(client, harness, speakers):
    await harness.sonos.look()
    r = await client.post("/sonos/play", json={"rooms": ["Kitchen"], "name": "Nothing"})
    assert r.status_code == 409 and r.json()["detail"].startswith("No Sonos Favorite or playlist named 'Nothing'.")
    r = await client.post("/sonos/volume", json={"room": "Kitchen", "level": 90})
    assert r.status_code == 409 and r.json()["detail"] == "The highest volume ZenBot may set is 60."
    assert (await client.post("/sonos/control", json={"room": "Kitchen", "action": "shuffle"})).status_code == 422
    assert (await client.post("/sonos/volume", json={"room": "Kitchen", "level": 101})).status_code == 422
    assert (await client.post("/sonos/play", json={"rooms": [], "name": "Dinner"})).status_code == 422
    await client.put("/sonos/settings", json={"enabled": False, "max_volume": 60})
    for path in ("/sonos/rooms", "/sonos/favorites"):
        r = await client.get(path)
        assert r.status_code == 409 and r.json()["detail"] == "Sonos is off."
    r = await client.post("/sonos/control", json={"room": "Kitchen", "action": "pause"})
    assert r.status_code == 409 and r.json()["detail"] == "Sonos is off."


def _post_paths(harness):
    router = sonos_router(harness.sonos, harness.sonos_tools)
    return sorted(r.path for r in router.routes if "POST" in (getattr(r, "methods", None) or ()))


async def test_every_post_needs_a_json_body(client, harness):
    paths = _post_paths(harness)
    assert paths == ["/sonos/control", "/sonos/discover", "/sonos/play", "/sonos/volume"]
    for path in paths:
        assert (await client.post(path)).status_code == 422
        assert (await client.post(path, content="x", headers={"content-type": "text/plain"})).status_code == 422


async def test_harness_builds_its_own_sonos_service(cfg, calendar, speakers):
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=calendar.ledger, catalog=Catalog(),
                calendar=calendar)
    assert isinstance(h.sonos, SonosService)
    assert not any(n.startswith("sonos_") for n in h.tools.names())
    h.sonos.speakers = speakers
    await h.sonos.look()
    assert "sonos_play" in h.tools.names()
    assert any(e.ref == "sonos" for e in h.catalog.mcps())
    await h.sonos.close()


async def test_room_turns_are_not_offered_sonos(harness):
    await harness.sonos.look()
    harness.sessions["in-room"] = Session(id="in-room", bot_id="zenbot", task="x", room_id="r1")
    harness.sessions["one-to-one"] = Session(id="one-to-one", bot_id="zenbot", task="x")
    assert "sonos_play" not in {s.name for s in harness.tools.specs(caller=("zenbot", "in-room"))}
    assert "sonos_play" in {s.name for s in harness.tools.specs(caller=("zenbot", "one-to-one"))}

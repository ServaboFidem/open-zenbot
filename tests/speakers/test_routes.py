"""The /speakers routes through the real app, with fake finders. No network."""

from __future__ import annotations

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.speakers.routes import speakers_router
from src.speakers.service import SpeakerService
from tests.conftest import ScriptedProvider
from tests.speakers.test_service import FakeFinder, sp


@pytest.fixture
def cfg(vault, tmp_path):
    c = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path)
    c.validate()
    return c


@pytest.fixture
async def harness(cfg):
    ledger = Ledger(cfg.vault_root)
    finders = {"airplay": FakeFinder([sp("airplay", "A", "Kitchen", address="10.0.0.20")]), "chromecast": FakeFinder(),
               "dlna": FakeFinder([sp("dlna", "D", "Hall", address="10.0.0.21")])}
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=ledger, catalog=Catalog(),
                speakers=SpeakerService(cfg.vault_root, ledger, finders=finders))
    yield h
    await h.speakers.close()


@pytest.fixture
async def client(harness):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(harness)), base_url="http://localhost") as c:
        yield c


async def test_status_starts_all_off(client):
    st = (await client.get("/speakers/status")).json()
    assert st["default"] is None and {p: v["on"] for p, v in st["protocols"].items()} == {"airplay": False, "chromecast": False, "dlna": False}


async def test_switch_on_lists_speakers_without_addresses(client, harness):
    r = await client.put("/speakers/settings", json={"airplay": True})
    assert r.status_code == 200 and r.json()["protocols"]["airplay"]["on"] is True
    await harness.speakers.settle()
    r = await client.get("/speakers/status")
    assert [s["name"] for s in r.json()["protocols"]["airplay"]["speakers"]] == ["Kitchen"] and "10.0.0.20" not in r.text


async def test_default_unknown_is_422_with_the_sentence(client):
    r = await client.put("/speakers/default", json={"key": "airplay:nope"})
    assert r.status_code == 422 and r.json()["detail"] == "Pick a speaker from the lists."


async def test_default_round_trip(client, harness):
    await client.put("/speakers/settings", json={"dlna": True})
    await harness.speakers.settle()
    r = await client.put("/speakers/default", json={"key": "dlna:D"})
    assert r.status_code == 200 and r.json()["default"]["name"] == "Hall"
    assert (await client.put("/speakers/default", json={"key": None})).json()["default"] is None


async def test_look_refusals_are_409(client, harness):
    r = await client.post("/speakers/look", json={})
    assert r.status_code == 409 and r.json()["detail"] == "No speaker type is switched on."
    await client.put("/speakers/settings", json={"dlna": True})
    await harness.speakers.settle()
    assert (await client.post("/speakers/look", json={})).status_code == 200
    await harness.speakers.settle()


async def test_every_post_needs_a_json_body(client, harness):
    paths = sorted(r.path for r in speakers_router(harness.speakers).routes if "POST" in (getattr(r, "methods", None) or ()))
    assert paths == ["/speakers/look"]
    for path in paths:
        assert (await client.post(path)).status_code == 422
        assert (await client.post(path, content="x", headers={"content-type": "text/plain"})).status_code == 422


async def test_the_harness_builds_its_own_service(cfg):
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(cfg.vault_root), catalog=Catalog())
    assert isinstance(h.speakers, SpeakerService) and h.speakers.status()["default"] is None
    await h.speakers.close()


async def test_an_empty_default_body_is_refused_and_an_explicit_null_clears(client, harness):
    await client.put("/speakers/settings", json={"dlna": True})
    await harness.speakers.settle()
    await client.put("/speakers/default", json={"key": "dlna:D"})
    assert (await client.put("/speakers/default", json={})).status_code == 422
    assert (await client.get("/speakers/status")).json()["default"]["key"] == "dlna:D"
    assert (await client.put("/speakers/default", json={"key": None})).json()["default"] is None

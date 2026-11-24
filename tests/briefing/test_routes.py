"""The /briefings routes on a BriefingService with fakes. No network, no speakers."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from src.briefing.routes import NOT_READY, briefing_router
from src.memory.ledger import Ledger
from src.vault_settings import settings_dir
from tests.briefing.test_service import Clock, Fakes, at, make

BODY = {"enabled": True, "slots": [{"days": [0, 1, 2, 3, 4], "time": "06:45"},
                                    {"days": [5, 6], "time": "08:30"}], "speakers": None, "volume": 25}


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def parts(vault):
    clock = Clock(at(5, 0))
    f = Fakes(vault, clock)
    svc = make(vault, clock, f, ledger=Ledger(vault))
    app = FastAPI()
    app.include_router(briefing_router(svc))
    return svc, f, app


@pytest.fixture
async def client(parts):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=parts[2]), base_url="http://t") as c:
        yield c


async def test_put_then_get_round_trip(client):
    r = await client.put("/briefings/morning", json=BODY)
    assert r.status_code == 200
    got = (await client.get("/briefings")).json()
    b = got["briefings"][0]
    assert b["kind"] == "morning" and b["enabled"] and b["volume"] == 25 and len(b["slots"]) == 2
    assert b["state"] == "idle" and b["ready"] is True and b["reason"] is None
    assert got["ceiling"] == 60 and got["rooms"] == ["Kitchen", "Office"]
    assert b["next_run"]


async def test_replace_keeps_id(client):
    await client.put("/briefings/morning", json=BODY)
    first = (await client.get("/briefings")).json()["briefings"][0]["id"]
    await client.put("/briefings/morning", json={**BODY, "volume": 30})
    again = (await client.get("/briefings")).json()["briefings"][0]
    assert again["id"] == first and again["volume"] == 30


async def test_overlap_is_422_with_sentence(client):
    body = {**BODY, "slots": [{"days": [0, 1], "time": "06:45"}, {"days": [1], "time": "07:00"}]}
    r = await client.put("/briefings/morning", json=body)
    assert r.status_code == 422 and r.json()["detail"] == "Each day can have only one time."


async def test_volume_above_ceiling_422(client):
    r = await client.put("/briefings/morning", json={**BODY, "volume": 61})
    assert r.status_code == 422 and "1 to 60" in r.json()["detail"]


async def test_malformed_body_422_without_echo(client):
    r = await client.put("/briefings/morning", json={"slots": "SECRET-ECHO"})
    assert r.status_code == 422 and "SECRET-ECHO" not in r.text
    r = await client.put("/briefings/morning", json=["SECRET-ECHO"])
    assert r.status_code == 422 and "SECRET-ECHO" not in r.text


async def test_unknown_kind_404(client):
    assert (await client.put("/briefings/evening", json=BODY)).status_code == 404
    assert (await client.delete("/briefings/evening")).status_code == 404
    assert (await client.post("/briefings/evening/play", json={})).status_code == 404


async def test_delete(client):
    assert (await client.delete("/briefings/morning")).status_code == 404
    await client.put("/briefings/morning", json=BODY)
    assert (await client.delete("/briefings/morning")).status_code == 200
    assert (await client.get("/briefings")).json()["briefings"] == []


async def test_every_post_needs_a_json_body(parts, client):
    posts = [r for r in briefing_router(parts[0]).routes if "POST" in (getattr(r, "methods", None) or ())]
    assert posts
    for route in posts:
        url = route.path.replace("{kind}", "morning")
        assert (await client.post(url)).status_code == 422
        assert (await client.post(url, data={"a": "b"})).status_code == 422
        assert (await client.post(url, content="{}", headers={"content-type": "text/plain"})).status_code == 422


async def test_play_busy_is_409(parts, client):
    svc, f, _ = parts
    await client.put("/briefings/morning", json=BODY)
    f.announce_gate = asyncio.Event()
    assert (await client.post("/briefings/morning/play", json={})).status_code == 200
    await asyncio.sleep(0.05)
    r = await client.post("/briefings/morning/play", json={})
    assert r.status_code == 409 and r.json()["detail"] == "A briefing is already playing."
    f.announce_gate.set()
    await asyncio.gather(*svc._tasks)


async def test_play_without_briefing_404(client):
    assert (await client.post("/briefings/morning/play", json={})).status_code == 404


def test_readiness_sentences_are_verbatim():
    assert NOT_READY == {
        "sonos_off": "Sonos is off. Switch it on in Settings > Connected apps.",
        "no_speakers": "No Sonos speaker was found.",
        "no_voice": "Set up a voice in Settings > Harness Tools > Read aloud first.",
        "ceiling_zero": "Your Sonos volume limit is 0."}


@pytest.mark.parametrize("reason", ["sonos_off", "no_speakers", "no_voice", "ceiling_zero"])
async def test_play_readiness_sentences(parts, client, reason):
    _, f, _ = parts
    await client.put("/briefings/morning", json=BODY)
    if reason == "sonos_off":
        f.sonos.settings.enabled = False
    elif reason == "no_speakers":
        f.sonos.rooms = []
    elif reason == "no_voice":
        f.voice_ok = False
    else:
        f.sonos.settings.max_volume = 0
    r = await client.post("/briefings/morning/play", json={})
    assert r.status_code == 409 and r.json()["detail"] == NOT_READY[reason]


async def test_put_and_delete_call_settings_saved(parts, client, monkeypatch):
    seen = []
    monkeypatch.setattr(parts[0], "settings_saved", lambda kind: seen.append(kind))
    await client.put("/briefings/morning", json=BODY)
    await client.delete("/briefings/morning")
    assert seen == ["morning", "morning"]


async def test_read_only_store_is_409(client, vault):
    d = settings_dir(vault)
    d.mkdir(parents=True, exist_ok=True)
    (d / "briefings.json").write_text("{not json", encoding="utf-8")
    r = await client.put("/briefings/morning", json=BODY)
    assert r.status_code == 409 and "could not be read" in r.json()["detail"]
    assert (d / "briefings.json").read_text(encoding="utf-8") == "{not json"


async def test_settings_ledger_entry_holds_only_counts(client, vault):
    await client.put("/briefings/morning", json={**BODY, "speakers": ["SECRET-ROOM"]})
    found = []
    for p in vault.parent.rglob("ledger.jsonl"):
        found += [json.loads(x) for x in p.read_text().splitlines()]
    e = [x for x in found if x.get("kind") == "briefing"]
    assert len(e) == 1
    assert e[0]["data"] == {"event": "settings", "enabled": True, "lines": 2}

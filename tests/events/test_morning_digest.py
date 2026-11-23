import json
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.events.morning_digest import MorningDigest
from src.events.morning_weather import WeatherSettings
from src.harness.config import HarnessConfig
from src.mail.settings import MailKeys, MailSettings
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.vault_settings import settings_dir
from tests.conftest import ScriptedProvider


@pytest.fixture
async def workspace(vault, tmp_path):
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path)
    cfg.validate()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        yield h, c
    await h.discover.close()
    await h.calendar.close()
    await h.mail.close()


async def test_new_chat_reads_updated_calendar_without_mail_or_weather(workspace):
    h, client = workspace
    first = await client.get("/morning/agenda")
    assert first.status_code == 200
    assert first.json()["events"] == []
    day = date.today()
    h.calendar.store.add({"title": "Library shift", "all_day": True, "start": day.isoformat(),
                          "end": (day + timedelta(days=1)).isoformat()})
    today = (await client.get("/morning/agenda")).json()
    assert [item["title"] for item in today["events"]] == ["Library shift"]
    assert "picks" not in today
    assert (await client.get("/morning/forecast")).json()["status"] == "unconfigured"


async def test_settings_only_accept_weather_and_preserve_the_previous_choice(workspace):
    h, client = workspace
    h.discover.settings.city = "Columbus"
    before = (await client.get("/morning/settings")).json()
    assert before["suggested_city"] == "Columbus"
    assert before["delivery"]["status"] == "disabled"
    assert "recipient" not in before
    data = {"location": {"name": "Paris", "latitude": 48.86, "longitude": 2.35}, "unit": "C"}
    assert (await client.put("/morning/settings", json=data)).json()["location"] == data["location"]
    assert (await client.put("/morning/settings", json={**data, "recipient": "elsewhere@example.org"})).status_code == 422
    assert (await client.get("/morning/settings")).json()["unit"] == "C"
    assert (await client.get("/morning/locations", params={"q": " "})).status_code == 422


async def test_daily_tick_delivers_shared_agenda_plus_a_verified_pick_once(workspace, monkeypatch):
    h, _ = workspace
    now = datetime(2026, 11, 23, 8).astimezone()
    day = now.date().isoformat()
    h.calendar.store.add({"title": "Volunteer shift", "all_day": True, "start": day, "end": "2026-11-24"})
    show = {"id": "show", "source": "eventbrite", "name": "Gallery open house", "start_date": day,
            "url": "https://www.eventbrite.com/e/1", "categories": [], "category_names": []}
    h.discover.store.upsert_pool([show, {"id": "person", "source": "facebook-birthday", "name": "Maya",
                                        "start_date": day}], day)
    h.discover.store.save_digest([(h.discover.store.pool_row("show"), "You like local printmakers.")],
                                 kind="weekly", note="", session_id="weekly", created_at=day)
    h.discover.settings.enabled, h.discover.settings.place = True, "oh--columbus"
    monkeypatch.setattr(h.discover, "refresh", AsyncMock(return_value={}))
    h.mail.keys = MailKeys("test-key")
    h.mail.settings = MailSettings("test@agentmail.to", "test@agentmail.to")
    (settings_dir(h.config.vault_root) / "morning_email.json").write_text(
        '{"recipient":"owner@example.org","time":"07:00"}', encoding="utf-8")
    drafts, sent = [], []

    def provider(req):
        if req.url.host == "api.open-meteo.com":
            return httpx.Response(200, json={"daily": {"time": [day], "weather_code": [0],
                "temperature_2m_max": [60], "temperature_2m_min": [40], "precipitation_probability_max": [0]}})
        assert req.url.host == "api.agentmail.to"
        if req.url.path.endswith("/drafts"):
            drafts.append(json.loads(req.content))
            return httpx.Response(201, json={"draft_id": "edition"})
        assert req.url.path.endswith("/drafts/edition/send")
        sent.append(req)
        return httpx.Response(200, json={"message_id": "receipt"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        monkeypatch.setattr(h.calendar, "http", lambda: http)
        monkeypatch.setattr(h.mail, "http", http)
        digest = MorningDigest(h)
        digest.weather.save(WeatherSettings(location={"name": "Columbus", "latitude": 40, "longitude": -83}, unit="F"))
        assert (await digest.delivery.tick(now))["status"] == "sent"
        await MorningDigest(h).delivery.tick(now)
    assert len(sent) == len(drafts) == 1 and drafts[0]["to"] == ["owner@example.org"]
    body = drafts[0]["text"]
    assert all(text in body for text in ("Clear sky", "Maya", "Volunteer shift", "Gallery open house",
                                         "You like local printmakers.", show["url"]))
    assert body.index("Volunteer shift") < body.index("Events you might enjoy today") < body.index("Gallery open house")

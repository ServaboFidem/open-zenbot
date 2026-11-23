import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from src.events.discover_store import LAST_REFRESH
from src.events.facebook_pool import BIRTHDAY_SOURCE
from src.providers.interface import Completion, Message, ProviderUnavailable
from tests.events.conftest import page_with, row


NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 1, 7, tzinfo=NY)
DAY = "2026-11-01"


@pytest.fixture
def h(store, discovery):
    discovery.today = lambda: DAY
    discovery.store.set_meta(LAST_REFRESH, discovery.now())
    discovery.settings.interests = "small jazz ensembles"
    return SimpleNamespace(calendar=SimpleNamespace(store=store), discover=discovery,
                           bots={"event-scout": SimpleNamespace(id="event-scout", closed=True)})


def timed(title, start, end):
    return {"title": title, "start": start, "end": end, "all_day": False, "tz": "America/New_York"}


def all_day(title):
    return {"title": title, "start": DAY, "end": "2026-11-02", "all_day": True}


def test_agenda_reads_visible_commitments_across_a_25_hour_day_and_updates(h):
    from src.events.morning_agenda import agenda

    store = h.calendar.store
    store.add(timed("Night shift", "2026-11-01T03:00:00Z", "2026-11-01T15:00:00Z"))
    store.add(timed("Before fallback", "2026-11-01T05:45:00Z", "2026-11-01T06:05:00Z"))
    store.add(timed("After fallback", "2026-11-01T06:15:00Z", "2026-11-01T06:45:00Z"))
    late = store.add(timed("Late shift", "2026-11-02T04:15:00Z", "2026-11-02T05:00:00Z"))
    store.add(timed("Tomorrow", "2026-11-02T05:00:00Z", "2026-11-02T06:00:00Z"))
    store.add(timed("Yesterday", "2026-11-01T02:00:00Z", "2026-11-01T04:00:00Z"))
    store.add(all_day("Out of office"))
    store.upsert_google([{"id": "team", "name": "Team", "writable": False}])
    store.apply_remote("team", "external", all_day("Team holiday"), etag="1", link="", series_id="")
    assert "Team holiday" in [e["title"] for e in agenda(h, NOW)["events"]]
    store.set_calendar("team", visible=False)
    day = agenda(h, NOW)
    assert day["date"] == DAY
    assert day["warnings"] == []
    assert [e["title"] for e in day["events"]] == [
        "Out of office", "Night shift", "Before fallback", "After fallback", "Late shift"]
    labels = [e["time"].lower() for e in day["events"]]
    assert "ongoing" in labels[1]
    assert "1:45 am" in labels[2] and "1:15 am" in labels[3] and "11:15 pm" in labels[4]
    store.delete(late["id"])
    assert "Late shift" not in [e["title"] for e in agenda(h, NOW)["events"]]


def test_birthdays_dedupe_calendar_holds_and_survive_either_source_failing(h):
    from src.events.morning_agenda import agenda

    h.discover.store.upsert_pool([
        {"id": "p", "source": BIRTHDAY_SOURCE, "name": "Fran", "start_date": DAY},
        {"id": "p2", "source": BIRTHDAY_SOURCE, "name": "Fran", "start_date": DAY},
        {"id": "p3", "source": BIRTHDAY_SOURCE, "name": "Ari", "start_date": "2026-11-02"},
        {"id": "invite", "source": "facebook", "name": "Pending invite", "start_date": DAY},
    ], DAY)
    h.calendar.store.add(all_day("Fran's birthday"))
    day = agenda(h, NOW)
    assert day["birthdays"] == ["Fran"]
    assert day["events"] == []
    calendar = h.calendar
    h.calendar = None
    day = agenda(h, NOW)
    assert day["birthdays"] == ["Fran"] and "calendar" in str(day["warnings"]).lower()
    h.calendar, h.discover = calendar, None
    day = agenda(h, NOW)
    assert [e["title"] for e in day["events"]] == ["Fran's birthday"]
    assert "birthday" in str(day["warnings"]).lower()


def test_agenda_excludes_local_midnight_on_the_short_spring_day(h):
    from src.events.morning_agenda import agenda

    h.calendar.store.add(timed("Late", "2026-03-09T03:45:00Z", "2026-03-09T04:00:00Z"))
    h.calendar.store.add(timed("Monday", "2026-03-09T04:00:00Z", "2026-03-09T05:00:00Z"))
    day = agenda(h, datetime(2026, 3, 8, 0, 30, tzinfo=NY))
    assert [(e["title"], e["time"]) for e in day["events"]] == [("Late", "11:45 PM")]


def listing(id, title, **fields):
    return {"id": id, "name": title, "source": "eventbrite", "start_date": DAY, "end_date": DAY,
            "start_time": "18:00", "end_time": "20:00", "timezone": "America/New_York",
            "url": f"https://www.eventbrite.com/e/{id}", **fields}


class Scout:
    def __init__(self, answer):
        self.answer = answer
        self.data = None

    async def complete(self, messages, *, model, tools=None):
        assert model == "configured-scout" and not tools
        self.data = json.loads(messages[-1].content)
        if self.answer is None:
            raise ProviderUnavailable()
        return Completion(Message("assistant", json.dumps({"picks": self.answer})), "stop", model)


@pytest.mark.asyncio
@pytest.mark.parametrize("model_available", [True, False])
async def test_picks_filter_before_model_validate_output_and_preserve_history(h, model_available):
    from src.events.morning_agenda import agenda, picks

    store = h.discover.store
    fresh = [listing(f"jam{i}", f"Jazz ensemble {i}") for i in range(7)]
    saved = listing("saved", "Modern quartet")
    dismissed = listing("dismissed", "Dismissed concert")
    store.upsert_pool([saved, dismissed, *fresh, listing("alias", "Modern Quartet"),
                       listing("also-dismissed", "Dismissed concert"), listing("added", "Added show"),
                       listing("clash", "Conflicting show", start_time="14:45", end_time="16:00"),
                       listing("over", "Ended", start_time="04:00", end_time="07:00"),
                       listing("tomorrow", "Tomorrow", start_date="2026-11-02", end_date="2026-11-02"),
                       listing("fb", "Private invite", source="facebook"),
                       listing("off-source", "Disabled source", source="findlocal")], DAY)
    digest = store.save_digest([(saved, "You enjoy the quartet's cool jazz."),
                                (dismissed, "An earlier recommendation.")],
                               kind="weekly", note="", session_id="weekly", created_at=DAY)
    store.set_state(digest["cards"][1]["id"], "dismissed")
    store.mark_event_added("added", "calendar-id", DAY)
    before = store.digests(None, 10)
    h.calendar.store.add(all_day("Fran's birthday"))
    h.calendar.store.add(timed("Private meeting", "2026-11-01T19:00:00Z", "2026-11-01T20:00:00Z"))
    answer = [{"id": "clash", "why": "Unknown to model"}, {"id": [], "why": "Malformed"},
              {"id": "jam5", "why": "Bad\nreason"}]
    answer += [{"id": f"jam{i}", "why": "Small jazz ensemble."} for i in [6, 6, 1, 2, 3, 4]]
    scout = Scout(answer if model_available else None)
    h._model_for = lambda bot: (scout, "configured-scout") if bot.id == "event-scout" else (None, None)
    day = agenda(h, NOW)
    result = await picks(h, NOW, day)
    assert ("New scout recommendations unavailable." in day["warnings"]) is (not model_available)
    assert set(r["id"] for r in scout.data["candidates"]) == {f"jam{i}" for i in range(7)}
    assert "Private meeting" not in str(scout.data) and "Fran" not in str(scout.data)
    assert result[0] == {"title": "Modern quartet", "time": "6:00 PM",
                         "why": "You enjoy the quartet's cool jazz.", "url": saved["url"]}
    assert [p["title"] for p in result[1:]] == (
        ["Jazz ensemble 6", "Jazz ensemble 1", "Jazz ensemble 2", "Jazz ensemble 3"] if model_available else [])
    assert store.digests(None, 10) == before
    h.discover.settings.enabled = False
    assert await picks(h, NOW, agenda(h, NOW)) == []


@pytest.mark.asyncio
async def test_scout_can_choose_zero(h):
    from src.events.morning_agenda import agenda, picks

    h.discover.store.upsert_pool([listing("sale", "Antique sale")], DAY)
    scout = Scout([])
    h._model_for = lambda bot: (scout, "configured-scout")
    assert await picks(h, NOW, agenda(h, NOW)) == []
    assert len(scout.data["candidates"]) == 1


@pytest.mark.asyncio
async def test_removed_dismissed_listing_cannot_reappear_under_a_new_id(h):
    from src.events.morning_agenda import agenda, picks

    store = h.discover.store
    old = listing("original", "The rehearsal")
    weekly = store.save_digest([(old, "A rehearsal suits your interests.")], kind="weekly", note="",
                               session_id="last-week", created_at="2026-10-25")
    store.set_state(weekly["cards"][0]["id"], "dismissed")
    store.upsert_pool([listing("new-id", "The Rehearsal")], DAY)
    scout = Scout([{"id": "new-id", "why": "Alt ID."}])
    h._model_for = lambda bot: (scout, "configured-scout")
    assert await picks(h, NOW, agenda(h, NOW)) == []


@pytest.mark.asyncio
async def test_email_only_refresh_populates_new_pool_and_uses_known_times(h, fake_site):
    from src.events.morning_agenda import agenda, picks

    h.discover.store.set_meta(LAST_REFRESH, "")
    def public(name, **fields):
        return row(name=name, start_date=DAY, end_date=None, end_time=None, **fields)

    candidates = [public("Morning jazz", start_time="10:00"), public("Already started", start_time="06:59"),
                  public("At meeting", start_time="10:45"), public("Missing zone", timezone=None),
                  public("Date only", start_time=None, timezone=None)]
    fake_site.fail[("this", 1)] = httpx.Response(200, text=page_with(candidates))
    fake_site.fail[("next", 1)] = httpx.Response(200, text=page_with([]))
    h.calendar.store.add(timed("Meeting", "2026-11-01T15:30:00Z", "2026-11-01T16:00:00Z"))
    scout = Scout([{"id": c["id"], "why": "Matches your musical interests."} for c in candidates])
    h._model_for = lambda bot: (scout, "configured-scout")
    day = agenda(h, NOW)
    assert not fake_site.requests
    result = await picks(h, NOW, day)
    assert [(p["title"], p["time"]) for p in result] == [("Morning jazz", "10:00 AM"), ("Date only", "Time not listed")]
    assert len(fake_site.requests) == 2
    assert await picks(h, NOW, day) == result and len(fake_site.requests) == 2


@pytest.mark.asyncio
async def test_cached_pick_survives_eventbrite_outage_and_edition_identifies_source(h, fake_site):
    from src.events.morning_agenda import agenda, picks

    store = h.discover.store
    h.discover.store.set_meta(LAST_REFRESH, "")
    fake_site.fail[("this", 1)] = 503
    saved = listing("saved", "Little quartet")
    store.upsert_pool([saved], DAY)
    store.save_digest([(saved, "You enjoy jazz quartets.")], kind="weekly", note="", session_id="weekly",
                      created_at="2026-10-30")
    day = agenda(h, NOW)
    assert [p["title"] for p in await picks(h, NOW, day)] == ["Little quartet"]
    assert any("Eventbrite" in warning for warning in day["warnings"])

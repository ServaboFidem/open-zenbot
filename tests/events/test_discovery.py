import json

import httpx
import pytest

from src.events.discover_store import LAST_ERROR, LAST_REFRESH
from src.events.discovery import (MAX_CARDS, MAX_NOTE, MAX_WHY, REFRESH_AFTER_S, DigestRefused, Discovery)
from tests.events.conftest import page_with, row

A, B, C, EARLIER, PAST = "9000000000001", "9000000000002", "9000000000003", "9000000000004", "9000000000005"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


def ev(id, **over):
    made = {"id": id, "name": f"Event {id}", "summary": "A summary.", "start_date": "2026-12-05",
            "start_time": "18:00", "end_date": "2026-12-05", "end_time": "20:00", "timezone": "America/New_York",
            "venue": "Example Hall", "address": "1 Example Street", "city": "Exampleton", "region": "OH",
            "image_url": f"https://img.evbuc.com/pic-{id}.png", "url": f"https://www.eventbrite.com/e/x-{id}",
            "format": "Concert", "categories": ["EventbriteCategory/103"], "category_names": ["Music"]}
    made.update(over)
    return made


POOL = {i: ev(i) for i in (A, B, C, EARLIER)}
POOL[PAST] = ev(PAST, start_date="2026-11-01")


@pytest.fixture
def seeded(discovery):
    """The pool holds A, B, C, EARLIER (already on a saved card) and PAST (before today)."""
    discovery.store.upsert_pool(list(POOL.values()), "2026-11-18T08:00:00Z")
    discovery.store.save_digest([(POOL[EARLIER], "old")], kind="weekly", note="", session_id="old",
                                created_at="2026-11-10T00:00:00Z")
    return discovery


def image_requests(site):
    return [r for r in site.requests if r.url.host == "img.evbuc.com"]


def page_requests(site):
    return [r for r in site.requests if r.url.host != "img.evbuc.com"]


async def test_refresh_fills_the_pool_and_records_the_time(discovery, fake_site):
    out = await discovery.refresh()
    assert out == {"pages": 5, "events": 1 + 2 * 5, "error": ""}      # 3 + 2 pages; the shared event once
    assert discovery.store.pool_size() == 11
    assert discovery.store.meta(LAST_REFRESH) == "2026-11-18T09:00:00Z"
    assert discovery.store.meta(LAST_ERROR) == ""
    assert len(page_requests(fake_site)) == 5


async def test_refresh_is_skipped_inside_twelve_hours_unless_forced(discovery, fake_site, clock):
    await discovery.refresh()
    first = len(page_requests(fake_site))
    clock.now += REFRESH_AFTER_S - 60
    again = await discovery.refresh()
    assert again == {"pages": 0, "events": 0, "error": ""}
    assert len(page_requests(fake_site)) == first
    forced = await discovery.refresh(force=True)
    assert forced["pages"] == 5 and len(page_requests(fake_site)) == 2 * first
    clock.now += REFRESH_AFTER_S + 60
    await discovery.refresh()
    assert len(page_requests(fake_site)) == 3 * first


async def test_a_page_with_unusable_rows_keeps_the_pool_and_records_source_changed(discovery, fake_site):
    await discovery.refresh()
    pool = discovery.store.candidates("2000-01-01", [], limit=60)
    broken = [{k: v for k, v in row().items() if k != "start_date"} for _ in range(5)]
    fake_site.fail[("this", 1)] = httpx.Response(200, text=page_with(broken, pages=3))
    out = await discovery.refresh(force=True)
    assert out == {"pages": 0, "events": 0, "error": "source_changed"}
    assert discovery.store.candidates("2000-01-01", [], limit=60) == pool
    assert discovery.store.meta(LAST_ERROR) == "source_changed"
    assert discovery.store.meta(LAST_REFRESH) == "2026-11-18T09:00:00Z"


async def test_a_failed_first_page_keeps_the_pool_and_records_the_kind(discovery, fake_site):
    await discovery.refresh()
    pool = discovery.store.candidates("2000-01-01", [], limit=60)
    fake_site.fail[("this", 1)] = httpx.Response(200, text="<html>nothing here</html>")
    out = await discovery.refresh(force=True)
    assert out == {"pages": 0, "events": 0, "error": "source_changed"}
    assert discovery.store.candidates("2000-01-01", [], limit=60) == pool
    assert discovery.store.meta(LAST_ERROR) == "source_changed"
    assert discovery.store.meta(LAST_REFRESH) == "2026-11-18T09:00:00Z"
    entries = [e for e in discovery.ledger.entries if e["event"] == "discover_refresh"]
    assert len(entries) == 2
    last = entries[-1]
    assert (last["pages"], last["events"], last["error"]) == (0, 0, "source_changed")
    assert "Made-up Event" not in json.dumps(last)


async def test_a_cut_short_refresh_keeps_what_it_read_and_says_so(discovery, fake_site):
    fake_site.fail[("this", 2)] = 503
    out = await discovery.refresh()
    assert out["pages"] == 1 and out["events"] == 3 and out["error"] == "provider"
    assert discovery.store.pool_size() == 3
    assert discovery.store.meta(LAST_ERROR) == "provider"
    assert discovery.store.meta(LAST_REFRESH) == "2026-11-18T09:00:00Z"


async def test_two_refreshes_at_once_fetch_once(discovery, fake_site):
    import asyncio

    one, two = await asyncio.gather(discovery.refresh(), discovery.refresh())
    assert sorted((one["pages"], two["pages"])) == [0, 5]
    assert len(page_requests(fake_site)) == 5


async def test_refresh_drops_past_events_and_does_nothing_when_not_active(discovery, fake_site):
    discovery.store.upsert_pool([ev(PAST, start_date="2026-11-01")], "x")
    await discovery.refresh()
    assert discovery.store.pool_row(PAST) is None
    discovery.settings.enabled = False
    assert not discovery.active
    before = len(fake_site.requests)
    out = await discovery.refresh(force=True)
    assert out["pages"] == 0 and len(fake_site.requests) == before


async def test_save_takes_only_ids_and_whys(seeded):
    saved = await seeded.save([{"id": A, "why": "You like jazz.", "name": "Injected", "url": "https://evil.example"}],
                              kind="weekly", note="", session_id="s1")
    card = saved["cards"][0]
    assert card["name"] == POOL[A]["name"] and card["url"] == POOL[A]["url"] and card["why"] == "You like jazz."
    assert card["event_id"] == A and saved["session_id"] == "s1" and saved["created_at"] == "2026-11-18T09:00:00Z"


@pytest.mark.parametrize("cards, reason", [
    ([{"id": "999", "why": "x"}], "unknown_event"),
    ([{"id": EARLIER, "why": "x"}], "not_a_candidate"),
    ([{"id": PAST, "why": "x"}], "not_a_candidate"),
    ([{"id": A, "why": "x"}, {"id": A, "why": "y"}], "duplicate_in_batch"),
    ([{"id": A, "why": ""}], "bad_why"), ([{"id": A, "why": "w" * 301}], "bad_why"),
    ([{"id": A, "why": "line\nbreak"}], "bad_why"), ([{"id": A, "why": "tab\there"}], "bad_why"),
    ([{"id": A, "why": "sep x"}], "bad_why"), ([{"id": A, "why": 7}], "bad_why"), ([{"id": A}], "bad_why"),
])
async def test_save_refuses_the_whole_digest(seeded, fake_site, cards, reason):
    with pytest.raises(DigestRefused) as e:
        await seeded.save([{"id": B, "why": "fine"}, *cards], kind="weekly", note="", session_id="s1")
    assert reason in [r for f in e.value.failures for r in f["reasons"]]
    assert [d["session_id"] for d in seeded.store.digests(None, 10)] == ["old"]    # nothing new, B included
    assert image_requests(fake_site) == []


async def test_save_refuses_a_card_that_is_not_an_object(seeded):
    with pytest.raises(DigestRefused) as e:
        await seeded.save(["nope", {"id": B, "why": "fine"}], kind="weekly", note="", session_id="s1")
    assert "unknown_event" in e.value.failures[0]["reasons"] and len(e.value.failures) == 1


async def test_save_bounds(seeded):
    ok = {"id": A, "why": "fine"}
    with pytest.raises(ValueError):
        await seeded.save([], kind="weekly", note="", session_id="s")
    ids = [str(9100000000000 + i) for i in range(MAX_CARDS + 1)]
    seeded.store.upsert_pool([ev(i) for i in ids], "x")
    with pytest.raises(ValueError):
        await seeded.save([{"id": i, "why": "w"} for i in ids], kind="weekly", note="", session_id="s")
    with pytest.raises(ValueError):
        await seeded.save([ok], kind="daily", note="", session_id="s")
    with pytest.raises(ValueError):
        await seeded.save([ok], kind="weekly", note="n" * (MAX_NOTE + 1), session_id="s")
    with pytest.raises(ValueError):
        await seeded.save("not a list", kind="weekly", note="", session_id="s")
    assert [d["session_id"] for d in seeded.store.digests(None, 10)] == ["old"]
    eight = await seeded.save([{"id": i, "why": "w" * MAX_WHY} for i in ids[:MAX_CARDS]], kind="on_demand",
                              note="n" * MAX_NOTE, session_id="s")
    assert len(eight["cards"]) == MAX_CARDS


async def test_thumbnails_are_fetched_for_saved_cards_only(seeded, fake_site):
    fake_site.images[f"https://img.evbuc.com/pic-{A}.png"] = httpx.Response(200, content=PNG)
    # B's picture is missing (404): the save still succeeds
    saved = await seeded.save([{"id": A, "why": "a"}, {"id": B, "why": "b"}], kind="weekly", note="",
                              session_id="s1")
    assert sorted(str(r.url) for r in image_requests(fake_site)) == [f"https://img.evbuc.com/pic-{A}.png",
                                                                   f"https://img.evbuc.com/pic-{B}.png"]
    by_event = {c["event_id"]: c for c in saved["cards"]}
    assert by_event[A]["has_image"] == 1 and by_event[B]["has_image"] == 0
    path = seeded.image_path(by_event[A]["id"])
    assert path is not None and path.read_bytes() == PNG and path.name == by_event[A]["id"]
    assert path.parent == seeded.images_dir and path.parent.name == "discover-images"
    assert path.parent.parent.name.endswith("-calendar")
    assert seeded.image_path(by_event[B]["id"]) is None
    assert seeded.image_path("../../x") is None and seeded.image_path("0" * 32) is None
    assert not [r for r in image_requests(fake_site) if str(C) in str(r.url)]      # C was never saved


async def test_a_card_without_a_picture_link_asks_for_nothing(seeded, fake_site):
    seeded.store.upsert_pool([ev(C, image_url="")], "x")
    saved = await seeded.save([{"id": C, "why": "c"}], kind="weekly", note="", session_id="s1")
    assert image_requests(fake_site) == [] and saved["cards"][0]["has_image"] == 0


async def test_the_ledger_has_counts_only(seeded, fake_site):
    fake_site.images[f"https://img.evbuc.com/pic-{A}.png"] = httpx.Response(200, content=PNG)
    await seeded.save([{"id": A, "why": "Secret reason text."}], kind="weekly", note="A private note.",
                      session_id="s1")
    entry = [e for e in seeded.ledger.entries if e["event"] == "discover_digest"][-1]
    assert entry["cards"] == 1 and entry["images"] == 1 and entry["digest_kind"] == "weekly"
    text = json.dumps(entry)
    assert "Secret reason" not in text and "private note" not in text and POOL[A]["name"] not in text
    assert str(seeded.images_dir.parent) not in text


async def test_a_place_change_clears_the_pool_and_the_refresh_state_but_keeps_saved_cards(seeded, fake_site):
    from src.events.discover_settings import DiscoverSettings
    from src.events.discovery import LAST_FAILURE

    await seeded.refresh()
    seeded.store.set_meta(LAST_ERROR, "network")
    seeded.store.set_meta(LAST_FAILURE, "2026-11-18T09:00:00Z")
    before = len(page_requests(fake_site))
    await seeded.apply(DiscoverSettings(enabled=True, place="oh--elsewhere"))
    assert seeded.store.pool_size() == 0
    assert seeded.candidates() == []
    assert (seeded.store.meta(LAST_REFRESH), seeded.store.meta(LAST_ERROR), seeded.store.meta(LAST_FAILURE)) == ("", "", "")
    assert len(seeded.store.digests(None, 10)) == 1      # the saved card's digest stays
    out = await seeded.refresh()                 # fetches at once: nothing is fresh, nothing recently failed
    assert out["pages"] > 0 and len(page_requests(fake_site)) > before
    assert "oh--elsewhere" in str(page_requests(fake_site)[-1].url)


async def test_saving_the_same_place_keeps_the_pool(seeded):
    from src.events.discover_settings import DiscoverSettings

    size = seeded.store.pool_size()
    await seeded.apply(DiscoverSettings(enabled=True, place="oh--exampleton", interests="jazz"))
    assert seeded.store.pool_size() == size


async def test_a_note_with_a_control_character_is_refused(seeded):
    for bad in ("two" + chr(10) + "lines", "tab" + chr(9) + "here", "bell" + chr(7), "sep" + chr(0x2028) + "x"):
        with pytest.raises(ValueError):
            await seeded.save([{"id": A, "why": "w"}], kind="weekly", note=bad, session_id="s")
    assert [d["session_id"] for d in seeded.store.digests(None, 10)] == ["old"]

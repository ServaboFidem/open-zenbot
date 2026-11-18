import json

import httpx
import pytest

from src.events.discover_store import LAST_ERROR, LAST_REFRESH
from src.events.discovery import (FINDLOCAL_AFTER_S, FL_LAST_ERROR, FL_LAST_FAILURE, FL_LAST_REFRESH, MAX_CARDS,
                                  MAX_NOTE, MAX_WHY, REFRESH_AFTER_S, DigestRefused, Discovery)
from tests.events.conftest import FakeSite, fl_row, page_with, row

A, B, C, EARLIER, PAST = "9000000000001", "9000000000002", "9000000000003", "9000000000004", "9000000000005"
FL_NONE = {"calls": 0, "events": 0, "error": ""}
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
    assert out == {"pages": 5, "events": 1 + 2 * 5, "error": "", "findlocal": FL_NONE}  # 3 + 2 pages; shared once
    assert discovery.store.pool_size() == 11
    assert discovery.store.meta(LAST_REFRESH) == "2026-11-18T09:00:00Z"
    assert discovery.store.meta(LAST_ERROR) == ""
    assert len(page_requests(fake_site)) == 5


async def test_refresh_is_skipped_inside_twelve_hours_unless_forced(discovery, fake_site, clock):
    await discovery.refresh()
    first = len(page_requests(fake_site))
    clock.now += REFRESH_AFTER_S - 60
    again = await discovery.refresh()
    assert again == {"pages": 0, "events": 0, "error": "", "findlocal": FL_NONE}
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
    assert out == {"pages": 0, "events": 0, "error": "source_changed", "findlocal": FL_NONE}
    assert discovery.store.candidates("2000-01-01", [], limit=60) == pool
    assert discovery.store.meta(LAST_ERROR) == "source_changed"
    assert discovery.store.meta(LAST_REFRESH) == "2026-11-18T09:00:00Z"


async def test_a_failed_first_page_keeps_the_pool_and_records_the_kind(discovery, fake_site):
    await discovery.refresh()
    pool = discovery.store.candidates("2000-01-01", [], limit=60)
    fake_site.fail[("this", 1)] = httpx.Response(200, text="<html>nothing here</html>")
    out = await discovery.refresh(force=True)
    assert out == {"pages": 0, "events": 0, "error": "source_changed", "findlocal": FL_NONE}
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


# ---- FindLocal ------------------------------------------------------------------------------------------

HOUR = 3600


def fl(n: int, **over) -> dict:
    return fl_row(**{"id": f"0b1c2d3e-0000-4000-8000-{n:012d}", "title": f"Findlocal Show {n}", **over})


async def set_up_findlocal(d, key: str = "KEY") -> None:
    from dataclasses import replace

    await d.apply(replace(d.settings, findlocal_city="columbus", timezone="America/New_York"))
    await d.set_key(key)


def fl_ids(d) -> list[str]:
    return [r["id"] for r in d.store.candidates("2000-01-01", [], limit=60) if r["source"] == "findlocal"]


async def test_findlocal_is_not_read_until_it_is_ready(discovery, fake_findlocal):
    from dataclasses import replace

    fake_findlocal.rows = [fl(1)]
    await discovery.apply(replace(discovery.settings, findlocal_city="columbus", timezone="America/New_York"))
    assert not discovery.findlocal_ready
    out = await discovery.refresh(weekly=True)
    assert out["findlocal"] == FL_NONE and fake_findlocal.requests == []
    await discovery.set_key("KEY")
    await discovery.apply(replace(discovery.settings, findlocal_city="", timezone=""))
    assert not discovery.findlocal_ready
    out = await discovery.refresh(weekly=True)
    assert out["findlocal"] == FL_NONE and fake_findlocal.requests == []


async def test_the_first_read_happens_on_any_run(discovery, fake_findlocal):
    fake_findlocal.rows = [fl(1), fl(2)]
    await set_up_findlocal(discovery)
    assert discovery.findlocal_ready
    out = await discovery.refresh()
    assert out["findlocal"] == {"calls": 1, "events": 2, "error": ""}
    assert len(fake_findlocal.requests) == 1
    rows = [discovery.store.pool_row(i) for i in fl_ids(discovery)]
    assert len(rows) == 2 and all(r["source"] == "findlocal" and r["timezone"] == "America/New_York" for r in rows)
    assert discovery.store.meta(FL_LAST_REFRESH) == "2026-11-18T09:00:00Z"


async def test_an_on_demand_refresh_does_not_read_again(discovery, fake_findlocal, clock):
    fake_findlocal.rows = [fl(1)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    clock.now += 8 * 24 * HOUR
    out = await discovery.refresh()
    assert out["findlocal"] == FL_NONE and len(fake_findlocal.requests) == 1


async def test_the_weekly_run_reads_after_twenty_hours(discovery, fake_findlocal, clock):
    assert FINDLOCAL_AFTER_S == 20 * HOUR
    fake_findlocal.rows = [fl(1)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    clock.now += 19 * HOUR
    await discovery.refresh(weekly=True)
    assert len(fake_findlocal.requests) == 1
    clock.now += 2 * HOUR
    out = await discovery.refresh(weekly=True)
    assert len(fake_findlocal.requests) == 2 and out["findlocal"]["calls"] == 1


async def test_force_does_not_read_findlocal(discovery, fake_findlocal, fake_site, clock):
    fake_findlocal.rows = [fl(1)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    clock.now += 21 * HOUR
    before = len(page_requests(fake_site))
    out = await discovery.refresh(force=True)
    assert out["pages"] == 5 and len(page_requests(fake_site)) > before     # Eventbrite is forced
    assert out["findlocal"] == FL_NONE and len(fake_findlocal.requests) == 1


async def test_a_failed_read_waits_ten_minutes_and_leaves_the_pool(discovery, fake_findlocal, clock):
    fake_findlocal.rows = [fl(1)]
    fake_findlocal.fail[1] = 429
    await set_up_findlocal(discovery)
    out = await discovery.refresh()
    assert out["findlocal"] == {"calls": 0, "events": 0, "error": "refused"} and out["error"] == ""
    assert discovery.store.pool_size("findlocal") == 0
    assert len(discovery.candidates()) == 11                      # Eventbrite's rows are still candidates
    assert discovery.store.meta(FL_LAST_ERROR) == "refused" and discovery.store.meta(FL_LAST_REFRESH) == ""
    clock.now += 5 * 60
    await discovery.refresh(weekly=True)
    assert len(fake_findlocal.requests) == 1
    del fake_findlocal.fail[1]
    clock.now += 6 * 60
    out = await discovery.refresh(weekly=True)
    assert len(fake_findlocal.requests) == 2 and out["findlocal"] == {"calls": 1, "events": 1, "error": ""}
    assert discovery.store.meta(FL_LAST_ERROR) == "" and discovery.store.meta(FL_LAST_FAILURE) == ""


async def test_a_partial_read_keeps_what_it_got(discovery, fake_findlocal):
    fake_findlocal.rows = [fl(n) for n in range(1, 151)]
    fake_findlocal.fail[2] = 503
    await set_up_findlocal(discovery)
    out = await discovery.refresh()
    assert out["findlocal"] == {"calls": 1, "events": 100, "error": "provider"}
    assert discovery.store.pool_size("findlocal") == 100
    assert discovery.store.meta(FL_LAST_REFRESH) == "2026-11-18T09:00:00Z"
    assert discovery.store.meta(FL_LAST_ERROR) == "provider" and discovery.store.meta(FL_LAST_FAILURE) == ""


async def test_duplicates_of_eventbrite_rows_are_not_stored(discovery, fake_findlocal):
    fake_findlocal.rows = [
        fl(1, ticket_page_url=f"https://www.eventbrite.com/e/made-up-event-tickets-{FakeSite.SHARED_ID}"),
        fl(2, title="Made-up Event", event_date="2026-12-05"),
        fl(3),
    ]
    await set_up_findlocal(discovery)
    out = await discovery.refresh()
    assert out["findlocal"]["events"] == 1
    assert fl_ids(discovery) == ["fl-0b1c2d3e-0000-4000-8000-000000000003"]


FL1 = "fl-0b1c2d3e-0000-4000-8000-000000000001"
FL2 = "fl-0b1c2d3e-0000-4000-8000-000000000002"


async def test_a_findlocal_row_is_a_candidate_a_week_after_it_was_read(discovery, fake_findlocal, clock):
    fake_findlocal.rows = [fl(1)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    clock.now += 6 * 24 * HOUR
    await discovery.refresh()
    assert FL1 in [r["id"] for r in discovery.candidates()]
    assert discovery.candidate_count() == len(discovery.candidates())
    assert discovery.store.is_candidate(FL1, discovery.today(), [])
    saved = await discovery.save([{"id": FL1, "why": "fine"}], kind="weekly", note="", session_id="s1")
    assert [c["event_id"] for c in discovery.store.digest(saved["id"])["cards"]] == [FL1]


async def test_a_complete_findlocal_read_drops_rows_it_did_not_see(discovery, fake_findlocal, clock):
    fake_findlocal.rows = [fl(1), fl(2)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    assert sorted(fl_ids(discovery)) == [FL1, FL2]
    fake_findlocal.rows = [fl(2)]
    clock.now += 7 * 24 * HOUR
    out = await discovery.refresh(weekly=True)
    assert out["findlocal"]["error"] == ""
    assert fl_ids(discovery) == [FL2]


async def test_a_failed_findlocal_read_drops_nothing(discovery, fake_findlocal, clock):
    fake_findlocal.rows = [fl(1), fl(2)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    fake_findlocal.fail[1] = 429
    clock.now += 7 * 24 * HOUR
    out = await discovery.refresh(weekly=True)
    assert out["findlocal"]["error"] == "refused"
    assert sorted(fl_ids(discovery)) == [FL1, FL2]


async def test_a_partial_findlocal_read_drops_nothing(discovery, fake_findlocal, clock):
    fake_findlocal.rows = [fl(1), fl(2)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    fake_findlocal.rows = [fl(n) for n in range(2, 152)]
    fake_findlocal.fail[2] = 503
    clock.now += 7 * 24 * HOUR
    out = await discovery.refresh(weekly=True)
    assert out["findlocal"]["error"] == "provider"
    assert FL1 in fl_ids(discovery) and FL2 in fl_ids(discovery)


async def test_forgetting_the_key_clears_findlocal_rows_and_its_last_read(discovery, fake_findlocal):
    fake_findlocal.rows = [fl(1)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    eb = discovery.store.pool_size("eventbrite")
    assert discovery.store.pool_size("findlocal") == 1 and eb > 0
    await discovery.set_key(None)
    assert discovery.store.pool_size("findlocal") == 0
    assert discovery.store.pool_size("eventbrite") == eb
    for k in (FL_LAST_REFRESH, FL_LAST_ERROR, FL_LAST_FAILURE):
        assert discovery.store.meta(k) == ""
    await discovery.set_key("KEY-abcdefgh")
    await discovery.refresh()
    assert len(fake_findlocal.requests) == 2 and discovery.store.pool_size("findlocal") == 1


async def test_saving_a_new_key_over_an_old_one_keeps_the_rows(discovery, fake_findlocal):
    fake_findlocal.rows = [fl(1)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    await discovery.set_key("NEW-abcdefgh")
    assert fl_ids(discovery) == [FL1]
    assert discovery.store.meta(FL_LAST_REFRESH) != ""


async def test_a_saved_findlocal_card_stays(discovery, fake_findlocal, clock):
    fake_findlocal.rows = [fl(1, event_date="2026-12-20")]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    fid = "fl-0b1c2d3e-0000-4000-8000-000000000001"
    saved = await discovery.save([{"id": fid, "why": "fine"}], kind="weekly", note="", session_id="s1")
    clock.now += 8 * 24 * HOUR
    await discovery.refresh()
    digest = discovery.store.digest(saved["id"])
    assert [c["event_id"] for c in digest["cards"]] == [fid] and digest["cards"][0]["source"] == "findlocal"


async def test_changing_the_findlocal_city_clears_only_its_rows(discovery, fake_findlocal):
    from dataclasses import replace

    fake_findlocal.rows = [fl(1)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    eventbrite = discovery.store.pool_size("eventbrite")
    discovery.store.set_meta(FL_LAST_FAILURE, "2026-11-18T09:00:00Z")
    await discovery.apply(replace(discovery.settings, findlocal_city="chicago", timezone="America/Chicago"))
    assert discovery.store.pool_size("findlocal") == 0 and discovery.store.pool_size("eventbrite") == eventbrite
    assert all(discovery.store.meta(k) == "" for k in (FL_LAST_REFRESH, FL_LAST_ERROR, FL_LAST_FAILURE))
    assert discovery.store.meta(LAST_REFRESH) == "2026-11-18T09:00:00Z"


async def test_changing_the_place_clears_only_eventbrite_rows(discovery, fake_findlocal):
    from dataclasses import replace

    fake_findlocal.rows = [fl(1)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    await discovery.apply(replace(discovery.settings, place="oh--elsewhere"))
    assert discovery.store.pool_size("eventbrite") == 0 and discovery.store.pool_size("findlocal") == 1
    assert discovery.store.meta(LAST_REFRESH) == ""
    assert discovery.store.meta(FL_LAST_REFRESH) == "2026-11-18T09:00:00Z"


async def test_a_new_key_clears_the_last_error(discovery, fake_findlocal):
    fake_findlocal.rows = [fl(1)]
    fake_findlocal.fail[1] = 401
    await set_up_findlocal(discovery)
    out = await discovery.refresh()
    assert out["findlocal"]["error"] == "key" and discovery.status({})["findlocal"]["last_error"] == "key"
    del fake_findlocal.fail[1]
    await discovery.set_key("NEW")
    assert discovery.keys.findlocal == "NEW"
    assert discovery.status({})["findlocal"]["last_error"] == ""
    assert discovery.store.meta(FL_LAST_FAILURE) == ""
    out = await discovery.refresh()
    assert len(fake_findlocal.requests) == 2 and out["findlocal"]["events"] == 1
    assert fake_findlocal.requests[-1].headers["authorization"] == "Bearer NEW"


async def test_set_key_checks_and_forgets(discovery):
    from src.events.discover_settings import DiscoverKeys

    with pytest.raises(ValueError):
        await discovery.set_key("has space")
    await discovery.set_key("  KEY-abcdefgh ")
    assert DiscoverKeys.load(discovery.vault).findlocal == "KEY-abcdefgh"
    await discovery.set_key(None)
    assert discovery.keys.findlocal == "" and DiscoverKeys.load(discovery.vault).findlocal == ""


async def test_is_weekly_run(discovery):
    from types import SimpleNamespace

    class Routines:
        def __init__(self):
            self.items = []

        def all(self):
            return list(self.items)

        def upsert(self, r):
            self.items = [x for x in self.items if x.id != r.id] + [r]

        def save(self):
            pass

    assert not discovery.is_weekly_run("s1")                       # no routine store, no scheduler
    discovery.routines = Routines()
    discovery.scheduler = SimpleNamespace(sessions={})
    assert not discovery.is_weekly_run("s1")                       # no routine
    routine = discovery.ensure_routine()
    discovery.scheduler.sessions[routine.id] = SimpleNamespace(id="s1")
    discovery.on_demand_sessions.add("s3")
    assert discovery.is_weekly_run("s1")
    assert not discovery.is_weekly_run("s2") and not discovery.is_weekly_run("")
    assert not discovery.is_weekly_run("s3")
    discovery.scheduler = None
    assert not discovery.is_weekly_run("s1")


async def test_ledger_entries_name_the_source_and_nothing_else(discovery, fake_findlocal, clock):
    fake_findlocal.rows = [fl(1), fl(2)]
    await set_up_findlocal(discovery)
    await discovery.refresh()
    fake_findlocal.fail[1] = 429
    clock.now += 21 * HOUR
    await discovery.refresh(weekly=True)
    entries = [e for e in discovery.ledger.entries if e["event"] == "discover_refresh"]
    assert [e["source"] for e in entries] == ["eventbrite", "findlocal", "eventbrite", "findlocal"]
    assert all(set(e) <= {"_kind", "event", "source", "pages", "events", "error"} for e in entries)
    assert (entries[1]["pages"], entries[1]["events"], entries[1]["error"]) == (1, 2, "")
    assert (entries[3]["pages"], entries[3]["events"], entries[3]["error"]) == (0, 0, "refused")
    text = json.dumps(discovery.ledger.entries)
    assert "KEY" not in text and "Findlocal Show" not in text and "Made-up Event" not in text


async def test_status_never_holds_the_key(discovery):
    await set_up_findlocal(discovery, key="KEY-abcdefgh")
    status = discovery.status({})
    assert "KEY-abcdefgh" not in json.dumps(status)
    assert status["findlocal"] == {"city": "columbus", "timezone": "America/New_York", "key_set": True,
                                   "key_hint": "efgh", "last_refresh": "", "last_error": "", "ready": True}


# the final review: any exception in a FindLocal read is a recorded failure, never Eventbrite's ------------

class RaisingReader:
    """A FindLocal reader whose read raises ``error``; ``calls`` counts the reads."""

    def __init__(self, error: Exception):
        self.error = error
        self.calls = 0

    async def read(self, *args, **kwargs):
        self.calls += 1
        raise self.error


def fl_failures_logged(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.name == "src.events.discovery")


async def test_an_unexpected_exception_in_a_findlocal_read_is_provider_and_waits(discovery, clock, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    await set_up_findlocal(discovery)
    reader = discovery.findlocal = RaisingReader(RuntimeError("KEY-abcdefgh inside"))
    out = await discovery.refresh()
    assert out["findlocal"] == {"calls": 0, "events": 0, "error": "provider"} and out["error"] == ""
    assert len(discovery.candidates()) == 11                      # Eventbrite's rows are still candidates
    assert discovery.store.meta(FL_LAST_ERROR) == "provider" and discovery.store.meta(FL_LAST_FAILURE)
    clock.now += 4 * 60
    again = await discovery.refresh()
    clock.now += 4 * 60
    await discovery.refresh(weekly=True)
    assert reader.calls == 1
    assert again["findlocal"] == {"calls": 0, "events": 0, "error": "provider"}
    logged = fl_failures_logged(caplog)
    assert "RuntimeError" in logged and "KEY-abcdefgh" not in logged and "inside" not in logged
    assert all(r.exc_info is None for r in caplog.records)
    entries = [e for e in discovery.ledger.entries if e.get("source") == "findlocal"]
    assert entries and entries[-1]["error"] == "provider" and "KEY-abcdefgh" not in json.dumps(entries)


async def test_a_store_error_after_a_good_findlocal_read_is_provider_and_waits(discovery, fake_findlocal, clock,
                                                                              caplog):
    import logging
    import sqlite3

    caplog.set_level(logging.DEBUG)
    await discovery.refresh()                                     # Eventbrite is read and fresh for 12 hours
    fake_findlocal.rows = [fl(1)]
    await set_up_findlocal(discovery)
    real = discovery.store.upsert_pool

    def upsert(events, *args, **kwargs):
        if events:
            raise sqlite3.OperationalError("KEY-abcdefgh inside")
        return real(events, *args, **kwargs)

    discovery.store.upsert_pool = upsert
    out = await discovery.refresh()
    assert out["findlocal"] == {"calls": 0, "events": 0, "error": "provider"}
    assert len(discovery.candidates()) == 11
    assert discovery.store.meta(FL_LAST_ERROR) == "provider" and discovery.store.meta(FL_LAST_FAILURE)
    assert discovery.store.meta(FL_LAST_REFRESH) == ""
    clock.now += 4 * 60
    await discovery.refresh()
    clock.now += 4 * 60
    await discovery.refresh(weekly=True)
    assert len(fake_findlocal.requests) == 1
    logged = fl_failures_logged(caplog)
    assert "OperationalError" in logged and "KEY-abcdefgh" not in logged and "inside" not in logged


async def test_a_value_error_from_a_findlocal_read_is_provider_with_a_failure_time(discovery, clock):
    await set_up_findlocal(discovery)
    reader = discovery.findlocal = RaisingReader(ValueError("not a city"))
    out = await discovery.refresh()
    assert out["findlocal"]["error"] == "provider"
    assert discovery.store.meta(FL_LAST_FAILURE) == "2026-11-18T09:00:00Z"
    clock.now += 5 * 60
    assert (await discovery.refresh())["findlocal"] == {"calls": 0, "events": 0, "error": "provider"}
    assert reader.calls == 1


async def test_a_failed_key_save_keeps_the_old_key(discovery, monkeypatch):
    from src.events.discover_settings import DiscoverKeys

    await discovery.set_key("OLD-KEY")

    def refuse(self, vault_root):
        raise OSError("disk full")

    monkeypatch.setattr(DiscoverKeys, "save", refuse)
    with pytest.raises(OSError):
        await discovery.set_key("NEW-KEY")
    assert discovery.keys.findlocal == "OLD-KEY"

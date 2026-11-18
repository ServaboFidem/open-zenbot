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


async def test_save_refuses_an_event_the_user_added(seeded):
    seeded.store.mark_event_added(A, "cal1", "2026-11-18T09:00:00Z")
    with pytest.raises(DigestRefused):
        await seeded.save([{"id": A, "why": "x"}], kind="weekly", note="", session_id="s1")


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
    clock.now += 21 * HOUR
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


# ---- a refresh in the background (POST /discover/refresh) ---------------------------------------------

async def test_start_refresh_runs_one_refresh_in_the_background(discovery, fake_site):
    assert discovery.refreshing is False and discovery.refresh_task is None
    assert discovery.start_refresh() is True
    assert discovery.refreshing is True
    assert discovery.start_refresh() is False               # one at a time
    await discovery.refresh_task
    assert discovery.refreshing is False and discovery.store.pool_size() > 0
    pages = len(page_requests(fake_site))
    assert discovery.start_refresh() is True                 # done: another may start, and is not forced
    await discovery.refresh_task
    assert len(page_requests(fake_site)) == pages


async def test_a_background_refresh_that_raises_is_logged_by_class_name_only(discovery, caplog):
    async def boom(*a, **k):
        raise RuntimeError("secret listing text")

    discovery.refresh = boom
    assert discovery.start_refresh() is True
    await discovery.refresh_task                             # nothing escapes
    assert discovery.refreshing is False
    assert "RuntimeError" in caplog.text and "secret listing text" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)


async def test_close_cancels_a_background_refresh(tmp_path, discovery):
    import asyncio

    started = asyncio.Event()

    async def forever(*a, **k):
        started.set()
        await asyncio.Event().wait()

    discovery.refresh = forever
    discovery.start_refresh()
    await started.wait()
    task = discovery.refresh_task
    await discovery.close()
    assert task.cancelled() and discovery.refreshing is False


# ---- a pool event's picture (Discovery.pool_image, sweep_pool_images) ----------------------------------

FL_ID = "fl-0b1c2d3e-0000-4000-8000-000000000001"


def pic(id) -> str:
    return f"https://img.evbuc.com/pic-{id}.png"


def kept(d) -> list[str]:
    return sorted(p.name for p in d.pool_images_dir.iterdir()) if d.pool_images_dir.exists() else []


@pytest.fixture
def pictured(discovery, fake_site):
    """A, B and C in the pool, each with a picture the image host serves."""
    discovery.store.upsert_pool([ev(i) for i in (A, B, C)], "2026-11-18T08:00:00Z")
    for i in (A, B, C):
        fake_site.images[pic(i)] = httpx.Response(200, content=PNG)
    return discovery


async def test_a_pool_picture_is_fetched_once_and_kept(pictured, fake_site):
    path = await pictured.pool_image(A)
    assert path == pictured.pool_images_dir / A and path.read_bytes() == PNG
    assert pictured.pool_images_dir == pictured.images_dir / "pool"
    assert [str(r.url) for r in image_requests(fake_site)] == [pic(A)]
    assert await pictured.pool_image(A) == path
    assert len(fake_site.requests) == 1 and kept(pictured) == [A]          # no second fetch, no part file left


@pytest.mark.parametrize("bad", ["../x", FL_ID, "12a", "1" * 31, "", "１２", None, 12])
async def test_a_pool_picture_needs_an_id_of_digits(discovery, fake_site, bad):
    rows = [ev("12a"), ev("1" * 31), ev(FL_ID, source="findlocal")]
    discovery.store.upsert_pool(rows, "2026-11-18T08:00:00Z")
    for r in rows:
        fake_site.images[r["image_url"]] = httpx.Response(200, content=PNG)
    assert await discovery.pool_image(bad) is None
    assert fake_site.requests == [] and not discovery.images_dir.exists()


async def test_no_picture_for_findlocal_a_row_without_one_or_an_unknown_id(discovery, fake_site):
    discovery.store.upsert_pool([ev(A, source="findlocal"), ev(B, image_url=""),
                                 ev(C, image_url="https://evil.example/pic.png")], "2026-11-18T08:00:00Z")
    fake_site.images[pic(A)] = httpx.Response(200, content=PNG)
    for i in (A, B, C, "9999"):
        assert await discovery.pool_image(i) is None
    assert fake_site.requests == [] and kept(discovery) == []


@pytest.mark.parametrize("answer", [httpx.Response(404), httpx.Response(200, content=b"<html>not a picture"),
                                    httpx.Response(200, content=PNG + b"0" * 2_000_000),
                                    httpx.Response(302, headers={"location": "https://evil.example/x.png"})])
async def test_a_failed_fetch_keeps_nothing_and_is_tried_again(pictured, fake_site, answer):
    fake_site.images[pic(A)] = answer
    assert await pictured.pool_image(A) is None
    assert kept(pictured) == []
    fake_site.images[pic(A)] = httpx.Response(200, content=PNG)
    assert (await pictured.pool_image(A)).read_bytes() == PNG
    assert {r.url.host for r in fake_site.requests} == {"img.evbuc.com"} and len(fake_site.requests) == 2


async def test_a_fetch_that_raises_is_logged_by_class_name_only(pictured, caplog):
    async def boom(url):
        raise RuntimeError("secret listing text")

    pictured.source.image = boom
    assert await pictured.pool_image(A) is None
    assert "RuntimeError" in caplog.text and "secret listing text" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records) and kept(pictured) == []


async def test_a_write_that_fails_leaves_no_part_file(pictured, caplog):
    (pictured.pool_images_dir / A).mkdir(parents=True)        # the rename onto it fails
    assert await pictured.pool_image(A) is None
    assert kept(pictured) == [A] and (pictured.pool_images_dir / A).is_dir()
    assert "Error" in caplog.text and str(pictured.images_dir) not in caplog.text


async def test_a_linked_picture_folder_is_refused_and_nothing_is_written_through_it(pictured, fake_site, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    pictured.images_dir.mkdir(parents=True)
    pictured.pool_images_dir.symlink_to(elsewhere, target_is_directory=True)
    assert await pictured.pool_image(A) is None
    assert list(elsewhere.iterdir()) == [] and fake_site.requests == []


async def test_a_linked_images_folder_is_refused_too(pictured, fake_site, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    pictured.images_dir.parent.mkdir(parents=True, exist_ok=True)
    pictured.images_dir.symlink_to(elsewhere, target_is_directory=True)
    assert await pictured.pool_image(A) is None
    assert list(elsewhere.iterdir()) == [] and fake_site.requests == []


async def test_a_kept_link_is_not_answered_and_a_planted_part_link_is_not_written_through(pictured, tmp_path):
    secret = tmp_path / "secret"
    secret.write_bytes(b"secret")
    pictured.pool_images_dir.mkdir(parents=True)
    (pictured.pool_images_dir / A).symlink_to(secret)
    (pictured.pool_images_dir / f"{A}.part").symlink_to(secret)
    path = await pictured.pool_image(A)
    assert path is not None and not path.is_symlink() and path.read_bytes() == PNG
    assert secret.read_bytes() == b"secret" and kept(pictured) == [A]


async def test_two_requests_for_one_picture_fetch_once(pictured, fake_site):
    import asyncio

    first, second = await asyncio.gather(pictured.pool_image(A), pictured.pool_image(A))
    assert first == second == pictured.pool_images_dir / A
    assert len(fake_site.requests) == 1


async def test_picture_fetches_are_a_gap_apart(pictured, fake_site, clock):
    from src.events.eventbrite import GAP_S

    await pictured.pool_image(A)
    await pictured.pool_image(B)                    # straight after: waits the whole gap
    assert fake_site.sleeps == [GAP_S]
    clock.now += 0.25
    fake_site.images[pic(C)] = httpx.Response(404)
    await pictured.pool_image(C)                    # a quarter of the gap passed: waits the rest
    assert fake_site.sleeps == [GAP_S, GAP_S - 0.25]
    clock.now += 5
    fake_site.images[pic(C)] = httpx.Response(200, content=PNG)
    await pictured.pool_image(C)                    # long after the last (failed) fetch: no wait
    assert len(fake_site.sleeps) == 2 and len(fake_site.requests) == 4


async def test_sweep_removes_pictures_of_events_no_longer_in_the_pool(pictured, tmp_path):
    assert pictured.sweep_pool_images() == 0                                  # no folder yet
    await pictured.pool_image(A)
    folder = pictured.pool_images_dir
    secret = tmp_path / "secret"
    secret.write_bytes(b"secret")
    (folder / "4242").write_bytes(PNG)                                       # its event left the pool
    (folder / f"{B}.part").write_bytes(b"half")                              # a stray part file
    (folder / "8888").symlink_to(secret)                                     # a link: removed, never followed
    (folder / "sub").mkdir()                                                 # a folder is left alone
    pictured.store.upsert_pool([ev(FL_ID, source="findlocal")], "x")
    (folder / FL_ID).write_bytes(PNG)                                        # not an Eventbrite pool id
    assert pictured.sweep_pool_images() == 4
    assert kept(pictured) == [A, "sub"] and secret.read_bytes() == b"secret"


async def test_sweep_never_follows_a_linked_folder(pictured, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "4242").write_bytes(PNG)
    pictured.images_dir.mkdir(parents=True)
    pictured.pool_images_dir.symlink_to(elsewhere, target_is_directory=True)
    assert pictured.sweep_pool_images() == 0 and (elsewhere / "4242").exists()


async def test_a_refresh_sweeps_after_reading_and_waits_for_a_fetch_in_flight(discovery, fake_site):
    import asyncio

    folder = discovery.pool_images_dir
    folder.mkdir(parents=True)
    (folder / "4242").write_bytes(PNG)
    (folder / "4242.part").write_bytes(b"half")
    async with discovery._image_lock:            # a picture fetch is under way
        task = asyncio.ensure_future(discovery.refresh())
        for _ in range(500):
            await asyncio.sleep(0)
        assert len(page_requests(fake_site)) == 5 and not task.done()
        assert (folder / "4242.part").exists()
    await task
    assert kept(discovery) == []


async def test_a_sweep_that_fails_never_fails_a_refresh(discovery, caplog):
    def boom():
        raise OSError("/secret/path")

    discovery.sweep_pool_images = boom
    out = await discovery.refresh()
    assert out["pages"] == 5 and "OSError" in caplog.text and "/secret/path" not in caplog.text


async def test_a_refresh_waiting_for_the_picture_lock_does_not_hold_the_refresh_lock(discovery, fake_site):
    import asyncio
    from dataclasses import replace

    folder = discovery.pool_images_dir
    folder.mkdir(parents=True)
    (folder / "4242.part").write_bytes(b"half")
    async with discovery._image_lock:            # picture fetches are queued ahead of the sweep
        task = asyncio.ensure_future(discovery.refresh())
        for _ in range(500):
            await asyncio.sleep(0)
        assert len(page_requests(fake_site)) == 5 and not task.done()
        assert not discovery._lock.locked()
        others = asyncio.ensure_future(asyncio.gather(
            discovery.set_key("KEY-abcdefgh"), discovery.apply(replace(discovery.settings, interests="jazz"))))
        for _ in range(50):
            await asyncio.sleep(0)
        assert others.done() and others.exception() is None     # a key save and a settings save are not held up
        assert (folder / "4242.part").exists() and not task.done()
    out = await task
    assert out["pages"] == 5 and kept(discovery) == []


async def test_a_clock_step_back_during_a_findlocal_read_keeps_the_rows_just_read(discovery, fake_findlocal, clock):
    fake_findlocal.rows = [fl(1), fl(2)]
    await set_up_findlocal(discovery)
    real = discovery.findlocal.read

    async def read_then_step_back(*a, **k):
        result = await real(*a, **k)
        clock.now -= HOUR                        # the wall clock is set back an hour while the read runs
        return result

    discovery.findlocal.read = read_then_step_back
    out = await discovery.refresh()
    assert out["findlocal"] == {"calls": 1, "events": 2, "error": ""}
    assert sorted(fl_ids(discovery)) == [FL1, FL2]

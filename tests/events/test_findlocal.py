import httpx
import pytest

from src.events.eventbrite import PAGE_LIMIT, SourceError
from src.events.findlocal import _event, category_names
from tests.events.conftest import fl_row

ZONE = "America/New_York"
TODAY = "2026-11-18"


async def read(site, key="KEY"):
    return await site.reader().read("columbus", key, ZONE, TODAY)


async def kind_of(site) -> str:
    with pytest.raises(SourceError) as info:
        await read(site)
    return info.value.kind


async def test_a_read_asks_only_the_events_endpoint_with_the_key(fake_findlocal):
    fake_findlocal.rows = [fl_row()]
    result = await fake_findlocal.reader().read("columbus", "KEY", ZONE, TODAY)
    req = fake_findlocal.requests[0]
    assert str(req.url).split("?")[0] == "https://findlocal.community/api/events"
    assert dict(req.url.params) == {"city": "columbus", "date_from": "2026-11-18", "date_to": "2027-01-17",
                                    "sort": "date", "limit": "100", "page": "1"}
    assert req.headers["Authorization"] == "Bearer KEY"
    assert (result.pages, len(result.events), result.error) == (1, 1, "")


async def test_a_city_outside_the_list_is_refused_before_any_request(fake_findlocal):
    with pytest.raises(ValueError):
        await fake_findlocal.reader().read("../x", "KEY", ZONE, TODAY)
    assert fake_findlocal.requests == []


def uuid_rows(n):
    return [fl_row(id=f"0b1c2d3e-0000-4000-8000-{i:012d}") for i in range(n)]


async def test_pages_are_read_one_second_apart_until_the_total(fake_findlocal):
    fake_findlocal.rows = uuid_rows(250)
    result = await read(fake_findlocal)
    assert (result.pages, len(result.events), fake_findlocal.sleeps) == (3, 250, [1.0, 1.0])


async def test_a_read_stops_at_ten_calls(fake_findlocal):
    fake_findlocal.rows = uuid_rows(1500)
    result = await read(fake_findlocal)
    assert (result.pages, len(result.events)) == (10, 1000)
    assert result.error == "" and result.complete is False      # stopped at the cap: not everything was seen


async def test_a_read_that_reaches_the_end_is_complete(fake_findlocal):
    fake_findlocal.rows = uuid_rows(250)
    assert (await read(fake_findlocal)).complete is True
    fake_findlocal.rows = uuid_rows(1000)                        # exactly the cap, and the total says so
    result = await read(fake_findlocal)
    assert (result.pages, result.complete) == (10, True)
    fake_findlocal.fail[2] = 503                                 # a read cut short by a failure is not
    result = await read(fake_findlocal)
    assert (result.error, result.complete) == ("provider", False)


@pytest.mark.parametrize("total", [None, "many", -1, 10**12])
async def test_an_unusable_total_still_ends_the_read(fake_findlocal, total):
    fake_findlocal.rows = uuid_rows(150)
    fake_findlocal.total = total
    result = await read(fake_findlocal)
    assert len(result.events) == 150 and result.error == ""
    assert result.pages <= 10


KEEP = "0b1c2d3e-0000-4000-8000-000000000001"


@pytest.mark.parametrize("over", [
    {"status": "cancelled"}, {"status": "postponed"}, {"attendance": "online"}, {"is_deleted": 1},
    {"duplicate_of": "abc"}, {"is_real_event": 0}, {"id": "not-a-uuid"}, {"title": ""},
    {"event_date": "2026-13-40"}, {"detail_page_url": None}, {"detail_page_url": "http://venue.example/a"},
    {"detail_page_url": "javascript:alert(1)"}, {"detail_page_url": "https://user@venue.example/"},
    {"detail_page_url": "https://venue.example/a b"}, {"detail_page_url": "https://venue.example/a\u0085b"},
    {"detail_page_url": "https://venue.example/a\u00a0b"}, {"detail_page_url": "https://venue.example/a\u2028b"},
    {"detail_page_url": "https://venue.example/a\u202eb"}, {"detail_page_url": "https://venue.example/" + "a" * 480},
])
def test_a_row_that_is_not_kept_is_dropped(over):
    assert _event(fl_row(**over), ZONE) is None


def test_a_row_that_is_not_a_dict_is_dropped():
    assert _event("row", ZONE) is None


@pytest.mark.parametrize("over", [{"status": "sold_out"}, {"status": "rescheduled"}, {"attendance": "hybrid"},
                                  {"attendance": None}, {"is_real_event": None}])
def test_a_row_that_may_still_be_attended_is_kept(over):
    assert _event(fl_row(**over), ZONE) is not None


def test_a_row_becomes_a_pool_event():
    e = _event(fl_row(id="0b1c2d3e-0000-4000-8000-000000000001"), ZONE)
    assert e["id"] == "fl-0b1c2d3e-0000-4000-8000-000000000001" and e["source"] == "findlocal"
    assert (e["start_date"], e["start_time"], e["end_date"], e["end_time"]) == ("2026-11-21", "19:00", "2026-11-21", "21:00")
    assert e["timezone"] == ZONE and e["image_url"] == ""
    assert e["url"] == "https://venue.example/shows/1"
    assert e["categories"] == ["EventbriteCategory/103"] and e["category_names"] == ["Music"]


def test_the_link_falls_back_to_the_ticket_page():
    ticket = "https://tickets.example/t/1"
    assert _event(fl_row(detail_page_url=None, ticket_page_url=ticket), ZONE)["url"] == ticket
    assert _event(fl_row(detail_page_url="http://bad.example/", ticket_page_url=ticket), ZONE)["url"] == ticket


def test_a_time_with_seconds_is_cut_to_minutes():
    assert _event(fl_row(start_time="17:00:00"), ZONE)["start_time"] == "17:00"


def test_an_end_before_the_start_is_no_end():
    e = _event(fl_row(start_time="22:00", end_time="01:00"), ZONE)
    assert (e["end_date"], e["end_time"]) == ("", "")


def test_an_all_day_row_ends_on_its_end_date():
    e = _event(fl_row(start_time=None, end_time=None, end_date="2026-11-23"), ZONE)
    assert (e["start_time"], e["end_date"], e["end_time"]) == ("", "2026-11-23", "")


def test_an_all_day_row_with_an_earlier_end_date_ends_on_its_start():
    assert _event(fl_row(start_time=None, end_date="2026-11-01"), ZONE)["end_date"] == "2026-11-21"


def test_a_timed_rows_end_date_is_ignored():
    assert _event(fl_row(end_date="2026-11-25"), ZONE)["end_date"] == "2026-11-21"


def test_a_venue_to_be_announced_has_no_address():
    e = _event(fl_row(venue_tba=1), ZONE)
    assert e["venue"] == "The Lodge" and e["address"] == ""


def test_a_long_title_is_cut_and_control_characters_go():
    assert len(_event(fl_row(title="x" * 400), ZONE)["name"]) == 300
    assert _event(fl_row(title="a\x00b\x07c"), ZONE)["name"] == "abc"


def test_an_eventbrite_link_gives_its_id():
    ticket = "https://www.eventbrite.com/e/jazz-night-tickets-1234567890"
    assert _event(fl_row(ticket_page_url=ticket), ZONE)["eventbrite_id"] == "1234567890"
    assert _event(fl_row(detail_page_url=ticket), ZONE)["eventbrite_id"] == "1234567890"
    assert _event(fl_row(), ZONE)["eventbrite_id"] == ""


def test_a_lookalike_host_gives_no_eventbrite_id():
    evil = "https://eventbrite.com.evil.example/e/x-123"
    assert _event(fl_row(ticket_page_url=evil), ZONE)["eventbrite_id"] == ""


def test_category_names():
    assert category_names("comedy") == ["Comedy", "Performing & Visual Arts"]
    assert category_names("food_drink") == ["Food & Drink"]
    assert category_names("music") == ["Music"]
    assert category_names("parks") == ["Parks"]
    assert category_names("x") == []


def test_an_unmapped_category_has_no_tag():
    assert _event(fl_row(category="parks"), ZONE)["categories"] == []


@pytest.mark.parametrize("outcome,kind", [(401, "key"), (429, "refused"), (302, "source_changed"),
                                          (400, "provider"), (503, "provider"),
                                          (httpx.ConnectError("x"), "network")])
async def test_a_failure_on_the_first_call_has_its_kind(fake_findlocal, outcome, kind):
    fake_findlocal.fail = {1: outcome}
    assert await kind_of(fake_findlocal) == kind
    assert len(fake_findlocal.requests) == 1


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not json"), httpx.Response(200, json=[]), httpx.Response(200, json={"meta": {}}),
    httpx.Response(200, json={"data": ["a", 1]}), httpx.Response(200, json={"data": [{"title": "x"}]}),
])
async def test_an_answer_of_the_wrong_shape_is_source_changed(fake_findlocal, response):
    fake_findlocal.fail = {1: response}
    assert await kind_of(fake_findlocal) == "source_changed"


async def test_rows_that_are_all_cancelled_are_no_error(fake_findlocal):
    fake_findlocal.rows = [fl_row(status="cancelled")]
    result = await read(fake_findlocal)
    assert (result.events, result.error) == ([], "")


async def test_a_failure_on_a_later_call_keeps_the_earlier_events(fake_findlocal):
    fake_findlocal.rows = uuid_rows(250)
    fake_findlocal.fail = {2: 429}
    result = await read(fake_findlocal)
    assert (len(result.events), result.pages, result.error) == (100, 1, "refused")


async def test_an_answer_over_the_limit_is_provider(fake_findlocal):
    fake_findlocal.fail = {1: httpx.Response(200, content=b"x" * (PAGE_LIMIT + 1))}
    assert await kind_of(fake_findlocal) == "provider"


@pytest.mark.parametrize("outcome", [401, 429, 503, httpx.ConnectError("boom")])
async def test_an_error_never_holds_the_key(fake_findlocal, outcome):
    fake_findlocal.fail = {1: outcome}
    with pytest.raises(SourceError) as info:
        await read(fake_findlocal, key="SECRET-KEY-123")
    assert "SECRET-KEY-123" not in str(info.value) and "SECRET-KEY-123" not in repr(info.value.args)


@pytest.mark.parametrize("key", ["caf\u00e9", "ab\ncd", "", "a b"])
async def test_a_key_that_cannot_go_in_a_header_is_refused_before_any_request(fake_findlocal, key):
    with pytest.raises(SourceError) as info:
        await read(fake_findlocal, key=key)
    assert info.value.kind == "key" and fake_findlocal.requests == []
    assert not key or key not in str(info.value)

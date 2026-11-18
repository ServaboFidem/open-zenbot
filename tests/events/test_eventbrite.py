import re
from pathlib import Path

import httpx
import pytest

from src.events import eventbrite
from src.events.discover_settings import CATEGORIES
from src.events.eventbrite import IMAGE_LIMIT, PAGE_LIMIT, ReadResult, SourceError, page_url, parse_page
from tests.events.conftest import page_with, row

FIXTURE = (Path(__file__).parent / "fixtures" / "eventbrite_page.html").read_text(encoding="utf-8")
KEPT_TIMED_ID = "1000000000001"
KEPT_ALL_DAY_ID = "1000000000004"
KEYS = {"id", "name", "summary", "start_date", "start_time", "end_date", "end_time", "timezone", "venue", "address",
        "city", "region", "image_url", "url", "categories", "category_names", "format"}
PNG = bytes.fromhex("89504e470d0a1a0a") + b"rest"
IMG = "https://img.evbuc.com/pic.png?s=1"


def test_page_url_builds_the_listing_address():
    assert page_url("oh--columbus", "this", 1) == "https://www.eventbrite.com/d/oh--columbus/events--this-month/?page=1"
    assert page_url("oh--columbus", "next", 7) == "https://www.eventbrite.com/d/oh--columbus/events--next-month/?page=7"


@pytest.mark.parametrize("args", [("../x", "this", 1), ("oh--columbus/..", "this", 1), ("", "this", 1),
                                  ("oh--columbus", "last", 1), ("oh--columbus", "this", 0), ("oh--columbus", "this", "1")])
def test_page_url_refuses_what_is_not_a_listing(args):
    with pytest.raises(ValueError):
        page_url(*args)


def test_parse_page_reads_the_fixture():
    events, pages = parse_page(FIXTURE)
    assert pages == 3 and [e["id"] for e in events] == [KEPT_TIMED_ID, KEPT_ALL_DAY_ID]   # cancelled and online dropped
    e = events[0]
    assert set(e) == KEYS
    assert e["url"].startswith("https://www.eventbrite.com/e/") and e["image_url"].startswith("https://img.evbuc.com/")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["start_date"]) and re.fullmatch(r"\d{2}:\d{2}", e["start_time"])
    assert (e["end_date"], e["end_time"], e["timezone"]) == ("2026-12-01", "14:00", "America/New_York")
    assert (e["venue"], e["address"], e["city"], e["region"]) == (
        "Example Hall", "1 Example Street, Exampleton, OH 43000", "Exampleton", "OH")
    assert e["categories"] == ["EventbriteCategory/113"] and e["category_names"] == ["Community & Culture"]
    assert e["format"] == "Class, Training, or Workshop"


def test_parse_page_reads_an_all_day_event():
    day = parse_page(FIXTURE)[0][1]
    assert day["start_time"] == "" and day["end_time"] == "" and day["end_date"] == "2026-12-05"
    assert day["categories"] == ["EventbriteCategory/110"]


def test_the_fixture_categories_are_known():
    assert set(re.findall(r'"tag": "(EventbriteCategory/\d+)"', FIXTURE)) <= set(CATEGORIES)


@pytest.mark.parametrize("html", [
    "", "<html>challenge</html>", "window.__SERVER_DATA__ = {not json};",
    'window.__SERVER_DATA__ = {"search_data": {}};', "window.__SERVER_DATA__ = [1];",
    'window.__SERVER_DATA__ = {"search_data": {"events": {"results": {}, "pagination": {"page_count": 1}}}};',
    'window.__SERVER_DATA__ = {"search_data": {"events": {"results": [], "pagination": {"page_count": "3"}}}};',
    'window.__SERVER_DATA__ = {"search_data": [1]};'])
def test_parse_page_without_the_shape_is_source_changed(html):
    with pytest.raises(SourceError) as e:
        parse_page(html)
    assert e.value.kind == "source_changed"


def test_parse_page_drops_and_caps_hostile_rows():
    rows = [row(id="12a"), row(name=""), row(start_date="tomorrow"), row(url="https://evil.example/e/x"),
            row(url="http://www.eventbrite.com/e/x"), row(name="n" * 5000, summary="s" * 5000),
            row(image="https://evil.example/a.png"), row(start_time="25:99"), row(end_date="2026-01-01", end_time="09:00")]
    events, _ = parse_page(page_with(rows))
    assert len(events) == 4                                   # the long one, the bad image, the bad time, the early end
    assert len(events[0]["name"]) == 300 and len(events[0]["summary"]) == 1000
    assert events[1]["image_url"] == "" and events[2]["start_time"] == "" and events[3]["end_time"] == ""
    assert events[3]["end_date"] == ""
    assert "\x00" not in parse_page(page_with([row(name="a\x00b\x1b[0m")]))[0][0]["name"]


@pytest.mark.parametrize("over", [{"url": "https://www.eventbrite.com.evil.example/e/x"},
                                  {"url": "https://www.eventbrite.com@evil.example/e/x"},
                                  {"url": "https://www.eventbrite.com/e/x y"},
                                  {"url": "https://www.eventbrite.com/d/oh--columbus/"},
                                  {"start_date": "2026-02-30"}, {"is_cancelled": True}, {"is_online_event": True},
                                  {"id": 12}, {"name": "\x00\x1b"}])
def test_parse_page_drops_one_bad_row(over):
    good = row()
    events = parse_page(page_with([row(**over), good]))[0]
    assert [e["id"] for e in events] == [good["id"]]


def test_parse_page_where_no_row_has_the_shape_is_source_changed():
    rows = [{k: v for k, v in row().items() if k != "start_date"} for _ in range(20)]     # a renamed field
    with pytest.raises(SourceError) as e:
        parse_page(page_with(rows, pages=3))
    assert e.value.kind == "source_changed"
    with pytest.raises(SourceError):
        parse_page(page_with([row(is_cancelled=True), row(name="")]))                    # one is a shape failure


def test_parse_page_of_only_cancelled_or_online_rows_is_not_an_error():
    assert parse_page(page_with([row(is_cancelled=True), row(is_online_event=True)], pages=2)) == ([], 2)
    assert parse_page(page_with([], pages=1)) == ([], 1)


def test_parse_page_keeps_text_clean_and_inside_its_caps():
    venue = {"name": "v" * 480, "address": {"localized_address_display": "a" * 100, "city": "c" * 500}}
    e = parse_page(page_with([row(name="Line\none two", summary="a\tb\x7f", primary_venue=venue)]))[0][0]
    assert e["name"] == "Line one two" and e["summary"] == "a b"
    assert e["venue"] == "v" * 480 and len(e["address"]) == 18          # 480 + ", " + 18 = 500
    assert len(e["venue"]) + len(", ") + len(e["address"]) <= 500
    assert len(e["city"]) == 100


def test_parse_page_reads_the_first_assignment_only():
    html = page_with([row(id="1")]) + 'window.__SERVER_DATA__ = {"search_data": {}};'
    assert [e["id"] for e in parse_page(html)[0]] == ["1"]


async def test_read_walks_both_months_politely(fake_site):
    out = await fake_site.eventbrite().read("oh--columbus")
    assert out.error == "" and out.pages == fake_site.this_pages + fake_site.next_pages
    assert all(str(r.url).startswith("https://www.eventbrite.com/d/oh--columbus/events--") for r in fake_site.requests)
    assert [r.url.params["page"] for r in fake_site.requests] == ["1", "2", "3", "1", "2"]
    assert fake_site.sleeps == [1.0] * (out.pages - 1)
    assert len({e["id"] for e in out.events}) == len(out.events)          # an event on two pages is kept once
    assert len(out.events) == 2 * out.pages + 1
    assert all(r.headers["user-agent"].startswith("Mozilla/") and r.headers["accept-language"] == "en-US"
               for r in fake_site.requests)


async def test_read_caps_pages_at_20_a_month(fake_site):        # 37 and 49 pages offered: 20 + 20 read
    fake_site.this_pages, fake_site.next_pages = 37, 49
    out = await fake_site.eventbrite().read("oh--columbus")
    assert out.pages == 40 and out.error == "" and len(fake_site.requests) == 40
    assert [r.url.params["page"] for r in fake_site.requests[:20]] == [str(n) for n in range(1, 21)]
    assert "events--next-month" in fake_site.requests[20].url.path and fake_site.requests[20].url.params["page"] == "1"


async def test_read_stops_at_max_pages(fake_site):
    fake_site.this_pages, fake_site.next_pages = 37, 49
    out = await fake_site.eventbrite().read("oh--columbus", max_pages=25)
    assert out.pages == 25 and len(fake_site.requests) == 25


@pytest.mark.parametrize("outcome, kind", [(429, "refused"), (403, "refused"), (500, "provider"), (404, "provider"),
                                           (httpx.ConnectError("x"), "network"), (httpx.ReadTimeout("x"), "network"),
                                           (httpx.Response(200, text="<html>challenge</html>"), "source_changed")])
async def test_first_page_failure_raises(fake_site, outcome, kind):
    fake_site.fail[("this", 1)] = outcome
    with pytest.raises(SourceError) as e:
        await fake_site.eventbrite().read("oh--columbus")
    assert e.value.kind == kind


async def test_a_later_failure_cuts_the_reading_short(fake_site):
    fake_site.fail[("this", 3)] = 429
    out = await fake_site.eventbrite().read("oh--columbus")
    assert isinstance(out, ReadResult) and out.error == "refused" and out.pages == 2
    assert len(out.events) == 5 and len(fake_site.requests) == 3        # nothing after the refusal is asked


async def test_a_failure_on_the_first_page_of_next_month_keeps_this_month(fake_site):
    fake_site.fail[("next", 1)] = httpx.ConnectError("x")
    out = await fake_site.eventbrite().read("oh--columbus")
    assert out.error == "network" and out.pages == 3 and len(out.events) == 7


@pytest.mark.parametrize("location", ["https://evil.example/e", "/signin", "https://www.eventbrite.com/signin"])
async def test_a_redirect_off_the_listing_is_not_followed(fake_site, location):
    fake_site.fail[("this", 1)] = httpx.Response(302, headers={"location": location})
    with pytest.raises(SourceError) as e:
        await fake_site.eventbrite().read("oh--columbus")
    assert e.value.kind == "source_changed" and len(fake_site.requests) == 1


async def test_a_page_over_the_limit_is_refused(fake_site):
    fake_site.fail[("this", 1)] = httpx.Response(200, content=b"x" * (PAGE_LIMIT + 1))
    with pytest.raises(SourceError) as e:
        await fake_site.eventbrite().read("oh--columbus")
    assert e.value.kind == "provider"


async def test_a_page_over_the_limit_with_no_length_is_refused(fake_site):
    fake_site.fail[("this", 1)] = httpx.Response(200, stream=httpx.ByteStream(b"x" * (PAGE_LIMIT + 1)))
    with pytest.raises(SourceError) as e:
        await fake_site.eventbrite().read("oh--columbus")
    assert e.value.kind == "provider"


async def test_a_page_at_the_limit_is_read(fake_site):
    html = page_with([row()])
    html += "<!--" + "x" * (PAGE_LIMIT - len(html.encode()) - 7) + "-->"
    assert len(html.encode()) == PAGE_LIMIT
    fake_site.fail[("this", 1)] = httpx.Response(200, text=html)
    fake_site.this_pages = fake_site.next_pages = 1
    assert (await fake_site.eventbrite().read("oh--columbus")).error == ""


async def test_read_refuses_a_bad_place_before_any_request(fake_site):
    with pytest.raises(ValueError):
        await fake_site.eventbrite().read("../etc")
    assert fake_site.requests == []


@pytest.mark.parametrize("body", [PNG, b"\xff\xd8\xff\xe0jpeg", b"RIFF\x00\x00\x00\x00WEBPVP8 ", b"GIF89a...."])
async def test_image_returns_a_picture(fake_site, body):
    fake_site.images[IMG] = httpx.Response(200, content=body)
    assert await fake_site.eventbrite().image(IMG) == body


@pytest.mark.parametrize("url", ["https://evil.example/a.png", "http://img.evbuc.com/a.png",
                                 "https://img.evbuc.com.evil.example/a.png", "https://img.evbuc.com@evil.example/a.png",
                                 "https://img.evbuc.com:8443/a.png", "https://www.eventbrite.com/a.png",
                                 "file:///etc/passwd", "", "https://img.evbuc.com/a b"])
async def test_image_never_asks_another_address(fake_site, url):
    assert await fake_site.eventbrite().image(url) is None
    assert fake_site.requests == []


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=PNG + b"x" * IMAGE_LIMIT),
    httpx.Response(200, stream=httpx.ByteStream(PNG + b"x" * IMAGE_LIMIT)),
    httpx.Response(200, content=b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
    httpx.Response(200, content=b"<html>not a picture</html>"),
    httpx.Response(200, content=b""),
    httpx.Response(404, content=PNG),
    httpx.Response(302, headers={"location": "https://evil.example/a.png"}, content=PNG),
    httpx.ConnectError("x"),
])
async def test_image_only_pictures(fake_site, response):
    fake_site.images[IMG] = response
    assert await fake_site.eventbrite().image(IMG) is None
    assert len(fake_site.requests) == 1


async def test_image_at_the_limit_is_kept(fake_site):
    fake_site.images[IMG] = httpx.Response(200, content=PNG + b"x" * (IMAGE_LIMIT - len(PNG)))
    assert len(await fake_site.eventbrite().image(IMG)) == IMAGE_LIMIT


def test_constants():
    assert (eventbrite.MAX_PAGES, eventbrite.PER_MONTH, eventbrite.GAP_S, eventbrite.PAGE_LIMIT, eventbrite.IMAGE_LIMIT,
            eventbrite.IMAGE_HOST) == (40, 20, 1.0, 5_000_000, 1_500_000, "img.evbuc.com")

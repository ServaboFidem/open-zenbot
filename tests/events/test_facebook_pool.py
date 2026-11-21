import pytest

from src.events import facebook_pool as fp
from src.events.discover_store import DiscoverStore

TODAY = "2026-11-20"


def event(**o):
    d = {"id": "12345", "name": "Block Party", "start_date": "2026-11-25",
         "start_time": "19:00", "end_date": "2026-11-25", "end_time": "21:00",
         "timezone": "America/New_York", "venue": "Hall", "address": "1 Main St",
         "url": "https://www.facebook.com/events/12345/party"}
    d.update(o)
    return d


def birthday(**o):
    d = {"type": "birthday", "person_id": "777", "name": "Ada Friend", "birthday": "11-25"}
    d.update(o)
    return d


@pytest.fixture
def store(tmp_path):
    s = DiscoverStore(tmp_path / "discover.db")
    yield s
    s.close()


# ---- events: the happy path --------------------------------------------------


def test_event_maps_to_a_stable_facebook_id():
    row, kind = fp.map_event(event())
    assert kind == "" and row["id"] == "fb-12345" and row["source"] == "facebook"


def test_event_id_comes_from_the_events_link_when_no_id_field():
    row, kind = fp.map_event(event(id=None, event_id=None,
                                   url="https://m.facebook.com/events/99999/?ref=x"))
    assert (kind, row["id"]) == ("", "fb-99999")


def test_event_id_accepts_plain_digits_and_ints():
    assert fp.map_event(event(id="42", url=""))[0]["id"] == "fb-42"
    assert fp.map_event(event(id=43, event_id=None, url=""))[0]["id"] == "fb-43"


def test_event_keeps_a_facebook_events_link_only():
    row, _ = fp.map_event(event(url="https://www.facebook.com/events/12345/x"))
    assert row["url"] == "https://www.facebook.com/events/12345/x"


def test_event_drops_a_foreign_link_but_keeps_the_row():
    row, kind = fp.map_event(event(url="https://evil.example/e/1"))
    assert (kind, row["url"]) == ("", "")


def test_event_builds_no_url_from_an_id_alone():
    row, kind = fp.map_event(event(id="12345", url=None, link=None, facebook_url=None))
    assert (kind, row["url"]) == ("", "")


def test_event_reads_helper_field_aliases():
    row, kind = fp.map_event({"event_id": "7", "title": "Jam", "start_date": "2026-12-01",
                              "location": "Park", "localized_address": "2 Oak",
                              "description": "Music", "link": "https://facebook.com/events/7/x"})
    assert (kind, row["name"], row["venue"], row["address"], row["summary"], row["url"]) == (
        "", "Jam", "Park", "2 Oak", "Music", "https://facebook.com/events/7/x")


def test_event_caps_match_eventbrite():
    row, kind = fp.map_event(event(name="n" * 400, summary="s" * 1500,
                                   venue="v" * 400, address="a" * 400))
    assert kind == ""
    assert len(row["name"]) == 300 and len(row["summary"]) == 1000
    assert len(row["venue"]) + len(row["address"]) <= 500


def test_event_strips_control_characters():
    row, kind = fp.map_event(event(name="Party\x00 Night\x07", summary="line1\nline2"))
    assert (kind, row["name"], row["summary"]) == ("", "Party Night", "line1 line2")


def test_event_without_a_start_time_is_all_day():
    row, kind = fp.map_event(event(start_time="", end_date="", end_time=""))
    assert (kind, row["start_time"], row["end_date"], row["end_time"]) == ("", "", "2026-11-25", "")


def test_event_with_an_end_before_its_start_keeps_no_end():
    row, kind = fp.map_event(event(end_date="2026-11-25", end_time="18:00"))
    assert (kind, row["end_date"], row["end_time"]) == ("", "", "")


def test_event_keeps_a_real_zone_and_drops_a_bad_one():
    assert fp.map_event(event())[0]["timezone"] == "America/New_York"
    assert fp.map_event(event(timezone="Mars Olympus!"))[0]["timezone"] == ""


def test_event_row_fits_the_pool_columns():
    row, kind = fp.map_event(event())
    assert kind == "" and row["image_url"] == "" and row["categories"] == []
    assert set(row) >= {"id", "name", "summary", "start_date", "start_time", "end_date", "end_time",
                        "timezone", "venue", "address", "city", "region", "image_url", "url",
                        "categories", "category_names", "format", "source"}


# ---- events: named drop kinds -------------------------------------------------


@pytest.mark.parametrize("raw", [None, "nope", 42, ["x"]])
def test_non_dict_event_raws_drop_as_not_a_dict(raw):
    assert fp.map_event(raw) == (None, "not_a_dict")


def test_grocery_and_other_kinds_drop_as_bad_type():
    assert fp.map_event(event(type="grocery")) == (None, "bad_type")
    assert fp.map_raw({"type": "grocery", "name": "Milk"}) == (None, "bad_type")


@pytest.mark.parametrize("raw", [event(is_cancelled=True), event(status="cancelled"),
                                 event(cancelled=1)])
def test_cancelled_events_drop_as_cancelled(raw):
    assert fp.map_event(raw) == (None, "cancelled")


@pytest.mark.parametrize("raw", [event(id="", event_id=None, url=""),
                                 event(id=None, event_id=None, url=None, link=None),
                                 event(id="abc", url=""), event(id="1" * 31, url="")])
def test_events_without_a_stable_id_drop(raw):
    assert fp.map_event(raw) == (None, "missing_id")


@pytest.mark.parametrize("raw", [event(name=""), event(name="   "), event(name=None, title="")])
def test_nameless_events_drop(raw):
    assert fp.map_event(raw) == (None, "missing_name")


def test_event_without_any_date_drops_as_missing_date():
    assert fp.map_event(event(start_date=None)) == (None, "missing_date")
    assert fp.map_event(event(start_date="")) == (None, "missing_date")


@pytest.mark.parametrize("day", ["tomorrow", "2026-13-01", "2026-02-30", "11-25", "2026-1-1"])
def test_event_with_a_malformed_date_drops_as_bad_date(day):
    assert fp.map_event(event(start_date=day)) == (None, "bad_date")


def test_event_with_a_birthday_type_drops_as_bad_type():
    assert fp.map_event(birthday()) == (None, "bad_type")


# ---- birthdays ----------------------------------------------------------------


def test_birthday_before_today_lands_this_year():
    row, kind = fp.map_birthday(birthday(), today=TODAY)
    assert (kind, row["start_date"], row["end_date"]) == ("", "2026-11-25", "2026-11-25")
    assert (row["source"], row["format"], row["start_time"]) == ("facebook-birthday", "birthday", "")


def test_birthday_already_passed_lands_next_year():
    row, kind = fp.map_birthday(birthday(birthday="11-10"), today=TODAY)
    assert (kind, row["start_date"]) == ("", "2027-11-10")


def test_birthday_today_stays_today():
    row, kind = fp.map_birthday(birthday(birthday="11-20"), today=TODAY)
    assert (kind, row["start_date"]) == ("", "2026-11-20")


def test_birthday_with_a_birth_year_schedules_by_month_and_day():
    row, kind = fp.map_birthday(birthday(birthday="1990-11-10"), today=TODAY)
    assert (kind, row["start_date"]) == ("", "2027-11-10")


def test_birthday_accepts_a_month_day_mapping():
    row, kind = fp.map_birthday(birthday(birthday={"month": 12, "day": 1}), today=TODAY)
    assert (kind, row["start_date"]) == ("", "2026-12-01")


def test_leap_birthday_lands_on_feb_28_off_leap_years():
    row, _ = fp.map_birthday(birthday(birthday="02-29"), today="2026-11-20")
    assert row["start_date"] == "2027-02-28"
    row, _ = fp.map_birthday(birthday(birthday="02-29"), today="2028-01-01")
    assert row["start_date"] == "2028-02-29"


def test_birthday_id_prefers_helper_person_digits():
    row, _ = fp.map_birthday(birthday(), today=TODAY)
    assert row["id"] == "fb-bday-777"


def test_birthday_id_reads_a_profile_link():
    row, _ = fp.map_birthday(birthday(person_id=None, id=None,
                                      profile_url="https://www.facebook.com/profile.php?id=555"),
                             today=TODAY)
    assert row["id"] == "fb-bday-555"
    row, _ = fp.map_birthday(birthday(person_id=None, id=None,
                                      profile_url="https://www.facebook.com/Ada.Friend"),
                             today=TODAY)
    assert row["id"] == "fb-bday-u-ada.friend"


def test_birthday_id_falls_back_to_name_and_day_stably():
    first, _ = fp.map_birthday(birthday(person_id=None, id=None), today=TODAY)
    second, _ = fp.map_birthday(birthday(person_id=None, id=None), today="2026-11-21")
    assert first["id"] == second["id"] == "fb-bday-adafriend-1125"


def test_birthday_caps_and_control_stripping_hold():
    row, kind = fp.map_birthday(birthday(name="A\x00da", summary="x" * 1500), today=TODAY)
    assert (kind, row["name"], len(row["summary"])) == ("", "Ada", 1000)


@pytest.mark.parametrize("raw", [None, 42])
def test_non_dict_birthday_raws_drop_as_not_a_dict(raw):
    assert fp.map_birthday(raw, today=TODAY) == (None, "not_a_dict")


def test_birthday_with_an_event_type_drops_as_bad_type():
    assert fp.map_birthday(event(type="event"), today=TODAY) == (None, "bad_type")


def test_birthday_without_a_name_drops():
    assert fp.map_birthday(birthday(name="  "), today=TODAY) == (None, "missing_name")


def test_birthday_without_any_day_drops_as_missing_birthday():
    assert fp.map_birthday(birthday(birthday=None, date=None), today=TODAY) == (None, "missing_birthday")


@pytest.mark.parametrize("day", ["soon", "13-01", "04-31", "02-30", {"month": 0, "day": 5},
                                 {"month": "x", "day": 1}])
def test_birthday_with_a_malformed_day_drops_as_bad_birthday(day):
    assert fp.map_birthday(birthday(birthday=day), today=TODAY) == (None, "bad_birthday")


@pytest.mark.parametrize("today", ["", "yesterday", "2026-13-01"])
def test_birthday_with_a_bad_today_drops(today):
    assert fp.map_birthday(birthday(), today=today) == (None, "bad_today")


# ---- dispatch, dedupe, batches -------------------------------------------------


def test_map_raw_dispatches_on_type():
    assert fp.map_raw(event())[0]["source"] == "facebook"
    row, kind = fp.map_raw(birthday(), today=TODAY)
    assert (kind, row["source"]) == ("", "facebook-birthday")
    assert fp.map_raw(birthday()) == (None, "bad_today")


def test_dedupe_drops_repeat_ids_and_repeat_day_names():
    first, _ = fp.map_event(event())
    same_id, _ = fp.map_event(event(summary="Changed text"))
    same_day_name, _ = fp.map_event(event(id="54321", name="  BLOCK party!!"))
    other_day, _ = fp.map_event(event(id="67890", start_date="2026-11-26"))
    kept, dropped = fp.dedupe([first, same_id, same_day_name, other_day])
    assert [r["id"] for r in kept] == ["fb-12345", "fb-67890"]
    assert dropped == [{"id": "fb-12345", "reason": "duplicate"},
                       {"id": "fb-54321", "reason": "duplicate"}]


def test_dedupe_honours_existing_pool_keys():
    row, _ = fp.map_event(event())
    ids, keys = fp.keys_of([row])
    repeat, _ = fp.map_event(event(id="99999"))
    kept, dropped = fp.dedupe([repeat], existing_ids=ids, existing_keys=keys)
    assert kept == [] and dropped == [{"id": "fb-99999", "reason": "duplicate"}]


def test_map_batch_counts_kept_and_each_drop_reason():
    result = fp.map_batch([event(),
                           event(id="12345", summary="A repeat"),
                           event(id="bogus", url=""),
                           {"type": "grocery"},
                           birthday(),
                           birthday(birthday="nope")], today=TODAY)
    assert [r["id"] for r in result.kept] == ["fb-12345", "fb-bday-777"]
    assert [(d["index"], d["kind"], d["id"]) for d in result.dropped] == [
        (1, "duplicate", "fb-12345"), (2, "missing_id", ""),
        (3, "bad_type", ""), (5, "bad_birthday", "")]
    for d in result.dropped:
        assert set(d) == {"index", "kind", "id"}  # ids and kinds only, never listing text


def test_map_batch_refuses_a_non_list():
    with pytest.raises(ValueError):
        fp.map_batch({"id": "1"})


def test_is_facebook_source_scopes_pool_work():
    assert fp.is_facebook_source("facebook") and fp.is_facebook_source("facebook-birthday")
    assert not fp.is_facebook_source("eventbrite") and not fp.is_facebook_source("")


# ---- the store takes mapped rows ------------------------------------------------


def test_mapped_rows_upsert_and_sweep_by_source(store):
    result = fp.map_batch([event(), birthday()], today=TODAY)
    assert store.upsert_pool(result.kept, "t1") == 2
    assert store.upsert_pool([{"id": "1", "name": "EB", "start_date": "2026-11-25",
                               "source": "eventbrite"}], "t1") == 1
    assert store.pool_size("facebook") == 1
    assert store.pool_size("facebook-birthday") == 1
    assert [r["id"] for r in store.listing("2026-11-20")] == ["1", "fb-bday-777", "fb-12345"]
    assert store.clear_pool("facebook") == 1
    assert store.pool_size() == 2  # the birthday and the eventbrite rows stay


def test_dedupe_against_a_store_listing(store):
    result = fp.map_batch([event()], today=TODAY)
    store.upsert_pool(result.kept, "t1")
    ids, keys = fp.keys_of(store.listing("2026-11-20"))
    repeat = fp.map_batch([event(summary="Seen before")], today=TODAY,
                          existing_ids=ids, existing_keys=keys)
    assert repeat.kept == [] and [d["kind"] for d in repeat.dropped] == ["duplicate"]

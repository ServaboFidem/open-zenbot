"""RSVP/personal split slice tests: approval holds, feed landings, like drops.

Four fixture classes: an RSVP tap (Interested/Going) or a friend-named
event waits as an approval card; any other event lands in the feed; a
like tap creates nothing. Cards only: the split never touches the
calendar, and the registry reads prove the friend check wrote nothing.
"""

from __future__ import annotations

import inspect

import pytest

from src.events import facebook_cards as fc
from src.memory.registry import PeopleRegistry


def row(**o):
    d = {"id": "fb-12345", "source": "facebook", "name": "Block Party",
         "summary": "Music", "start_date": "2026-11-25", "start_time": "19:00",
         "end_date": "2026-11-25", "end_time": "21:00",
         "timezone": "America/New_York", "venue": "Hall", "address": "1 Main St",
         "city": "", "region": "", "image_url": "",
         "url": "https://www.facebook.com/events/12345/party",
         "categories": [], "category_names": [], "format": ""}
    d.update(o)
    return d


@pytest.fixture
def people(tmp_path):
    reg = PeopleRegistry(tmp_path / "people.json")
    reg.set_owner("Alex Rivera", [])
    reg.add("Dana Reyes", relation="friend", tier="close")
    reg.add("Lee Morgan", relation="colleague", tier="known")
    reg.add_alias("Dana Reyes", "Dana R")
    return reg


def snapshot(reg: PeopleRegistry):
    before = sorted((e.name, e.tier, tuple(e.aliases)) for e in reg.all())
    text = reg.path.read_text(encoding="utf-8") if reg.path.exists() else ""
    return before, text


def assert_unchanged(reg: PeopleRegistry, before) -> None:
    before_entries, before_text = before
    after = sorted((e.name, e.tier, tuple(e.aliases)) for e in reg.all())
    assert after == before_entries
    assert reg.path.read_text(encoding="utf-8") == before_text


# ---- RSVP: Interested or Going holds ------------------------------------------


def test_interested_tap_waits_as_an_approval_card(people):
    before = snapshot(people)
    out = fc.split([row(rsvp="Interested")], people)
    assert [(c["id"], c["reason"]) for c in out.approval] == [("fb-12345", "rsvp")]
    assert out.approval[0]["row"]["name"] == "Block Party"
    assert out.feed == [] and out.dropped == []
    assert_unchanged(people, before)


def test_going_tap_waits_as_an_approval_card(people):
    out = fc.split([row(rsvp="Going")], people)
    assert [(c["id"], c["reason"]) for c in out.approval] == [("fb-12345", "rsvp")]
    assert out.feed == [] and out.dropped == []


def test_tap_matches_case_insensitively_and_trims(people):
    assert fc.classify(row(rsvp="  GOING  "), people) == ("approval", "rsvp")
    assert fc.classify(row(rsvp="interested"), people) == ("approval", "rsvp")


def test_response_key_counts_like_rsvp(people):
    assert fc.classify(row(response="Going"), people) == ("approval", "rsvp")


def test_rsvp_on_a_friends_event_names_both_reasons(people):
    out = fc.split([row(rsvp="Going", inviter="Dana Reyes")], people)
    assert [(c["id"], c["reason"]) for c in out.approval] == [
        ("fb-12345", "rsvp_personal")]
    assert out.feed == [] and out.dropped == []


# ---- personal: a friend in memory holds ---------------------------------------


def test_inviter_friend_waits_as_an_approval_card(people):
    before = snapshot(people)
    out = fc.split([row(inviter="Dana Reyes")], people)
    assert [(c["id"], c["reason"]) for c in out.approval] == [("fb-12345", "personal")]
    assert out.feed == [] and out.dropped == []
    assert_unchanged(people, before)


def test_host_and_friends_going_hold(people):
    assert fc.classify(row(host="Dana Reyes"), people) == ("approval", "personal")
    assert fc.classify(row(friends_going=["Sam Stranger", "Dana R"]), people) == (
        "approval", "personal")


def test_alias_and_case_match_a_friend(people):
    assert fc.classify(row(inviter="dana r"), people) == ("approval", "personal")


def test_known_but_not_close_is_not_personal(people):
    assert fc.classify(row(inviter="Lee Morgan"), people) == ("feed", "other")


def test_unknown_inviter_is_not_personal(people):
    assert fc.classify(row(inviter="Sam Stranger"), people) == ("feed", "other")


def test_owner_as_inviter_is_not_personal(people):
    assert fc.classify(row(inviter="Alex Rivera"), people) == ("feed", "other")


def test_venue_named_like_a_friend_is_not_personal(people):
    assert fc.classify(row(venue="Dana Reyes"), people) == ("feed", "other")


def test_no_people_means_not_personal():
    assert fc.classify(row(inviter="Dana Reyes"), None) == ("feed", "other")


# ---- other: everything else lands in the feed ---------------------------------


def test_plain_event_lands_in_the_feed(people):
    out = fc.split([row()], people)
    assert [(c["id"], c["reason"]) for c in out.feed] == [("fb-12345", "other")]
    assert out.approval == [] and out.dropped == []


def test_unknown_tap_lands_in_the_feed(people):
    assert fc.classify(row(rsvp="Maybe"), people) == ("feed", "other")


def test_non_string_tap_lands_in_the_feed(people):
    assert fc.classify(row(rsvp=7), people) == ("feed", "other")


# ---- like: a like tap creates nothing -----------------------------------------


def test_like_tap_creates_nothing(people):
    before = snapshot(people)
    out = fc.split([row(rsvp="Like")], people)
    assert out.approval == [] and out.feed == []
    assert out.dropped == [{"id": "fb-12345", "reason": "like"}]
    assert_unchanged(people, before)


def test_like_spellings_create_nothing(people):
    assert fc.classify(row(rsvp="likes"), people) == ("dropped", "like")
    assert fc.classify(row(rsvp=" LIKED "), people) == ("dropped", "like")


def test_like_on_a_friends_event_still_creates_nothing(people):
    out = fc.split([row(rsvp="Like", inviter="Dana Reyes")], people)
    assert out.approval == [] and out.feed == []
    assert out.dropped == [{"id": "fb-12345", "reason": "like"}]


# ---- boundaries: birthdays, bad rows, cards only -------------------------------


def test_birthday_rows_drop_outside_this_slice(people):
    out = fc.split([row(source="facebook-birthday")], people)
    assert out.approval == [] and out.feed == []
    assert out.dropped == [{"id": "fb-12345", "reason": "not_event_source"}]


def test_foreign_source_rows_drop(people):
    out = fc.split([row(id="42", source="eventbrite")], people)
    assert out.dropped == [{"id": "42", "reason": "not_event_source"}]


def test_bad_rows_drop_with_ids_and_reasons_only(people):
    out = fc.split(["nope", {"source": "facebook"}, row()], people)
    assert out.dropped == [{"id": "", "reason": "bad_row"},
                           {"id": "", "reason": "bad_row"}]
    assert len(out.feed) == 1 and out.approval == []


def test_split_refuses_a_non_batch(people):
    with pytest.raises(ValueError, match="not a batch"):
        fc.split("fb-12345", people)


def test_split_takes_no_store_and_writes_no_calendar():
    assert list(inspect.signature(fc.split).parameters) == ["rows", "people"]


def test_mixed_batch_splits_to_exact_counts(people):
    rows = [row(id="fb-1", rsvp="Going"),           # approval: rsvp
            row(id="fb-2", inviter="Dana Reyes"),   # approval: personal
            row(id="fb-3"),                          # feed: other
            row(id="fb-4", rsvp="Maybe"),            # feed: other
            row(id="fb-5", rsvp="Like"),             # dropped: like
            row(id="fb-6", source="facebook-birthday", name="Ada")]  # dropped
    before = snapshot(people)
    out = fc.split(rows, people)
    assert [c["id"] for c in out.approval] == ["fb-1", "fb-2"]
    assert [c["id"] for c in out.feed] == ["fb-3", "fb-4"]
    assert out.dropped == [{"id": "fb-5", "reason": "like"},
                           {"id": "fb-6", "reason": "not_event_source"}]
    assert_unchanged(people, before)

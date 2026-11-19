import json

import pytest

from src.events.convert import to_google
from src.events.google import GoogleError
from src.events.store import google_event_id
from tests.events.conftest import SYNC_NOW

ZERO = {"added": 0, "updated": 0, "removed": 0, "failed": 0}


def ev(**k):
    return {"title": "Dentist", "all_day": False, "start": "2026-11-20T09:00:00Z",
            "end": "2026-11-20T10:00:00Z", "tz": "Europe/London", **k}


def timed(id, title="Standup", **k):
    return {"id": id, "status": "confirmed", "summary": title,
            "start": {"dateTime": "2026-11-21T09:00:00Z", "timeZone": "Europe/London"},
            "end": {"dateTime": "2026-11-21T09:30:00Z"}, **k}


def pushed(store, cal="dest", gid="m1", **k):
    """A made-here event already pushed as ``gid`` on ``cal`` (not dirty); the fake does not hold it."""
    e = store.add(ev(**k))
    store.mark_pushed(e["id"], google_calendar=cal, google_id=gid, etag='"0"', link="")
    return store.event(e["id"])


def writes(fake):
    return [r for r in fake.requests if r.method in ("POST", "PATCH", "DELETE", "PUT")
            and "/calendar/v3/" in str(r.url)]


def body(request):
    return json.loads(request.content) if request.content else {}


def ledger_text(sync):
    return sync.ledger.file.read_text() if sync.ledger.file.exists() else ""


async def test_push_is_off_by_default_and_sends_nothing(sync, store, fake):
    e = store.add(ev())
    assert sync.can_push() is False
    assert await sync.push() == ZERO
    assert fake.requests == []
    assert store.event(e["id"])["dirty"] is True and store.event(e["id"])["google_id"] == ""


async def test_can_push_needs_every_condition(sync, push_on, conn):
    assert sync.can_push() is True
    conn.settings.destination = ""
    assert sync.can_push() is False
    conn.settings.destination = "dest"
    conn.settings.scopes = [s for s in conn.settings.scopes if not s.endswith("/calendar.events")]
    assert sync.can_push() is False
    conn.settings.scopes.append("https://www.googleapis.com/auth/calendar.events")
    conn.settings.push = False
    assert sync.can_push() is False
    conn.settings.push = True
    conn.mark_needs_reconnect()
    assert sync.can_push() is False


async def test_events_made_before_push_was_on_are_sent(sync, store, fake, push_on):
    push_on.settings.push = False
    a, b = store.add(ev()), store.add(ev(title="Gym"))
    assert await sync.push() == ZERO and fake.requests == []
    push_on.settings.push = True
    assert await sync.push() == {**ZERO, "added": 2}
    for e in (a, b):
        row = store.event(e["id"])
        gid = google_event_id(e)
        assert row["google_calendar"] == "dest" and row["google_id"] == gid and row["dirty"] is False
        assert fake.events["dest"][gid]["summary"] == e["title"]
        assert row["etag"] == fake.events["dest"][gid]["etag"]


async def test_create_then_edit_then_delete_follow(sync, store, fake, push_on):
    e = store.add(ev())
    gid = google_event_id(e)
    assert (await sync.push())["added"] == 1
    store.update(e["id"], ev(title="Dentist (moved)", start="2026-11-20T11:00:00Z", end="2026-11-20T12:00:00Z"))
    assert await sync.push() == {**ZERO, "updated": 1}
    item = fake.events["dest"][gid]
    assert item["summary"] == "Dentist (moved)" and item["start"]["dateTime"] == "2026-11-20T11:00:00Z"
    assert len(fake.events["dest"]) == 1 and store.event(e["id"])["dirty"] is False
    store.delete(e["id"])
    assert await sync.push() == {**ZERO, "removed": 1}
    assert fake.events["dest"][gid]["status"] == "cancelled" and store.event(e["id"]) is None
    assert store.pending() == []


async def test_a_retried_create_meets_409_and_adopts_the_live_copy(sync, store, fake, push_on):
    e = store.add(ev())
    fake.events["dest"] = {google_event_id(e): {"id": google_event_id(e), "summary": "old", "status": "confirmed"}}
    assert (await sync.push())["added"] == 1
    assert len(fake.events["dest"]) == 1 and fake.events["dest"][google_event_id(e)]["summary"] == "Dentist"
    row = store.event(e["id"])
    assert row["google_id"] == google_event_id(e) and row["generation"] == 0 and row["dirty"] is False


async def test_409_on_a_cancelled_copy_takes_a_new_generation(sync, store, fake, push_on):
    e = store.add(ev())
    old = google_event_id(e)
    fake.put("dest", {"id": old, "summary": "gone", "status": "cancelled"})
    assert await sync.push() == {**ZERO, "added": 1}
    row = store.event(e["id"])
    assert row["generation"] == 1 and row["google_id"].endswith("g1") and row["google_id"] == google_event_id(row)
    assert fake.events["dest"][row["google_id"]]["summary"] == "Dentist"
    assert fake.events["dest"][old]["status"] == "cancelled"


async def test_409_on_cancelled_copies_gives_up_after_three_inserts(sync, store, fake, push_on):
    e = store.add(ev())
    for n in range(3):
        fake.put("dest", {"id": google_event_id({**e, "generation": n}), "status": "cancelled"})
    assert await sync.push() == {**ZERO, "failed": 1}
    assert len([r for r in writes(fake) if r.method == "POST"]) == 3
    row = store.event(e["id"])
    assert row["dirty"] is True and row["push_error"] == "conflict" and row["google_id"] == ""
    assert await sync.push() == {**ZERO, "added": 1}  # the next run takes the next free id
    assert store.event(e["id"])["google_id"].endswith("g3")


async def test_both_changed_local_wins(sync, store, fake, push_on):
    e = store.add(ev())
    await sync.run()
    gid = google_event_id(e)
    fake.put("dest", {**fake.events["dest"][gid], "summary": "Google's title"})
    store.update(e["id"], ev(title="My title"))
    out = await sync.run()
    assert out["push"]["updated"] == 1
    assert fake.events["dest"][gid]["summary"] == "My title"
    assert store.event(e["id"])["title"] == "My title" and store.event(e["id"])["dirty"] is False


async def test_a_failed_push_keeps_the_event_dirty_and_retries(sync, store, fake, push_on):
    e = store.add(ev())
    fake.fail["/calendars/dest/events"] = 500
    assert await sync.push() == {**ZERO, "failed": 1}
    row = store.event(e["id"])
    assert row["dirty"] is True and row["push_error"] == "provider" and row["google_id"] == ""
    fake.fail.clear()
    assert await sync.push() == {**ZERO, "added": 1}
    row = store.event(e["id"])
    assert row["dirty"] is False and row["push_error"] == "" and google_event_id(e) in fake.events["dest"]


async def test_one_failure_does_not_stop_the_others(sync, store, fake, push_on):
    a = pushed(store, cal="work", gid="w1")
    store.update(a["id"], ev(title="Edited"))
    b = store.add(ev(title="New"))
    fake.fail["/calendars/work/events"] = 403
    assert await sync.push() == {**ZERO, "added": 1, "failed": 1}
    assert store.event(a["id"])["push_error"] == "forbidden" and store.event(b["id"])["dirty"] is False


async def test_auth_stops_the_push_and_is_raised(sync, store, fake, push_on):
    a, b = store.add(ev()), store.add(ev(title="Gym"))
    fake.fail["/calendars/dest/events"] = 401
    with pytest.raises(GoogleError) as caught:
        await sync.push()
    assert caught.value.kind == "auth"
    # the first event twice (a 401 is retried once with a new access token), the second never
    assert len([r for r in fake.requests if r.method == "POST" and "/events" in str(r.url)]) == 2
    assert store.event(a["id"])["dirty"] is True and store.event(b["id"])["dirty"] is True


async def test_changes_wait_while_push_is_off_and_preview_counts_them(sync, store, fake):
    edited = pushed(store, gid="m1")
    store.update(edited["id"], ev(title="Edited"))
    removed = pushed(store, gid="m2")
    store.delete(removed["id"])
    store.add(ev(title="New"))
    assert sync.preview("dest") == {"add": 1, "update": 1, "remove": 1}
    assert await sync.push() == ZERO
    assert fake.requests == [] and len(store.pending()) == 3


async def test_preview_counts_pulled_rows_and_skips_read_only_ones(sync, store, fake):
    fake.put("work", timed("w1"))
    fake.put("work", timed("w2"))
    await sync.pull()
    store.update(store.by_google("work", "w1")["id"], ev(title="Edited"))
    store.delete(store.by_google("work", "w2")["id"])
    store.apply_remote("home", "h1", ev(title="Read only"), etag="", link="", series_id="")
    store.update(store.by_google("home", "h1")["id"], ev(title="Should not be dirty"))
    assert sync.preview("dest") == {"add": 0, "update": 1, "remove": 1}


async def test_a_pushed_event_keeps_its_old_calendar_after_the_destination_changes(sync, store, fake, push_on):
    e = store.add(ev())
    await sync.push()
    gid = google_event_id(e)
    push_on.settings.destination = "work"
    store.update(e["id"], ev(title="Edited"))
    assert await sync.push() == {**ZERO, "updated": 1}
    assert fake.events["dest"][gid]["summary"] == "Edited" and gid not in fake.events.get("work", {})
    assert store.event(e["id"])["google_calendar"] == "dest"
    new = store.add(ev(title="Later"))
    await sync.push()
    assert store.event(new["id"])["google_calendar"] == "work" and google_event_id(new) in fake.events["work"]


async def test_an_edit_to_a_pulled_event_goes_back_to_its_own_calendar(sync, store, fake, push_on):
    fake.put("work", timed("w1"))
    await sync.pull()
    row = store.by_google("work", "w1")
    store.update(row["id"], ev(title="Standup (moved)"))
    assert await sync.push() == {**ZERO, "updated": 1}
    sent = writes(fake)
    assert [(r.method, r.url.path.rsplit("/", 3)[-3]) for r in sent] == [("PATCH", "work")]
    assert "If-Match" not in sent[0].headers and "id" not in body(sent[0])
    assert fake.events["work"]["w1"]["summary"] == "Standup (moved)" and not fake.events.get("dest")
    after = store.event(row["id"])
    assert after["origin"] == "google" and after["dirty"] is False
    assert after["etag"] == fake.events["work"]["w1"]["etag"]


async def test_a_dirty_pulled_row_on_a_read_only_calendar_is_skipped_and_cleaned(sync, store, fake, push_on):
    store.apply_remote("home", "h1", ev(title="Read only"), etag='"1"', link="", series_id="")
    row = store.by_google("home", "h1")
    store.update(row["id"], ev(title="Should not be dirty"))
    assert await sync.push() == ZERO
    assert writes(fake) == [] and store.event(row["id"])["dirty"] is False
    assert store.pending() == []


async def test_a_delete_on_a_read_only_calendar_is_undone_not_hidden(sync, store, fake, push_on):
    store.apply_remote("home", "h1", ev(title="Read only"), etag='"1"', link="", series_id="")
    row = store.by_google("home", "h1")
    store.delete(row["id"])
    assert await sync.push() == ZERO
    after = store.event(row["id"])
    assert writes(fake) == [] and after["deleted"] is False and after["dirty"] is False
    assert [e["title"] for e in store.events("2026-01-01", "2027-12-31")] == ["Read only"]


def edit_once_during(fake, store, id, method, title):
    """Make a local edit while Google handles the first ``method`` request on an event."""
    def on_request(request):
        if request.method == method and "/events" in request.url.path and fake.on_request is on_request:
            fake.on_request = None
            store.update(id, ev(title=title))
    fake.on_request = on_request


async def test_an_edit_made_during_its_insert_goes_out_on_the_next_push(sync, store, fake, push_on):
    e = store.add(ev())
    gid = google_event_id(e)
    edit_once_during(fake, store, e["id"], "POST", "Edited meanwhile")
    assert await sync.push() == {**ZERO, "added": 1}
    row = store.event(e["id"])
    assert row["dirty"] is True and row["google_id"] == gid and row["google_calendar"] == "dest"
    assert fake.events["dest"][gid]["summary"] == "Dentist"
    assert await sync.push() == {**ZERO, "updated": 1}
    assert fake.events["dest"][gid]["summary"] == "Edited meanwhile" and len(fake.events["dest"]) == 1
    assert store.event(e["id"])["dirty"] is False


async def test_an_edit_made_during_its_patch_goes_out_on_the_next_push(sync, store, fake, push_on):
    e = store.add(ev())
    await sync.push()
    gid = google_event_id(e)
    store.update(e["id"], ev(title="First edit"))
    edit_once_during(fake, store, e["id"], "PATCH", "Second edit")
    assert await sync.push() == {**ZERO, "updated": 1}
    assert fake.events["dest"][gid]["summary"] == "First edit" and store.event(e["id"])["dirty"] is True
    assert await sync.push() == {**ZERO, "updated": 1}
    assert fake.events["dest"][gid]["summary"] == "Second edit" and store.event(e["id"])["dirty"] is False


async def test_a_delete_made_during_a_patch_is_sent_next(sync, store, fake, push_on):
    e = store.add(ev())
    await sync.push()
    gid = google_event_id(e)
    store.update(e["id"], ev(title="Edited"))

    def on_request(request):
        if request.method == "PATCH" and fake.on_request is on_request:
            fake.on_request = None
            store.delete(e["id"])
    fake.on_request = on_request
    await sync.push()
    assert store.event(e["id"])["deleted"] is True and store.event(e["id"])["dirty"] is True
    assert await sync.push() == {**ZERO, "removed": 1}
    assert fake.events["dest"][gid]["status"] == "cancelled" and store.event(e["id"]) is None


def delete_once_during(fake, store, id, method, then=None):
    """Delete an event here while Google handles the first ``method`` request on events."""
    def on_request(request):
        if request.method == method and "/events" in request.url.path and fake.on_request is on_request:
            fake.on_request = None
            store.delete(id)
            if then:
                then()
    fake.on_request = on_request


async def test_an_event_deleted_during_its_insert_is_removed_from_google(sync, store, fake, push_on):
    e = store.add(ev())
    gid = google_event_id(e)
    delete_once_during(fake, store, e["id"], "POST")
    assert await sync.push() == ZERO
    assert store.event(e["id"]) is None and fake.events["dest"][gid]["status"] == "cancelled"
    assert [r.method for r in writes(fake)] == ["POST", "DELETE"]


async def test_a_copy_that_cannot_be_removed_is_counted_as_orphaned(sync, store, fake, push_on):
    e = store.add(ev())
    gid = google_event_id(e)
    delete_once_during(fake, store, e["id"], "POST", then=lambda: fake.fail.update({f"/events/{gid}": 500}))
    out = await sync.run()
    assert out["push"] == ZERO and store.event(e["id"]) is None
    assert fake.events["dest"][gid]["status"] == "confirmed"
    entry = json.loads(ledger_text(sync).splitlines()[-1])["data"]
    assert entry["orphaned"] == 1 and "error" not in entry and "Dentist" not in ledger_text(sync)


async def test_an_event_deleted_during_its_remake_is_removed_from_google(sync, store, fake, push_on):
    e = pushed(store, cal="work", gid="w-gone")
    store.update(e["id"], ev(title="Edited"))
    delete_once_during(fake, store, e["id"], "POST")
    assert await sync.push() == ZERO
    assert store.event(e["id"]) is None
    assert [x["status"] for x in fake.events["dest"].values()] == ["cancelled"]


async def test_an_event_deleted_while_a_live_copy_is_adopted_removes_that_copy(sync, store, fake, push_on):
    e = store.add(ev())
    gid = google_event_id(e)
    fake.put("dest", {"id": gid, "summary": "old", "status": "confirmed"})
    delete_once_during(fake, store, e["id"], "PATCH")
    assert await sync.push() == ZERO
    assert store.event(e["id"]) is None and fake.events["dest"][gid]["status"] == "cancelled"


async def test_a_made_here_event_deleted_during_a_patch_that_finds_it_gone_is_not_sent_again(
        sync, store, fake, push_on):
    e = pushed(store, cal="work", gid="w-gone")
    store.update(e["id"], ev(title="Edited"))
    delete_once_during(fake, store, e["id"], "PATCH")
    assert await sync.push() == ZERO
    assert store.event(e["id"]) is None and [r.method for r in writes(fake)] == ["PATCH"]
    assert await sync.push() == ZERO
    assert not fake.events.get("dest") and [r.method for r in writes(fake)] == ["PATCH"]


async def test_a_pulled_event_deleted_during_a_patch_that_finds_it_gone_is_not_a_lost_edit(
        sync, store, fake, push_on):
    store.apply_remote("work", "w1", ev(title="Pulled"), etag='"1"', link="", series_id="")
    row = store.by_google("work", "w1")
    store.update(row["id"], ev(title="Edited"))
    delete_once_during(fake, store, row["id"], "PATCH")
    assert await sync.push() == ZERO
    assert store.event(row["id"]) is None and store.meta("lost_edits") == ""


async def test_an_event_deleted_between_inserts_in_the_409_loop_is_not_sent(sync, store, fake, push_on):
    e = store.add(ev())
    old = google_event_id(e)
    fake.put("dest", {"id": old, "summary": "gone", "status": "cancelled"})
    delete_once_during(fake, store, e["id"], "GET")  # the read after the 409
    assert await sync.push() == ZERO
    assert store.event(e["id"]) is None
    assert [r.method for r in writes(fake)] == ["POST"] and list(fake.events["dest"]) == [old]


async def test_rows_changed_before_their_turn_are_read_again(sync, store, fake, push_on):
    a, b, c = store.add(ev(title="First")), store.add(ev(title="Second")), store.add(ev(title="Third"))

    def on_request(request):
        if request.method == "POST" and fake.on_request is on_request:
            fake.on_request = None
            store.delete(b["id"])
            store.update(c["id"], ev(title="Third, edited"))
    fake.on_request = on_request
    assert await sync.push() == {**ZERO, "added": 2}
    assert store.event(b["id"]) is None and google_event_id(b) not in fake.events["dest"]
    assert fake.events["dest"][google_event_id(c)]["summary"] == "Third, edited"
    assert store.event(c["id"])["dirty"] is False and store.event(a["id"])["dirty"] is False


async def test_a_read_only_edit_is_replaced_by_googles_version_on_the_next_pull(sync, store, fake, push_on):
    store.set_calendar("home", selected=True)
    fake.put("home", timed("h1", "Google's title"))
    await sync.pull()
    row = store.by_google("home", "h1")
    assert {c["id"]: c for c in store.calendars()}["home"]["sync_token"]
    store.update(row["id"], ev(title="Local edit"))
    await sync.push()
    assert {c["id"]: c for c in store.calendars()}["home"]["sync_token"] is None
    await sync.pull()
    assert store.event(row["id"])["title"] == "Google's title" and store.event(row["id"])["dirty"] is False


async def test_run_makes_again_a_made_here_event_deleted_on_google(sync, store, fake, push_on):
    e = store.add(ev())
    await sync.run()
    gid = google_event_id(e)
    fake.put("dest", {**fake.events["dest"][gid], "status": "cancelled"})
    store.update(e["id"], ev(title="Edited"))
    out = await sync.run()
    assert out["push"] == {**ZERO, "added": 1}
    row = store.event(e["id"])
    assert row["google_id"].endswith("g1") and row["dirty"] is False
    assert fake.events["dest"][row["google_id"]]["status"] == "confirmed"
    assert fake.events["dest"][row["google_id"]]["summary"] == "Edited"
    assert [x["title"] for x in store.events("2026-01-01", "2027-12-31")] == ["Edited"]


async def test_run_records_an_internal_error(sync, store, fake, push_on, monkeypatch):
    async def broken():
        raise RuntimeError("boom")
    monkeypatch.setattr(sync, "push", broken)
    with pytest.raises(RuntimeError):
        await sync.run()
    assert store.meta("last_error") == "internal" and store.meta("last_sync") == ""
    assert json.loads(ledger_text(sync).splitlines()[-1])["data"]["error"] == "internal"


async def test_deleting_a_pulled_event_deletes_it_on_its_own_calendar(sync, store, fake, push_on):
    fake.put("work", timed("w1"))
    await sync.pull()
    row = store.by_google("work", "w1")
    store.delete(row["id"])
    assert await sync.push() == {**ZERO, "removed": 1}
    assert [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in writes(fake)] == [("DELETE", "w1")]
    assert fake.events["work"]["w1"]["status"] == "cancelled" and store.event(row["id"]) is None


async def test_deleting_an_event_google_no_longer_has_counts_as_done(sync, store, fake, push_on):
    absent = pushed(store, gid="m1")
    store.delete(absent["id"])
    cancelled = pushed(store, gid="m2")
    fake.put("dest", {"id": "m2", "status": "cancelled"})
    store.delete(cancelled["id"])
    assert await sync.push() == {**ZERO, "removed": 2}
    assert store.event(absent["id"]) is None and store.event(cancelled["id"]) is None


async def test_an_edit_to_a_made_here_event_deleted_on_google_makes_it_again(sync, store, fake, push_on):
    push_on.settings.destination = "work"
    e = pushed(store, cal="dest", gid="m1")
    fake.put("dest", {"id": "m1", "summary": "Dentist", "status": "cancelled"})
    store.update(e["id"], ev(title="Edited"))
    assert await sync.push() == {**ZERO, "added": 1}
    row = store.event(e["id"])
    assert row["generation"] == 1 and row["google_calendar"] == "work" and row["google_id"].endswith("g1")
    assert fake.events["work"][row["google_id"]]["summary"] == "Edited" and row["dirty"] is False
    assert [r.method for r in writes(fake)] == ["PATCH", "POST"]


async def test_a_patch_that_answers_a_cancelled_event_makes_it_again(sync, store, fake, push_on):
    fake.patch_cancelled = True
    e = pushed(store, gid="m1")
    fake.put("dest", {"id": "m1", "summary": "Dentist", "status": "cancelled"})
    store.update(e["id"], ev(title="Edited"))
    assert await sync.push() == {**ZERO, "added": 1}
    row = store.event(e["id"])
    assert row["google_id"] == google_event_id(row) and row["generation"] == 1
    assert fake.events["dest"][row["google_id"]]["status"] == "confirmed"


async def test_a_failed_remake_inserts_next_time_without_patching_again(sync, store, fake, push_on):
    e = pushed(store, cal="work", gid="w-gone")
    store.update(e["id"], ev(title="Edited"))
    fake.fail["/calendars/dest/events"] = 500
    assert await sync.push() == {**ZERO, "failed": 1}
    row = store.event(e["id"])
    # no link, and the calendar the failed insert was sent to is kept for the retry
    assert row["google_id"] == "" and row["google_calendar"] == "dest" and row["dirty"] is True
    assert row["generation"] == 1 and row["push_error"] == "provider"
    fake.fail.clear()
    fake.requests.clear()
    assert await sync.push() == {**ZERO, "added": 1}
    assert [r.method for r in writes(fake)] == ["POST"]
    assert store.event(e["id"])["google_id"].endswith("g1")


async def test_an_edit_to_a_pulled_event_deleted_on_google_is_dropped(sync, store, fake, push_on):
    store.apply_remote("work", "w1", ev(title="Pulled"), etag='"1"', link="", series_id="")
    row = store.by_google("work", "w1")
    store.update(row["id"], ev(title="Edited"))
    assert await sync.push() == ZERO
    assert store.event(row["id"]) is None and "w1" not in fake.events.get("work", {})
    assert store.meta("lost_edits") == "1"


async def test_no_guests_or_reminders_are_ever_sent(sync, store, fake, push_on):
    fake.put("work", timed("w1", attendees=[{"email": "a@example.com"}], reminders={"useDefault": True}))
    await sync.pull()
    store.update(store.by_google("work", "w1")["id"], ev(title="Edited"))
    e = store.add(ev())
    fake.put("dest", {"id": google_event_id(e), "status": "confirmed", "summary": "x"})  # a 409 then a patch
    await sync.push()
    store.update(e["id"], ev(title="Again"))
    other = store.add(ev(title="Fresh"))
    await sync.push()
    sent = [body(r) for r in writes(fake)]
    assert len(sent) >= 4 and google_event_id(other) in fake.events["dest"]
    for b in sent:
        assert "attendees" not in b and "reminders" not in b and "conferenceData" not in b


async def test_run_sets_last_sync_only_on_success(sync, store, fake, push_on):
    store.add(ev())
    out = await sync.run()
    assert out["push"] == {**ZERO, "added": 1} and out["pull"]["calendars"] == 2
    assert store.meta("last_sync") == SYNC_NOW and store.meta("last_error") == ""
    store.set_meta("last_sync", "2026-11-01T00:00:00Z")
    fake.fail["/users/me/calendarList"] = 500
    with pytest.raises(GoogleError):
        await sync.run()
    assert store.meta("last_sync") == "2026-11-01T00:00:00Z" and store.meta("last_error") == "provider"
    fake.fail.clear()
    await sync.run()
    assert store.meta("last_sync") == SYNC_NOW and store.meta("last_error") == ""
    entries = [json.loads(line) for line in ledger_text(sync).splitlines()]
    runs = [x for x in entries if x["kind"] == "calendar" and x["data"].get("event") == "sync"]
    assert len(runs) == 3
    assert runs[0]["data"]["push"]["added"] == 1 and "error" not in runs[0]["data"]
    assert runs[1]["data"]["error"] == "provider"
    assert "Dentist" not in ledger_text(sync)


async def test_a_run_never_resets_lost_edits(sync, store, fake, push_on):
    """The counter only grows: the page acknowledges what it has shown (POST /calendar/lost-edits/ack)."""
    store.set_meta("lost_edits", "4")
    await sync.run()
    assert store.meta("lost_edits") == "4"
    store.apply_remote("work", "w1", ev(title="Pulled"), etag='"1"', link="", series_id="")
    store.update(store.by_google("work", "w1")["id"], ev(title="Edited"))
    out = await sync.run()  # work holds no w1: the patch finds it gone
    assert store.meta("lost_edits") == "5" and out["pull"]["lost_edits"] == 0


async def test_run_does_nothing_unless_connected(sync, store, fake, conn, push_on):
    store.add(ev())
    store.set_meta("lost_edits", "2")
    conn.mark_needs_reconnect()
    out = await sync.run()
    assert out["push"] == ZERO and out["pull"]["calendars"] == 0
    assert fake.requests == [] and store.meta("last_sync") == "" and store.meta("lost_edits") == "2"
    assert ledger_text(sync) == ""


async def test_invalid_grant_during_run_marks_needs_reconnect_and_keeps_events(sync, store, fake, conn):
    e = store.add(ev())
    store.apply_remote("work", "w1", ev(title="Pulled"), etag='"1"', link="", series_id="")
    fake.refresh_ok = False
    with pytest.raises(GoogleError) as caught:
        await sync.run()
    assert caught.value.kind == "auth"
    assert conn.state() == "needs_reconnect"
    assert store.event(e["id"])["dirty"] is True and store.by_google("work", "w1") is not None
    assert store.meta("last_error") == "auth" and store.meta("last_sync") == ""
    assert '"error": "auth"' in ledger_text(sync)


# the final review's findings ----------------------------------------------------


async def test_an_insert_whose_answer_was_lost_is_linked_by_the_next_pull_not_added_twice(
        push_on, sync, store, fake):
    row = store.add(ev())
    gid = google_event_id(row)
    # the insert reached Google, its answer never came back: Google holds the copy, the row has no link
    fake.put("dest", {"id": gid, "status": "confirmed", **to_google(row)})
    await sync.run()
    rows = store.events("2026-11-01", "2026-12-01")
    assert [(r["id"], r["origin"], r["calendar_id"], r["google_calendar"], r["google_id"], r["dirty"])
            for r in rows] == [(row["id"], "local", "local", "dest", gid, False)]
    assert [r.method for r in writes(fake)] == ["PATCH"] and len(fake.events["dest"]) == 1


async def test_a_pulled_id_of_our_shape_links_only_the_same_generation_without_a_link(push_on, sync, store, fake):
    row = store.add(ev())
    store.bump_generation(row["id"])
    old = google_event_id({**row, "generation": 0})
    fake.put("dest", {"id": old, "status": "confirmed", **to_google(row)})
    await sync.pull()
    assert store.event(row["id"])["google_id"] == ""  # another generation: not this row's copy
    assert store.by_google("dest", old)["origin"] == "google"
    fake.put("dest", {"id": "f" * 32 + "g0", "status": "confirmed", **to_google(row)})  # nobody's id here
    await sync.pull()
    assert store.by_google("dest", "f" * 32 + "g0")["origin"] == "google"


def test_mark_pushed_drops_a_pulled_row_holding_the_same_google_event(store):
    store.upsert_google([{"id": "dest", "name": "Dest", "writable": True}])
    e = store.add(ev())
    store.apply_remote("dest", "gid-1", ev(title="Pulled twin"), etag='"1"', link="", series_id="")
    store.apply_remote("dest", "gid-2", ev(title="Another"), etag='"1"', link="", series_id="")
    assert store.mark_pushed(e["id"], google_calendar="dest", google_id="gid-1", etag="", link="") is True
    rows = store.events("2026-11-01", "2026-12-01")
    assert sorted(r["title"] for r in rows) == ["Another", "Dentist"]
    assert store.by_google("dest", "gid-1")["id"] == e["id"]


async def test_push_stops_when_it_is_switched_off_mid_run(push_on, sync, store, fake):
    for i in range(3):
        store.add(ev(title=f"e{i}"))
    seen = []

    def hook(request):
        if request.method == "POST" and "/events" in str(request.url):
            seen.append(1)
            push_on.settings.push = False  # the user switches push off while the first insert is in flight

    fake.on_request = hook
    out = await sync.run()
    assert len(seen) == 1 and out["push"] == {**ZERO, "added": 1}
    assert len(store.pending()) == 2


async def test_all_day_to_timed_and_back_patches_without_leaving_both_forms(sync, store, fake, push_on):
    e = store.add(ev())
    gid = google_event_id(e)
    await sync.push()
    assert "date" not in body(writes(fake)[-1])["start"]  # an insert carries no nulls
    assert fake.events["dest"][gid]["start"] == {"dateTime": "2026-11-20T09:00:00Z", "timeZone": "Europe/London"}
    store.update(e["id"], ev(all_day=True, start="2026-11-20", end="2026-11-21", tz=""))
    assert await sync.push() == {**ZERO, "updated": 1}
    assert fake.events["dest"][gid]["start"] == {"date": "2026-11-20"}
    assert fake.events["dest"][gid]["end"] == {"date": "2026-11-21"}
    store.update(e["id"], ev())
    assert await sync.push() == {**ZERO, "updated": 1}
    assert fake.events["dest"][gid]["end"] == {"dateTime": "2026-11-20T10:00:00Z", "timeZone": "Europe/London"}


async def test_an_adopted_copy_of_the_other_form_is_patched_cleanly(sync, store, fake, push_on):
    e = store.add(ev())
    gid = google_event_id(e)
    fake.put("dest", {"id": gid, "status": "confirmed", "summary": "old", "start": {"date": "2026-11-20"},
                      "end": {"date": "2026-11-21"}})
    assert (await sync.push())["added"] == 1
    assert fake.events["dest"][gid]["start"] == {"dateTime": "2026-11-20T09:00:00Z", "timeZone": "Europe/London"}


async def test_a_retried_insert_goes_to_the_calendar_it_was_first_sent_to(sync, store, fake, push_on):
    fake.calendars.append({"id": "other", "summary": "Other", "accessRole": "owner"})
    await sync.refresh_calendars()
    e = store.add(ev())
    gid = google_event_id(e)
    fake.fail = {"/calendars/dest/events": 500}
    assert await sync.push() == {**ZERO, "failed": 1}
    assert store.event(e["id"])["google_calendar"] == "dest" and store.event(e["id"])["google_id"] == ""
    fake.fail = {}
    fake.put("dest", {"id": gid, "status": "confirmed", **to_google(e)})  # that insert had landed after all
    push_on.settings.destination = "other"
    assert await sync.push() == {**ZERO, "added": 1}
    assert store.event(e["id"])["google_calendar"] == "dest" and not fake.events.get("other")
    later = store.add(ev(title="Later"))
    await sync.push()
    assert store.event(later["id"])["google_calendar"] == "other"  # a new event takes the new destination


async def test_a_saved_target_that_is_no_longer_writable_gives_way_to_the_destination(sync, store, fake, push_on):
    fake.calendars.append({"id": "other", "summary": "Other", "accessRole": "owner"})
    await sync.refresh_calendars()
    e = store.add(ev())
    fake.fail = {"/calendars/dest/events": 500}
    await sync.push()
    fake.fail = {}
    fake.calendars[:] = [c for c in fake.calendars if c["id"] != "dest"]
    await sync.refresh_calendars()
    push_on.settings.destination = "other"
    assert await sync.push() == {**ZERO, "added": 1}
    assert store.event(e["id"])["google_calendar"] == "other"


async def test_a_pulled_event_with_no_title_goes_back_with_an_empty_summary(sync, store, fake, push_on):
    fake.put("work", {"id": "w1", "status": "confirmed", "start": {"date": "2026-11-20"},
                      "end": {"date": "2026-11-21"}})
    await sync.pull()
    row = store.by_google("work", "w1")
    assert row["title"] == "(No title)"
    store.update(row["id"], {**row, "location": "Here"})
    assert await sync.push() == {**ZERO, "updated": 1}
    assert fake.events["work"]["w1"]["summary"] == "" and fake.events["work"]["w1"]["location"] == "Here"
    mine = store.add(ev(title="(No title)"))
    await sync.push()
    assert fake.events["dest"][google_event_id(mine)]["summary"] == "(No title)"  # the user's own words


async def test_a_long_pulled_title_is_kept_whole_and_an_edit_of_it_is_refused(sync, store, fake, push_on):
    from src.events.store import EventError

    fake.put("work", timed("w1", "t" * 1000, location="l" * 900, description="d" * 9000))
    await sync.pull()
    row = store.by_google("work", "w1")
    assert (len(row["title"]), len(row["location"]), len(row["description"])) == (1000, 900, 9000)
    with pytest.raises(EventError, match="The title is longer than 300 characters."):
        store.update(row["id"], {**row, "location": ""})
    assert store.by_google("work", "w1")["dirty"] is False and writes(fake) == []
    store.update(row["id"], {**row, "title": "Short", "location": "", "description": ""})
    await sync.push()
    assert fake.events["work"]["w1"]["summary"] == "Short"


async def test_a_calendar_list_with_no_primary_is_a_provider_error_and_drops_nothing(sync, store, fake):
    store.apply_remote("work", "w1", ev(title="Pulled"), etag='"1"', link="", series_id="")
    for listed in ([], [{"id": "home", "summary": "Home", "accessRole": "reader"}]):
        fake.calendars = listed
        with pytest.raises(GoogleError) as caught:
            await sync.run()
        assert caught.value.kind == "provider"
        assert {c["id"] for c in store.calendars()} == {"local", "work", "home"}
        assert store.by_google("work", "w1") is not None and store.meta("last_error") == "provider"

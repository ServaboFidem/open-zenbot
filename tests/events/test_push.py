import json

import pytest

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
    assert len([r for r in fake.requests if r.method == "POST" and "/events" in str(r.url)]) == 1
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
    assert row["google_id"] == "" and row["google_calendar"] == "" and row["dirty"] is True
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
        assert "attendees" not in b and "reminders" not in b


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


async def test_run_resets_lost_edits_to_this_runs_count(sync, store, fake, push_on):
    store.set_meta("lost_edits", "4")
    await sync.run()
    assert store.meta("lost_edits") == "0"


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

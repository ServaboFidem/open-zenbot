"""The service: setup, the loop, never twice, the retry rules and the daily cap. Telnyx is the stand-in."""

import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from src.events.store import CalendarStore
from src.memory.ledger import Ledger
from src.telnyx.client import TelnyxError
from src.telnyx.service import DAILY_CAP, TEST_TEXT, TelnyxService
from src.telnyx.settings import TEXTS_FILE, TelnyxKeys, TelnyxNumber
from src.vault_settings import settings_dir
from tests.telnyx.conftest import KEY

ZONE = timezone(timedelta(hours=13))  # far from any test machine's zone: a day read from the host's clock shows
NOW = datetime(2026, 11, 23, 14, 0, tzinfo=ZONE)
OWNER = "+15551234567"


def utc(moment):
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def store(tmp_path):
    s = CalendarStore(tmp_path / "c.db")
    yield s
    s.close()


@pytest.fixture
def ledger(vault):
    return Ledger(vault)


def make(vault, ledger, store, fake):
    return TelnyxService(vault, ledger, lambda: store, http=fake.client)


def texts(vault, **over):
    value = {"phone": OWNER, "lead_minutes": 30, "evening": "20:00", "before": True, "rundown": False} | over
    path = settings_dir(vault) / TEXTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


async def ready(vault, ledger, store, fake, **over):
    service = make(vault, ledger, store, fake)
    await service.set_key(KEY)
    await service.choose_number("n1")
    texts(vault, **over)
    return service


def event(store, title, start, **fields):
    return store.add({"title": title, "all_day": False, "start": utc(start),
                      "end": utc(start + timedelta(hours=1)), "tz": "America/New_York", **fields})


def entries(ledger):
    return [e.data for e in ledger.tail(0, kind="telnyx")]


def sends(fake):
    return sum(1 for r in fake.requests if r.url.path == "/v2/messages")


async def test_nothing_without_setup(vault, ledger, store, fake_telnyx):
    service = make(vault, ledger, store, fake_telnyx)
    texts(vault)
    event(store, "Soon", NOW + timedelta(minutes=10))
    await service.tick(NOW)
    assert fake_telnyx.requests == []


async def test_nothing_when_texts_off(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx, before=False)
    event(store, "Soon", NOW + timedelta(minutes=10))
    await service.tick(NOW)
    assert fake_telnyx.sent == []


async def test_sends_reminder_once(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    event(store, "Dentist", NOW + timedelta(minutes=20), location="1 Main St")
    await service.tick(NOW)
    await service.tick(NOW + timedelta(minutes=1))
    assert fake_telnyx.sent == [{"from": "+15550001111", "to": OWNER,
                                 "text": "Reminder: Dentist at 2:20 PM - 1 Main St"}]
    sent = [e for e in entries(ledger) if e.get("event") == "sent"]
    assert sent == [{"event": "sent", "type": "before", "message_id": "msg-1"}]
    assert "Dentist" not in json.dumps(entries(ledger)) and OWNER not in json.dumps(entries(ledger))


async def test_never_twice_after_crash(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    row = event(store, "Dentist", NOW + timedelta(minutes=20))
    key = f"before:{row['id']}:{row['start']}"
    assert service.record.claim(key, "before", "2026-11-23", NOW.timestamp())  # a send cut off mid-way
    again = make(vault, ledger, store, fake_telnyx)
    assert again.settle() == 1
    await again.tick(NOW)
    assert fake_telnyx.sent == []
    assert again.record.known(key) == "uncertain"


@pytest.mark.parametrize("answer, retried", [
    (httpx.ConnectError("down"), True),
    (429, True),
    (httpx.ReadTimeout("slow"), False),
    (500, False),
    (422, False),
    (401, False),
])
async def test_retry_rules(vault, ledger, store, fake_telnyx, answer, retried):
    service = await ready(vault, ledger, store, fake_telnyx)
    event(store, "Dentist", NOW + timedelta(minutes=20))
    fake_telnyx.fail["POST /messages"] = answer
    await service.tick(NOW)
    assert sends(fake_telnyx) == 1
    await service.tick(NOW + timedelta(minutes=1))
    assert sends(fake_telnyx) == (2 if retried else 1)
    await service.tick(NOW + timedelta(minutes=2))
    assert sends(fake_telnyx) == (2 if retried else 1)  # a retry is tried once more, never again


async def test_a_refused_number_is_failed_and_shown(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    event(store, "Dentist", NOW + timedelta(minutes=20))
    fake_telnyx.fail["POST /messages"] = 422
    await service.tick(NOW)
    assert service.status(NOW)["last_error"] == "invalid"
    assert [e for e in entries(ledger) if e.get("event") == "failed"] == \
        [{"event": "failed", "type": "before", "error": "invalid", "retry": False}]


async def test_a_timeout_is_uncertain(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    event(store, "Dentist", NOW + timedelta(minutes=20))
    fake_telnyx.fail["POST /messages"] = httpx.ReadTimeout("slow")
    await service.tick(NOW)
    assert service.status(NOW)["last_error"] == "uncertain"  # it may have gone: never "could not reach"
    assert [e for e in entries(ledger) if e.get("event") == "uncertain"] == \
        [{"event": "uncertain", "type": "before", "error": "network"}]


async def test_a_retry_that_then_works_is_sent(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    event(store, "Dentist", NOW + timedelta(minutes=20))
    fake_telnyx.fail["POST /messages"] = 429
    await service.tick(NOW)
    del fake_telnyx.fail["POST /messages"]
    await service.tick(NOW + timedelta(minutes=1))
    assert len(fake_telnyx.sent) == 1
    assert service.status(NOW)["sent_today"] == 1


async def test_a_retry_that_fails_again_is_failed_and_shown(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    row = event(store, "Dentist", NOW + timedelta(minutes=20))
    fake_telnyx.fail["POST /messages"] = httpx.ConnectError("down")
    await service.tick(NOW)
    assert service.status(NOW)["last_error"] == "network"  # waiting to be tried again: shown, not hidden
    await service.tick(NOW + timedelta(minutes=1))
    assert service.record.known(f"before:{row['id']}:{row['start']}") == "failed"
    assert service.status(NOW)["last_error"] == "network"


def test_the_service_clock_is_naive_local():
    from src.telnyx.service import _local
    assert _local(None).tzinfo is None and _local(1_800_000_000.0).tzinfo is None


async def test_a_retry_is_not_sent_once_the_event_started(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    event(store, "Dentist", NOW + timedelta(minutes=1))
    fake_telnyx.fail["POST /messages"] = 429
    await service.tick(NOW)
    del fake_telnyx.fail["POST /messages"]
    await service.tick(NOW + timedelta(minutes=2))
    assert fake_telnyx.sent == []


async def test_per_pass_and_daily_cap(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx, lead_minutes=120)
    for n in range(DAILY_CAP + 5):
        event(store, f"Meeting {n}", NOW + timedelta(minutes=60, seconds=n))
    for expected in (10, 20, 30, 30):
        await service.tick(NOW)
        assert len(fake_telnyx.sent) == expected
    status = service.status(NOW)
    assert (status["sent_today"], status["skipped_today"]) == (30, 5)
    with pytest.raises(PermissionError):
        await service.test(NOW + timedelta(hours=1))
    assert [e for e in entries(ledger) if e.get("event") == "capped"] == [{"event": "capped", "skipped": 5}]


async def test_rundown_not_resent_after_restart(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx, before=False, rundown=True)
    event(store, "Dentist", datetime(2026, 11, 24, 9, 30, tzinfo=ZONE))
    await service.tick(NOW.replace(hour=20))
    assert [s["text"] for s in fake_telnyx.sent] == ["Tomorrow: 9:30 AM Dentist"]
    again = make(vault, ledger, store, fake_telnyx)
    again.settle()
    await again.tick(NOW.replace(hour=20, minute=30))
    assert len(fake_telnyx.sent) == 1


async def test_empty_rundown_recorded_once(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx, before=False, rundown=True)
    await service.tick(NOW.replace(hour=20))
    assert service.record.known("rundown:2026-11-23") == "empty"
    event(store, "Late add", datetime(2026, 11, 24, 9, 30, tzinfo=ZONE))
    await service.tick(NOW.replace(hour=21))
    assert fake_telnyx.sent == []


async def test_test_text_gap(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    await service.test(NOW)
    with pytest.raises(PermissionError):
        await service.test(NOW + timedelta(seconds=30))
    await service.test(NOW + timedelta(seconds=61))
    assert [s["text"] for s in fake_telnyx.sent] == [TEST_TEXT, TEST_TEXT]
    assert service.status(NOW)["sent_today"] == 2


async def test_a_failed_test_text_raises_its_error(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    fake_telnyx.fail["POST /messages"] = 422
    with pytest.raises(TelnyxError) as caught:
        await service.test(NOW)
    assert caught.value.kind == "invalid"


async def test_test_needs_setup(vault, ledger, store, fake_telnyx):
    service = make(vault, ledger, store, fake_telnyx)
    with pytest.raises(LookupError):
        await service.test()
    await service.set_key(KEY)
    await service.choose_number("n1")
    with pytest.raises(LookupError):  # no phone saved in the desktop app
        await service.test()


async def test_set_key_clears_unlisted_number(vault, ledger, store, fake_telnyx):
    await ready(vault, ledger, store, fake_telnyx)
    fake_telnyx.numbers = {"p1": []}
    service = make(vault, ledger, store, fake_telnyx)
    status = await service.set_key(KEY)
    assert status["number"] is None
    with pytest.raises(KeyError):
        await service.choose_number("n1")


def broken_save(monkeypatch):
    """Saving an empty sending number fails, as a full disk or a locked file would."""
    real = TelnyxNumber.save

    def save(self, vault_root):
        if not self.id:
            raise OSError("disk full")
        return real(self, vault_root)
    monkeypatch.setattr(TelnyxNumber, "save", save)


async def test_forget_stops_using_the_key_even_if_clearing_the_number_fails(vault, ledger, store, fake_telnyx,
                                                                           monkeypatch):
    service = await ready(vault, ledger, store, fake_telnyx)
    event(store, "Dentist", NOW + timedelta(minutes=20))
    broken_save(monkeypatch)
    with pytest.raises(OSError):
        service.forget_key()
    assert service.keys.api_key == ""
    await service.tick(NOW)
    assert fake_telnyx.requests[-1].url.path != "/v2/messages" and fake_telnyx.sent == []


async def test_a_new_key_never_sends_from_a_number_it_cannot_see(vault, ledger, store, fake_telnyx, monkeypatch):
    service = await ready(vault, ledger, store, fake_telnyx)
    event(store, "Dentist", NOW + timedelta(minutes=20))
    fake_telnyx.numbers = {"p1": []}
    broken_save(monkeypatch)
    with pytest.raises(OSError):
        await service.set_key(KEY)
    assert service.number.phone == ""
    await service.tick(NOW)
    assert fake_telnyx.sent == []


async def test_a_number_checked_with_a_replaced_key_is_not_saved(vault, ledger, store, fake_telnyx):
    service = make(vault, ledger, store, fake_telnyx)
    await service.set_key(KEY)
    real = service.numbers

    async def racing():
        listed = await real()
        service.keys = TelnyxKeys(api_key="OTHERKEY0000")  # a new key saved while the list was being read
        return listed
    service.numbers = racing
    with pytest.raises(KeyError):
        await service.choose_number("n1")
    assert service.number.phone == ""


async def test_the_loop_survives_a_failed_settle(vault, ledger, store, fake_telnyx, monkeypatch):
    import sqlite3

    import src.telnyx.service as module
    service = make(vault, ledger, store, fake_telnyx)
    calls, fail = [], [True]

    def settle():
        if fail:
            fail.pop()
            raise sqlite3.OperationalError("database is locked")
        calls.append("settle")
        return 0

    async def tick(now=None):
        calls.append("tick")
    service.settle, service.tick = settle, tick
    monkeypatch.setattr(module, "INTERVAL_S", 0)
    task = asyncio.create_task(service.run())
    for _ in range(100):
        await asyncio.sleep(0)
        if "tick" in calls:
            break
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert calls[:2] == ["settle", "tick"]


async def test_a_reminder_whose_event_started_during_the_pass_is_not_sent(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    first = event(store, "Dentist", NOW + timedelta(minutes=20))
    second = event(store, "Call", NOW + timedelta(minutes=21))
    times = iter([NOW, NOW, NOW + timedelta(minutes=25)])  # the first send took the pass past the second's start
    service.clock = lambda: next(times)
    await service.tick()
    assert [s["text"] for s in fake_telnyx.sent] == ["Reminder: Dentist at 2:20 PM"]
    assert service.record.known(f"before:{second['id']}:{second['start']}") is None
    assert service.record.known(f"before:{first['id']}:{first['start']}") == "sent"


async def test_a_pass_that_crosses_midnight_stops(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    late = NOW.replace(hour=23, minute=50)
    event(store, "Late", late + timedelta(minutes=5))
    event(store, "Later", late + timedelta(minutes=15))
    times = iter([late, late, late + timedelta(minutes=11)])
    service.clock = lambda: next(times)
    await service.tick()
    assert len(fake_telnyx.sent) == 1  # the next pass rebuilds the list for the new day


async def test_settings_changed_during_a_pass_end_it(vault, ledger, store, fake_telnyx, monkeypatch):
    import src.telnyx.service as module
    from src.telnyx.settings import TextsConfig
    service = await ready(vault, ledger, store, fake_telnyx)
    event(store, "Dentist", NOW + timedelta(minutes=20))
    event(store, "Call", NOW + timedelta(minutes=21))
    on = TextsConfig(phone=OWNER, lead_minutes=30, evening="20:00", before=True, rundown=False)
    moved = TextsConfig(phone="+15559998888", lead_minutes=30, evening="20:00", before=True, rundown=False)
    answers = iter([on, on, moved, moved])
    monkeypatch.setattr(module, "read_texts", lambda vault_root: next(answers))
    await service.tick(NOW)
    assert [s["to"] for s in fake_telnyx.sent] == [OWNER]


async def test_a_retry_carried_past_midnight_counts_toward_the_new_day(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    row = event(store, "Dentist", NOW + timedelta(minutes=20))
    key = f"before:{row['id']}:{row['start']}"
    service.record.claim(key, "before", "2026-11-22", NOW.timestamp() - 600)
    service.record.mark(key, "retry", NOW.timestamp() - 600, error="network")
    await service.tick(NOW)
    assert len(fake_telnyx.sent) == 1
    assert service.status(NOW)["sent_today"] == 1


async def test_a_retry_carried_past_midnight_is_capped_on_a_full_day(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    row = event(store, "Dentist", NOW + timedelta(minutes=20))
    key = f"before:{row['id']}:{row['start']}"
    service.record.claim(key, "before", "2026-11-22", NOW.timestamp() - 600)
    service.record.mark(key, "retry", NOW.timestamp() - 600, error="network")
    for n in range(DAILY_CAP):
        service.record.mark(f"other:{n}", "sent", NOW.timestamp() - 60, kind="before", day="2026-11-23")
    await service.tick(NOW)
    assert fake_telnyx.sent == [] and service.record.known(key) == "capped"


async def test_status_never_has_key_or_full_phone(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    status = service.status(NOW)
    text = json.dumps(status)
    assert KEY not in text and OWNER not in text
    assert status["texts"]["phone_hint"] == "4567"
    assert status["number"] == "+15550001111"
    assert KEY not in json.dumps(entries(ledger)) and KEY not in repr(service)
    service.forget_key()
    assert service.status(NOW)["key_set"] is False and service.status(NOW)["number"] is None

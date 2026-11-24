"""The service: setup, the loop, never twice, the retry rules and the daily cap. Telnyx is the stand-in."""

import json
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from src.events.store import CalendarStore
from src.memory.ledger import Ledger
from src.telnyx.client import TelnyxError
from src.telnyx.service import DAILY_CAP, TEST_TEXT, TelnyxService
from src.telnyx.settings import TEXTS_FILE
from src.vault_settings import settings_dir
from tests.telnyx.conftest import KEY

ZONE = timezone(timedelta(hours=-5))
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
        await service.test(NOW.timestamp() + 3600)
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
    at = NOW.timestamp()
    await service.test(at)
    with pytest.raises(PermissionError):
        await service.test(at + 30)
    await service.test(at + 61)
    assert [s["text"] for s in fake_telnyx.sent] == [TEST_TEXT, TEST_TEXT]
    assert service.status(NOW)["sent_today"] == 2


async def test_a_failed_test_text_raises_its_error(vault, ledger, store, fake_telnyx):
    service = await ready(vault, ledger, store, fake_telnyx)
    fake_telnyx.fail["POST /messages"] = 422
    with pytest.raises(TelnyxError) as caught:
        await service.test(NOW.timestamp())
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

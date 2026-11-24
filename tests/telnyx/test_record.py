"""The send record: a key is claimed and committed before its text goes, so no text goes twice."""

import pytest

from src.telnyx.record import SendRecord

DAY = "2026-11-23"


@pytest.fixture
def record(tmp_path):
    return SendRecord(tmp_path / "texts.sqlite3")


def test_claim_once(record):
    assert record.claim("before:e1:t", "before", DAY, 1.0) is True
    assert record.claim("before:e1:t", "before", DAY, 2.0) is False
    assert record.known("before:e1:t") == "sending"
    assert record.known("before:e2:t") is None


def test_retry_claims_once_more(record):
    record.claim("k", "before", DAY, 1.0)
    record.mark("k", "retry", 2.0, error="network")
    assert record.claim("k", "before", DAY, 3.0) is True
    record.mark("k", "retry", 4.0, error="network")
    assert record.claim("k", "before", DAY, 5.0) is False


def test_settle_turns_sending_uncertain_and_it_is_never_claimed(record):
    record.claim("k", "before", DAY, 1.0)
    assert record.settle() == 1
    assert record.known("k") == "uncertain"
    assert record.claim("k", "before", DAY, 2.0) is False
    assert record.settle() == 0


def test_count_and_skipped(record):
    for key, state in [("a", "sent"), ("b", "sent"), ("c", "failed"), ("d", "capped"), ("e", "empty")]:
        record.mark(key, state, 1.0, kind="before", day=DAY)
    record.mark("f", "sent", 1.0, kind="before", day="2026-11-24")
    assert record.count(DAY) == 3
    assert record.skipped(DAY) == 1


def test_last_sent_error_and_test(record):
    assert (record.last_sent_at(), record.last_error(), record.last_test_at()) == (None, "", None)
    record.claim("t1", "test", DAY, 10.0)
    record.mark("t1", "sent", 11.0, message_id="m1")
    assert record.last_sent_at() == 11.0 and record.last_test_at() == 11.0
    record.claim("k", "before", DAY, 20.0)
    record.mark("k", "failed", 21.0, error="invalid")
    assert record.last_error() == "invalid"
    record.claim("k2", "before", DAY, 30.0)
    record.mark("k2", "sent", 31.0, message_id="m2")
    assert record.last_error() == ""


def test_prune(record):
    record.mark("old", "sent", 1.0, kind="before", day="2026-10-01")
    record.mark("new", "sent", 100.0, kind="before", day=DAY)
    assert record.prune(50.0) == 1
    assert record.known("old") is None and record.known("new") == "sent"


def test_settle_records_why(record):
    record.claim("k", "before", DAY, 1.0)
    record.settle()
    assert record.last_error() == "interrupted"


def test_a_pending_retry_shows_its_error_and_an_uncertain_one_says_so(record):
    record.claim("a", "before", DAY, 1.0)
    record.mark("a", "retry", 1.0, error="network")
    assert record.last_error() == "network"
    record.claim("b", "before", DAY, 2.0)
    record.mark("b", "uncertain", 2.0, error="network")
    assert record.last_error() == "uncertain"

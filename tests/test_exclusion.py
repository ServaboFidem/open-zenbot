import pytest

from src.memory.exclusion import ExclusionError, ExclusionFilter


@pytest.fixture
def f() -> ExclusionFilter:
    return ExclusionFilter()


@pytest.mark.parametrize("text,reason", [
    ("key sk-or-v1-abcdefghijklmnopqrstuvwxyz", "openai-style-key"),
    ("token ghp_abcdefghijklmnopqrstuvwxyz0123", "github-token"),
    ("AKIAIOSFODNN7EXAMPLE", "aws-access-key"),
    ("xoxb-123456789012-abcdefghijkl", "slack-token"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "bearer-token"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop", "jwt"),
    ("-----BEGIN RSA PRIVATE KEY-----", "private-key-block"),
    ("password = hunter22", "credential-assignment"),
    ("api_key: abcdef123", "credential-assignment"),
])
def test_patterns(f, text, reason):
    assert reason in f.find(text)
    assert not f.check_text(text)


def test_plain_text_passes(f):
    assert f.check_text("The quarterly brief is due Friday.")
    assert f.find("") == []


def test_registered_value_is_caught_and_redacted(f):
    f.register_secret_value("hunter2hunter2")
    assert "registered-secret" in f.find("my pass is hunter2hunter2 ok")
    text, reasons = f.redact("my pass is hunter2hunter2 ok")
    assert "hunter2hunter2" not in text and "[redacted:registered-secret]" in text
    assert reasons == ["registered-secret"]


def test_short_values_are_not_registered(f):
    f.register_secret_value("abc")
    assert f.check_text("abc")


def test_redact_patterns(f):
    text, reasons = f.redact("use sk-or-v1-abcdefghijklmnopqrstuvwxyz and ghp_abcdefghijklmnopqrstuvwxyz0123")
    assert "sk-or" not in text and "ghp_" not in text
    assert set(reasons) == {"openai-style-key", "github-token"}


def test_approval_kind_is_structural(f):
    v = f.check_ingest({"kind": "approval_grant", "text": "ok go"})
    assert not v and "approval-kind:approval_grant" in v.reasons
    v = f.check_recall({"kind": "approved_draft", "text": "Dear team"})
    assert not v


def test_approval_fields_are_refused_anywhere(f):
    v = f.check_ingest({"kind": "turn", "data": {"nested": {"grant": "yes"}}})
    assert "approval-field:grant" in v.reasons


def test_record_walks_nested_strings(f):
    v = f.check_ingest({"kind": "tool_call", "out": ["fine", {"deep": "AKIAIOSFODNN7EXAMPLE"}]})
    assert "aws-access-key" in v.reasons


def test_ingest_and_recall_share_rules(f):
    rec = {"kind": "turn", "content": "password=letmein1"}
    assert f.check_ingest(rec).reasons == f.check_recall(rec).reasons


def test_require_raises(f):
    with pytest.raises(ExclusionError, match="blocked at ingest"):
        f.require({"kind": "approval_grant"})
    f.require({"kind": "turn", "content": "fine"})

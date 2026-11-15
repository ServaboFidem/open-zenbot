import pytest

from src.providers.secrets import Secret, SecretNotFound, resolve_secret


def test_env_ref(monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-or-abcdef1234567890")
    s = resolve_secret("env:MY_KEY")
    assert s.reveal() == "sk-or-abcdef1234567890"


def test_vault_ref_reads_vault_prefixed_env(monkeypatch):
    monkeypatch.setenv("VAULT_OPENROUTER", "sk-or-vault1234567890")
    assert resolve_secret("vault:OPENROUTER").reveal() == "sk-or-vault1234567890"


def test_missing_is_not_found(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(SecretNotFound):
        resolve_secret("env:NOPE")


def test_bad_refs():
    with pytest.raises(ValueError):
        resolve_secret("MY_KEY")
    with pytest.raises(ValueError):
        resolve_secret("file:MY_KEY")


def test_secret_never_prints_its_value():
    s = Secret("sk-or-supersecretvalue123")
    assert "supersecret" not in repr(s)
    assert "supersecret" not in str(s)
    assert "supersecret" not in f"{s}"
    assert "supersecret" not in repr([s])
    assert "supersecret" not in repr({"k": s})

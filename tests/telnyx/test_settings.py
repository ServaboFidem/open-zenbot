"""Telnyx settings: the key file, the sending number and the texts file only the desktop app writes."""

import json
import os

import pytest

from src.telnyx.settings import (BAD_KEY, KEYS_FILE, TEXTS_FILE, TelnyxKeys, TelnyxNumber, TextsConfig, check_key,
                                 last4, read_texts)
from src.vault_settings import settings_dir


def test_keys_file_is_owner_only_and_key_not_in_repr(vault):
    keys = TelnyxKeys(api_key="KEY0123456789")
    keys.save(vault)
    assert "KEY0123456789" not in repr(keys)
    if os.name != "nt":  # Windows cannot express the mode through chmod
        assert (settings_dir(vault) / KEYS_FILE).stat().st_mode & 0o777 == 0o600
    assert TelnyxKeys.load(vault).public() == {"key_set": True, "key_hint": "6789"}


def test_number_round_trips(vault):
    TelnyxNumber("n1", "+15550001111", "p1").save(vault)
    assert TelnyxNumber.load(vault) == TelnyxNumber("n1", "+15550001111", "p1")
    assert TelnyxNumber.load(vault).public() == {"id": "n1", "phone": "+15550001111", "profile_id": "p1"}


def _write(vault, value):
    path = settings_dir(vault) / TEXTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def test_read_texts_defaults_and_valid(vault):
    _write(vault, {"phone": "+15551234567", "before": True})
    config = read_texts(vault)
    assert config == TextsConfig("+15551234567", 30, "20:00", True, False)
    assert config.on is True
    _write(vault, {"phone": "", "before": True})
    assert read_texts(vault).on is False


@pytest.mark.parametrize("value", [
    {"phone": "5551234567"}, {"phone": "+0551234567"}, {"phone": "+1555"},
    {"lead_minutes": 45}, {"lead_minutes": "30"}, {"lead_minutes": True},
    {"evening": "24:00"}, {"evening": "8:00"}, {"before": 1}, [], "{not json",
])
def test_read_texts_rejects(vault, value):
    _write(vault, value)
    assert read_texts(vault) is None


def test_read_texts_missing_file(vault):
    assert read_texts(vault) is None


@pytest.mark.parametrize("value", ["", "a b", "x" * 201, "ké", None])
def test_check_key_refuses(value):
    with pytest.raises(ValueError, match=BAD_KEY):
        check_key(value)


def test_check_key_trims():
    assert check_key("  k1  ") == "k1"


def test_last4():
    assert last4("+15551234567") == "4567"
    assert last4("") == ""

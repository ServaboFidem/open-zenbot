"""AgentMail settings: the chosen inbox, the owner-only key file and the two checks."""

import os
import stat

import pytest

from src.mail.settings import KEYS_FILE, SETTINGS_FILE, MailKeys, MailSettings, check_key, check_username
from src.vault_settings import settings_dir


def test_key_file_is_owner_only_and_hint_is_last_four(vault):
    MailKeys(api_key="am_abcdefgh3f9c").save(vault)
    path = settings_dir(vault) / KEYS_FILE
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert MailKeys.load(vault).public() == {"key_set": True, "key_hint": "3f9c"}
    assert "am_abcdefgh3f9c" not in repr(MailKeys.load(vault))


def test_settings_round_trip(vault):
    MailSettings(inbox_id="zen@agentmail.to", email="zen@agentmail.to").save(vault)
    assert MailSettings.load(vault).public() == {"inbox_id": "zen@agentmail.to", "email": "zen@agentmail.to"}


def test_files_live_beside_the_vault_not_in_it(vault):
    MailKeys(api_key="am_abcdefgh3f9c").save(vault)
    MailSettings(inbox_id="zen@agentmail.to", email="zen@agentmail.to").save(vault)
    assert not list(vault.rglob("agentmail*.json"))
    assert (settings_dir(vault) / SETTINGS_FILE).exists()


def test_damaged_files_load_empty(vault):
    d = settings_dir(vault)
    d.mkdir(parents=True, exist_ok=True)
    (d / KEYS_FILE).write_text("{not json")
    (d / SETTINGS_FILE).write_text('{"inbox_id": 5}')
    assert MailKeys.load(vault).public() == {"key_set": False, "key_hint": ""}
    assert MailSettings.load(vault).public() == {"inbox_id": "", "email": ""}
    (d / KEYS_FILE).write_text('{"api_key": 12345678}')
    assert MailKeys.load(vault).api_key == ""


@pytest.mark.parametrize("bad", ["", "  ", "a b", "x" * 201, "kéy", "a\tb", None, 5])
def test_check_key_refuses(bad):
    with pytest.raises(ValueError, match="plain text"):
        check_key(bad)


def test_check_key_trims_and_takes_any_prefix():
    assert check_key("  sk_live_123  ") == "sk_live_123"


@pytest.mark.parametrize("bad", ["", "-zen", ".zen", "a" * 65, "zen bot", "zen@x", "zén", None])
def test_check_username_refuses(bad):
    with pytest.raises(ValueError):
        check_username(bad)


def test_check_username_lowercases():
    assert check_username(" ZenBot.Inbox ") == "zenbot.inbox"

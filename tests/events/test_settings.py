import json
import os
import stat

from src.events.settings import CalendarKeys, CalendarSettings, calendar_dir
from src.harness.allowed_folders import private_folders
from src.harness.mcp_keys import hint
from src.vault_settings import settings_dir


def test_calendar_dir_sits_beside_vault(tmp_path):
    assert calendar_dir(tmp_path / "vault") == tmp_path / "vault-calendar"


def test_calendar_dir_is_private(tmp_path):
    v = tmp_path / "vault"
    assert calendar_dir(v) in private_folders(v)


def test_settings_round_trip_and_bad_file(tmp_path):
    v = tmp_path / "vault"
    CalendarSettings(push=True, destination="a@b", account="me@x", scopes=["s"]).save(v)
    assert CalendarSettings.load(v) == CalendarSettings(push=True, destination="a@b", account="me@x", scopes=["s"])
    (settings_dir(v) / "calendar.json").write_text("[1]")
    assert CalendarSettings.load(v) == CalendarSettings()


def test_keys_are_owner_only_and_never_public(tmp_path):
    v = tmp_path / "vault"
    k = CalendarKeys(client_id="id-123456789", client_secret="sec-ABCDEFGH", refresh_token="rt-12345678")
    k.save(v)
    path = settings_dir(v) / "calendar_keys.json"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    pub = CalendarKeys.load(v).public()
    assert pub == {"credentials": True, "client_id_hint": hint("id-123456789"), "connected": True}
    assert "sec-ABCDEFGH" not in json.dumps(pub) and "rt-12345678" not in json.dumps(pub)

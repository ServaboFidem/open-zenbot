import json

from src.harness.allowed_folders import private_folders
from src.music.settings import MusicKeys, MusicSettings, music_dir
from src.vault_settings import settings_dir


def test_music_dir_sits_beside_vault(tmp_path):
    assert music_dir(tmp_path / "vault") == tmp_path / "vault-music"


def test_settings_round_trip_and_defaults(tmp_path):
    v = tmp_path / "vault"
    assert MusicSettings.load(v) == MusicSettings()
    MusicSettings(enabled=True, username="collector", focus=["blues rock"], count=4).save(v)
    s = MusicSettings.load(v)
    assert (s.username, s.focus, s.count) == ("collector", ["blues rock"], 4)
    assert (settings_dir(v) / "music.json").exists()


def test_settings_clamp_bad_values(tmp_path):
    v = tmp_path / "vault"; settings_dir(v).mkdir(parents=True)
    (settings_dir(v) / "music.json").write_text('{"count": 99, "era_mix": 7, "focus": "x"}')
    s = MusicSettings.load(v)
    assert s.count == 10 and s.era_mix == 1.0 and s.focus == []


def test_token_file_is_owner_only_and_never_public(tmp_path):
    v = tmp_path / "vault"
    MusicKeys(token="abcdEFGH1234").save(v)
    p = settings_dir(v) / "music_keys.json"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert MusicKeys.load(v).hint() == "1234"
    assert "abcdEFGH1234" not in json.dumps(MusicSettings.load(v).public())


def test_the_discogs_token_is_redacted_wherever_it_shows(tmp_path):
    from src.memory.exclusion import exclusion

    MusicKeys(token="dg-synthetic-token-123456").save(tmp_path / "vault")
    MusicKeys.load(tmp_path / "vault")
    text, found = exclusion.redact("curl https://api.discogs.com/masters/1?token=dg-synthetic-token-123456")
    assert "dg-synthetic-token-123456" not in text and found


def test_music_dir_is_private(tmp_path):
    v = tmp_path / "vault"
    assert music_dir(v) in private_folders(v)

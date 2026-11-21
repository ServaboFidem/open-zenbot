"""Sonos settings: sonos.json beside the vault, never in it; a damaged file falls back to defaults."""

import pytest

from src.sonos.settings import SonosSettings, check_volume
from src.vault_settings import settings_dir


def test_defaults_when_missing(vault):
    s = SonosSettings.load(vault)
    assert (s.enabled, s.max_volume, s.known) == (True, 60, [])


def test_round_trip_outside_the_vault(vault):
    SonosSettings(enabled=False, max_volume=35, known=["192.0.2.10"]).save(vault)
    path = settings_dir(vault) / "sonos.json"
    assert path.exists() and vault not in path.parents
    s = SonosSettings.load(vault)
    assert (s.enabled, s.max_volume, s.known) == (False, 35, ["192.0.2.10"])


def test_damaged_file_falls_back(vault):
    p = settings_dir(vault) / "sonos.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert SonosSettings.load(vault).max_volume == 60
    p.write_text('{"enabled": "yes", "max_volume": 400, "known": "x"}', encoding="utf-8")
    s = SonosSettings.load(vault)
    assert (s.enabled, s.max_volume, s.known) == (True, 60, [])
    p.write_text('{"known": ["192.0.2.1", 5, "not an ip"], "max_volume": true}', encoding="utf-8")
    s = SonosSettings.load(vault)
    assert (s.known, s.max_volume) == (["192.0.2.1"], 60)


@pytest.mark.parametrize("bad", [-1, 101, 3.5, "60", None, True])
def test_check_volume_refuses(bad):
    with pytest.raises(ValueError, match="whole number from 0 to 100"):
        check_volume(bad)


@pytest.mark.parametrize("good", [0, 60, 100])
def test_check_volume_takes(good):
    assert check_volume(good) == good


def test_public_has_no_known():
    assert SonosSettings(known=["192.0.2.10"]).public() == {"enabled": True, "max_volume": 60}

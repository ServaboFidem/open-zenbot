"""Wi-Fi speakers settings: wifi_speakers.json beside the vault, read tolerantly, and the speaker record."""

from __future__ import annotations

from src.speakers.settings import Speaker, SpeakerSettings, clean
from src.vault_settings import settings_dir


def test_defaults_are_all_off_with_no_default(tmp_path):
    s = SpeakerSettings.load(tmp_path / "vault")
    assert s.switches() == {"airplay": False, "chromecast": False, "dlna": False} and s.default is None and s.known == {}


def test_round_trip_keeps_switches_default_and_known(tmp_path):
    v = tmp_path / "vault"
    sp = Speaker("airplay:AA", "airplay", "Kitchen", "HomePod mini", "192.168.1.20")
    SpeakerSettings(airplay=True, default="airplay:AA", known={"airplay": [sp]}).save(v)
    s = SpeakerSettings.load(v)
    assert s.airplay and s.default == "airplay:AA" and s.known["airplay"] == [sp]
    assert (settings_dir(v) / "wifi_speakers.json").exists() and not (v / "wifi_speakers.json").exists()


def test_a_broken_or_hostile_file_gives_defaults_and_drops_bad_entries(tmp_path):
    v = tmp_path / "vault"
    d = settings_dir(v)
    d.mkdir(parents=True)
    (d / "wifi_speakers.json").write_text('{"airplay": "yes", "default": 5, "known": {"airplay": [{"key": 1}], "zune": []}}', encoding="utf-8")
    s = SpeakerSettings.load(v)
    assert s.airplay is False and s.default is None
    assert s.known.get("airplay", []) == [] and "zune" not in s.known
    (d / "wifi_speakers.json").write_text("not json", encoding="utf-8")
    assert SpeakerSettings.load(v).switches() == {"airplay": False, "chromecast": False, "dlna": False}


def test_known_is_capped_and_entries_are_cleaned_on_load(tmp_path):
    v = tmp_path / "vault"
    many = [Speaker(f"dlna:{i}", "dlna", f"S{i}", "", "10.0.0.1") for i in range(100)]
    SpeakerSettings(known={"dlna": many}).save(v)
    assert len(SpeakerSettings.load(v).known["dlna"]) == 64
    assert Speaker.from_dict({"key": "airplay:A", "protocol": "airplay", "name": "Kit\x00chen", "model": "", "address": "1.2.3.4"}).name == "Kitchen"
    assert Speaker.from_dict({"key": "zune:A", "protocol": "zune", "name": "x", "model": "", "address": ""}) is None


def test_clean_strips_control_characters_and_caps():
    assert clean("Kit\x00chen  ") == "Kitchen"
    assert len(clean("x" * 10_000)) == 120 and clean(None) == ""

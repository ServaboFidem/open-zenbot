import json
import stat

import pytest

from src.events.discover_settings import (
    BAD_URL, FINDLOCAL_CITIES, DiscoverKeys, DiscoverSettings, check_key, parse_place,
)
from src.vault_settings import settings_dir


@pytest.mark.parametrize("url, place", [
    ("https://www.eventbrite.com/d/oh--columbus/events--this-month/?page=1", "oh--columbus"),
    ("https://eventbrite.com/d/ny--new-york/events/", "ny--new-york"),
    ("https://www.eventbrite.com/d/OH--Columbus", "oh--columbus"),          # case folded, no trailing slash
    ("  https://www.eventbrite.com/d/ca--san-jose/music--events/  ", "ca--san-jose"),
])
def test_parse_place(url, place):
    assert parse_place(url) == place


@pytest.mark.parametrize("url", [
    "", "columbus", "http://www.eventbrite.com/d/oh--columbus/events/", "https://www.eventbrite.co.uk/d/oh--columbus/",
    "https://evil.example/d/oh--columbus/", "https://www.eventbrite.com/d/online/events/",
    "https://www.eventbrite.com/e/some-event-123", "https://www.eventbrite.com/d/oh--/events/",
    "https://www.eventbrite.com/d/oh--col umbus/", "https://www.eventbrite.com.evil.example/d/oh--columbus/",
    "https://user@www.eventbrite.com/d/oh--columbus/",
    "https://www.eventbrite.com:8443/d/oh--columbus/", "https://www.eventbrite.com/d/oh--columbus%2f..",
    "https://www.eventbrite.com/d/oh--columbus--x/../../e/1", "https://www.eventbrite.com\\@evil.example/d/oh--columbus/",
])
def test_parse_place_refuses(url):
    with pytest.raises(ValueError) as e:
        parse_place(url)
    assert str(e.value) == BAD_URL


def test_settings_round_trip_and_bounds(tmp_path):
    s = DiscoverSettings.validated({"enabled": True, "city": "Columbus", "state": "OH",
        "source_url": "https://www.eventbrite.com/d/oh--columbus/events/", "categories": ["EventbriteCategory/103"],
        "interests": "live jazz, woodworking"})
    assert (s.place, s.country) == ("oh--columbus", "US")
    s.save(tmp_path / "vault"); assert DiscoverSettings.load(tmp_path / "vault") == s
    for bad in ({"categories": ["EventbriteCategory/999999"]}, {"interests": "x" * 2001}, {"city": "x" * 101},
                {"country": "CA"}, {"enabled": True, "source_url": ""}):
        with pytest.raises(ValueError): DiscoverSettings.validated({**s.public(), **bad})
    assert DiscoverSettings.load(tmp_path / "nowhere") == DiscoverSettings()   # a missing or broken file: defaults


def test_broken_file_gives_defaults(tmp_path):
    from src.vault_settings import settings_dir
    p = settings_dir(tmp_path / "vault") / "events.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    assert DiscoverSettings.load(tmp_path / "vault") == DiscoverSettings()


BASE = {"enabled": True, "source_url": "https://www.eventbrite.com/d/oh--columbus/events/"}


def test_findlocal_cities_are_the_134_listed():
    assert len(FINDLOCAL_CITIES) == 134 and len(set(FINDLOCAL_CITIES)) == 134
    assert "columbus" in FINDLOCAL_CITIES and "columbia-mo" in FINDLOCAL_CITIES


def test_a_city_takes_the_zone_from_the_body():
    s = DiscoverSettings.validated({**BASE, "findlocal_city": "columbus", "timezone": "America/New_York"})
    assert (s.findlocal_city, s.timezone) == ("columbus", "America/New_York")


def test_an_unknown_city_is_refused():
    with pytest.raises(ValueError, match="Pick a FindLocal city from the list."):
        DiscoverSettings.validated({**BASE, "findlocal_city": "atlantis", "timezone": "America/New_York"})


def test_a_city_without_a_usable_zone_is_refused():
    for zone in ("", "America/New York", None):
        with pytest.raises(ValueError, match="The time zone is missing. Pick the FindLocal city again."):
            DiscoverSettings.validated({**BASE, "findlocal_city": "columbus", "timezone": zone})


def test_the_saved_zone_is_kept_while_the_city_stays():
    saved = DiscoverSettings(findlocal_city="columbus", timezone="America/New_York")
    s = DiscoverSettings.validated({**BASE, "findlocal_city": "columbus", "timezone": "Europe/Paris"}, saved)
    assert s.timezone == "America/New_York"


def test_a_new_city_takes_the_new_zone():
    saved = DiscoverSettings(findlocal_city="columbus", timezone="America/New_York")
    s = DiscoverSettings.validated({**BASE, "findlocal_city": "chicago", "timezone": "America/Chicago"}, saved)
    assert s.timezone == "America/Chicago"


def test_no_city_means_no_zone():
    assert DiscoverSettings.validated({**BASE, "timezone": "America/Chicago"}).timezone == ""


def test_settings_round_trip_with_findlocal(tmp_path):
    DiscoverSettings(findlocal_city="columbus", timezone="America/New_York").save(tmp_path / "vault")
    loaded = DiscoverSettings.load(tmp_path / "vault")
    assert (loaded.findlocal_city, loaded.timezone) == ("columbus", "America/New_York")


def test_a_stored_city_not_in_the_list_loads_empty(tmp_path):
    path = settings_dir(tmp_path / "vault") / "events.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"findlocal_city": "atlantis", "timezone": "America/New_York"}), encoding="utf-8")
    loaded = DiscoverSettings.load(tmp_path / "vault")
    assert (loaded.findlocal_city, loaded.timezone) == ("", "")


def test_check_key():
    assert check_key("  fl_live_abc123  ") == "fl_live_abc123"
    for bad in ("", "has space", "x" * 201, "tab\there", "caf\u00e9", 5, None):
        with pytest.raises(ValueError, match="A FindLocal key is plain text with no spaces, at most 200 characters."):
            check_key(bad)


def test_keys_file_is_owner_only_and_beside_the_vault(tmp_path):
    DiscoverKeys(findlocal="fl_live_abcdefgh").save(tmp_path / "vault")
    path = settings_dir(tmp_path / "vault") / "events_keys.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert DiscoverKeys.load(tmp_path / "vault").findlocal == "fl_live_abcdefgh"


def test_keys_public_never_holds_the_key():
    assert DiscoverKeys(findlocal="fl_live_abcdefgh").public() == {"key_set": True, "key_hint": "efgh"}
    assert DiscoverKeys().public() == {"key_set": False, "key_hint": ""}


# the final review: the key stays out of a repr, and a failed save keeps the old key in memory -------------

def test_the_key_is_not_in_the_keys_repr():
    from src.events.discover_settings import DiscoverKeys

    assert "KEY-abcdefgh" not in repr(DiscoverKeys(findlocal="KEY-abcdefgh"))

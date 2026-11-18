import pytest

from src.events.discover_settings import BAD_URL, DiscoverSettings, parse_place


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

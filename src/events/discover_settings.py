"""Event discovery settings and the check of a pasted Eventbrite city-page URL.

    <vault>-settings/events.json   DiscoverSettings (no secrets)

`parse_place` is a security boundary: only the place slug it returns ever leaves this module, and later fetches
are built from it alone (https://www.eventbrite.com/d/<place>/...)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.harness.mcp_keys import hint
from src.memory.exclusion import exclusion
from src.vault_settings import settings_dir

SETTINGS_FILE = "events.json"
KEYS_FILE = "events_keys.json"
BAD_URL = "Paste the address of your city's Eventbrite page, like https://www.eventbrite.com/d/oh--columbus/events/"

HOSTS = ("www.eventbrite.com", "eventbrite.com")
PLACE = re.compile(r"[a-z]{2}--[a-z0-9-]{1,80}")
ZONE = re.compile(r"[A-Za-z0-9_+\-/]{1,64}")
MAX_NAME = 100
MAX_INTERESTS = 2000

# The cities FindLocal covers (its own slugs).
FINDLOCAL_CITIES: tuple[str, ...] = (
    "albuquerque", "ann-arbor", "asheville", "atlanta", "austin", "baltimore", "bangor", "birmingham",
    "bloomington", "boise", "boston", "brattleboro", "buffalo", "burlington", "cape-cod", "charleston", "charlotte",
    "chicago", "cincinnati", "cleveland", "columbia-mo", "columbus", "dallas", "denver", "des-moines", "detroit",
    "duluth", "fort-collins", "grand-rapids", "green-bay", "hanover", "harrisburg", "hartford", "hilton-head",
    "houston", "hudson-valley", "indianapolis", "iowa-city", "jersey-shore", "kansas-city", "lansing", "las-vegas",
    "lincoln", "london", "los-angeles", "louisville", "madison", "manchester", "memphis", "miami", "milwaukee",
    "minneapolis", "nashville", "new-haven", "new-orleans", "new-york", "northampton", "oklahoma-city", "orlando",
    "philadelphia", "phoenix", "pittsburgh", "pittsfield", "portland", "portland-me", "portsmouth", "providence",
    "raleigh", "richmond", "rochester", "rockland", "rutland", "sacramento", "salt-lake-city", "san-antonio",
    "san-diego", "san-francisco", "santa-barbara", "seattle", "sonoma", "spokane", "st-louis", "stamford", "tampa",
    "traverse-city", "tucson", "washington", "wenatchee", "wilmington-nc", "worcester", "berlin", "munich",
    "augsburg", "stuttgart", "nuremberg", "regensburg", "kempten", "frankfurt", "mannheim", "cologne", "ruhr",
    "hamburg", "hannover", "leipzig", "jena", "ulm", "freiburg", "konstanz", "rostock", "paris", "lyon",
    "marseille", "bordeaux", "montpellier", "nantes", "lille", "grenoble", "agen", "brussels", "amsterdam",
    "rotterdam", "lisbon", "porto", "barcelona", "madrid", "valencia", "mallorca", "vienna", "bern", "zurich",
    "warsaw", "brighton", "karlsruhe", "bristol",
)

# Eventbrite category tags seen on real pages (a later task completes the list from a real page).
CATEGORIES: dict[str, str] = {
    "EventbriteCategory/101": "Business & Professional",
    "EventbriteCategory/103": "Music",
    "EventbriteCategory/105": "Performing & Visual Arts",
    "EventbriteCategory/107": "Health & Wellness",
    "EventbriteCategory/108": "Sports & Fitness",
    "EventbriteCategory/110": "Food & Drink",
    "EventbriteCategory/111": "Charity & Causes",
    "EventbriteCategory/113": "Community & Culture",
    "EventbriteCategory/114": "Religion & Spirituality",
    "EventbriteCategory/116": "Seasonal & Holiday",
    "EventbriteCategory/117": "Home & Lifestyle",
    "EventbriteCategory/119": "Hobbies & Special Interest",
}


def parse_place(url: str) -> str:
    """The place slug of a pasted Eventbrite city page, or ValueError(BAD_URL)."""
    if not isinstance(url, str):
        raise ValueError(BAD_URL)
    text = url.strip()
    if not text or "\\" in text or any(c.isspace() or ord(c) < 32 for c in text):
        raise ValueError(BAD_URL)
    try:
        parts = urlsplit(text)
    except ValueError:
        raise ValueError(BAD_URL) from None
    # the netloc must be exactly a host: no user info, no port
    if parts.scheme != "https" or parts.netloc.lower() not in HOSTS:
        raise ValueError(BAD_URL)
    segments = parts.path.split("/")  # "", "d", "<place>", ...
    if len(segments) < 3 or segments[0] != "" or segments[1] != "d":
        raise ValueError(BAD_URL)
    if any(seg in (".", "..") for seg in segments):
        raise ValueError(BAD_URL)
    place = segments[2].lower()
    if not PLACE.fullmatch(place):
        raise ValueError(BAD_URL)
    return place


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


@dataclass
class DiscoverSettings:
    enabled: bool = False
    city: str = ""
    state: str = ""
    country: str = "US"
    source_url: str = ""
    place: str = ""
    categories: list[str] = field(default_factory=list)
    interests: str = ""
    findlocal_city: str = ""
    timezone: str = ""

    @classmethod
    def load(cls, vault_root: Path) -> "DiscoverSettings":
        raw = _read(settings_dir(vault_root) / SETTINGS_FILE)
        cats = raw.get("categories")
        place = _text(raw, "place")
        fl_city, zone = _text(raw, "findlocal_city"), _text(raw, "timezone")
        if fl_city not in FINDLOCAL_CITIES or not ZONE.fullmatch(zone):
            fl_city, zone = "", ""
        return cls(
            enabled=raw["enabled"] if isinstance(raw.get("enabled"), bool) else False,
            city=_text(raw, "city"), state=_text(raw, "state"),
            country=_text(raw, "country") or "US",
            source_url=_text(raw, "source_url"),
            place=place if PLACE.fullmatch(place) else "",
            categories=[c for c in cats if isinstance(c, str) and c in CATEGORIES] if isinstance(cats, list) else [],
            interests=_text(raw, "interests")[:MAX_INTERESTS],
            findlocal_city=fl_city, timezone=zone,
        )

    @classmethod
    def validated(cls, raw: dict[str, Any], saved: "DiscoverSettings | None" = None) -> "DiscoverSettings":
        """Settings from a request body, or ValueError with a sentence. The FindLocal city's time zone is the
        body's (the user may correct it: the page's first guess is the computer's own zone, which is wrong for a
        city elsewhere); `saved` gives the zone only when the body sends none for a city that has not changed."""
        if not isinstance(raw, dict):
            raise ValueError("The settings must be an object.")
        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("Discovery must be on or off.")
        for key in ("city", "state", "source_url", "country", "interests", "findlocal_city", "timezone"):
            if raw.get(key) is not None and not isinstance(raw.get(key), str):
                raise ValueError(f"The {key.replace('_', ' ')} must be text.")
        city, state = _text(raw, "city"), _text(raw, "state")
        if len(city) > MAX_NAME:
            raise ValueError(f"The city can be at most {MAX_NAME} characters.")
        if len(state) > MAX_NAME:
            raise ValueError(f"The state can be at most {MAX_NAME} characters.")
        country = _text(raw, "country") or "US"
        if country != "US":
            raise ValueError("Only the United States (US) is supported for now.")
        interests = _text(raw, "interests")
        if len(interests) > MAX_INTERESTS:
            raise ValueError(f"Your interests can be at most {MAX_INTERESTS:,} characters.")
        cats = raw.get("categories", [])
        if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
            raise ValueError("The categories must be a list.")
        if any(c not in CATEGORIES for c in cats):
            raise ValueError("One of the categories is not one Eventbrite lists.")
        fl_city, zone = _text(raw, "findlocal_city"), ""
        if fl_city:
            if fl_city not in FINDLOCAL_CITIES:
                raise ValueError("Pick a FindLocal city from the list.")
            zone = _text(raw, "timezone")
            if not zone and saved is not None and saved.findlocal_city == fl_city:
                zone = saved.timezone
            if not ZONE.fullmatch(zone):
                raise ValueError("The time zone is missing. Pick the FindLocal city again.")
        source_url = _text(raw, "source_url")
        if source_url:
            place = parse_place(source_url)
        elif enabled:
            raise ValueError(BAD_URL)
        else:
            place = ""
        return cls(enabled=enabled, city=city, state=state, country=country, source_url=source_url,
                   place=place, categories=list(dict.fromkeys(cats)), interests=interests,
                   findlocal_city=fl_city, timezone=zone)

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.public(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def public(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "city": self.city, "state": self.state, "country": self.country,
                "source_url": self.source_url, "place": self.place, "categories": list(self.categories),
                "interests": self.interests, "findlocal_city": self.findlocal_city, "timezone": self.timezone}


BAD_KEY = "A FindLocal key is plain text with no spaces, at most 200 characters."


def check_key(value: object) -> str:
    """The key trimmed, or ValueError(BAD_KEY)."""
    if not isinstance(value, str):
        raise ValueError(BAD_KEY)
    key = value.strip()
    if not key or len(key) > 200 or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
        raise ValueError(BAD_KEY)
    return key


@dataclass
class DiscoverKeys:
    """The FindLocal key, in <vault>-settings/events_keys.json (owner only)."""

    findlocal: str = field(default="", repr=False)  # not in a repr: telemetry sends frame locals

    def __post_init__(self) -> None:
        exclusion.register_secret_value(self.findlocal)  # the ledger masks it if it reaches a tool output

    @classmethod
    def load(cls, vault_root: Path) -> "DiscoverKeys":
        return cls(findlocal=_text(_read(settings_dir(vault_root) / KEYS_FILE), "findlocal"))

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / KEYS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner only: it holds a secret
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"findlocal": self.findlocal}, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def public(self) -> dict[str, Any]:
        """Safe for the GUI: whether a key is set and its last four characters, never the key."""
        return {"key_set": bool(self.findlocal), "key_hint": hint(self.findlocal)}

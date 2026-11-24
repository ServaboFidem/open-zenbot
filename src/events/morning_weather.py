"""Open-Meteo forecast and city search for the morning digest."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.events.eventbrite import _body
from src.vault_settings import settings_dir

GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
CONDITIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Light freezing drizzle",
    57: "Freezing drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Light freezing rain",
    67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light rain showers", 81: "Rain showers", 82: "Heavy rain showers", 85: "Snow showers",
    86: "Heavy snow showers", 95: "Thunderstorms", 96: "Thunderstorms with hail", 99: "Thunderstorms with hail",
}


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=220)
    latitude: float = Field(strict=True, ge=-90, le=90)
    longitude: float = Field(strict=True, ge=-180, le=180)

    @field_validator("name")
    @classmethod
    def single_line(cls, value: str) -> str:
        if not value.isprintable():
            raise ValueError("Choose a city name on one line.")
        return value


class WeatherSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: Location | None
    unit: Literal["F", "C"]


class WeatherUnavailable(Exception):
    pass


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    try:
        async with asyncio.timeout(6), client.stream("GET", url, params=params, follow_redirects=False,
                                                    timeout=5.0) as response:
            response.raise_for_status()
            raw = await _body(response, 64_000)
            data = json.loads(raw) if raw else None
    except (httpx.HTTPError, TimeoutError, ValueError):
        raise WeatherUnavailable from None
    if not isinstance(data, dict) or data.get("error"):
        raise WeatherUnavailable
    return data


def _summary(data: dict, day: str, unit: str) -> dict:
    daily = data.get("daily")
    if not isinstance(daily, dict) or daily.get("time") != [day]:
        raise WeatherUnavailable

    def value(key: str) -> float:
        values = daily.get(key)
        if not isinstance(values, list) or len(values) != 1:
            raise WeatherUnavailable
        number = values[0]
        if type(number) not in (int, float) or not -1000 <= number <= 1000 or not math.isfinite(number):
            raise WeatherUnavailable
        return number

    high = value("temperature_2m_max")
    low = value("temperature_2m_min")
    chance = value("precipitation_probability_max")
    condition = CONDITIONS.get(value("weather_code"))
    if not condition or not 0 <= chance <= 100 or low > high:
        raise WeatherUnavailable
    summary = f"{condition} · High {high:.0f}°{unit}, low {low:.0f}°{unit} · {chance:.0f}% chance of precipitation"
    return {"summary": summary, "condition": condition.lower(), "high": round(high), "low": round(low),
            "precipitation": round(chance)}


class MorningWeather:
    def __init__(self, vault: Path, http: Callable[[], httpx.AsyncClient]):
        self.path = settings_dir(vault) / "morning_weather.json"
        self.http = http
        try:
            self.settings = WeatherSettings.model_validate_json(self.path.read_bytes())
        except (OSError, ValidationError):
            self.settings = WeatherSettings(location=None, unit="F")
        self._lock = asyncio.Lock()
        self._cache_key: tuple | None = None
        self._cached: dict = {}
        self._expires = 0.0

    def save(self, settings: WeatherSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(settings.model_dump_json(), encoding="utf-8")
        tmp.replace(self.path)
        self.settings = settings
        self._cache_key = None

    async def locations(self, query: str) -> list[dict]:
        data = await _get(self.http(), GEOCODING, {"name": query, "count": 6, "language": "en"})
        rows = data.get("results", [])
        if not isinstance(rows, list):
            raise WeatherUnavailable
        locations = []
        for row in rows[:6]:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                continue
            parts = [row.get(key) for key in ("name", "admin1", "country_code")]
            name = ", ".join(dict.fromkeys(v for v in parts if isinstance(v, str) and v))
            try:
                city = Location(name=name, latitude=row.get("latitude"), longitude=row.get("longitude"))
            except ValidationError:
                continue
            locations.append(city.model_dump())
        return locations

    async def forecast(self, day: date) -> dict:
        async with self._lock:
            location, unit = self.settings.location, self.settings.unit
            stamp = day.isoformat()
            result = {"status": "unconfigured", "location": "", "date": stamp, "summary": ""}
            if location is None:
                return result
            key = (stamp, location.latitude, location.longitude, location.name, unit)
            if self._cache_key == key and time.monotonic() < self._expires:
                return self._cached
            result["location"] = location.name
            try:
                data = await _get(self.http(), FORECAST, {
                    "latitude": location.latitude, "longitude": location.longitude, "timezone": "auto",
                    "start_date": stamp, "end_date": stamp, "daily": DAILY,
                    "temperature_unit": "fahrenheit" if unit == "F" else "celsius",
                })
                parts = _summary(data, stamp, unit)
                result.update(status="ready", unit=unit, **parts)
            except WeatherUnavailable:
                result["status"] = "unavailable"
            self._cache_key, self._cached = key, result
            self._expires = time.monotonic() + (3600 if result["status"] == "ready" else 120)
            return result

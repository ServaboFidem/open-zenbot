import asyncio
from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from src.events.morning_weather import MorningWeather, WeatherSettings


DAY = date(2026, 11, 23)
CITY = {"name": "Columbus, Ohio, US", "latitude": 39.96, "longitude": -83.0}


async def test_forecast_is_opt_in_and_cache_is_keyed_by_day_location_and_unit(vault):
    requests = []

    def provider(req):
        requests.append(req)
        stamp = req.url.params["start_date"]
        return httpx.Response(200, json={"daily": {"time": [stamp], "weather_code": [61],
            "temperature_2m_max": [47.8], "temperature_2m_min": [36.1], "precipitation_probability_max": [80]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        weather = MorningWeather(vault, lambda: client)
        assert (await weather.forecast(DAY))["status"] == "unconfigured"
        assert not requests
        weather.save(WeatherSettings(location=CITY, unit="F"))
        a, b = await asyncio.gather(weather.forecast(DAY), weather.forecast(DAY))
        assert a == b and a["status"] == "ready"
        assert all(word in a["summary"] for word in ("rain", "48°F", "36°F", "80%"))
        assert len(requests) == 1
        assert requests[0].url.host == "api.open-meteo.com"
        assert requests[0].url.params["temperature_unit"] == "fahrenheit"
        assert MorningWeather(vault, lambda: client).settings.location.name == CITY["name"]
        await weather.forecast(date(2026, 11, 24))
        weather.save(WeatherSettings(location=CITY, unit="C"))
        await weather.forecast(DAY)
        assert len(requests) == 3


@pytest.mark.parametrize("payload", [{"daily": {}}, {"daily": {"time": ["1990-01-01"]}}, [], {"error": True}])
async def test_bad_provider_data_leaves_forecast_unavailable(vault, payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))) as client:
        weather = MorningWeather(vault, lambda: client)
        weather.save(WeatherSettings(location=CITY, unit="F"))
        assert (await weather.forecast(DAY))["status"] == "unavailable"


async def test_city_search_returns_unambiguous_coordinates_and_drops_invalid_results(vault):
    def provider(req):
        assert req.url.host == "geocoding-api.open-meteo.com"
        assert req.url.params["name"] == "Columbus"
        return httpx.Response(200, json={"results": [{"name": "Columbus", "admin1": "Ohio",
            "country_code": "US", "latitude": 39.96, "longitude": -83}, {"name": "bad", "latitude": 170}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        weather = MorningWeather(vault, lambda: client)
        assert await weather.locations("Columbus") == [CITY]
    with pytest.raises(ValidationError):
        WeatherSettings(location={**CITY, "latitude": 90.1}, unit="F")
    with pytest.raises(ValidationError):
        WeatherSettings(location=CITY, unit="F", recipient="redirect@example.com")

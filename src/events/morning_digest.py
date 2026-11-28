"""Today's card and the morning email share an agenda. Only the scheduled edition selects discoveries."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query

from src.events.morning_agenda import agenda, picks
from src.events.morning_weather import MorningWeather, WeatherSettings, WeatherUnavailable
from src.mail.morning_delivery import MorningDelivery

log = logging.getLogger(__name__)

SYNC_WAIT_S = 30  # how long the email waits for Google Calendar; the sync itself goes on after it
SYNC_STALE = "Google Calendar could not be synced, so appointments added elsewhere may be missing."


def _line(value: str) -> str:
    return " ".join(value.split())


def email_text(today: dict, forecast: dict, recommendations: list[dict]) -> tuple[str, str]:
    day = date.fromisoformat(today["date"])
    heading = f"{day:%A, %B} {day.day}, {day.year}"
    lines = [heading, ""]
    if forecast["status"] == "ready":
        lines.append(f"{forecast['location']} · {forecast['summary']} (Open-Meteo, https://open-meteo.com/)")
    elif forecast["status"] == "unconfigured":
        lines.append("Choose a weather city in Settings > Notifications > Morning digest to include a forecast.")
    else:
        lines.append("Weather forecast unavailable.")
    lines.extend(["", "Birthdays"])
    if today["birthdays"]:
        lines.append(", ".join(_line(person) for person in today["birthdays"]))
    elif "Birthdays unavailable." not in today["warnings"]:
        lines.append("No known birthdays today.")
    lines.extend(["", "On your calendar"])
    for event in today["events"]:
        lines.append(f"{event['time']} · {_line(event['title'])}")
    if not today["events"] and "Calendar unavailable." not in today["warnings"]:
        lines.append("Nothing on your calendar today.")
    lines.extend(today["warnings"])
    if recommendations:
        lines.extend(["", "Events you might enjoy today"])
        for pick in recommendations:
            title = _line(pick["title"])
            lines.append(f"{pick['time']} · {title}" if pick["time"] else title)
            lines.extend([_line(pick["why"]), pick["url"], ""])
    return f"ZenBot morning digest · {heading}", "\n".join(lines).rstrip()


async def synced_calendar(calendar) -> str | None:
    """With Google connected, run a sync (or join the one in flight, such as the first after start) so what is read
    holds appointments made elsewhere. Answers a warning when it failed or took too long."""
    if calendar.conn.state() != "connected":
        return None
    try:
        status = await asyncio.wait_for(calendar.sync(), SYNC_WAIT_S)
    except Exception as exc:
        # Class name only: a traceback through the Google client holds its secret and tokens.
        log.warning("Morning calendar sync unavailable (%s)", type(exc).__name__)
        return SYNC_STALE
    return SYNC_STALE if (status or {}).get("sync", {}).get("error") else None


class MorningDigest:
    def __init__(self, harness):
        self.h = harness
        self.weather = MorningWeather(harness.config.vault_root, harness.calendar.http)
        self.delivery = MorningDelivery(harness.config.vault_root, harness.mail, self.edition)

    async def edition(self, now: datetime) -> tuple[str, str]:
        # The scheduler supplies the machine's wall time. Keep its local timezone rules for both ends of a DST day.
        local = now.replace(tzinfo=None)
        stale = await self._synced()
        today = agenda(self.h, local)
        if stale:
            today["warnings"].append(stale)
        forecast, recommendations = await asyncio.gather(
            self.weather.forecast(local.date()), self._recommendations(local, today))
        return email_text(today, forecast, recommendations)

    async def _synced(self) -> str | None:
        return await synced_calendar(self.h.calendar)

    async def _recommendations(self, local: datetime, today: dict) -> list[dict]:
        try:
            async with asyncio.timeout(45):
                return await picks(self.h, local, today)
        except Exception as exc:
            # A discovery or model failure must not prevent the scheduled calendar email.
            log.warning("Morning event picks unavailable (%s)", type(exc).__name__)
            today["warnings"].append("Event recommendations unavailable.")
            return []


def morning_router(digest: MorningDigest) -> APIRouter:
    router = APIRouter(prefix="/morning")

    @router.get("/agenda")
    async def today() -> dict:
        return agenda(digest.h, datetime.now())

    @router.get("/forecast")
    async def forecast() -> dict:
        return await digest.weather.forecast(date.today())

    @router.get("/settings")
    async def settings() -> dict:
        return {**digest.weather.settings.model_dump(), "suggested_city": digest.h.discover.settings.city,
                "delivery": digest.delivery.status()}

    @router.put("/settings")
    async def save(body: WeatherSettings) -> dict:
        try:
            digest.weather.save(body)
        except OSError:
            raise HTTPException(500, "Weather settings could not be saved.") from None
        return await settings()

    @router.get("/locations")
    async def locations(q: str = Query(min_length=2, max_length=150)) -> dict:
        query = q.strip()
        if len(query) < 2 or not query.isprintable():
            raise HTTPException(422, "Enter at least two characters of a city name.")
        try:
            return {"locations": await digest.weather.locations(query)}
        except WeatherUnavailable:
            raise HTTPException(502, "Weather city search is unavailable.") from None

    return router

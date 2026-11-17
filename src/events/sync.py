"""Bringing Google into the built-in calendar (pull); Task 6 adds sending to Google (push).

The built-in calendar is the master. A pending local edit (``dirty``) or a local delete (a tombstone) is never
overwritten by pull; an event deleted on Google that was edited here survives when it was made here (push makes
it again) and is lost when it was pulled from Google (counted in ``lost_edits``)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from src.events.connect import Connection
from src.events.convert import calendar_row, to_local
from src.events.google import Google, GoogleError
from src.events.store import CalendarStore
from src.memory.ledger import Ledger

LOST_EDITS = "lost_edits"


def _z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Sync:
    WINDOW_BACK = timedelta(days=31)
    WINDOW_AHEAD = timedelta(days=366)

    def __init__(self, store: CalendarStore, google: Google, conn: Connection, ledger: Ledger, *,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.store = store
        self.google = google
        self.conn = conn
        self.ledger = ledger
        self._now = now

    async def refresh_calendars(self) -> None:
        self.store.upsert_google([calendar_row(i) for i in await self.google.calendar_list()])

    # pull -------------------------------------------------------------------

    async def pull(self) -> dict:
        out = {"calendars": 0, "changed": 0, "removed": 0, "lost_edits": 0}
        if self.conn.state() != "connected":
            return out
        now = self._now()
        lo, hi = now - self.WINDOW_BACK, now + self.WINDOW_AHEAD
        window = (_z(lo), _z(hi), lo.date().isoformat(), hi.date().isoformat())
        for cal in self.store.calendars():
            if cal["source"] != "google" or not cal["selected"]:
                continue
            items, token = await self._read(cal["id"], cal["sync_token"], _z(lo), _z(hi))
            for item in items:
                self._take(cal["id"], item, window, out)
            self.store.set_sync_token(cal["id"], token or None)
            out["calendars"] += 1
        return out

    async def _read(self, calendar_id: str, token: str | None, time_min: str,
                    time_max: str) -> tuple[list[dict], str]:
        """A sync token's changes, else the window in full. A ``gone`` answer clears the calendar's pulled
        events and reads it in full once; a second ``gone`` or any other error goes to the caller."""
        try:
            if token:
                return await self.google.list_events(calendar_id, sync_token=token)
            return await self.google.list_events(calendar_id, time_min=time_min, time_max=time_max)
        except GoogleError as e:
            if e.kind != "gone":
                raise
        self.store.clear_pulled(calendar_id)
        return await self.google.list_events(calendar_id, time_min=time_min, time_max=time_max)

    def _take(self, calendar_id: str, item: dict, window: tuple[str, str, str, str], out: dict) -> None:
        google_id = item.get("id")
        if not isinstance(google_id, str) or not google_id:
            return
        row = self.store.by_google(calendar_id, google_id)
        fields = to_local(item)
        if fields is None:
            self._cancelled(row, out)
            return
        if row is not None and (row["dirty"] or row["deleted"]):
            return  # the built-in copy wins
        made_here = row is not None and row["origin"] == "local"
        if not made_here and self._outside(fields, window):
            if row is not None:
                self.store.purge(row["id"])
                out["removed"] += 1
            return
        # On a held made-here row apply_remote updates fields, etag, link and series id in place and leaves
        # origin, calendar_id and the Google link alone, so the event stays one made here.
        self.store.apply_remote(calendar_id, google_id, fields, etag=str(item.get("etag") or ""),
                                link=str(item.get("htmlLink") or ""),
                                series_id=str(item.get("recurringEventId") or ""))
        out["changed"] += 1

    def _cancelled(self, row: dict | None, out: dict) -> None:
        if row is None:
            return
        if row["deleted"]:
            self.store.purge(row["id"])  # deleted on both sides
        elif row["origin"] == "local":
            if row["dirty"]:
                return  # edited here: push makes it again
            self.store.purge(row["id"])
        elif row["dirty"]:
            self.store.purge(row["id"])
            out["lost_edits"] += 1
            self._count_lost_edit()
        else:
            self.store.remove_remote(row["google_calendar"], row["google_id"])
        out["removed"] += 1

    def _count_lost_edit(self) -> None:
        held = self.store.meta(LOST_EDITS)
        count = int(held) if held.isdigit() else 0
        self.store.set_meta(LOST_EDITS, str(count + 1))

    @staticmethod
    def _outside(fields: dict, window: tuple[str, str, str, str]) -> bool:
        """True when the event ends before the window opens or starts after it closes. Timed events compare
        as ``...Z`` instants, all-day events as dates (end exclusive), both as strings."""
        lo, hi, lo_day, hi_day = window
        if fields["all_day"]:
            return fields["end"] <= lo_day or fields["start"] > hi_day
        return fields["end"] < lo or fields["start"] > hi

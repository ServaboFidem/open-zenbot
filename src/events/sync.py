"""Bringing Google into the built-in calendar (pull), sending its changes back (push), and one sync run.

The built-in calendar is the master. A pending local edit (``dirty``) or a local delete (a tombstone) is never
overwritten by pull; an event deleted on Google that was edited here survives when it was made here (push makes
it again) and is lost when it was pulled from Google (counted in ``lost_edits``).

Push sends only what the user switched on: nothing unless ``can_push``. A made-here event goes to the
destination calendar under its own id (``google_event_id``), so a retried insert meets a 409 instead of making a
second copy; once pushed, an event stays on the calendar it was pushed to. A pulled event goes back to its own
calendar. The body is ``to_google`` only: no guests, no reminders, no ``If-Match`` (the local copy wins)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from src.events.connect import Connection
from src.events.convert import calendar_row, to_google, to_local
from src.events.google import Google, GoogleError
from src.events.store import CalendarStore, google_event_id
from src.memory.ledger import Kind, Ledger

LOST_EDITS = "lost_edits"
LAST_SYNC = "last_sync"
LAST_ERROR = "last_error"
INSERT_TRIES = 3
_GONE = ("missing", "gone")


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
        events that have no pending local change and reads it in full once; a second ``gone`` or any other
        error goes to the caller."""
        try:
            if token:
                return await self.google.list_events(calendar_id, sync_token=token)
            return await self.google.list_events(calendar_id, time_min=time_min, time_max=time_max)
        except GoogleError as e:
            if e.kind != "gone":
                raise
        self.store.clear_clean_pulled(calendar_id)
        return await self.google.list_events(calendar_id, time_min=time_min, time_max=time_max)

    def _take(self, calendar_id: str, item: dict, window: tuple[str, str, str, str], out: dict) -> None:
        google_id = item.get("id")
        if not isinstance(google_id, str) or not google_id:
            return
        row = self.store.by_google(calendar_id, google_id)
        if item.get("status") == "cancelled":
            self._cancelled(row, out)
            return
        fields = to_local(item)
        if fields is None:
            return  # not an event this calendar can show: change nothing
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

    # push -------------------------------------------------------------------

    def can_push(self) -> bool:
        s = self.conn.settings
        return self.conn.state() == "connected" and self.conn.can_write() and s.push and bool(s.destination)

    def _writable(self) -> set[str]:
        return {c["id"] for c in self.store.calendars() if c["writable"]}

    @staticmethod
    def _held_back(row: dict, writable: set[str]) -> bool:
        """A pulled row on a calendar this account may not write (or one no longer listed): never sent."""
        return row["origin"] == "google" and row["calendar_id"] not in writable

    def preview(self, destination: str) -> dict:
        """What push would send now. No network. ``destination`` is the calendar new events would go to."""
        out = {"add": 0, "update": 0, "remove": 0}
        writable = self._writable()
        for row in self.store.pending():
            if self._held_back(row, writable):
                continue
            if row["deleted"]:
                out["remove"] += 1
            elif row["origin"] == "local" and not row["google_id"]:
                out["add"] += 1
            else:
                out["update"] += 1
        return out

    async def push(self) -> dict:
        out = {"added": 0, "updated": 0, "removed": 0, "failed": 0}
        if not self.can_push():
            return out
        destination = self.conn.settings.destination
        writable = self._writable()
        for row in self.store.pending():
            if self._held_back(row, writable):
                # it should never be dirty or deleted; dropping the change keeps a bad row from retrying forever
                # and from staying hidden
                self.store.restore_remote(row["id"])
                continue
            try:
                counted = await self._push_one(row, destination)
            except GoogleError as e:
                if e.kind == "auth":
                    raise
                self.store.push_failed(row["id"], e.kind)
                out["failed"] += 1
                continue
            if counted:
                out[counted] += 1
        return out

    async def _push_one(self, row: dict, destination: str) -> str | None:
        """Send one pending row; answers the count it falls under (None for a pulled event whose Google copy
        is gone: dropped and counted in ``lost_edits``). Raises ``GoogleError`` on a failure."""
        if row["deleted"]:
            if row["google_id"]:
                try:
                    await self.google.delete_event(row["google_calendar"], row["google_id"])
                except GoogleError as e:
                    if e.kind != "missing":
                        raise
            self.store.purge(row["id"])
            return "removed"
        if not row["google_id"]:
            await self._insert(row["id"], destination)
            return "added"
        item: dict | None
        try:
            item = await self.google.patch_event(row["google_calendar"], row["google_id"], to_google(row))
        except GoogleError as e:
            if e.kind not in _GONE:
                raise
            item = None
        if item is not None and item.get("status") != "cancelled":
            self._pushed(row["id"], row["google_calendar"], row["google_id"], item, row["rev"])
            return "updated"
        # the Google copy is gone
        if row["origin"] == "local":
            self.store.unlink(row["id"])
            self.store.bump_generation(row["id"])
            await self._insert(row["id"], destination)
            return "added"
        self.store.purge(row["id"])
        self._count_lost_edit()
        return None

    async def _insert(self, id: str, destination: str) -> None:
        """Insert a made-here event at ``destination`` under its own id. A 409 means the id is taken: a live
        copy (an earlier insert whose answer was lost) is adopted and patched; a cancelled one takes the next
        generation. Gives up with ``conflict`` after ``INSERT_TRIES`` inserts."""
        for _ in range(INSERT_TRIES):
            row = self.store.event(id)
            gid = google_event_id(row)
            try:
                item = await self.google.insert_event(destination, {**to_google(row), "id": gid})
            except GoogleError as e:
                if e.kind != "conflict":
                    raise
                item = await self._adopt(destination, gid, row)
                if item is None:
                    self.store.bump_generation(id)
                    continue
            self._pushed(id, destination, gid, item, row["rev"])
            return
        raise GoogleError("conflict", "Google kept refusing the event's id")

    async def _adopt(self, calendar_id: str, gid: str, row: dict) -> dict | None:
        """The live copy at ``gid`` patched with the local fields, or None when that id is cancelled or gone."""
        try:
            held = await self.google.get_event(calendar_id, gid)
            if held.get("status") == "cancelled":
                return None
            item = await self.google.patch_event(calendar_id, gid, to_google(row))
        except GoogleError as e:
            if e.kind in _GONE:
                return None
            raise
        return None if item.get("status") == "cancelled" else item

    def _pushed(self, id: str, calendar_id: str, gid: str, item: dict, rev: int) -> None:
        """``rev``: the revision whose fields were sent; a newer local edit keeps the row pending."""
        self.store.mark_pushed(id, google_calendar=calendar_id, google_id=str(item.get("id") or gid),
                               etag=str(item.get("etag") or ""), link=str(item.get("htmlLink") or ""), rev=rev)

    # one run ----------------------------------------------------------------

    async def run(self) -> dict:
        out = {"pull": {"calendars": 0, "changed": 0, "removed": 0, "lost_edits": 0},
               "push": {"added": 0, "updated": 0, "removed": 0, "failed": 0}}
        if self.conn.state() != "connected":
            return out
        self.store.set_meta(LOST_EDITS, "0")
        try:
            await self.refresh_calendars()
            out["pull"] = await self.pull()
            out["push"] = await self.push()
        except GoogleError as e:
            if e.kind == "auth":
                self.conn.mark_needs_reconnect()
            self.store.set_meta(LAST_ERROR, e.kind)
            self._log(out, error=e.kind)
            raise
        self.store.set_meta(LAST_SYNC, _z(self._now()))
        self.store.set_meta(LAST_ERROR, "")
        self._log(out)
        return out

    def _log(self, out: dict, **extra) -> None:
        self.ledger.append(Kind.calendar, "", "", event="sync", pull=dict(out["pull"]), push=dict(out["push"]),
                           **extra)

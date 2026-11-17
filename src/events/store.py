"""SQLite store for the built-in calendar: calendars, events and a small meta table.

One connection shared across threads (``check_same_thread=False``) behind a lock; WAL mode.
A timed event stores ``start`` and ``end`` as UTC instants ``YYYY-MM-DDTHH:MM:SSZ`` plus ``tz``
(an IANA name); an all-day event stores ``YYYY-MM-DD`` dates, end exclusive. Both compare as
strings, which is how ``events`` finds an overlap. Rows come back as plain dicts.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

LOCAL = "local"
MAX_TITLE, MAX_LOCATION, MAX_DESCRIPTION = 300, 500, 8000

SCHEMA = """
CREATE TABLE IF NOT EXISTS calendars(
  id TEXT PRIMARY KEY, name TEXT, source TEXT, color TEXT, tz TEXT, writable INTEGER,
  selected INTEGER DEFAULT 0, visible INTEGER DEFAULT 1, sync_token TEXT);
CREATE TABLE IF NOT EXISTS events(
  id TEXT PRIMARY KEY, calendar_id TEXT, origin TEXT, title TEXT, location TEXT, description TEXT,
  all_day INTEGER, start TEXT, end TEXT, tz TEXT, google_calendar TEXT DEFAULT '',
  google_id TEXT DEFAULT '', generation INTEGER DEFAULT 0, etag TEXT DEFAULT '',
  link TEXT DEFAULT '', series_id TEXT DEFAULT '', dirty INTEGER DEFAULT 0,
  deleted INTEGER DEFAULT 0, push_error TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS events_start ON events(start);
CREATE INDEX IF NOT EXISTS events_google ON events(google_calendar, google_id);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

_CAL_BOOLS = ("writable", "selected", "visible")
_EVENT_BOOLS = ("all_day", "dirty", "deleted")
_INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class EventError(ValueError):
    """An event the calendar will not keep; the message is a sentence for the user."""


def _instant(s: str) -> datetime | None:
    if not isinstance(s, str) or not _INSTANT.fullmatch(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _day(s: str) -> date | None:
    if not isinstance(s, str) or not _DAY.fullmatch(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _text(fields: dict, key: str, limit: int, label: str) -> str:
    v = fields.get(key, "")
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise EventError(f"The {label} must be text.")
    if len(v) > limit:
        raise EventError(f"The {label} is longer than {limit:,} characters.")
    return v


def validate(fields: dict) -> dict:
    """Clean an event's fields or raise ``EventError``."""
    title = _text(fields, "title", 10**6, "title").strip()
    if not title:
        raise EventError("An event needs a title.")
    if len(title) > MAX_TITLE:
        raise EventError(f"The title is longer than {MAX_TITLE} characters.")
    location = _text(fields, "location", MAX_LOCATION, "location")
    description = _text(fields, "description", MAX_DESCRIPTION, "description")
    all_day = fields.get("all_day", False)
    if not isinstance(all_day, bool):
        raise EventError("All day must be true or false.")
    start, end = fields.get("start"), fields.get("end")
    tz = fields.get("tz") or ""
    if not isinstance(tz, str):
        raise EventError("The time zone must be text.")
    if all_day:
        a, b = _day(start), _day(end)
        if a is None or b is None:
            raise EventError("An all-day event needs start and end dates as YYYY-MM-DD.")
        if b <= a:
            raise EventError("An all-day event must last at least one day.")
        tz = ""
    else:
        a, b = _instant(start), _instant(end)
        if a is None or b is None:
            raise EventError("A timed event needs start and end times as UTC, YYYY-MM-DDTHH:MM:SSZ.")
        if b <= a:
            raise EventError("The event must end after it starts.")
        if not tz.strip():
            raise EventError("A timed event needs a time zone.")
        tz = tz.strip()
    return {"title": title, "location": location, "description": description, "all_day": all_day,
            "start": start, "end": end, "tz": tz}


def google_event_id(event: dict) -> str:
    """The id an event is pushed to Google under (lowercase hex plus ``g`` plus digits: base32hex)."""
    return event["id"].replace("-", "") + "g" + str(event["generation"])


def _row(row: sqlite3.Row | None, bools: tuple[str, ...]) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in bools:
        d[k] = bool(d[k])
    return d


class CalendarStore:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._db.execute(
                "INSERT OR IGNORE INTO calendars(id, name, source, color, tz, writable, selected, visible)"
                " VALUES(?, 'OpenZenBot', 'local', '', '', 1, 0, 1)", (LOCAL,))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def validate(self, fields: dict) -> dict:
        return validate(fields)

    # calendars --------------------------------------------------------------

    def _calendar(self, id: str) -> dict | None:
        return _row(self._db.execute("SELECT * FROM calendars WHERE id=?", (id,)).fetchone(), _CAL_BOOLS)

    def calendars(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM calendars ORDER BY source != 'local', rowid").fetchall()
        return [_row(r, _CAL_BOOLS) for r in rows]

    def upsert_google(self, rows: list[dict]) -> None:
        with self._lock, self._db:
            keep = [r["id"] for r in rows]
            for r in rows:
                self._db.execute(
                    "INSERT INTO calendars(id, name, source, color, tz, writable, selected, visible)"
                    " VALUES(?, ?, 'google', ?, ?, ?, 0, 1)"
                    " ON CONFLICT(id) DO UPDATE SET name=excluded.name, color=excluded.color,"
                    " tz=excluded.tz, writable=excluded.writable",
                    (r["id"], r["name"], r.get("color", ""), r.get("tz", ""), int(bool(r["writable"]))))
            marks = ",".join("?" * len(keep))
            gone = [x["id"] for x in self._db.execute(
                f"SELECT id FROM calendars WHERE source='google' AND id NOT IN ({marks})", keep)]
            for cid in gone:
                self._db.execute("DELETE FROM events WHERE origin='google' AND calendar_id=?", (cid,))
                self._db.execute("DELETE FROM calendars WHERE id=?", (cid,))

    def set_calendar(self, id: str, *, visible: bool | None = None,
                     selected: bool | None = None) -> dict | None:
        with self._lock, self._db:
            if self._calendar(id) is None:
                return None
            if visible is not None:
                self._db.execute("UPDATE calendars SET visible=? WHERE id=?", (int(visible), id))
            if selected is not None:
                self._db.execute("UPDATE calendars SET selected=? WHERE id=?", (int(selected), id))
            return self._calendar(id)

    def set_sync_token(self, id: str, token: str | None) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE calendars SET sync_token=? WHERE id=?", (token, id))

    def clear_pulled(self, calendar_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM events WHERE origin='google' AND calendar_id=?", (calendar_id,))
            self._db.execute("UPDATE calendars SET sync_token=NULL WHERE id=?", (calendar_id,))

    def drop_google(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM events WHERE origin='google'")
            self._db.execute("DELETE FROM calendars WHERE source='google'")

    # events -----------------------------------------------------------------

    def events(self, start: str, end: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE deleted=0 AND start < ? AND end > ? ORDER BY start, rowid",
                (end, start)).fetchall()
        return [_row(r, _EVENT_BOOLS) for r in rows]

    def event(self, id: str) -> dict | None:
        with self._lock:
            return _row(self._db.execute("SELECT * FROM events WHERE id=?", (id,)).fetchone(),
                        _EVENT_BOOLS)

    def add(self, fields: dict) -> dict:
        f = validate(fields)
        id = str(uuid.uuid4())
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO events(id, calendar_id, origin, title, location, description, all_day,"
                " start, end, tz, dirty) VALUES(?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, 1)",
                (id, LOCAL, f["title"], f["location"], f["description"], int(f["all_day"]),
                 f["start"], f["end"], f["tz"]))
        return self.event(id)

    def update(self, id: str, fields: dict) -> dict | None:
        f = validate(fields)
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE events SET title=?, location=?, description=?, all_day=?, start=?, end=?,"
                " tz=?, dirty=1 WHERE id=?",
                (f["title"], f["location"], f["description"], int(f["all_day"]), f["start"],
                 f["end"], f["tz"], id))
            if cur.rowcount == 0:
                return None
        return self.event(id)

    def delete(self, id: str) -> bool:
        with self._lock, self._db:
            e = self.event(id)
            if e is None:
                return False
            if e["google_id"]:
                self._db.execute("UPDATE events SET deleted=1, dirty=1 WHERE id=?", (id,))
            else:
                self._db.execute("DELETE FROM events WHERE id=?", (id,))
            return True

    # sync support -----------------------------------------------------------

    def by_google(self, calendar_id: str, google_id: str) -> dict | None:
        with self._lock:
            return _row(self._db.execute(
                "SELECT * FROM events WHERE google_calendar=? AND google_id=?",
                (calendar_id, google_id)).fetchone(), _EVENT_BOOLS)

    def apply_remote(self, calendar_id: str, google_id: str, fields: dict, *, etag: str,
                     link: str, series_id: str) -> None:
        """Insert a pulled event, or replace the copy already held.

        A held row that is ``dirty`` (a pending local edit) or ``deleted`` (a tombstone) is left
        untouched: the built-in calendar wins, and the pull code decides what to do with such rows
        before calling. A replaced timed event keeps its ``tz`` when the incoming one is empty.
        """
        with self._lock, self._db:
            have = self.by_google(calendar_id, google_id)
            if have is not None and (have["dirty"] or have["deleted"]):
                return
            tz = fields.get("tz", "")
            if have is not None and not tz and not fields["all_day"]:
                tz = have["tz"]
            vals = (fields["title"], fields.get("location", ""), fields.get("description", ""),
                    int(bool(fields["all_day"])), fields["start"], fields["end"], tz,
                    etag, link, series_id)
            if have is not None:
                self._db.execute(
                    "UPDATE events SET title=?, location=?, description=?, all_day=?, start=?, end=?,"
                    " tz=?, etag=?, link=?, series_id=?, dirty=0, deleted=0, push_error='' WHERE id=?",
                    (*vals, have["id"]))
            else:
                self._db.execute(
                    "INSERT INTO events(id, calendar_id, origin, title, location, description,"
                    " all_day, start, end, tz, etag, link, series_id, google_calendar, google_id,"
                    " dirty) VALUES(?, ?, 'google', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (str(uuid.uuid4()), calendar_id, *vals, calendar_id, google_id))

    def remove_remote(self, calendar_id: str, google_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM events WHERE google_calendar=? AND google_id=?",
                             (calendar_id, google_id))

    def pending(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM events WHERE dirty=1 ORDER BY rowid").fetchall()
        return [_row(r, _EVENT_BOOLS) for r in rows]

    def mark_pushed(self, id: str, *, google_calendar: str, google_id: str, etag: str,
                    link: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE events SET google_calendar=?, google_id=?, etag=?, link=?, dirty=0,"
                " push_error='' WHERE id=?", (google_calendar, google_id, etag, link, id))

    def push_failed(self, id: str, kind: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE events SET push_error=? WHERE id=?", (kind, id))

    def bump_generation(self, id: str) -> int:
        with self._lock, self._db:
            self._db.execute("UPDATE events SET generation=generation+1 WHERE id=?", (id,))
            row = self._db.execute("SELECT generation FROM events WHERE id=?", (id,)).fetchone()
        return row["generation"] if row else 0

    def purge(self, id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM events WHERE id=?", (id,))

    def pushed_calendars(self) -> set[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT google_calendar FROM events WHERE origin='local' AND google_id != ''"
            ).fetchall()
        return {r["google_calendar"] for r in rows}

    def clear_links(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM events WHERE origin='local' AND deleted=1")
            self._db.execute(
                "UPDATE events SET google_calendar='', google_id='', etag='', link='', generation=0,"
                " dirty=1, push_error='' WHERE origin='local'")

    def meta(self, key: str) -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key)"
                             " DO UPDATE SET value=excluded.value", (key, value))

    def counts(self) -> dict:
        with self._lock:
            q = lambda sql: self._db.execute(sql).fetchone()[0]  # noqa: E731
            return {"local": q("SELECT COUNT(*) FROM events WHERE origin='local' AND deleted=0"),
                    "pulled": q("SELECT COUNT(*) FROM events WHERE origin='google'"),
                    "pending": q("SELECT COUNT(*) FROM events WHERE dirty=1")}

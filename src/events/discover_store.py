"""SQLite store for event discovery: the pool of fetched events, saved digests and their cards.

A separate file (``discover.db``) beside ``calendar.db``. One connection shared across threads behind a
lock; WAL mode. Lists are stored as JSON text. A card is a snapshot of a pool row as it was when saved:
a later refresh never changes it. Rows come back as plain dicts.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path

LAST_REFRESH, LAST_ERROR = "last_refresh", "last_error"
MAX_LIMIT = 60
STATES = ("new", "dismissed", "added")

# the event fields (Task 4's event dict, minus its id), in column order
_TEXT = ("name", "summary", "start_date", "start_time", "end_date", "end_time", "timezone", "venue",
         "address", "city", "region", "image_url", "url", "format")
_LISTS = ("categories", "category_names")
_FIELDS = _TEXT + _LISTS

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS pool(
  id TEXT PRIMARY KEY, {", ".join(f"{c} TEXT DEFAULT ''" for c in _TEXT)},
  {", ".join(f"{c} TEXT DEFAULT '[]'" for c in _LISTS)}, seen_at TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS pool_start ON pool(start_date, start_time, id);
CREATE TABLE IF NOT EXISTS digests(
  id TEXT PRIMARY KEY, kind TEXT, note TEXT, session_id TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS digests_created ON digests(created_at);
CREATE INDEX IF NOT EXISTS digests_session ON digests(session_id);
CREATE TABLE IF NOT EXISTS cards(
  id TEXT PRIMARY KEY, digest_id TEXT, event_id TEXT, why TEXT, state TEXT DEFAULT 'new',
  calendar_event_id TEXT DEFAULT '', has_image INTEGER DEFAULT 0, position INTEGER DEFAULT 0,
  {", ".join(f"{c} TEXT DEFAULT ''" for c in _TEXT)},
  {", ".join(f"{c} TEXT DEFAULT '[]'" for c in _LISTS)});
CREATE INDEX IF NOT EXISTS cards_digest ON cards(digest_id, position);
CREATE INDEX IF NOT EXISTS cards_event ON cards(event_id);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def _event_values(event: dict) -> list:
    out = []
    for c in _TEXT:
        v = event.get(c)
        out.append("" if v is None else str(v))
    for c in _LISTS:
        v = event.get(c)
        out.append(json.dumps([str(x) for x in v] if isinstance(v, (list, tuple)) else []))
    return out


def _unpack(d: dict) -> dict:
    for c in _LISTS:
        try:
            v = json.loads(d.get(c) or "[]")
        except ValueError:
            v = []
        d[c] = v if isinstance(v, list) else []
    return d


def _pool(row: sqlite3.Row | None) -> dict | None:
    return None if row is None else _unpack(dict(row))


def _card(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = _unpack(dict(row))
    d["has_image"] = bool(d["has_image"])
    d.pop("position", None)
    return d


class DiscoverStore:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # the pool ---------------------------------------------------------------

    def upsert_pool(self, events: list[dict], seen_at: str) -> int:
        """Insert or update each event by id; delete nothing. Returns how many were written."""
        cols = ", ".join(("id",) + _FIELDS + ("seen_at",))
        marks = ", ".join("?" * (len(_FIELDS) + 2))
        sets = ", ".join(f"{c}=excluded.{c}" for c in _FIELDS + ("seen_at",))
        n = 0
        with self._lock, self._db:
            for e in events:
                eid = e.get("id")
                if not isinstance(eid, str) or not eid:
                    continue
                self._db.execute(f"INSERT INTO pool({cols}) VALUES({marks}) ON CONFLICT(id) DO UPDATE SET {sets}",
                                 [eid, *_event_values(e), seen_at])
                n += 1
        return n

    def drop_past(self, today: str) -> int:
        """Delete pool rows starting before ``today`` (a day key). Returns how many went."""
        with self._lock, self._db:
            return self._db.execute("DELETE FROM pool WHERE start_date < ?", (today,)).rowcount

    def pool_size(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM pool").fetchone()[0]

    def pool_row(self, id: str) -> dict | None:
        with self._lock:
            return _pool(self._db.execute("SELECT * FROM pool WHERE id=?", (id,)).fetchone())

    @staticmethod
    def _wanted(row: dict, ticked: set[str]) -> bool:
        tags = row["categories"]
        return not ticked or not tags or any(t in ticked for t in tags)

    def candidates(self, today: str, categories: list[str], *, category: str = "", text: str = "",
                   start: str = "", end: str = "", limit: int = 60, offset: int = 0) -> list[dict]:
        sql = ("SELECT * FROM pool WHERE start_date >= ?"
               " AND id NOT IN (SELECT event_id FROM cards)")
        args: list = [today]
        if start:
            sql += " AND start_date >= ?"
            args.append(start)
        if end:
            sql += " AND start_date <= ?"
            args.append(end)
        sql += " ORDER BY start_date, start_time, id"
        with self._lock:
            rows = [_pool(r) for r in self._db.execute(sql, args)]
        ticked = set(categories)
        cat, needle = category.strip().lower(), text.strip().lower()
        out = []
        for r in rows:
            if not self._wanted(r, ticked):
                continue
            if cat and not any(cat == t.lower() for t in r["categories"] + r["category_names"]):
                continue
            if needle and not any(needle in (r[f] or "").lower() for f in ("name", "summary", "venue")):
                continue
            out.append(r)
        limit = max(0, min(int(limit), MAX_LIMIT))
        offset = max(0, int(offset))
        return out[offset:offset + limit]

    def is_candidate(self, id: str, today: str, categories: list[str]) -> bool:
        row = self.pool_row(id)
        if row is None or row["start_date"] < today or not self._wanted(row, set(categories)):
            return False
        with self._lock:
            return self._db.execute("SELECT 1 FROM cards WHERE event_id=?", (id,)).fetchone() is None

    # digests and cards ------------------------------------------------------

    def save_digest(self, cards: list[tuple[dict, str]], *, kind: str, note: str, session_id: str,
                    created_at: str) -> dict:
        """Save a digest and a snapshot card for each (pool row, why), whole or not at all."""
        digest_id = uuid.uuid4().hex
        cols = ", ".join(("id", "digest_id", "event_id", "why", "position") + _FIELDS)
        marks = ", ".join("?" * (5 + len(_FIELDS)))
        with self._lock, self._db:
            self._db.execute("INSERT INTO digests(id, kind, note, session_id, created_at) VALUES(?,?,?,?,?)",
                             (digest_id, kind, note, session_id, created_at))
            for i, (row, why) in enumerate(cards):
                self._db.execute(f"INSERT INTO cards({cols}) VALUES({marks})",
                                 [uuid.uuid4().hex, digest_id, row["id"], why, i, *_event_values(row)])
        return self.digest(digest_id)  # type: ignore[return-value]

    def _cards_of(self, digest_id: str) -> list[dict]:
        rows = self._db.execute("SELECT * FROM cards WHERE digest_id=? ORDER BY position", (digest_id,))
        return [_card(r) for r in rows]  # type: ignore[misc]

    def _digest(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        d["cards"] = self._cards_of(d["id"])
        return d

    def digest(self, id: str) -> dict | None:
        with self._lock:
            return self._digest(self._db.execute("SELECT * FROM digests WHERE id=?", (id,)).fetchone())

    def digests(self, before: str | None, limit: int) -> list[dict]:
        """Newest first; ``before`` is a ``created_at`` to page past."""
        sql, args = "SELECT * FROM digests", []
        if before:
            sql += " WHERE created_at < ?"
            args.append(before)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(max(0, int(limit)))
        with self._lock:
            return [self._digest(r) for r in self._db.execute(sql, args)]  # type: ignore[misc]

    def has_digest_for(self, session_id: str) -> bool:
        if not session_id:
            return False
        with self._lock:
            return self._db.execute("SELECT 1 FROM digests WHERE session_id=?", (session_id,)).fetchone() is not None

    def card(self, id: str) -> dict | None:
        with self._lock:
            return _card(self._db.execute("SELECT * FROM cards WHERE id=?", (id,)).fetchone())

    def set_state(self, card_id: str, state: str) -> dict | None:
        if state not in STATES:
            raise ValueError(f"Unknown card state: {state}")
        with self._lock, self._db:
            self._db.execute("UPDATE cards SET state=? WHERE id=?", (state, card_id))
        return self.card(card_id)

    def mark_added(self, card_id: str, calendar_event_id: str, *, replace: bool = False) -> dict | None:
        """Mark a card added. The first calendar event id stays if it is called again, unless ``replace`` (the
        event it named was deleted and a new one was made)."""
        keep = "?" if replace else "CASE WHEN calendar_event_id='' THEN ? ELSE calendar_event_id END"
        with self._lock, self._db:
            self._db.execute(f"UPDATE cards SET state='added', calendar_event_id={keep} WHERE id=?",
                             (calendar_event_id, card_id))
        return self.card(card_id)

    def set_image(self, card_id: str, has: bool) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE cards SET has_image=? WHERE id=?", (1 if has else 0, card_id))

    # meta -------------------------------------------------------------------

    def meta(self, key: str) -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key)"
                             " DO UPDATE SET value=excluded.value", (key, value))

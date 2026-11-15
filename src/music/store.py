"""SQLite store for the music feature: collection, scan state, taste profile, digests, cards.

One connection shared across threads (``check_same_thread=False``) behind a lock; WAL mode.
Digest ids: a weekly digest is its ISO week (``2026-W46``, from ``week_of``), with ``-2``, ``-3``
... appended when that week already has one; an on-demand digest gets a uuid hex. Card ids are
uuid hex. ``created_at`` is a UTC ISO timestamp with microseconds, used for ``before=`` paging.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

REACTIONS = ("wanted", "passed", "owned", "listened")

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases(
  release_id INTEGER PRIMARY KEY, master_id INTEGER, title TEXT, year INTEGER,
  artists_json TEXT, formats_json TEXT, styles_json TEXT, genres_json TEXT,
  rating INTEGER, date_added TEXT, mapped_json TEXT);
CREATE TABLE IF NOT EXISTS scan(
  id INTEGER PRIMARY KEY CHECK (id = 1), state TEXT, stage TEXT, done INTEGER, total INTEGER,
  mapped INTEGER, unmapped_json TEXT, started_at TEXT, finished_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS profile(id INTEGER PRIMARY KEY CHECK (id = 1), json TEXT, built_at TEXT);
CREATE TABLE IF NOT EXISTS artist_links(discogs_id INTEGER PRIMARY KEY, mbid TEXT, checked_at TEXT);
CREATE TABLE IF NOT EXISTS digests(
  id TEXT PRIMARY KEY, kind TEXT, week_of TEXT, created_at TEXT, note TEXT, read_at TEXT,
  session_id TEXT);
CREATE INDEX IF NOT EXISTS digests_created ON digests(created_at);
CREATE TABLE IF NOT EXISTS cards(
  id TEXT PRIMARY KEY, digest_id TEXT, master_id INTEGER, release_id INTEGER, json TEXT,
  reaction TEXT, reaction_at TEXT, on_wantlist INTEGER DEFAULT 0, wanted_by_card INTEGER DEFAULT 0,
  position INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS cards_digest ON cards(digest_id);
CREATE TABLE IF NOT EXISTS owned_marks(
  master_id INTEGER PRIMARY KEY, artist TEXT, title TEXT, genres_json TEXT, marked_at TEXT);
"""

SCAN_FIELDS = ("state", "stage", "done", "total", "mapped", "unmapped_json", "started_at",
               "finished_at", "error")
# Release dict key -> column holding it as JSON.
_JSON_KEYS = {"artists": "artists_json", "formats": "formats_json", "styles": "styles_json",
              "genres": "genres_json", "mapped": "mapped_json"}


def _master(v):
    """Discogs reports master_id 0 for a release with no master; store that as None."""
    return v or None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class MusicStore:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # releases ---------------------------------------------------------------

    def replace_releases(self, rows: list[dict]) -> None:
        """Swap the whole collection in one transaction; any failure keeps the old rows."""
        with self._lock:
            try:
                self._db.execute("BEGIN")
                self._db.execute("DELETE FROM releases")
                for r in rows:
                    self._db.execute(
                        "INSERT INTO releases VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (r["release_id"], _master(r.get("master_id")), r.get("title"), r.get("year"),
                         json.dumps(r.get("artists", [])), json.dumps(r.get("formats", [])),
                         json.dumps(r.get("styles", [])), json.dumps(r.get("genres", [])),
                         r.get("rating"), r.get("date_added"), json.dumps(r.get("mapped", []))))
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def releases(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM releases ORDER BY release_id").fetchall()
        out = []
        for row in rows:
            d = dict(row)
            for key, col in _JSON_KEYS.items():
                d[key] = json.loads(d.pop(col) or "[]")
            out.append(d)
        return out

    # scan -------------------------------------------------------------------

    def set_scan(self, **fields) -> None:
        """Update scan columns. ``unmapped`` (a list) is accepted for ``unmapped_json``."""
        if "unmapped" in fields:
            fields["unmapped_json"] = json.dumps(fields.pop("unmapped"))
        bad = set(fields) - set(SCAN_FIELDS)
        if bad:
            raise ValueError(f"unknown scan fields: {sorted(bad)}")
        if not fields:
            return
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        sets = ", ".join(f"{c}=excluded.{c}" for c in fields)
        with self._lock:
            self._db.execute(
                f"INSERT INTO scan(id, {cols}) VALUES (1, {marks}) "
                f"ON CONFLICT(id) DO UPDATE SET {sets}", tuple(fields.values()))
            self._db.commit()

    def scan(self) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM scan WHERE id=1").fetchone()
        d = dict(row) if row else {}
        d.pop("id", None)
        out = {"state": "idle", "stage": None, "done": 0, "total": 0, "mapped": 0,
               "unmapped_json": "[]", "started_at": None, "finished_at": None, "error": None}
        out.update({k: v for k, v in d.items() if v is not None})
        out["unmapped"] = json.loads(out["unmapped_json"])
        return out

    # profile and artist links -----------------------------------------------

    def save_profile(self, p: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO profile(id, json, built_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET json=excluded.json, built_at=excluded.built_at",
                (json.dumps(p), _now()))
            self._db.commit()

    def profile(self) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT json FROM profile WHERE id=1").fetchone()
        return json.loads(row["json"]) if row else None

    def link(self, discogs_id: int) -> str | None:
        """The cached MBID for a Discogs artist, or None (unchecked and checked-miss alike)."""
        with self._lock:
            row = self._db.execute("SELECT mbid FROM artist_links WHERE discogs_id=?",
                                   (discogs_id,)).fetchone()
        return row["mbid"] if row else None

    def checked_ids(self) -> set[int]:
        """Every Discogs artist id that has been looked up, matched or not."""
        with self._lock:
            rows = self._db.execute("SELECT discogs_id FROM artist_links").fetchall()
        return {r[0] for r in rows}

    def set_link(self, discogs_id: int, mbid: str | None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO artist_links VALUES (?,?,?) ON CONFLICT(discogs_id) "
                "DO UPDATE SET mbid=excluded.mbid, checked_at=excluded.checked_at",
                (discogs_id, mbid, _now()))
            self._db.commit()

    # ownership --------------------------------------------------------------

    def mark_owned(self, master_id: int, artist: str, title: str, genres: list[str]) -> None:
        """Mark a master as owned. A missing master (None or 0) is ignored: nothing to key on."""
        if not _master(master_id):
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO owned_marks VALUES (?,?,?,?,?) ON CONFLICT(master_id) DO UPDATE SET "
                "artist=excluded.artist, title=excluded.title, genres_json=excluded.genres_json, "
                "marked_at=excluded.marked_at",
                (master_id, artist, title, json.dumps(genres), _now()))
            self._db.commit()

    def owned_marks(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM owned_marks ORDER BY marked_at").fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["genres"] = json.loads(d.pop("genres_json") or "[]")
            out.append(d)
        return out

    def owned_master_ids(self) -> set[int]:
        """Master ids in the collection plus the ones marked 'I own this'."""
        with self._lock:
            rows = self._db.execute(
                "SELECT master_id FROM releases WHERE master_id IS NOT NULL "
                "UNION SELECT master_id FROM owned_marks").fetchall()
        return {r[0] for r in rows}

    def suggested_master_ids(self) -> set[int]:
        """Master ids of every card ever made, weekly or on demand."""
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT master_id FROM cards WHERE master_id IS NOT NULL").fetchall()
        return {r[0] for r in rows}

    # digests and cards ------------------------------------------------------

    def _new_digest_id(self, digest: dict) -> str:
        if digest.get("kind") != "weekly":
            return uuid.uuid4().hex
        week = str(digest.get("week_of") or "")
        try:
            iso = date.fromisoformat(week[:10]).isocalendar()
            base = f"{iso[0]}-W{iso[1]:02d}"
        except ValueError:
            base = week or "weekly"
        taken = {r[0] for r in self._db.execute(
            "SELECT id FROM digests WHERE id = ? OR id LIKE ?", (base, base + "-%")).fetchall()}
        cand, n = base, 1
        while cand in taken:
            n += 1
            cand = f"{base}-{n}"
        return cand

    def add_digest(self, digest: dict, cards: list[dict]) -> str:
        """Store a digest and its cards; returns the digest id."""
        with self._lock:
            try:
                self._db.execute("BEGIN")
                did = self._new_digest_id(digest)
                self._db.execute(
                    "INSERT INTO digests VALUES (?,?,?,?,?,?,?)",
                    (did, digest.get("kind"), digest.get("week_of"), _now(), digest.get("note"),
                     digest.get("read_at"), digest.get("session_id")))
                for pos, card in enumerate(cards):
                    self._db.execute(
                        "INSERT INTO cards(id, digest_id, master_id, release_id, json, position) "
                        "VALUES (?,?,?,?,?,?)",
                        (uuid.uuid4().hex, did, _master(card.get("master_id")), card.get("release_id"),
                         json.dumps(card), pos))
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise
        return did

    @staticmethod
    def _card(row: sqlite3.Row) -> dict:
        d = json.loads(row["json"])
        d.update(id=row["id"], digest_id=row["digest_id"], master_id=row["master_id"],
                 release_id=row["release_id"], reaction=row["reaction"],
                 reaction_at=row["reaction_at"], on_wantlist=bool(row["on_wantlist"]),
                 wanted_by_card=bool(row["wanted_by_card"]))
        return d

    def _digest(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["cards"] = [self._card(c) for c in self._db.execute(
            "SELECT * FROM cards WHERE digest_id=? ORDER BY position", (row["id"],)).fetchall()]
        return d

    def digest(self, id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM digests WHERE id=?", (id,)).fetchone()
            return self._digest(row) if row else None

    def has_digest_for(self, session_id: str) -> bool:
        """True when a digest was saved by this session."""
        with self._lock:
            return bool(session_id) and self._db.execute(
                "SELECT 1 FROM digests WHERE session_id=? LIMIT 1", (session_id,)).fetchone() is not None

    def digests(self, before: str | None, limit: int) -> list[dict]:
        """Newest first, with cards; ``before`` is a ``created_at`` to page past."""
        sql, args = "SELECT * FROM digests", []
        if before:
            sql += " WHERE created_at < ?"
            args.append(before)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [self._digest(r) for r in self._db.execute(sql, args).fetchall()]

    def card(self, id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM cards WHERE id=?", (id,)).fetchone()
        return self._card(row) if row else None

    def set_reaction(self, card_id: str, reaction: str | None) -> bool:
        """False when the card does not exist."""
        if reaction is not None and reaction not in REACTIONS:
            raise ValueError(f"unknown reaction: {reaction}")
        with self._lock:
            cur = self._db.execute("UPDATE cards SET reaction=?, reaction_at=? WHERE id=?",
                                   (reaction, _now() if reaction else None, card_id))
            self._db.commit()
            return cur.rowcount > 0

    def set_wantlist(self, card_id: str, on: bool, by_card: bool) -> bool:
        """Record the wantlist state; ``by_card`` says the card's own button added it.

        False when the card does not exist."""
        with self._lock:
            cur = self._db.execute("UPDATE cards SET on_wantlist=?, wanted_by_card=? WHERE id=?",
                                   (int(on), int(on and by_card), card_id))
            self._db.commit()
            return cur.rowcount > 0

    def mark_read(self, digest_id: str) -> bool:
        """Stamp read_at once; False when the digest does not exist."""
        with self._lock:
            cur = self._db.execute("UPDATE digests SET read_at=COALESCE(read_at, ?) WHERE id=?",
                                   (_now(), digest_id))
            self._db.commit()
            return cur.rowcount > 0

    def unread_count(self) -> int:
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM digests WHERE read_at IS NULL").fetchone()[0]

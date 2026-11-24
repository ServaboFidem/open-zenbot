"""What was texted, kept in <vault>-settings/telnyx_texts.sqlite3.

A text's key is claimed, and the claim committed, before the text is sent. A claim still ``sending`` when the harness
starts (a crash or a kill mid-send) becomes ``uncertain`` and is never sent again: Telnyx has no lookup by our key,
and a text twice is worse than a text missed. A ``retry`` (Telnyx certainly never took it) may be claimed once more.

States: sending, sent, retry, failed, uncertain, capped (skipped by the daily cap), empty (a rundown with nothing)."""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

COUNTED = ("sending", "sent", "retry", "failed", "uncertain")  # what the daily cap counts
_COUNTED = "(" + ", ".join(f"'{s}'" for s in COUNTED) + ")"
MAX_ATTEMPTS = 2


class SendRecord:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS texts (
                key TEXT PRIMARY KEY, kind TEXT NOT NULL, day TEXT NOT NULL, state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, message_id TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', at REAL NOT NULL)""")
            yield db

    def settle(self) -> int:
        """At start: every claim left ``sending`` becomes ``uncertain``. Answers how many."""
        with self._db() as db:
            return db.execute("UPDATE texts SET state='uncertain', error='interrupted' WHERE state='sending'").rowcount

    def count(self, day: str) -> int:
        with self._db() as db:
            return db.execute(f"SELECT COUNT(*) FROM texts WHERE day=? AND state IN {_COUNTED}", (day,)).fetchone()[0]

    def skipped(self, day: str) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM texts WHERE day=? AND state='capped'", (day,)).fetchone()[0]

    def known(self, key: str) -> str | None:
        with self._db() as db:
            row = db.execute("SELECT state FROM texts WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def claim(self, key: str, kind: str, day: str, at: float) -> bool:
        """Claim a key for sending; True only when this call made the claim (committed before it answers)."""
        with self._db() as db:
            return db.execute("""
                INSERT INTO texts (key, kind, day, state, attempts, at) VALUES (?, ?, ?, 'sending', 1, ?)
                ON CONFLICT(key) DO UPDATE SET state='sending', attempts=texts.attempts + 1, day=excluded.day,
                    at=excluded.at
                WHERE texts.state='retry' AND texts.attempts < ?
            """, (key, kind, day, at, MAX_ATTEMPTS)).rowcount == 1

    def mark(self, key: str, state: str, at: float, *, kind: str = "", day: str = "", message_id: str = "",
             error: str = "") -> None:
        """Set a key's state, adding the row when there is none (capped and empty rows have no claim)."""
        with self._db() as db:
            db.execute("""
                INSERT INTO texts (key, kind, day, state, message_id, error, at) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET state=excluded.state, message_id=excluded.message_id,
                    error=excluded.error, at=excluded.at,
                    day=CASE WHEN excluded.day != '' THEN excluded.day ELSE texts.day END
            """, (key, kind, day, state, message_id, error, at))

    def last_sent_at(self) -> float | None:
        with self._db() as db:
            return db.execute("SELECT MAX(at) FROM texts WHERE state='sent'").fetchone()[0]

    def last_test_at(self) -> float | None:
        with self._db() as db:
            return db.execute(f"SELECT MAX(at) FROM texts WHERE kind='test' AND state IN {_COUNTED}").fetchone()[0]

    def day(self, key: str) -> str:
        """The local day a key's text is counted on ("" when unknown). A retry claimed again moves to that day."""
        with self._db() as db:
            row = db.execute("SELECT day FROM texts WHERE key=?", (key,)).fetchone()
        return row[0] if row else ""

    def attempts(self, key: str) -> int:
        with self._db() as db:
            row = db.execute("SELECT attempts FROM texts WHERE key=?", (key,)).fetchone()
        return row[0] if row else 0

    def last_error(self) -> str:
        """Why the newest text did not certainly go; else empty. A text that failed, or waits to be tried again,
        answers its error kind; one that may have gone answers ``uncertain`` (``interrupted`` when the harness
        stopped mid-send), never a kind that would say it certainly did not go."""
        with self._db() as db:
            row = db.execute(f"SELECT state, error FROM texts WHERE state IN {_COUNTED} "
                             "ORDER BY at DESC, rowid DESC LIMIT 1").fetchone()
        if not row or row[0] in ("sent", "sending"):
            return ""
        if row[0] == "uncertain":
            return "interrupted" if row[1] == "interrupted" else "uncertain"
        return row[1] or "provider"

    def prune(self, before: float) -> int:
        with self._db() as db:
            return db.execute("DELETE FROM texts WHERE at < ? AND state != 'sending'", (before,)).rowcount

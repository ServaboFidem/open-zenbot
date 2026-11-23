"""The scheduled plain-text morning email. Only Electron writes the destination.

The committed SQLite claim precedes the outbound send. An interrupted send can only be
acknowledged by a matching sent message. An empty provider search never releases its claim.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from src.mail.agentmail import MailError
from src.mail.service import MailService
from src.vault_settings import settings_dir

_ATOM = r"[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+"
_DOMAIN_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_ADDRESS = re.compile(rf"{_ATOM}(?:\.{_ATOM})*@{_DOMAIN_LABEL}(?:\.{_DOMAIN_LABEL})+")
_TIME = re.compile(r"(?:0[0-9]|1[01]):[0-5][0-9]")
_INTERVAL = 60


def _config(path: Path) -> tuple[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return "", ""
    if not isinstance(raw, dict):
        return "", ""
    recipient, time = raw.get("recipient"), raw.get("time", "07:00")
    recipient = recipient.strip() if isinstance(recipient, str) else ""
    if (len(recipient) > 254 or not _ADDRESS.fullmatch(recipient) or len(recipient.partition("@")[0]) > 64
            or not isinstance(time, str) or not _TIME.fullmatch(time)):
        return "", ""
    return recipient, time


def _now() -> datetime:
    return datetime.now().astimezone()


class MorningDelivery:
    def __init__(self, vault: Path, mail: MailService,
                 edition: Callable[[datetime], Awaitable[tuple[str, str]]]):
        folder = settings_dir(vault)
        self.config = folder / "morning_email.json"
        self.database = folder / "morning_delivery.sqlite3"
        self.mail = mail
        self.edition = edition
        self._lock = asyncio.Lock()
        self._observed: datetime | None = None
        self._unavailable = False

    @contextmanager
    def _db(self):
        self.database.parent.mkdir(exist_ok=True, parents=True)
        with closing(sqlite3.connect(self.database, timeout=1)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                day TEXT PRIMARY KEY, status TEXT NOT NULL, inbox TEXT NOT NULL, label TEXT NOT NULL,
                message_id TEXT NOT NULL DEFAULT '', retry_at REAL NOT NULL DEFAULT 0)""")
            yield db

    def _record(self, day: str):
        if not self.database.exists():
            return None
        with self._db() as db:
            return db.execute("SELECT * FROM deliveries WHERE day = ?", (day,)).fetchone()

    def _claim(self, day, status, inbox, label, timestamp, retry_at=0) -> bool:
        # A different harness may have claimed this date while the model or draft request was running.
        with self._db() as db:
            changed = db.execute("""
                INSERT INTO deliveries (day, status, inbox, label, retry_at) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET status=excluded.status, inbox=excluded.inbox,
                    label=excluded.label, retry_at=excluded.retry_at
                WHERE deliveries.status='retry' AND deliveries.retry_at <= ?
            """, (day, status, inbox, label, retry_at, timestamp))
            return changed.rowcount == 1

    def _finish(self, day, label, status, message_id="", retry_at=0):
        with self._db() as db:
            db.execute("""UPDATE deliveries SET status=?, message_id=?, retry_at=?
                          WHERE day=? AND label=? AND status='uncertain'""",
                       (status, message_id, retry_at, day, label))

    def status(self) -> dict:
        now = self._observed or _now()
        day = now.date().isoformat()
        try:
            record = self._record(day)
            if self._unavailable:
                status = "unavailable"
            elif record is not None and record["status"] in ("uncertain", "sent", "failed"):
                status = record["status"]
            elif not self.mail.enabled or not _config(self.config)[0]:
                status = "disabled"
            elif now.hour >= 12:
                status = "expired"
            else:
                status = record["status"] if record is not None else "waiting"
        except (sqlite3.Error, OSError):
            status = "unavailable"
        return {"status": status, "date": day}

    def _eligible(self, now, config, inbox) -> bool:
        return (bool(config[0]) and self.mail.enabled and self.mail.settings.inbox_id == inbox
                and now.hour < 12 and now.strftime("%H:%M") >= config[1])

    async def tick(self, now: datetime | None = None) -> dict:
        async with self._lock:
            observed = now or _now()
            self._observed = observed
            self._unavailable = False
            try:
                await self._tick(observed, lambda: now or _now())
            except Exception:
                # Source/provider errors may contain private mail. The next poll can recover.
                self._unavailable = True
            return self.status()

    async def _tick(self, now, clock):
        day = now.date().isoformat()
        record = self._record(day)
        if record is not None:
            status = record["status"]
            if status == "uncertain" and self.mail.enabled:
                try:
                    receipt = await self.mail.morning_client().morning_receipt(record["inbox"], record["label"])
                except MailError:
                    return
                if receipt:
                    self._finish(day, record["label"], "sent", message_id=receipt)
            if status != "retry" or record["retry_at"] > now.timestamp():
                return

        config = _config(self.config)
        inbox = self.mail.settings.inbox_id
        if not self._eligible(now, config, inbox):
            return

        def still_due():
            current = clock()
            return (current.date() == now.date() and _config(self.config) == config
                    and self._eligible(current, config, inbox))

        subject, text = await self.edition(now)
        if not still_due():
            return
        # A retry of the same edition gets the same draft. New content or a new recipient gets a fresh one.
        signature = json.dumps([day, inbox, config[0], subject, text], ensure_ascii=True)
        client_id = "zen-morning-" + uuid.uuid5(uuid.NAMESPACE_URL, signature).hex
        client = self.mail.morning_client()
        label = "zen-morning-" + uuid.uuid4().hex
        try:
            draft = await client.morning_draft(inbox, client_id, config[0], subject, text)
        except MailError as exc:
            # An unacknowledged draft is safe to create again; create does not send.
            status = "failed" if exc.status in (400, 401, 403, 404, 422) else "retry"
            timestamp = clock().timestamp()
            self._claim(day, status, inbox, label, timestamp, timestamp + max(exc.retry_after, _INTERVAL))
            return
        if not still_due() or not self._claim(day, "uncertain", inbox, label, clock().timestamp()):
            return
        try:
            receipt = await client.send_morning_draft(inbox, draft, label)
        except MailError as exc:
            if exc.status == 429 or exc.before_request:
                self._finish(day, label, "retry", retry_at=clock().timestamp() + max(exc.retry_after, _INTERVAL))
            elif exc.status in (400, 401, 403, 422):
                self._finish(day, label, "failed")
            return  # a lost acknowledgment, 404 or conflict stays uncertain
        self._finish(day, label, "sent", message_id=receipt)

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(_INTERVAL)

"""The Telnyx service: the saved key and sending number, one HTTP client, the reminder loop and the test text.

Every send holds one lock, so texts go one at a time and the daily cap is counted with nothing in flight. A text's
key is claimed in the send record before it goes (see record.py). Telnyx certainly never took a text when the
request never left or Telnyx answered 429: only then is it tried once more, on the next pass, if it is still due. A
timeout after the request went, a 5xx or an answer that cannot be read may mean Telnyx sent it: it is ``uncertain``
and never sent again. A refused key or a refused text is ``failed``.

Nothing here logs a text, an event's title or location, or a full phone number. The ledger gets kind ``telnyx``
entries (``key_saved``, ``key_forgotten``, ``number_chosen`` with the last four digits, ``sent``, ``failed``,
``uncertain``, ``capped``) carrying kinds, counts and Telnyx's message id."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import httpx

from src.memory.ledger import Kind
from src.telnyx.client import TIMEOUT_S, Telnyx, TelnyxError
from src.telnyx.due import reminders, rundown
from src.telnyx.record import MAX_ATTEMPTS, SendRecord
from src.telnyx.settings import TelnyxKeys, TelnyxNumber, check_key, last4, read_texts
from src.vault_settings import settings_dir

log = logging.getLogger(__name__)

RECORD_FILE = "telnyx_texts.sqlite3"
DAILY_CAP = 30  # texts a local day, of every kind: invitations from anyone could otherwise run up a bill
PER_PASS = 10  # a backstop: the rest wait for the next pass, where an event that started is no longer due
INTERVAL_S = 60
TEST_GAP_S = 60
KEEP_S = 30 * 86400
TEST_TEXT = "This is a test text from ZenBot."


def _local(now: datetime | float | None) -> datetime:
    """The clock the due rules read. Left naive (this computer's local time) so each instant is put in local time
    with its own offset: an aware ``astimezone()`` carries today's fixed offset, which misprints tomorrow's times and
    misplaces tomorrow's midnight on the evening before a clock change."""
    if isinstance(now, datetime):
        return now
    return datetime.fromtimestamp(now) if now is not None else datetime.now()


def _still_on(start, now, kind: str) -> bool:
    """The texts settings still allow this kind of text, to the phone the pass started with."""
    return bool(now and now.on and now.phone == start.phone and (now.before if kind == "before" else now.rundown))


class TelnyxService:
    def __init__(self, vault_root: Path, ledger, calendar_store: Callable[[], object],
                 http: httpx.AsyncClient | None = None):
        self.vault_root = vault_root
        self.ledger = ledger
        self.calendar_store = calendar_store
        self.keys = TelnyxKeys.load(vault_root)
        self.number = TelnyxNumber.load(vault_root)
        self.record = SendRecord(settings_dir(vault_root) / RECORD_FILE)
        self._own_http = http is None
        self.http = http if http is not None else httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)
        self._lock = asyncio.Lock()
        self._capped_day = ""
        self.clock: Callable[[], datetime] = datetime.now  # read before every send, not only at a pass's start

    def __repr__(self) -> str:  # never the key
        return "TelnyxService()"

    def _client(self, key: str | None = None) -> Telnyx:
        return Telnyx(self.http, key if key is not None else self.keys.api_key)

    def _now(self, now: datetime | float | None) -> datetime:
        return _local(now if now is not None else self.clock())

    def _log(self, event: str, **data) -> None:
        self.ledger.append(Kind.telnyx, "", "", event=event, **data)

    def status(self, now: datetime | float | None = None) -> dict:
        day = _local(now).date().isoformat()
        config = read_texts(self.vault_root)
        texts = None if config is None else {
            "valid": True, "before": config.before, "rundown": config.rundown, "lead_minutes": config.lead_minutes,
            "evening": config.evening, "phone_hint": last4(config.phone)}
        return {**self.keys.public(), "number": self.number.phone or None, "texts": texts,
                "last_sent_at": self.record.last_sent_at(), "last_error": self.record.last_error(),
                "sent_today": self.record.count(day), "skipped_today": self.record.skipped(day)}

    # setup -------------------------------------------------------------------

    async def set_key(self, raw: object) -> dict:
        """Check the key against Telnyx, then save it. A chosen number the new key cannot see is cleared."""
        key = check_key(raw)
        listed = await self._client(key).numbers()
        cleared = bool(self.number.id) and all(n["id"] != self.number.id for n in listed)
        keys = TelnyxKeys(api_key=key)
        keys.save(self.vault_root)  # written before it is used, so a failed write leaves the old key in use
        self.keys = keys
        self._log("key_saved", number_cleared=cleared)
        if cleared:
            self.number = TelnyxNumber()  # never sent from with the new key, even if clearing its file fails below
            self.number.save(self.vault_root)
        return self.status()

    def forget_key(self) -> dict:
        """Forget the key and the sending number. Nothing changes at Telnyx."""
        keys = TelnyxKeys()
        keys.save(self.vault_root)  # a failed write leaves the old key saved and in use
        self.keys, self.number = keys, TelnyxNumber()  # out of use once the key file is cleared
        self._log("key_forgotten")
        self.number.save(self.vault_root)
        return self.status()

    async def numbers(self) -> list[dict]:
        if not self.keys.api_key:
            raise LookupError("no key")
        return await self._client().numbers()

    async def choose_number(self, number_id: str) -> dict:
        """KeyError for a number the key cannot see, or one checked with a key replaced while the list was read."""
        key = self.keys.api_key
        found = next((n for n in await self.numbers() if n["id"] == number_id), None)
        if found is None or self.keys.api_key != key:
            raise KeyError(number_id)
        number = TelnyxNumber(id=found["id"], phone=found["phone"], profile_id=found["profile_id"])
        number.save(self.vault_root)
        self.number = number
        self._log("number_chosen", last4=last4(number.phone))
        return self.status()

    # sending -----------------------------------------------------------------

    async def _send(self, key: str, kind: str, text: str, to: str, day: str, at: float) -> TelnyxError | None:
        """Claim the key, then send. Answers the TelnyxError when the send failed, None otherwise (sent, or the key
        was not claimed)."""
        if not self.record.claim(key, kind, day, at):
            return None
        try:
            message_id = await self._client().send(self.number.phone, to, text)
        except TelnyxError as err:
            if err.before_request or err.status == 429:
                # tried once more on a later pass; out of tries, it failed (so Settings shows it, never stuck)
                state = "retry" if self.record.attempts(key) < MAX_ATTEMPTS else "failed"
            elif err.kind in ("key", "forbidden", "invalid"):
                state = "failed"
            else:
                state = "uncertain"
            self.record.mark(key, state, at, error=err.kind)
            if state == "uncertain":
                self._log("uncertain", type=kind, error=err.kind)
            else:
                self._log("failed", type=kind, error=err.kind, retry=state == "retry")
            return err
        except Exception:
            self.record.mark(key, "uncertain", at, error="provider")  # the request may have gone
            self._log("uncertain", type=kind, error="provider")
            raise
        self.record.mark(key, "sent", at, message_id=message_id)
        self._log("sent", type=kind, message_id=message_id)
        return None

    async def test(self, now: datetime | float | None = None) -> dict:
        """Send the fixed test sentence to the owner's saved phone. LookupError when not set up, PermissionError
        within a minute of the last test or once the day's cap is reached; a failed send raises its TelnyxError."""
        async with self._lock:
            config = read_texts(self.vault_root)
            if not (self.keys.api_key and self.number.phone and config and config.phone):
                raise LookupError("not set up")
            current = self._now(now)
            at, day = current.timestamp(), current.date().isoformat()
            last = self.record.last_test_at()
            if (last is not None and at - last < TEST_GAP_S) or self.record.count(day) >= DAILY_CAP:
                raise PermissionError("wait")
            err = await self._send(f"test:{uuid.uuid4().hex}", "test", TEST_TEXT, config.phone, day, at)
            if err is not None:
                raise err
            return self.status(current)

    async def tick(self, now: datetime | None = None) -> None:
        """One pass: send what is due, at most PER_PASS, within the day's cap. A send can take up to the client's
        timeout, so the clock and the texts settings are read again before each text: a reminder whose event has
        started is not sent, and a new day or changed settings end the pass (the next one builds its list afresh)."""
        async with self._lock:
            current = self._now(now)
            config = read_texts(self.vault_root)
            if not (self.keys.api_key and self.number.phone and config and config.on):
                return
            store = self.calendar_store()
            due = reminders(store, current, config.lead_minutes) if config.before else []
            if config.rundown and (evening := rundown(store, current, config.evening)) is not None:
                due.append(evening)
            day, at = current.date().isoformat(), current.timestamp()
            sent = 0
            for item in due:
                state = self.record.known(item.key)
                if state not in (None, "retry"):
                    continue
                moment = self._now(now)
                if moment.date().isoformat() != day or not _still_on(config, read_texts(self.vault_root), item.kind):
                    break
                if item.start is not None and moment.astimezone(UTC) >= item.start:
                    continue  # started while an earlier text was sending; nothing is recorded
                at = moment.timestamp()
                if not item.text:
                    self.record.mark(item.key, "empty", at, kind=item.kind, day=day)
                    continue
                # a retry was counted on its first day; carried past midnight it counts toward the new one
                counted_today = state == "retry" and self.record.day(item.key) == day
                if not counted_today and self.record.count(day) >= DAILY_CAP:
                    self.record.mark(item.key, "capped", at, kind=item.kind, day=day)
                    continue
                if sent >= PER_PASS:
                    break
                await self._send(item.key, item.kind, item.text, config.phone, day, at)
                sent += 1
            skipped = self.record.skipped(day)
            if skipped and self._capped_day != day:
                self._capped_day = day
                self._log("capped", skipped=skipped)
            self.record.prune(at - KEEP_S)

    def settle(self) -> int:
        """At start: a send cut off by a crash or a kill becomes ``uncertain`` and is never tried again."""
        count = self.record.settle()
        if count:
            self._log("uncertain", type="interrupted", count=count)
        return count

    async def run(self) -> None:
        settled = False
        while True:
            try:
                if not settled:  # tried again each pass until it works; no text goes before it has
                    self.settle()
                    settled = True
                await self.tick()
            except Exception as e:  # noqa: BLE001 - the loop outlives any failure; texts may be in the frames
                log.warning("telnyx pass failed: %s", type(e).__name__)
            await asyncio.sleep(INTERVAL_S)

    async def close(self) -> None:
        if self._own_http:
            await self.http.aclose()

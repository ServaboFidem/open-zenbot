"""The built-in calendar as the harness holds it: the store, the Google connection, sync runs and their timing.

Exactly one ``Sync.run`` happens at a time, whoever asks. ``sync()`` joins the run in flight; ``request()`` (a
change the next run must see) joins it and queues at most one more run after it. Signing in, disconnecting and
dropping a calendar's events wait for the run in flight, so a run never writes over them halfway. Nothing reaches
Google until it is connected."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from src.events.connect import Connection
from src.events.google import Google, GoogleError
from src.events.settings import calendar_dir
from src.events.store import CalendarStore
from src.events.sync import LAST_ERROR, LAST_SYNC, LOST_EDITS, Sync
from src.memory.ledger import Kind

log = logging.getLogger(__name__)

SYNC_EVERY_S = 900
TIMEOUT_S = 30.0


class _Client:
    """The service's http client, made on first use (Google holds this, not the client itself)."""

    def __init__(self, service: "CalendarService"):
        self._service = service

    def __getattr__(self, name: str):
        return getattr(self._service.http(), name)


class CalendarService:
    def __init__(self, config, ledger, *, client: httpx.AsyncClient | None = None):
        self.config = config
        self.ledger = ledger
        self.vault = Path(config.vault_root)
        self.client = client
        self._own_client = client is None
        self.every = SYNC_EVERY_S
        self.store = CalendarStore(calendar_dir(self.vault) / "calendar.db")
        # Connection loads CalendarKeys, which registers the saved secrets with the ledger's exclusion filter
        self.google = Google(_Client(self), lambda: self.conn.keys)
        self.conn = Connection(self.vault, self.store, self.google, ledger, port=config.port)
        self.syncer = Sync(self.store, self.google, self.conn, ledger)
        self._tasks: set[asyncio.Task] = set()
        self._run: asyncio.Task | None = None
        self._again = False
        self._lock = asyncio.Lock()  # held by a run, a sign-in, a disconnect and a calendar's drop

    def http(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=TIMEOUT_S)
            self._own_client = True
        return self.client

    # status -------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._run is not None and not self._run.done()

    def status(self) -> dict:
        conn, settings, store = self.conn, self.conn.settings, self.store
        keys = conn.keys.public()
        return {
            "google": {"state": conn.state(), "credentials": keys["credentials"],
                       "client_id_hint": keys["client_id_hint"], "account": settings.account,
                       "can_write": conn.can_write(), "redirect_uri": conn.redirect_uri},
            "push": {"on": settings.push, "destination": settings.destination},
            "sync": {"running": self.running, "last": store.meta(LAST_SYNC), "error": store.meta(LAST_ERROR),
                     "lost_edits": int(store.meta(LOST_EDITS) or 0)},
            "counts": store.counts(),
        }

    # runs ---------------------------------------------------------------------

    def _job(self) -> asyncio.Task:
        if self._run is None or self._run.done():
            self._again = False
            self._run = self.spawn(self._runs())
        return self._run

    async def _runs(self) -> None:
        while True:
            self._again = False
            async with self._lock:
                try:
                    await self.syncer.run()
                except GoogleError:
                    pass  # recorded by the run: last_error, needs_reconnect and its ledger entry
                except Exception:
                    log.exception("calendar sync failed")  # recorded by the run as last_error "internal"
            if not self._again:
                return

    async def sync(self) -> dict:
        """Run a sync, or join the one in flight; answers ``status()``."""
        await asyncio.shield(self._job())
        return self.status()

    def request(self) -> None:
        """Start a run in the background; while one runs, queue one more after it (never more than one)."""
        if self.running:
            self._again = True
        else:
            self._job()

    def kick(self) -> None:
        """After a local change: send it to Google soon, when push is on."""
        if self.syncer.can_push():
            self.request()

    # changes a run must not overlap ---------------------------------------------

    async def finish(self, state: str | None, code: str | None, error: str | None) -> tuple[bool, str]:
        async with self._lock:
            ok, message = await self.conn.finish(state, code, error)
        if ok:
            self.request()
        return ok, message

    async def disconnect(self) -> None:
        async with self._lock:
            await self.conn.disconnect()

    async def unselect(self, calendar_id: str) -> dict | None:
        async with self._lock:
            cal = self.store.set_calendar(calendar_id, selected=False)
            if cal is not None:
                self.store.clear_pulled(calendar_id)
        return cal

    # background work ------------------------------------------------------------

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled() or task.exception() is None:
            return
        err = task.exception()
        log.error("calendar background task failed", exc_info=err)
        self.ledger.append(Kind.error, "", "", error=f"calendar background task: {type(err).__name__}")

    async def start(self) -> None:
        """The periodic sync: now, then every ``every`` seconds, while Google is connected."""
        while True:
            if self.conn.state() == "connected":
                await self.sync()
            await asyncio.sleep(self.every)

    async def settle(self) -> None:
        """Wait for the run in flight and the one queued after it (not the periodic loop)."""
        while self._run is not None and not self._run.done():
            await asyncio.gather(self._run, return_exceptions=True)
            await asyncio.sleep(0)

    async def close(self) -> None:
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.client is not None and self._own_client:
            await self.client.aclose()
            self.client = None
        self.store.close()

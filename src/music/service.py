"""MusicService: owns the music settings, the Discogs token, the atlas, the store, the digests and the digest routine.

The harness builds one. The routes (`routes.py`) and the bot's tools (`tools.py`) both go through it, so a revoked
token seen by either marks the connection, and a settings or token change re-registers the tools."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from src.harness.loop import SessionState
from src.harness.scheduler import Routine, Schedule
from src.harness.tools import current_call
from src.memory.ledger import Kind
from src.music import collection
from src.music.collection import link_artists
from src.music.atlas import Atlas, AtlasError, ensure_atlas
from src.music.check import Gate
from src.music.digest import BOT_ID, Digests
from src.music.discogs import Discogs, DiscogsError
from src.music.musicbrainz import MusicBrainz
from src.music.settings import MusicKeys, MusicSettings, music_dir
from src.music.store import MusicStore
from src.music.styles import StyleMap
from src.music.tools import MusicTools, RunBudget
from src.music.ytmusic import YTMusicLookup
from src.vault_settings import settings_dir

log = logging.getLogger(__name__)

STATE_FILE = "music_state.json"
ROUTINE_NAME = "Weekly album digest"
ROUTINE_SKILL = "weekly-digest"
MANAGED_BY = "music"
FRESH = timedelta(minutes=10)  # a scan this recent is not repeated before a digest
WANTLIST_TTL = 600.0  # seconds the wantlist ids are reused across gates (a want or unwant drops them)
SESSION_GATES = 16  # sessions whose gates are kept: one gate per (session, widened) for the run
STOP_KINDS = {"token_invalid", "rate_limited"}
CONNECTION_STATES = {"ok", "not_found", "private", "token_invalid", "token_other_user", "rate_limited", "network", "unset"}


class _Watched(Discogs):
    """A Discogs client that reports a rejected token (401), whoever made the call."""

    def __init__(self, *args, on_invalid, **kw):
        super().__init__(*args, **kw)
        self._on_invalid = on_invalid

    async def _request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        try:
            return await super()._request(method, path, params)
        except DiscogsError as e:
            if e.kind == "token_invalid" and self.token:
                self._on_invalid()
            raise


class MusicService:
    def __init__(self, config, ledger, memory, tools, catalog, routines=None, scheduler=None, *,
                 client: httpx.AsyncClient | None = None, ytm=None, ensure=ensure_atlas, musicbrainz=None):
        self.config = config
        self.ledger = ledger
        self.memory = memory
        self.tools = tools
        self.catalog = catalog
        self.routines = routines  # the harness's RoutineStore, attached once the app builds it
        self.scheduler = scheduler
        self.client = client
        self.ytm = ytm or YTMusicLookup()
        self.ensure = ensure
        self.mb = musicbrainz
        self.vault = Path(config.vault_root)
        self.dir = music_dir(self.vault)
        self.atlas_dir = self.dir / "atlas"
        self.settings = MusicSettings.load(self.vault)
        self.keys = MusicKeys.load(self.vault)
        self._state, self._scanned_user = self._read_state()
        self._atlas: Atlas | None = None
        self._styles: StyleMap | None = None
        self._atlas_state = "missing"
        self._store: MusicStore | None = None
        self._digests: Digests | None = None
        self._discogs: tuple[tuple[str, str], Discogs] | None = None
        self._tasks: set[asyncio.Task] = set()
        self._prepare_lock: asyncio.Lock | None = None
        self._scan_task: asyncio.Task | None = None
        self._link_task: asyncio.Task | None = None
        self.clock = time.monotonic
        self.budget = RunBudget(clock=lambda: self.clock())  # the 15-minute run budget, per session
        self._gates: OrderedDict[tuple[str, bool], Gate] = OrderedDict()
        self.widened_masters: dict[str, set[int]] = {}  # session id -> masters its music_candidates(widen=true) returned
        self._wants: tuple[float, tuple[str, str], set[int]] | None = None  # (fetched at, (user, token), ids)

    # parts --------------------------------------------------------------------

    def http(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=30)
        return self.client

    @property
    def store(self) -> MusicStore:
        if self._store is None:
            self._store = MusicStore(self.dir / "music.db")
        return self._store

    @property
    def atlas(self) -> Atlas:
        """The loaded atlas; an empty one until `prepare` has loaded it."""
        return self._atlas or Atlas(dir=self.atlas_dir)

    @property
    def styles(self) -> StyleMap:
        return self._styles or StyleMap.load(self.atlas)

    @property
    def digests(self) -> Digests:
        if self._digests is None:
            self._digests = Digests(self.store, self.gate, self.discogs, self.ytm, self.memory,
                                    self.dir / "covers", self.http(), lambda: self.keys.token)
            self._digests.on_wantlist_change = self._wantlist_changed
        return self._digests

    def discogs(self) -> Discogs:
        """The Discogs client for the saved username and token (one per pair, so its rate limit holds)."""
        key = (self.settings.username, self.keys.token)
        if self._discogs is None or self._discogs[0] != key:
            self._discogs = (key, _Watched(key[0], key[1], client=self.http(), on_invalid=self._token_rejected))
        return self._discogs[1]

    def gate(self, widened: bool = False) -> Gate:
        """One gate per (session, widened) for the run, so its verdicts are cached per master across tool calls;
        a widened one never leaks into a normal one. Outside a session (the routes), a new gate per call."""
        sid = current_call.get()[1]
        key = (sid, widened)
        g = self._gates.get(key) if sid else None
        if g is None or g.discogs is not self.discogs():
            g = Gate(self.discogs(), self.store, self.styles, self.settings, datetime.now().year, widened=widened,
                     wantlist=self.wantlist_ids)
            if sid:
                self._gates[key] = g
        if sid:
            self._gates.move_to_end(key)
            while len(self._gates) > 2 * SESSION_GATES:
                self._gates.popitem(last=False)
        return g

    async def wantlist_ids(self) -> set[int]:
        """The user's wantlist ids, reused for 10 minutes: every gate needs them, and they cost a page per 100."""
        who = (self.settings.username, self.keys.token)
        now = self.clock()
        if self._wants is None or self._wants[1] != who or now - self._wants[0] >= WANTLIST_TTL:
            self._wants = (now, who, set(await self.discogs().wantlist_ids()))
        return self._wants[2]

    def _wantlist_changed(self) -> None:
        self._wants = None

    async def profile(self) -> dict | None:
        return self.store.profile()

    # connection state ----------------------------------------------------------

    def _read_state(self) -> tuple[str, str]:
        try:
            raw = json.loads((settings_dir(self.vault) / STATE_FILE).read_text(encoding="utf-8"))
            state, user = raw.get("connection"), raw.get("scanned_user")
        except (OSError, ValueError, AttributeError):
            return "unset", ""
        return (state if state in CONNECTION_STATES else "unset"), (user if isinstance(user, str) else "")

    def _write_state(self) -> None:
        path = settings_dir(self.vault) / STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"connection": self._state, "scanned_user": self._scanned_user}), encoding="utf-8")
        tmp.replace(path)

    def _set_state(self, state: str) -> None:
        self._state = state
        self._write_state()

    def _token_rejected(self) -> None:
        if self._state != "token_invalid":
            self._set_state("token_invalid")

    def running_run(self, sessions: dict) -> str:
        """The id of a librarian run still going (an on-demand one or the digest routine's), or ""."""
        ended = (SessionState.done, SessionState.failed, SessionState.stopped)
        r = self.managed_routine()
        routine_session = self.scheduler.sessions.get(r.id) if r and self.scheduler else None
        candidates = [sessions.get(sid) for sid in self.digests.on_demand_sessions] + [routine_session]
        return next((s.id for s in candidates if s is not None and s.state not in ended), "")

    def run_failure(self, session) -> str:
        """Why a digest routine run failed although its session ended: it saved no digest. "" when it did."""
        return "" if self.store.has_digest_for(session.id) else "no digest saved"

    def connection_state(self) -> str:
        return self._state if self.settings.username else "unset"

    # status -------------------------------------------------------------------

    def managed_routine(self) -> Routine | None:
        return next((r for r in self.routines.all() if r.managed_by == MANAGED_BY), None) if self.routines else None

    def digest_state(self) -> str:
        if not self.keys.token:
            return "needs_token"
        r = self.managed_routine()
        return "on" if r and r.enabled else "off"

    def status(self) -> dict[str, Any]:
        r = self.managed_routine()
        return {
            "enabled": self.settings.enabled,
            "notify": self.settings.notify,
            "settings": self.settings.public(),
            "connection": {"username": self.settings.username, "token": bool(self.keys.token),
                           "token_hint": self.keys.hint(), "state": self.connection_state()},
            "scan": self.store.scan(),
            "atlas": {"state": self._atlas_state, "licenses": self._atlas.licenses() if self._atlas else []},
            "digest": self.digest_state(),
            "routine_id": r.id if r else None,
            "unread": self.store.unread_count(),
        }

    # background work ----------------------------------------------------------

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
        log.error("music background task failed", exc_info=err)
        self.ledger.append(Kind.error, "", "", error=f"music background task: {type(err).__name__}: {err}")

    async def settle(self) -> None:
        """Wait for background work (a scan, an atlas download) to finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await asyncio.sleep(0)  # let the done callbacks drop the finished tasks

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await self.settle()
        if self._discogs is not None:
            self._discogs = None
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        if self._store is not None:
            self._store.close()
            self._store = None

    async def prepare(self) -> bool:
        """Download the atlas if missing, load it with the style table. False when it cannot be had."""
        if self._atlas is not None:
            return True
        if self._prepare_lock is None:
            self._prepare_lock = asyncio.Lock()
        async with self._prepare_lock:
            if self._atlas is not None:
                return True
            self._atlas_state = "downloading"
            try:
                await self.ensure(self.atlas_dir, self.http())
                atlas = await asyncio.to_thread(Atlas.load, self.atlas_dir)
                self._styles = await asyncio.to_thread(StyleMap.load, atlas)
                self._atlas = atlas
            except (AtlasError, OSError, ValueError, KeyError) as e:
                self._atlas_state = "failed"
                self.ledger.append(Kind.error, "", "", error=f"music atlas: {e}")
                return False
            self._atlas_state = "ready"
            return True

    async def start(self) -> None:
        """At harness start, and when music is switched on: tools, atlas, and a scan when none matches the username."""
        self.register()
        if not self.settings.enabled:
            return
        if not await self.prepare():
            return
        if self.settings.username and (self.store.profile() is None or self._scanned_user != self.settings.username):
            await self.scan()

    def _scan_job(self) -> asyncio.Task:
        """The one tracked scan: a request while one runs joins it."""
        if self._scan_task is None or self._scan_task.done():
            self.store.set_scan(state="running", stage="reading", done=0, total=0)
            self._scan_task = self.spawn(self._scan())
        return self._scan_task

    async def scan(self) -> None:
        await asyncio.shield(self._scan_job())

    def start_scan(self) -> dict:
        """Begin a rescan in the background unless one is running; progress is in `status()["scan"]`."""
        self._scan_job()
        return self.store.scan()

    async def _scan(self) -> None:
        if not await self.prepare():
            self.store.set_scan(state="failed", error="atlas")
            return
        user = self.settings.username
        result = await collection.scan(self.discogs(), self.store, self.styles, self.atlas)
        if result["state"] == "done":
            self._scanned_user = user
            self._set_state("ok")  # the scan read the collection with the saved token: it works
            self._start_linking()
        elif result.get("error") in CONNECTION_STATES:
            self._set_state(result["error"])

    def _start_linking(self) -> None:
        """MusicBrainz ids for the collection's artists, in the background, one run at a time."""
        if self._link_task is None or self._link_task.done():
            if self.mb is None:
                self.mb = MusicBrainz(self.http())
            self._link_task = self.spawn(link_artists(self.store, self.mb, self.store.releases()))

    async def refresh(self) -> dict | None:
        """The profile, after a rescan unless one finished in the last 10 minutes.

        A failed scan returns the previous profile with `scan_error`; a rejected token or a rate limit raises."""
        if not self.settings.username:
            return self.store.profile()
        sc = self.store.scan()
        if sc["state"] == "done" and sc["finished_at"]:
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(sc["finished_at"])
            except ValueError:
                age = FRESH
            if age < FRESH and self._scanned_user == self.settings.username:
                return self.store.profile()
        await self.scan()
        sc = self.store.scan()
        profile = self.store.profile()
        if sc["state"] == "failed":
            if sc["error"] in STOP_KINDS:
                raise DiscogsError(sc["error"])
            return {**profile, "scan_error": sc["error"]} if profile else None
        return profile

    # tools --------------------------------------------------------------------

    def register(self) -> bool:
        return MusicTools(self).register(self.tools, self.catalog)

    # settings and token ---------------------------------------------------------

    async def enable(self, settings: MusicSettings, digest: bool = False) -> dict:
        """Apply new settings. Switching on (or `digest=True`) with a token makes the weekly routine; switching off pauses it."""
        before = self.settings
        self.settings = settings
        settings.save(self.vault)
        if settings.username != before.username:
            self._set_state("unset")
        if settings.enabled and self.keys.token and (digest or not before.enabled):
            self.ensure_routine()
        elif not settings.enabled:
            self._pause_routine()
        self.register()
        if settings.enabled and (not before.enabled or settings.username != before.username):
            self.spawn(self.start())
        return self.status()

    def ensure_routine(self) -> Routine | None:
        if self.routines is None:
            return None
        r = self.managed_routine()
        if r is None:
            r = Routine(uuid4().hex[:10], ROUTINE_NAME, BOT_ID, ROUTINE_SKILL, True,
                        Schedule("weekly", [4], "09:00", 0), managed_by=MANAGED_BY)
        else:
            r.enabled = True
        self.routines.upsert(r)
        self.routines.save()
        return r

    def _pause_routine(self) -> None:
        r = self.managed_routine()
        if r is not None and r.enabled:
            r.enabled = False
            self.routines.save()

    def set_token(self, token: str) -> dict:
        had = bool(self.keys.token)
        self.keys = MusicKeys(token=token.strip())
        self.keys.save(self.vault)
        if self._state == "token_invalid":
            self._set_state("ok" if self.settings.username else "unset")
        if self.settings.enabled and not had and self.keys.token:
            self.ensure_routine()
        self.register()
        return self.status()

    def remove_token(self) -> dict:
        self.keys = MusicKeys()
        self.keys.save(self.vault)
        if self._state in ("token_invalid", "token_other_user"):
            self._set_state("ok" if self.settings.username else "unset")
        r = self.managed_routine()
        if r is not None:
            self.routines.delete(r.id)
            self.routines.save()
        self.register()
        return self.status()

    async def connect(self, username: str, token: str | None = None) -> dict:
        """Test a username (and token) without saving either. The saved token fills in when none is given."""
        username = username.strip()
        token = (token or "").strip()
        use = token or self.keys.token
        out: dict[str, Any] = {"state": "ok", "digest_needs_token": not use}
        d = Discogs(username, use, client=self.http())
        try:
            if use:
                who = await d.identity()
                if who.lower() != username.lower():
                    out["state"] = "token_other_user"
                    return out
            out["records"] = await d.collection_size()
        except DiscogsError as e:
            out["state"] = e.kind if e.kind in CONNECTION_STATES else "network"
        if username == self.settings.username and (out["state"] != "token_other_user"):
            self._set_state(out["state"])
        return out

    # map ----------------------------------------------------------------------

    def map(self) -> dict:
        profile = self.store.profile() or {"genres": []}
        related = collection.related_genres(profile, self.atlas, self.settings.focus) if profile["genres"] else []
        return collection.map_payload(profile, related, self.atlas)

    def genre(self, name: str) -> dict | None:
        detail = self.atlas.detail(name)
        if not detail:
            return None
        mine = next((g for g in (self.store.profile() or {}).get("genres", []) if g["genre"] == name), None)
        return {**detail, "holdings": {"share": mine["share"], "owned": mine.get("owned", 0)} if mine
                else {"share": 0, "owned": 0}}

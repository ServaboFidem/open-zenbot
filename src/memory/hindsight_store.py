"""Hindsight as the memory store: the write side.

The exact `Fact` index is a `LocalGraph` under `<root>/hindsight`, so `get`,
`facts` and `entities` never touch the network. Each fact is mirrored into one
Hindsight document (document id = fact id) whose tags name where it lives:
`bot:<id>` for a private fact, one tag per target store for a shared one, and
`shared` until a shared fact has a store. Retains are asynchronous. A per-fact
record (`retained.json`) holds what was last retained (text hash, mission
version, the tags that retain sent) and the operation's state; `reconcile`
polls pending operations, sends what never reached Hindsight, retries failures
(at most `MAX_RETRIES` times per round; a `failed` retain gets a new round on
start, on a key change and every `RETRY_FAILED_S`) and sends tag changes that
waited on a retain. `retained.json` also names the database it describes: when
that changes, or the bank is new, every record is sent again (`_resync`).
A forgotten fact that may be in Hindsight leaves a tombstone record (state
`deleting`) until its document delete goes through, so forget works offline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable
from uuid import NAMESPACE_URL, uuid5

from .graph import SHARED, Fact, LocalGraph, Target
from .hindsight_http import HindsightError, HindsightHTTP, HindsightTimeout
from .stores import PERSONAL, SHARED_TAG, WORLD, is_org

if TYPE_CHECKING:
    from .hindsight_process import HindsightConfig, HindsightServer
    from .registry import PersonEntry

log = logging.getLogger(__name__)

MAX_RETRIES = 3  # after the first send: at most 4 sends per round, then `failed` and a ledger error
RETRY = frozenset({"failed", "cancelled", "not_found"})  # not_found: pruned, or never stored; a replace resend is safe
RUNNING = frozenset({"pending", "processing"})
CLOSE_JOIN_S = 10.0  # how long close() waits for a reconciler pass in flight
RECONCILE_S = 120.0  # the background reconciler's period; also the least time between two restarts of a dead API
RETRY_FAILED_S = 3600.0  # how often the reconciler gives every `failed` retain a new round of MAX_RETRIES
RECALL_CACHE_S = 5.0  # one Hindsight recall serves search and graph_context for this long
TRUNCATED_LEDGER_S = 60.0  # at most one `source_facts_truncated` ledger entry this often
NO_KEY_YET = "The memory dashboard starts once a key is bound."


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _key_digest(key: str) -> str:
    """Tells a new key value from the old one. Kept in memory only: never saved, never logged."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def retain_mission(owner: str, aliases: list[str]) -> str:
    """The bank's extraction guidance: who the owner is, and that their first-person text is about them."""
    others = [a for a in dict.fromkeys(aliases) if a and a.lower() != owner.lower()]
    also = f" (also called {', '.join(others)})" if others else ""
    return (f"This memory belongs to {owner}{also}. Text written in the first person (I, me, my, we, our) by the "
            f"owner is about {owner}: record it as facts about {owner}. Keep track of the people, organisations, "
            f"places and plans in {owner}'s life, and of who each person is to {owner}.")


def _check_store(store: str) -> None:
    """Only a shared store can tag a shared fact: never `bot:<id>`, never another namespace."""
    if store not in (PERSONAL, WORLD) and not is_org(store):
        raise ValueError(f"{store!r} is not a shared store")


def _union(*groups: Iterable[str]) -> list[str]:
    """Order kept, duplicates dropped case-insensitively."""
    seen: set[str] = set()
    out: list[str] = []
    for e in (e for g in groups for e in g):
        if e and e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out


def _new_record(tags: list[str]) -> dict[str, Any]:
    return {"tags": tags, "sent_tags": [], "text_hash": None, "mission": None, "op": None, "state": "unsent", "attempts": 0}


class _Recall:
    """One recall, in flight or finished; callers for the same key wait on `done`."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.results: list[dict[str, Any]] = []
        self.error: BaseException | None = None
        self.at = 0.0


class HindsightGraph:
    kind = "hindsight"

    def __init__(self, root: Path, config: "HindsightConfig | None" = None, *,
                 http: HindsightHTTP | None = None, server: "HindsightServer | None" = None) -> None:
        self.root = Path(root)
        self.config = config
        self.server = server
        self.http = http if http is not None else (HindsightHTTP(config.api_url) if config is not None else None)
        self.ledger: Any = None  # MemoryGraph hands over its ledger
        self.search_timeout_s = float(os.environ.get("ZEN_RECALL_GRAPH_TIMEOUT", 20))  # a search without its own limit
        self.owner_source: Callable[[], PersonEntry | None] = lambda: None
        # a document body to send again once its record dropped it (M2): the harness reads the fact's vault copy
        self.text_source: Callable[[Fact], str | None] = lambda fact: None
        self._graph = LocalGraph(self.root / "hindsight")
        self.path = self.root / "hindsight" / "retained.json"
        # _index: records and mission; memory work only, never held across an HTTP call. Reads take only this.
        # _send: held across HTTP on purpose. It serializes a write's Hindsight exchange against the reconciler's
        # on the same record (a forget racing a resend could otherwise leave a document behind). It blocks no
        # read (reads never take it), and writes are already serialized across HTTP by MemoryGraph._write, so it
        # only makes a write wait for the one reconciler exchange in flight (the reconciler takes it per fact).
        self._index = threading.Lock()
        self._send = threading.RLock()
        self._version, self._mission_text, self._records = 0, "", {}
        # _recall_lock: the recall cache only, memory work, never held across HTTP. Never nested with _send.
        self._recall_lock = threading.Lock()
        self._recalls: dict[tuple, _Recall] = {}
        self._clock: Callable[[], float] = time.monotonic
        self._reconciler: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False
        self._starting = False
        self._start_error = ""  # why the last start failed; cleared by one that succeeds
        self._life = threading.Lock()  # start/close, the rebind thread's restart, and the API supervisor only
        self._key_digest = ""  # of the key the running server was started with (memory only)
        self._rebind_lock = threading.Lock()  # _pending_key and _rebinder only; never held across anything slow
        self._pending_key: tuple[str, str] | None = None  # (ref, value) the rebind thread applies next
        self._rebinder: threading.Thread | None = None
        self._failed_round_at = self._clock()  # the last time failed retains were given a new round
        self._restarted_at = float("-inf")  # the last time the supervisor restarted a dead API
        self._truncated_at = float("-inf")  # the last `source_facts_truncated` ledger entry
        self._no_body_logged: set[str] = set()  # facts whose missing body was logged once already
        self._identity: dict[str, str] | None = None  # which Hindsight database the records describe
        if self.path.exists():
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self._version, self._mission_text = int(d.get("mission", 0)), d.get("mission_text", "")
            self._records = d.get("facts", {})
            self._identity = d.get("identity")

    # -- lifecycle -------------------------------------------------------------

    def _key(self, key_ref: str | None = None) -> str:
        from ..providers.secrets import resolve_secret
        return resolve_secret(key_ref or self.config.key_ref).reveal()

    def start(self) -> None:
        """Boot Hindsight (unless external), wait for it, make the bank, reconcile, then keep reconciling.
        Raises HindsightError when it is not ready within `ready_timeout_s`; the children are stopped first.
        A close() while this waits ends the wait and the children, and start returns. Idempotent.
        Once ready: when the records describe another database (or the bank was just created), every record
        is queued to be sent again (`_resync`), and every `failed` retain gets a new round."""
        with self._life:
            if self._started:
                return
            ev = threading.Event()  # this start's own: a reconciler stuck from an earlier start keeps its set one
            self._stop = ev
            self._starting = True
            try:
                if self.server is not None:
                    key = self._key()
                    self.server.start(key)
                    self._key_digest = _key_digest(key)
                    if not self.server.wait_ready(self.config.ready_timeout_s, ev) and not ev.is_set():
                        raise HindsightError(f"Hindsight was not ready within {self.config.ready_timeout_s:g}s; "
                                             "see hindsight/hindsight.log")
                if not ev.is_set():
                    if self.http is not None:
                        self._resync(bool(self.http.ensure_bank()))
                        self._requeue_failed()
                    self.reconcile()
            except BaseException as e:
                self._start_error = f"{type(e).__name__}: {e}"[:300] if isinstance(e, Exception) else ""
                self._starting = False
                if self.server is not None:
                    self.server.stop()  # idempotent; a start that failed half way may have spawned the API
                raise
            if ev.is_set():  # closed while starting
                self._starting = False
                if self.server is not None:
                    self.server.stop()
                return
            self._start_error = ""
            self._reconciler = threading.Thread(target=self._reconcile_loop, args=(ev,),
                                                name="hindsight-reconciler", daemon=True)
            self._reconciler.start()
            self._started = True  # before _starting clears: a rebind_key in between must never see neither
            self._starting = False

    def _reconcile_loop(self, ev: threading.Event) -> None:
        while not ev.wait(RECONCILE_S):
            try:
                self._tick(ev)
            except Exception:  # noqa: BLE001 - a bad pass must not end the loop
                log.exception("hindsight reconcile failed")

    def _tick(self, ev: threading.Event | None = None) -> None:
        """One reconciler period: restart a dead API, give failed retains a new round when one is due, reconcile."""
        self._supervise(ev)
        if self._clock() - self._failed_round_at >= RETRY_FAILED_S:
            self._requeue_failed()
        self.reconcile()

    def _supervise(self, ev: threading.Event | None = None) -> None:
        """Our API child exited (a crash): start it again, at most once per RECONCILE_S, and ledger it.
        Skipped while start/close/a rebind holds _life: each of those settles the server itself."""
        srv = self.server
        if srv is None or self.config is None or self.config.url or not getattr(srv, "api_exited", lambda: False)():
            return
        if not self._life.acquire(blocking=False):
            return
        try:
            if not self._started or (ev is not None and ev.is_set()) or not srv.api_exited():
                return
            now = self._clock()
            if now - self._restarted_at < RECONCILE_S:
                return
            self._restarted_at = now
            try:
                key = self._key()
                srv.start(key)  # kills the dead child's tree (its Postgres) first
                self._key_digest = _key_digest(key)
                error = None
            except Exception as e:  # noqa: BLE001 - the next period tries again
                error = f"{type(e).__name__}: {e}"[:300]
            if self.ledger is not None:
                self.ledger.append("memory", "", "", event="server_restarted", error=error)
            else:
                log.warning("Hindsight API exited; restarted%s", f" (failed: {error})" if error else "")
        finally:
            self._life.release()

    def close(self) -> None:
        self._stop.set()  # before _life: it ends a start() that is still waiting for Hindsight
        with self._life:
            self._started = False
            t, self._reconciler = self._reconciler, None
            if t is not None and t is not threading.current_thread():
                t.join(timeout=CLOSE_JOIN_S)
            if self.server is not None:
                self.server.stop()

    def rebind_key(self, key_ref: str) -> None:
        """Follow the key bound in Settings unless ZEN_HINDSIGHT_KEY_REF pins one. Never blocks: the event loop
        calls this, and start() holds _life for minutes. Before start() or after close() only the config
        changes. While started (or starting), a new key VALUE (compared by digest, so a new key under the same
        ref counts) restarts the server on a daemon thread; a newer key handed over while that thread waits
        supersedes the older one. A key that does not resolve changes nothing."""
        if self.config is None or self.config.pinned:
            return
        if not (self._started or self._starting):
            self.config.key_ref = key_ref
            return
        try:
            key = self._key(key_ref)
        except Exception:  # noqa: BLE001 - keep the working binding
            log.warning("hindsight: key %r did not resolve; keeping %r", key_ref, self.config.key_ref)
            return
        with self._rebind_lock:
            if self._pending_key is None and (self._rebinder is None or not self._rebinder.is_alive()) \
                    and _key_digest(key) == self._key_digest:
                self.config.key_ref = key_ref  # the same key under another name: nothing to restart
                return
            self._pending_key = (key_ref, key)
            if self._rebinder is None or not self._rebinder.is_alive():
                self._rebinder = threading.Thread(target=self._rebind_run, name="hindsight-rebind", daemon=True)
                self._rebinder.start()

    def _rebind_run(self) -> None:
        while True:
            with self._life:  # waits out a start() in flight: it may have started the server with the old key
                with self._rebind_lock:  # taken only once _life is held: the latest key handed over wins
                    pending, self._pending_key = self._pending_key, None
                    if pending is None:
                        self._rebinder = None
                        return
                key_ref, key = pending
                self.config.key_ref = key_ref
                digest = _key_digest(key)
                if not self._started or self.server is None or digest == self._key_digest:
                    continue
                try:
                    self.server.restart(key)  # not waiting for it: the reconciler catches up once it is ready
                except Exception as e:  # noqa: BLE001 - the API is wanted and not running: the supervisor retries it
                    log.warning("hindsight: restart with the new key failed: %s", e)
                    continue
                self._key_digest = digest
                self._requeue_failed()  # a retain that failed on the old key gets its chance on the new one

    def _wait_rebind(self, timeout: float = 5.0) -> bool:
        """True once no rebind is pending (tests, shutdown)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._rebind_lock:
                t = self._rebinder
            if t is None or not t.is_alive():
                return True
            t.join(min(0.05, max(0.0, deadline - time.monotonic())))
        return False

    def ui_status(self) -> dict[str, str]:
        """The dashboard as the store knows it: off only when it is disabled or the server is external; before any
        key is bound, waiting for one; after a failed start, why it failed; else what the server reports."""
        if self.server is None:
            return {"url": "", "state": "off", "reason": ""}
        status = self.server.ui_status()
        if self.config is None or not self.config.ui or self.config.url or self._started or self._starting:
            return status
        if self._start_error:
            return {**status, "state": "unavailable", "reason": f"Memory did not start: {self._start_error}"}
        return {**status, "state": "unavailable", "reason": NO_KEY_YET}

    # -- re-sending: failed retains, and a database that does not hold what the records say ----------------

    def _requeue_failed(self) -> int:
        """Every `failed` retain goes out again with a fresh MAX_RETRIES (a new epoch: a new operation id)."""
        with self._index:
            self._failed_round_at = self._clock()
            n = 0
            for rec in self._records.values():
                if rec["state"] == "failed":
                    rec.update(state="unsent", op=None, attempts=0, epoch=rec.get("epoch", 0) + 1)
                    n += 1
            if n:
                self._save()
        if n:
            log.info("Hindsight: %d failed retain(s) queued again", n)
        return n

    def _current_identity(self) -> dict[str, str]:
        """The database the records are about: our pg0 instance, or an external server's URL; and the bank."""
        bank = str(getattr(self.http, "bank", ""))
        if self.server is not None:
            from .hindsight_process import instance_name
            return {"server": "pg0:" + instance_name(getattr(self.server, "memory_root", self.root)), "bank": bank}
        return {"server": str(getattr(self.http, "base_url", "")), "bank": bank}

    def _resync(self, created: bool) -> int:
        """Records say `ok` for documents this database may not have: after a vault move, a deleted ~/.pg0, or
        another ZEN_HINDSIGHT_URL, or when the bank was only just created. Then every record goes back to
        `unsent` (a new epoch, so a new operation id) and the mission is sent again. Tombstones stay; in a
        bank just created there is nothing to delete, so they are done. A record that dropped its document
        body gets it again from `text_source` when it is sent. Records written before the identity was
        kept adopt the current one (unless the bank is new)."""
        current = self._current_identity()
        with self._index:
            stored, self._identity = self._identity, current
            moved = stored is not None and stored != current
            if not (created or moved):
                if stored != current:
                    self._save()
                return 0
            n = dropped = 0
            for fid, rec in list(self._records.items()):
                if rec["state"] == "deleting":
                    if created:
                        del self._records[fid]
                        dropped += 1
                    continue
                rec.update(state="unsent", op=None, attempts=0, sent_tags=[], epoch=rec.get("epoch", 0) + 1)
                n += 1
            self._mission_text = ""  # the new bank has no retain mission yet: the next retain sends it
            self._save()
        if (n or dropped) and self.ledger is not None:
            self.ledger.append("memory", "", "", event="store_resync", count=n, tombstones_dropped=dropped,
                               reason="new bank" if created else "another database")
        return n

    def _save(self) -> None:  # caller holds _index
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mission": self._version, "mission_text": self._mission_text,
                                   "identity": self._identity, "facts": self._records}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # -- index (exact, offline) ---------------------------------------------

    def get(self, fact_id: str) -> Fact | None:
        return self._graph.get(fact_id)

    def facts(self, namespace: str) -> list[Fact]:
        return self._graph.facts(namespace)

    def entities(self, namespaces: list[str]) -> set[str]:
        return self._graph.entities(namespaces)

    def stores(self) -> list[str]:
        """The shared stores (`personal`, `org:<slug>`, `world`) some known fact is tagged with."""
        with self._index:
            return sorted({t for fid, r in self._records.items() if self._graph.get(fid) is not None
                           for t in r["tags"] if t in (PERSONAL, WORLD) or is_org(t)})

    def tags_for(self, fact: Fact, targets: list[Target] | None = None) -> list[str]:
        if fact.namespace != SHARED:
            return [fact.namespace]  # private: its own bot's tag, whatever the targets say
        stores = list(dict.fromkeys(t.store for t in targets or []))
        for s in stores:
            _check_store(s)
        return stores or [SHARED_TAG]

    def has_item(self, fact_id: str, store: str) -> bool:
        """`store` is in the fact's tags and its last retain ran under the current mission
        (a renamed owner counts as a new mission, though it is sent only by the next retain)."""
        owner = self.owner_source()
        wanted = retain_mission(owner.name, list(owner.aliases)) if owner is not None else None
        with self._index:
            rec = self._records.get(fact_id)
            if rec is None or store not in rec["tags"] or rec["mission"] != self._version:
                return False
            return wanted is None or wanted == self._mission_text

    # -- writes ---------------------------------------------------------------

    def put(self, fact: Fact, targets: list[Target] | None = None) -> None:
        tags = self.tags_for(fact, targets)
        hints = _union(*(t.hints for t in targets or [])) if fact.namespace == SHARED else []
        with self._send:
            with self._index:
                rec = self._records.setdefault(fact.id, _new_record(tags))
                if rec["state"] == "deleting":  # the id comes back: its retain replaces the old document
                    rec = self._records[fact.id] = _new_record(tags) | {"sent": True}
                rec["tags"] = tags
                rec.pop("hints", None)
                if hints:
                    rec["hints"] = hints
                self._save()  # the record first: a crash before the fact leaves a record reconcile drops
                self._graph.put(fact)
            self._sync(fact.id, fact.text)

    def attach(self, fact_id: str, text: str, target: Target) -> None:
        """Add `target.store` to a shared fact's tags (dropping the `shared` placeholder) and bring
        Hindsight in line: a new text or mission retains `text` again, else the tags are patched."""
        _check_store(target.store)
        with self._send:
            with self._index:
                fact = self._graph.get(fact_id)
                if fact is None:
                    raise KeyError(fact_id)
                if fact.namespace != SHARED:
                    raise ValueError(f"fact {fact_id} is private to {fact.namespace}; it cannot join a shared store")
                rec = self._records.setdefault(fact_id, _new_record(self.tags_for(fact)))
                tags = [t for t in rec["tags"] if t != SHARED_TAG]
                rec["tags"] = tags if target.store in tags else [*tags, target.store]
                if hints := _union(rec.get("hints", []), target.hints):
                    rec["hints"] = hints
                self._save()
            self._sync(fact_id, text)

    def delete(self, fact_id: str) -> bool:
        """Forget a fact; works with Hindsight down. A fact no retain ever reached goes locally. Otherwise
        a tombstone record (state `deleting`) is saved first, then the fact leaves the index, then the
        pending retain is cancelled and the document deleted; if that fails, the reconciler finishes it."""
        with self._send:
            with self._index:
                fact, rec = self._graph.get(fact_id), self._records.get(fact_id)
                if fact is None and (rec is None or rec["state"] == "deleting"):
                    return False
                rec = rec or {}
                if not (rec.get("sent") or rec.get("op") or rec.get("sent_tags")):  # never sent at all
                    self._records.pop(fact_id, None)
                    self._save()
                    self._graph.delete(fact_id)
                    return True
                op = rec.get("op") if rec.get("state") in ("pending", "unsent") else None
                self._records[fact_id] = {"tags": [], "sent_tags": rec.get("sent_tags", []), "text_hash": None,
                                          "mission": None, "op": op, "state": "deleting",
                                          "attempts": rec.get("attempts", 0), "sent": True}
                self._save()  # the tombstone before the fact goes: a crash in between still deletes remotely
                self._graph.delete(fact_id)
            if self._forget_remote(fact_id, op):
                with self._index:
                    self._records.pop(fact_id, None)
                    self._save()
            return fact is not None

    def drop_shared_datasets(self) -> int:
        """A rebuild: forget what was retained for every shared fact, so its next attach retains again."""
        with self._index:
            n = 0
            for fid, rec in self._records.items():
                if (f := self._graph.get(fid)) is not None and f.namespace == SHARED:
                    # the epoch keeps the next retain's operation id new (Hindsight returns the old operation
                    # for a reused id); `text` stays, so an unsent document body still goes out as the body
                    rec.update(text_hash=None, mission=None, epoch=rec.get("epoch", 0) + 1)
                    n += 1
            self._save()
            return n

    def reconcile(self) -> dict[str, int]:
        """One pass over the records: send unsent retains, poll pending ones, retry failures, send
        waiting tag changes. Stops at the first sign Hindsight is unreachable; never raises for it."""
        out = dict.fromkeys(("sent", "completed", "retried", "failed", "patched", "forgotten"), 0)
        if self.http is None:
            return out
        with self._index:
            fids = list(self._records)
        try:
            for fid in fids:
                with self._send:
                    if not self._reconcile_one(fid, out):
                        break
        except HindsightError as e:
            log.info("Hindsight reconcile stopped: %s", e)
        return out

    def _reconcile_one(self, fid: str, out: dict[str, int]) -> bool:
        """False when Hindsight could not be reached (stop the pass). Caller holds _send."""
        with self._index:
            rec = self._records.get(fid)
            if rec is None:
                return True
            if rec["state"] != "deleting" and self._graph.get(fid) is None:  # a put that died before its fact
                del self._records[fid]
                self._save()
                return True
            state, op, n = rec["state"], rec["op"], rec["attempts"]
        if state == "deleting":  # a forgotten fact: finish the delete, never resend it
            if not self._forget_remote(fid, op):
                return False
            with self._index:
                self._records.pop(fid, None)
                self._save()
            out["forgotten"] += 1
            return True
        if state == "pending":
            status = self.http.operation(op)
            if status in RETRY and n > MAX_RETRIES:
                self._set(fid, state="failed")
                out["failed"] += 1
                err = f"retain {status} after {n} attempts"
                if self.ledger is not None:
                    self.ledger.append("error", "", "", where="hindsight_retain", fact_id=fid, op=op, error=err)
                else:
                    log.warning("Hindsight %s: %s", fid, err)
                return True
            if status in RETRY:
                state = "retry"
            elif status == "completed":
                with self._index:
                    if (done := self._records.get(fid)) is not None:
                        done["state"] = "ok"
                        done.pop("text", None)  # in Hindsight now; a resend reads the body again (`text_source`)
                        self._save()
                out["completed"] += 1
                state = "ok"
            else:
                return True  # still running
        if state in ("unsent", "retry"):
            if not self._push(fid, self._mission()):
                return False
            out["sent" if state == "unsent" else "retried"] += 1
        elif state == "ok":
            with self._index:
                drift = list(rec["tags"]) if rec["tags"] != rec["sent_tags"] else None
            if drift is not None and self._patch(fid, drift):
                out["patched"] += 1
        return True

    # -- Hindsight calls (caller holds _send, never _index) -------------------

    def _sync(self, fid: str, text: str) -> None:
        """Retain when the text or mission changed, the last retain failed, or it never went out;
        otherwise patch drifted tags, or leave them to the reconciler while a retain is pending."""
        try:
            mission, up = self._mission(), self.http is not None
        except HindsightError as e:
            log.info("Hindsight mission not sent: %s", e)
            with self._index:
                mission, up = self._version, False
        h = text_hash(text)
        stale, patch = None, None
        with self._index:
            rec, fact = self._records[fid], self._graph.get(fid)
            same = rec["text_hash"] == h and rec["mission"] == mission
            retain = not same or rec["state"] in ("failed", "unsent")
            if retain:
                if not same:
                    rec["attempts"] = 0
                    stale = rec["op"] if rec["state"] == "pending" else None  # superseded: do not let it land last
                    rec.update(text_hash=h, mission=mission, state="unsent", op=None)
                rec.pop("text", None)
                rec.pop("body", None)
                if fact is not None and text != fact.text:
                    # the reconciler resends this, not the fact's own text; `body` outlives the text (dropped once
                    # the retain completes), so a later resend knows to fetch the document body again
                    rec.update(text=text, body=True)
                self._save()
            elif rec["state"] == "ok" and rec["tags"] != rec["sent_tags"]:
                patch = list(rec["tags"])
        if not up:
            return
        if stale:
            self._cancel(stale)
        if retain:
            self._push(fid, mission)
        elif patch is not None:
            self._patch(fid, patch)

    def _mission(self) -> int:
        """The mission version a retain goes out under, sending a changed mission first."""
        owner = self.owner_source()
        with self._index:
            if owner is None:
                return self._version
            text = retain_mission(owner.name, list(owner.aliases))
            if text == self._mission_text:
                return self._version
        if self.http is None:
            raise HindsightError("Hindsight is not configured")
        self.http.set_config({"retain_mission": text})
        with self._index:
            if text != self._mission_text:
                self._mission_text, self._version = text, self._version + 1
                self._save()
            return self._version

    def _push(self, fid: str, mission: int) -> bool:
        """Send the record's retain; True when Hindsight accepted it. A record whose document body was dropped
        (it completed once) gets it from `text_source` (the vault copy), else sends the fact's own text."""
        with self._index:
            rec, fact = self._records.get(fid), self._graph.get(fid)
            refetch = fact if rec is not None and rec.get("body") and "text" not in rec else None
        body = None
        if refetch is not None:
            try:
                body = self.text_source(refetch)  # a file read: outside _index
            except Exception as e:  # noqa: BLE001 - the fact's own text still goes out
                log.info("Hindsight: the body of %s could not be read: %s", fid, e)
        with self._index:
            rec, fact = self._records.get(fid), self._graph.get(fid)
            if rec is None or fact is None:
                return True
            if refetch is not None and not body and fid not in self._no_body_logged:
                self._no_body_logged.add(fid)
                log.info("Hindsight: no document body for %s (vault copy gone?); sending the fact's own text", fid)
            text = rec.get("text") or body or fact.text
            h = text_hash(text)
            if (rec["text_hash"], rec["mission"]) != (h, mission):
                rec.update(text_hash=h, mission=mission, attempts=0)
            n, tags = rec["attempts"], list(rec["tags"])
            entities = _union(fact.entities, rec.get("hints", []))
        # Hindsight returns the original operation for a reused id, so each resend after a failure gets its own
        epoch = rec.get("epoch", 0)  # bumped by a rebuild, a resync, and each new round of a failed retain
        key = f"{fid}:{h}:{mission}" + (f":r{epoch}" if epoch else "") + (f":{n}" if n else "")
        meta = {"namespace": fact.namespace, "tier": str(fact.tier), "bot_id": fact.bot_id, "source": fact.source, "at": fact.at}
        op_id = str(uuid5(NAMESPACE_URL, key))
        try:
            if self.http is None:
                raise HindsightError("Hindsight is not configured")
            op =self.http.retain(fid, text, tags, metadata=meta, entities=entities, operation_id=op_id)
        except HindsightError as e:
            log.info("Hindsight retain of %s not sent: %s", fid, e)
            # a timeout may have been taken: keep its id so a forget can cancel it (a resend reuses it anyway)
            maybe = {"sent": True, "op": op_id} if isinstance(e, HindsightTimeout) else {"op": None}
            self._set(fid, state="unsent", **maybe)
            return False
        self._set(fid, state="pending", op=op, sent_tags=tags, attempts=n + 1, sent=True)
        return True

    def _patch(self, fid: str, tags: list[str]) -> bool:
        try:
            self.http.patch_tags(fid, tags)  # type: ignore[union-attr]
        except HindsightError as e:
            log.info("Hindsight tags of %s not patched: %s", fid, e)
            return False
        self._set(fid, sent_tags=tags)
        return True

    def _forget_remote(self, fid: str, op: str | None) -> bool:
        """Cancel the fact's retain and delete its document; True once nothing of it can come back."""
        if self.http is None:
            return False
        try:
            if op:
                self._cancel(op)
            self.http.delete_document(fid)
            if op and self.http.operation(op) in RUNNING:  # it could still write the document: delete again later
                return False
        except HindsightError as e:
            log.info("Hindsight delete of %s not done yet: %s", fid, e)
            return False
        return True

    def _cancel(self, op: str) -> None:
        try:
            if self.http is not None:
                self.http.cancel(op)
        except HindsightError:
            pass  # already terminal, unknown, or unreachable: the document delete or replace still follows

    def _set(self, fid: str, **fields: Any) -> None:
        with self._index:
            if (rec := self._records.get(fid)) is not None:
                rec.update(fields)
                self._save()

    # -- reads ----------------------------------------------------------------

    def scope(self, namespaces: list[str]) -> list[str]:
        """The Hindsight tags a read over `namespaces` may see: each bot's own, and for `shared` every shared store."""
        tags = [n for n in namespaces if n.startswith("bot:")]
        if SHARED in namespaces:
            tags += [SHARED_TAG, *self.stores()]
        return list(dict.fromkeys(tags))

    def _recall(self, query: str, namespaces: list[str], timeout: float | None) -> list[dict[str, Any]]:
        """The raw results of one recall per (query, namespaces); concurrent and recent callers share it.
        Takes only _recall_lock (briefly) and the network, never _send or _index while waiting."""
        tags = self.scope(namespaces)
        if not tags or self.http is None:
            return []
        key = (query, tuple(sorted(namespaces)))
        with self._recall_lock:
            now = self._clock()
            self._recalls = {k: r for k, r in self._recalls.items()
                             if not r.done.is_set() or (r.error is None and now - r.at < RECALL_CACHE_S)}
            entry = self._recalls.get(key)
            mine = entry is None
            if mine:
                entry = self._recalls[key] = _Recall()
        if not mine:
            if not entry.done.wait(timeout):
                raise HindsightTimeout(f"recall still running after {timeout:g} s")
            if entry.error is not None:
                raise entry.error
            return entry.results
        try:
            entry.results = self.http.recall(query, tags, timeout=timeout)
            entry.at = self._clock()
            if getattr(entry.results, "source_facts_truncated", False):
                self._note_truncated(entry.results)
        except BaseException as e:  # waiters get the error too; an error is never cached
            entry.error = e
            with self._recall_lock:
                if self._recalls.get(key) is entry:
                    del self._recalls[key]
            raise
        finally:
            entry.done.set()
        return entry.results

    def _note_truncated(self, results: list[dict[str, Any]]) -> None:
        """Hindsight cut some observations' source facts to its token budget: those facts cannot stand in for
        their observation in `search`. Ledgered at most once per TRUNCATED_LEDGER_S."""
        with self._recall_lock:
            now = self._clock()
            if now - self._truncated_at < TRUNCATED_LEDGER_S:
                return
            self._truncated_at = now
        if self.ledger is not None:
            self.ledger.append("memory", "", "", event="source_facts_truncated",
                               observations=sum(1 for r in results if r.get("type") == "observation"))

    def search(self, query: str, namespaces: list[str], limit: int = 10, timeout: float | None = None) -> list[Fact]:
        """Facts in result order. A consolidated fact comes back as an observation alone: the observation (if
        its own tags are all in scope) stands in for its source facts, each filtered as a direct hit is."""
        scope = set(self.scope(namespaces))
        in_scope = lambda tags: bool(tags) and all(t in scope for t in tags)  # noqa: E731 - as graph_context's
        out: list[Fact] = []
        seen: set[str] = set()
        for r in self._recall(query, namespaces, self.search_timeout_s if timeout is None else timeout):
            if r.get("type") == "observation":
                if not in_scope(r.get("tags")):
                    continue
                hits = [s for s in r.get("source_facts") or [] if in_scope(s.get("tags"))]
            else:
                hits = [r]
            for h in hits:
                fact = self._fact_of(h)
                if fact is None or fact.namespace not in namespaces or fact.id in seen:  # privacy: asked-for only
                    continue
                seen.add(fact.id)
                out.append(fact)
                if len(out) >= limit:
                    return out
        return out

    def _fact_of(self, r: dict[str, Any]) -> Fact | None:
        """The indexed fact behind one result; an unknown document is ledgered."""
        did = r.get("document_id")
        fact = self._graph.get(did) if did else None
        if fact is None and self.ledger is not None:
            self.ledger.append("memory", "", "", event="hindsight_unknown_document", document_id=did,
                               result_id=r.get("id"))
        return fact

    def _cites_forgotten(self, r: dict[str, Any]) -> bool:
        """An observation one of whose source facts comes from a document we forgot (a tombstone) or never had.
        Without source facts in the response there is nothing to check: it is kept."""
        dids = [d for s in r.get("source_facts") or [] if (d := s.get("document_id"))]
        if not dids:
            return False
        with self._index:
            return any(self._graph.get(d) is None or (self._records.get(d) or {}).get("state") == "deleting"
                       for d in dids)

    def graph_context(self, query: str, namespaces: list[str], limit: int = 12, timeout: float | None = None) -> list[str]:
        scope = set(self.scope(namespaces))
        out: list[str] = []
        for r in self._recall(query, namespaces, timeout):
            tags = r.get("tags") or []
            text = (r.get("text") or "").strip()
            if r.get("type") == "observation" and text and tags and all(t in scope for t in tags) \
                    and not self._cites_forgotten(r):
                out.append(text)
                if len(out) >= limit:
                    break
        return out

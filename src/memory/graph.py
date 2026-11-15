"""The memory graph.

One graph, split by namespace: `shared` holds user preferences (tier
`user_pref`) and project facts (tier `project`); `bot:<id>` is one
bot's private namespace (tier `private`). The session cache (tier
`session`) is a plain in-process append with no model call. Every
write runs the exclusion filter; a refused write is in the ledger and
nothing lands. Retrieval routes on its own: a query that names a known
entity walks the graph from that entity (facts sharing an entity are
linked, one hop out); any other query ranks by token overlap. The
store behind the graph is swappable: `HindsightGraph` is the default (an
HTTP client of a Hindsight server); `LocalGraph` is the no-model JSON graph
under the vault (`ZEN_MEMORY_BACKEND=local`).
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .exclusion import ExclusionError, exclusion

__all__ = ["Fact", "Hits", "LocalGraph", "MemoryGraph", "SessionCache", "Stopped", "Target", "Tier", "SHARED", "private_ns", "make_store"]

SHARED = "shared"
TOKEN = re.compile("[a-z0-9]+")
STOP = frozenset("a an and are as at be by do for from how i in is it of on or the this to we what when where which who with you our".split())


class Stopped(RuntimeError):
    """A multi-store write stopped between stores (shutdown); the stores it wrote stand."""


def private_ns(bot_id: str) -> str:
    return f"bot:{bot_id}"


class Tier(StrEnum):
    user_pref = "user_pref"  # tier 1, shared
    project = "project"  # tier 2, shared
    private = "private"  # one bot's own
    session = "session"  # tier 3, cache


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Fact:
    text: str
    namespace: str
    tier: Tier
    bot_id: str = ""
    entities: list[str] = field(default_factory=list)  # lower-cased names this fact is about
    source: str = ""  # session id or vault pointer
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "namespace": self.namespace, "tier": str(self.tier), "bot_id": self.bot_id,
                "entities": list(self.entities), "source": self.source, "at": self.at}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Fact":
        return cls(text=d["text"], namespace=d["namespace"], tier=Tier(d["tier"]), bot_id=d.get("bot_id", ""),
                   entities=list(d.get("entities") or []), source=d.get("source", ""), id=d["id"], at=d.get("at") or _now())


@dataclass
class Hits:
    facts: list[Fact]
    route: str  # "semantic" or "walk"
    seeds: list[str] = field(default_factory=list)  # entities the walk started from


@dataclass(frozen=True)
class Target:
    """One shared store a fact is written into (`personal`, `org:<slug>`,
    `world`). `hints` are entity names (registry people, the org) the
    store should link the text to."""

    store: str
    hints: tuple[str, ...] = ()


class GraphStore(Protocol):
    kind: str

    def put(self, fact: Fact, targets: list[Target] | None = None) -> None: ...
    def get(self, fact_id: str) -> Fact | None: ...
    def delete(self, fact_id: str) -> bool: ...
    def facts(self, namespace: str) -> list[Fact]: ...
    def entities(self, namespaces: list[str]) -> set[str]: ...


class LocalGraph:
    """JSON file per graph. Small on purpose: a few thousand facts read
    whole is cheaper than a database and leaves nothing to install."""

    kind = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "graph.json"
        self._facts: dict[str, Fact] = {}
        self._index = threading.Lock()  # reads may run while another thread writes
        if self.path.exists():
            for d in json.loads(self.path.read_text(encoding="utf-8")).get("facts", []):
                f = Fact.from_dict(d)
                self._facts[f.id] = f

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"facts": [f.to_dict() for f in self._facts.values()]}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def put(self, fact: Fact, targets: list[Target] | None = None) -> None:  # one store: targets do not apply
        with self._index:
            self._facts[fact.id] = fact
            self._save()

    def get(self, fact_id: str) -> Fact | None:
        with self._index:
            return self._facts.get(fact_id)

    def delete(self, fact_id: str) -> bool:
        with self._index:
            if self._facts.pop(fact_id, None) is None:
                return False
            self._save()
            return True

    def facts(self, namespace: str) -> list[Fact]:
        with self._index:
            return [f for f in self._facts.values() if f.namespace == namespace]

    def entities(self, namespaces: list[str]) -> set[str]:
        with self._index:
            return {e for f in self._facts.values() if f.namespace in namespaces for e in f.entities}


def make_store(root: Path, backend: str | None = None) -> GraphStore:
    """Hindsight is the store (an HTTP client; the server is a child process
    the store starts, or an external one named by ZEN_HINDSIGHT_URL).
    `local` keeps the no-model JSON graph, which the offline suite pins."""
    backend = backend or os.environ.get("ZEN_MEMORY_BACKEND", "hindsight")
    if backend == "hindsight":
        from src.memory.hindsight_process import HindsightConfig, HindsightServer
        from src.memory.hindsight_store import HindsightGraph

        config = HindsightConfig.from_env()
        server = None if config.url else HindsightServer(config, root)  # an external server is not ours to start
        return HindsightGraph(root, config, server=server)  # type: ignore[return-value]
    if backend == "local":
        return LocalGraph(root)
    raise ValueError(f"unknown memory backend {backend!r}: use 'hindsight' or 'local'")


class SessionCache:
    """Tier 3. In process, per session, append only, no model call. The
    exclusion filter still runs: a secret never sits in memory, even
    briefly."""

    def __init__(self) -> None:
        self._by_session: dict[str, list[Fact]] = {}

    def add(self, session_id: str, bot_id: str, text: str, entities: list[str] | None = None) -> Fact:
        exclusion.require({"kind": "memory", "text": text}, "ingest")
        f = Fact(text=text, namespace=f"session:{session_id}", tier=Tier.session, bot_id=bot_id,
                 entities=[e.lower() for e in entities or []], source=session_id)
        self._by_session.setdefault(session_id, []).append(f)
        return f

    def facts(self, session_id: str) -> list[Fact]:
        return list(self._by_session.get(session_id, []))

    def get(self, fact_id: str) -> Fact | None:
        return next((f for fs in self._by_session.values() for f in fs if f.id == fact_id), None)

    def remove(self, fact_id: str) -> bool:
        for fs in self._by_session.values():
            for i, f in enumerate(fs):
                if f.id == fact_id:
                    del fs[i]
                    return True
        return False

    def clear(self, session_id: str) -> int:
        return len(self._by_session.pop(session_id, []))


class MemoryGraph:
    """Durable tiers over a store, with the filter at every write and the
    ledger for every remember, forget, and refusal."""

    def __init__(self, store: GraphStore, ledger: Any) -> None:
        self.store = store
        self.ledger = ledger
        if getattr(store, "ledger", False) is None:  # a store that ledgers its own background errors (Hindsight)
            store.ledger = ledger  # type: ignore[attr-defined]
        # Writes are serialized: an import writes from a worker thread while the API or a bot may write too.
        # Reads never take this lock: a store write holds it for tens of seconds, and a read waiting on it
        # from the event loop froze the whole harness. Each store guards its own index for concurrent reads.
        self._write = threading.RLock()

    @property
    def kind(self) -> str:
        return self.store.kind

    def remember(self, text: str, namespace: str, tier: Tier, *, bot_id: str = "", entities: list[str] | None = None,
                 source: str = "", session_id: str = "", targets: list[Target] | None = None) -> Fact:
        self.require_clean(text, namespace, bot_id=bot_id, entities=entities, session_id=session_id, tier=str(tier))
        f = Fact(text=text, namespace=namespace, tier=tier, bot_id=bot_id, entities=[e.lower() for e in entities or []], source=source)
        try:
            with self._write:
                self.store.put(f, targets)
        except Exception as e:
            self.ledger.append("memory", bot_id, session_id, event="failed", namespace=namespace, tier=str(tier),
                               error=f"{type(e).__name__}: {e}"[:300])
            raise
        self.ledger.append("memory", bot_id, session_id, event="remember", fact_id=f.id, namespace=namespace, tier=str(tier),
                           entities=f.entities, chars=len(text))
        return f

    def require_clean(self, text: str, namespace: str, *, bot_id: str = "", entities: list[str] | None = None,
                      session_id: str = "", **ledger_data: Any) -> None:
        """The exclusion filter every write passes: a block is ledgered as
        `refused` and raises `ExclusionError`. Callers that send the text
        anywhere first (a routing call) run it before that, too."""
        verdict = exclusion.check_ingest({"kind": "memory", "text": text, "entities": list(entities or [])})
        if not verdict:
            self.ledger.append("memory", bot_id, session_id, event="refused", namespace=namespace, reasons=verdict.reasons, **ledger_data)
            raise ExclusionError("blocked at ingest: " + ", ".join(verdict.reasons))

    def attach(self, fact_id: str, text: str, targets: list[Target], *, bot_id: str = "", session_id: str = "",
               stop: threading.Event | None = None) -> None:
        """Extract more text (a document's full markdown) into the shared
        stores of an existing fact. Idempotent per store: a store the fact
        already holds an item in is skipped, so a retry after a partial
        failure writes only what is missing. Only a store with its own graph
        (Hindsight) can attach. `stop` (shutdown) is checked before each store:
        once set, the store in flight finishes and `Stopped` is raised."""
        own = getattr(self.store, "attach", None)
        if own is None:
            raise TypeError(f"the {self.kind} memory store has no attach; extraction needs the Hindsight store")
        self.require_clean(text, SHARED, bot_id=bot_id, session_id=session_id, fact_id=fact_id)
        has = getattr(self.store, "has_item", lambda fid, store: False)
        done: list[str] = []
        try:
            with self._write:
                for t in targets:
                    if has(fact_id, t.store):
                        continue
                    if stop is not None and stop.is_set():
                        raise Stopped(f"stopped before the {t.store} store")
                    own(fact_id, text, t)
                    done.append(t.store)
        except Stopped:
            self.ledger.append("memory", bot_id, session_id, event="attach_stopped", fact_id=fact_id, stores=done)
            raise
        except Exception as e:
            self.ledger.append("memory", bot_id, session_id, event="failed", namespace=SHARED, fact_id=fact_id,
                               stores=done, error=f"{type(e).__name__}: {e}"[:300])
            raise
        self.ledger.append("memory", bot_id, session_id, event="attach", fact_id=fact_id, stores=done,
                           skipped=[t.store for t in targets if t.store not in done], chars=len(text))

    def drop_shared(self) -> int:
        """Drop the store's shared datasets (a rebuild; `HindsightGraph.drop_shared_datasets`)
        under the write lock, so no shared write lands in one mid-drop."""
        own = getattr(self.store, "drop_shared_datasets", None)
        if own is None:
            raise TypeError(f"the {self.kind} memory store has no shared datasets to drop")
        with self._write:
            return own()

    def forget(self, fact_id: str, *, bot_id: str = "", session_id: str = "") -> bool:
        with self._write:
            ok = self.store.delete(fact_id)
        self.ledger.append("memory", bot_id, session_id, event="forget", fact_id=fact_id, found=ok)
        return ok

    def get(self, fact_id: str) -> Fact | None:
        return self.store.get(fact_id)

    def facts(self, namespace: str) -> list[Fact]:
        return self.store.facts(namespace)

    # -- retrieval ----------------------------------------------------------

    def close(self, wait: float = 30.0) -> None:
        """Release the store at shutdown (Hindsight's server
        process); a store with nothing to release does nothing. Waits for an
        in-flight write first, so a fact is never cut off mid-save."""
        own = getattr(self.store, "close", None)
        if own is None:
            return
        if not self._write.acquire(timeout=wait):  # a write stuck past this: closing under it is worse than not closing
            self.ledger.append("memory", "", "", event="close_skipped", reason=f"a write was still running after {wait:g} s")
            return
        try:
            own()
        finally:
            self._write.release()

    def graph_context(self, query: str, namespaces: list[str], limit: int = 12, timeout: float | None = None) -> list[str]:
        """Sentences from the store's own knowledge graph, when it has one
        (Hindsight); a store without one (local JSON) has nothing to add."""
        own = getattr(self.store, "graph_context", None)
        return own(query, namespaces, limit=limit, timeout=timeout) if own is not None else []

    def search(self, query: str, namespaces: list[str], limit: int = 10) -> Hits:
        """Routes on its own: a query naming a known entity walks the graph
        from it; otherwise semantic (token overlap) ranking."""
        return self._search(query, namespaces, limit)

    def _search(self, query: str, namespaces: list[str], limit: int) -> Hits:
        q = tokens(query)
        qs = set(q)
        known = sorted(self.store.entities(namespaces))
        seeds = [e for e in known if (et := tokens(e)) and all(t in qs for t in et)]  # a whole name, single or multi word
        if seeds:
            return Hits(self._walk(seeds, namespaces, limit), "walk", seeds)
        if (own := getattr(self.store, "search", None)) is not None:  # a store with its own embeddings ranks
            try:
                return Hits(own(query, namespaces, limit), "semantic")
            except Exception as e:  # the store's search is down: rank the index instead
                self.ledger.append("error", "", "", where="memory_search", error=f"{type(e).__name__}: {e}"[:300])
        return Hits(self._semantic(q, namespaces, limit), "semantic")

    def _semantic(self, q: list[str], namespaces: list[str], limit: int) -> list[Fact]:
        qs = set(q)
        scored = []
        for ns in namespaces:
            for f in self.store.facts(ns):
                ft = set(tokens(f.text)) | set(f.entities)
                overlap = len(qs & ft)
                if overlap:
                    scored.append((-overlap / (len(qs | ft) or 1), f.at, f))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [f for _, _, f in scored[:limit]]

    def _walk(self, seeds: list[str], namespaces: list[str], limit: int, depth: int = 1) -> list[Fact]:
        """Facts about the seed entities first, then facts one hop out
        through the entities those facts mention."""
        pool = [f for ns in namespaces for f in self.store.facts(ns) if f.entities]
        pool.sort(key=lambda f: f.at)
        out: list[Fact] = []
        seen: set[str] = set()
        frontier = set(seeds)
        for _ in range(depth + 1):
            hop = [f for f in pool if f.id not in seen and frontier & set(f.entities)]
            for f in hop:
                seen.add(f.id)
                out.append(f)
            frontier = {e for f in hop for e in f.entities} - frontier
            if not frontier or len(out) >= limit:
                break
        return out[:limit]

"""ProfileMemory: the one way into the `shared` namespace's stores.

A shared text is routed (unless the caller names its stores), any new
organization the owner holds is registered, and each store gets its own
target: the names of the registry people in the text (and, for an org store,
the org) travel as hints the store links the text to.
Nothing goes into `personal` or an org store until the owner's name is known:
callers get `OwnerUnknown` and queue the text instead.

Both entry points are async and run the blocking store writes in a worker
thread, so a store write never stalls the event loop.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from .graph import SHARED, Fact, MemoryGraph, Target, Tier
from .stores import PERSONAL, WORLD, is_org
from .registry import OrgRegistry, PeopleRegistry, PersonEntry
from .router import OWNER_RELATIONS, StoreRouter


class OwnerUnknown(RuntimeError):
    """A write needs the owner's name (personal or org store) and none is set yet."""


class ProfileMemory:
    def __init__(self, graph: MemoryGraph, people: PeopleRegistry, orgs: OrgRegistry, router: StoreRouter, ledger: Any) -> None:
        self.graph = graph
        self.people = people
        self.orgs = orgs
        self.router = router
        self.ledger = ledger

    def _owner_for(self, stores: list[str]) -> PersonEntry | None:
        owner = self.people.owner()
        if owner is None and any(s == PERSONAL or is_org(s) for s in stores):
            raise OwnerUnknown("the owner's name is not known yet; personal and org writes wait for it")
        return owner

    def targets_for(self, stores: list[str], text: str) -> list[Target]:
        owner = self._owner_for(stores)
        people = self.people.for_text(text) if owner is not None else []
        names = tuple(p.name for p in people)  # the store links the text to the names in it
        out = []
        for s in stores:
            if s == PERSONAL or s == WORLD:
                out.append(Target(s, hints=names))
            elif is_org(s):
                org = self.orgs.get(s.removeprefix("org:"))
                if org is None:
                    raise ValueError(f"no organization registered for store {s!r}")
                out.append(Target(s, hints=(*names, org.name)))
            else:
                raise ValueError(f"unknown store {s!r}")
        return out

    async def plan_stores(self, text: str, stores: list[str] | None = None) -> list[str]:
        """Route (when the caller named no stores) and register the owner's
        new orgs; returns the stores. Raises `OwnerUnknown` before registering
        anything when a store needs the owner. Callers that must pin the
        routing across retries (the extraction queue) persist the result."""
        new_orgs: list[tuple[str, str]] = []
        if stores is None:
            owner = self.people.owner()
            if owner is None:  # routing may pick the personal store: wait before spending the call
                raise OwnerUnknown("the owner's name is not known yet; routed writes wait for it")
            route = await self.router.route(text, self.orgs.all(), owner.name)
            stores, new_orgs = route.stores, route.new_orgs
        self._owner_for(stores)  # before any org is registered
        for name, relation in new_orgs:
            if relation in OWNER_RELATIONS:
                self.orgs.add(name, relation)  # type: ignore[arg-type]
        return stores

    async def _plan(self, text: str, stores: list[str] | None) -> list[Target]:
        """`plan_stores`, then one target per store."""
        return self.targets_for(await self.plan_stores(text, stores), text)

    async def remember_shared(self, text: str, *, bot_id: str, source: str, entities: list[str] | None = None,
                              stores: list[str] | None = None, tier: Tier = Tier.project, session_id: str = "") -> Fact:
        # the filter runs before routing too: a blocked text must not reach the routing model
        self.graph.require_clean(text, SHARED, bot_id=bot_id, entities=entities, session_id=session_id, tier=str(tier))
        targets = await self._plan(text, stores)
        fact = await asyncio.to_thread(self.graph.remember, text, SHARED, tier, bot_id=bot_id, entities=entities,
                                       source=source, session_id=session_id, targets=targets)
        return fact

    def preflight(self, fact_id: str, text: str) -> None:
        """The checks `extract` makes before spending a routing call: the store
        can attach (`TypeError`: only Hindsight can), the fact exists (`KeyError`)
        and the text is clean (`ExclusionError`). Callers that route on their
        own (the extraction queue) run it first, so blocked text never reaches
        the routing model nor registers an org."""
        if getattr(self.graph.store, "attach", None) is None:
            raise TypeError(f"the {self.graph.kind} memory store has no attach; extraction needs the Hindsight store")
        if self.graph.get(fact_id) is None:
            raise KeyError(fact_id)
        self.graph.require_clean(text, SHARED, fact_id=fact_id)

    async def extract(self, fact_id: str, text: str, stores: list[str] | None,
                      stop: threading.Event | None = None) -> list[str]:
        """Extract `text` into each routed store's graph, tied to an existing
        fact (a document's full text next to its summary fact). Safe to retry:
        a store the fact already holds is skipped (`MemoryGraph.attach`).
        Raises before any routing call is spent when the store cannot attach
        (`TypeError`: only Hindsight can), the fact is unknown (`KeyError`) or the
        text is blocked (`ExclusionError`). Returns every store in the plan,
        skipped ones included, so a retry can pass them back as `stores`.
        With `stop` set (shutdown), the store in flight finishes and the rest
        wait for a later run (`Stopped`)."""
        self.preflight(fact_id, text)
        targets = await self._plan(text, stores)
        await asyncio.to_thread(self.graph.attach, fact_id, text, targets, stop=stop)
        return [t.store for t in targets]

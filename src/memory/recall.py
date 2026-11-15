"""Recall and promotion.

Recall order is session cache, then the bot's private namespace, then
shared. Every hit passes the exclusion filter at read; a hit that fails
is dropped and named in the ledger. The decider scores the surviving
hits with one noul each (purpose `recall`) and `select_floor` keeps
every hit at or above an absolute floor, best first, under a top-k cap.
Beside the facts, the store's knowledge graph (Hindsight) offers sentences
about the entities near the task; they are filtered and scored the same
way, under their own cap. Scorer absent
or failing fails open to retrieval order capped at k. Promotion from
session to private is automatic above a threshold; private to shared
is gated the same way but never lands without a person's confirm.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.decider.interface import (BandDecision, ConfirmState, DecisionRequest, NoulQuestion, Outcome, PromotionGate,
                                   Scorer, decide_and_log, select_floor)

from .exclusion import exclusion
from .graph import SHARED, Fact, MemoryGraph, SessionCache, Tier, private_ns

if TYPE_CHECKING:
    from src.harness.bot import Bot

__all__ = ["MemoryRecall", "RecallConfig", "RecallResult"]


@dataclass
class RecallConfig:
    # Facts don't compete: every fact the decider scores at or above the floor is offered, up to k.
    # (A band relative to the top score let one obvious fact, like the user's name, crowd out the rest.)
    floor: float = field(default_factory=lambda: float(os.environ.get("ZEN_RECALL_FLOOR", "0.5")))
    k: int = field(default_factory=lambda: int(os.environ.get("ZEN_RECALL_K", "5")))
    # The store's knowledge graph (Hindsight): sentences about entities and how they relate, scored like facts
    # under the same floor but with their own cap, so short graph lines never crowd out whole facts.
    graph: bool = field(default_factory=lambda: os.environ.get("ZEN_RECALL_GRAPH", "1") not in ("0", "false", "no"))
    graph_k: int = field(default_factory=lambda: int(os.environ.get("ZEN_RECALL_GRAPH_K", "5")))
    graph_candidates: int = field(default_factory=lambda: int(os.environ.get("ZEN_RECALL_GRAPH_CANDIDATES", "12")))
    graph_timeout: float = field(default_factory=lambda: float(os.environ.get("ZEN_RECALL_GRAPH_TIMEOUT", "20")))
    per_source: int = field(default_factory=lambda: int(os.environ.get("ZEN_RECALL_PER_SOURCE", "8")))
    promote_threshold: float = field(default_factory=lambda: float(os.environ.get("ZEN_PROMOTE_THRESHOLD", "0.7")))


@dataclass
class RecallResult:
    facts: list[str]
    band: dict[str, Any] | None
    sources: dict[str, int]
    blocked: list[str]
    fallback: bool
    routes: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {"facts": self.facts, "band": self.band, "fallback": self.fallback, "sources": self.sources, "blocked": self.blocked}


@dataclass
class Pending:
    fact: Fact
    gate: PromotionGate
    bot_id: str
    session_id: str


class MemoryRecall:
    kind = "graph"

    def __init__(self, graph: MemoryGraph, cache: SessionCache, scorer: Scorer | None, ledger: Any, config: RecallConfig | None = None) -> None:
        self.graph = graph
        self.cache = cache
        self.scorer = scorer
        self.ledger = ledger
        self.config = config or RecallConfig()
        self.pending: dict[str, Pending] = {}
        # set by the harness on a store that can attach: promotion into shared goes through the profile, like any shared write
        self.profile: Any = None
        self.extract_queue: Any = None

    # -- recall ---------------------------------------------------------------

    def gather(self, bot_id: str, task: str, session_id: str) -> tuple[list[tuple[str, Fact]], list[str], dict[str, str]]:
        """Hits in recall order, each tagged with its source; blocked ids; routes by source."""
        n = self.config.per_source
        ordered: list[tuple[str, Fact]] = [("cache", f) for f in self.cache.facts(session_id)[-n:]] if session_id else []
        routes: dict[str, str] = {}
        # one search over both namespaces (one Hindsight recall per turn, shared with gather_graph), split by namespace
        hits = self.graph.search(task, [private_ns(bot_id), SHARED], limit=2 * n)
        routes["private"] = routes["shared"] = hits.route
        for source, ns in (("private", private_ns(bot_id)), ("shared", SHARED)):
            ordered.extend((source, f) for f in [f for f in hits.facts if f.namespace == ns][:n])
        kept: list[tuple[str, Fact]] = []
        blocked: list[str] = []
        seen: set[str] = set()
        for source, f in ordered:
            if f.id in seen:
                continue
            seen.add(f.id)
            if exclusion.check_recall({"kind": "memory", "text": f.text, "entities": f.entities}):
                kept.append((source, f))
            else:
                blocked.append(f.id)
        return kept, blocked, routes

    def gather_graph(self, bot_id: str, task: str) -> tuple[list[Fact], list[str], str]:
        """Graph sentences as transient facts (never stored), filtered like
        any read; an error string instead of raising, since recall goes on without them."""
        if not self.config.graph:
            return [], [], ""
        try:
            lines = self.graph.graph_context(task, [private_ns(bot_id), SHARED], limit=self.config.graph_candidates,
                                             timeout=self.config.graph_timeout)
        except TimeoutError as e:  # HindsightTimeout among them: the limit that ran out is this one
            return [], [], f"{type(e).__name__}: {e} (ZEN_RECALL_GRAPH_TIMEOUT)"[:300]
        except Exception as e:  # noqa: BLE001 - the graph is extra context; facts still recall without it
            return [], [], f"{type(e).__name__}: {e}"[:300]
        kept: list[Fact] = []
        blocked: list[str] = []
        for text in lines:
            f = Fact(text=text, namespace="graph", tier=Tier.project, bot_id=bot_id, source="graph",
                     id="g" + hashlib.md5(text.encode("utf-8")).hexdigest()[:11])
            if exclusion.check_recall({"kind": "memory", "text": f.text, "entities": []}):
                kept.append(f)
            else:
                blocked.append(f.id)
        return kept, blocked, ""

    async def recall(self, bot: "Bot", task: str, session_id: str = "") -> list[str]:
        return (await self.recall_detail(bot, task, session_id)).facts

    async def recall_detail(self, bot: "Bot", task: str, session_id: str = "") -> RecallResult:
        # facts (chunk search) and graph sentences (graph search) at once: both block on the store, neither needs the other
        (kept, blocked, routes), (graph, graph_blocked, graph_error) = await asyncio.gather(
            asyncio.to_thread(self.gather, bot.id, task, session_id), asyncio.to_thread(self.gather_graph, bot.id, task))
        if graph_error:
            self.ledger.append("error", bot.id, session_id, where="recall_graph", error=graph_error, action="recall without the graph")
        routes = {**routes, "graph": "error" if graph_error else ("graph" if self.config.graph else "off")}
        seen_text = {f.text for _, f in kept}
        graph = [g for g in graph if g.text not in seen_text]
        blocked = blocked + graph_blocked
        candidates = kept + [("graph", g) for g in graph]
        sources = {s: sum(1 for src, _ in candidates if src == s) for s in ("cache", "private", "shared", "graph")}
        base: dict[str, Any] = {"event": "recall", "sources": sources, "routes": routes, "blocked": blocked, "fallback": False}
        if not candidates:
            if blocked:  # nothing to say when memory is simply empty; a blocked hit is worth a line
                self.ledger.append("memory", bot.id, session_id, band=None, injected=[], **base)
            return RecallResult([], None, sources, blocked, False, routes)
        by_id = {f.id: f for _, f in candidates}
        graph_ids = {g.id for g in graph}
        band = None
        if self.scorer is not None:
            questions = {f.id: _useful_question(f, task, bot) for _, f in candidates}
            request = DecisionRequest(state={"task": task, "bot": bot.name, "role": bot.role}, questions=questions, purpose="recall")
            try:
                decision = await decide_and_log(self.scorer, request, self.ledger, bot.id, session_id)
                scores = {fid: decision.noul(fid).noul for fid in by_id}
                band = _two_caps(scores, graph_ids, self.config)
            except Exception as e:  # any scorer trouble fails open, like the router
                self.ledger.append("error", bot.id, session_id, where="recall", error=f"{type(e).__name__}: {e}", action="fail open: retrieval order")
        if band is None:
            winners = [f.id for _, f in kept][: self.config.k] + [g.id for g in graph][: self.config.graph_k]
            base["fallback"] = True
        else:
            winners = band.winners if band.outcome == Outcome.act else []
        order = {f.id: i for i, (_, f) in enumerate(candidates)}  # keep recall order (facts, then graph), not score order
        winners = sorted(winners, key=order.get)
        self.ledger.append("memory", bot.id, session_id, band=band.to_dict() if band else None, injected=winners, **base)
        return RecallResult([by_id[w].text for w in winners], band.to_dict() if band else None, sources, blocked, base["fallback"], routes)

    # -- promotion ------------------------------------------------------------

    async def promote(self, fact_id: str, *, session_id: str, bot_id: str) -> PromotionGate:
        fact = self.cache.get(fact_id) or self.graph.get(fact_id)
        if fact is None:
            raise KeyError(fact_id)
        if fact.tier == Tier.session:
            to_tier, needs_confirm = Tier.private, False
        elif fact.tier == Tier.private:
            to_tier, needs_confirm = Tier.project, True
        else:
            raise ValueError(f"a {fact.tier} fact has nowhere to go")
        p = 1.0
        if self.scorer is not None:
            request = DecisionRequest(state={"fact": fact.text, "from": str(fact.tier), "to": str(to_tier)},
                                      questions={"promote": _promote_question(fact, to_tier)}, purpose="promotion")
            p = (await decide_and_log(self.scorer, request, self.ledger, bot_id, session_id)).noul("promote").noul
        allowed = p >= self.config.promote_threshold
        gate = PromotionGate(allowed, p, str(fact.tier), str(to_tier), ConfirmState.pending if allowed and needs_confirm else ConfirmState.not_needed)
        self.ledger.append("promotion", bot_id, session_id, event="gated", fact_id=fact.id, allowed=allowed, probability=p,
                           from_tier=gate.from_tier, to_tier=gate.to_tier, confirm=str(gate.confirm))
        if allowed and not needs_confirm:
            await self._land(fact, to_tier, bot_id, session_id)
        elif allowed:
            self.pending[fact.id] = Pending(fact, gate, bot_id, session_id)
        return gate

    async def _land(self, fact: Fact, to_tier: Tier, bot_id: str, session_id: str) -> Fact:
        """Write the promoted copy, then drop the old one. The store writes block, so they run off the loop."""
        ns = SHARED if to_tier in (Tier.project, Tier.user_pref) else private_ns(fact.bot_id or bot_id)
        kw = dict(bot_id=fact.bot_id or bot_id, entities=fact.entities, source=fact.source, session_id=session_id)
        if ns == SHARED and self.profile is not None:  # the fact enters shared now: route it like any shared write
            from .extract_queue import remember_shared

            new = await remember_shared(self.profile, self.extract_queue, fact.text, tier=to_tier, **kw)
        else:
            new = await asyncio.to_thread(self.graph.remember, fact.text, ns, to_tier, **kw)
        if fact.tier == Tier.session:
            self.cache.remove(fact.id)
        else:
            await asyncio.to_thread(self.graph.forget, fact.id, bot_id=bot_id, session_id=session_id)
        return new

    async def confirm_promotion(self, fact_id: str) -> PromotionGate:
        p = self.pending.pop(fact_id)
        await self._land(p.fact, Tier(p.gate.to_tier), p.bot_id, p.session_id)
        p.gate.confirm = ConfirmState.confirmed
        self.ledger.append("promotion", p.bot_id, p.session_id, event="confirmed", fact_id=fact_id, to_tier=p.gate.to_tier)
        return p.gate

    def deny_promotion(self, fact_id: str) -> PromotionGate:
        p = self.pending.pop(fact_id)
        p.gate.confirm = ConfirmState.denied
        self.ledger.append("promotion", p.bot_id, p.session_id, event="denied", fact_id=fact_id, to_tier=p.gate.to_tier)
        return p.gate


def _two_caps(scores: dict[str, float], graph_ids: set[str], config: RecallConfig) -> BandDecision:
    """One floor, two caps: facts up to k, graph sentences up to graph_k.
    The ledger keeps one band: every score, both sets of winners."""
    facts = select_floor({i: p for i, p in scores.items() if i not in graph_ids}, config.floor, config.k)
    graph = select_floor({i: p for i, p in scores.items() if i in graph_ids}, config.floor, config.graph_k)
    winners = facts.winners + graph.winners
    return BandDecision(dict(scores), winners, max(scores.values(), default=0.0), config.floor, facts.capped or graph.capped,
                        Outcome.act if winners else Outcome.defer)


def _useful_question(f: Fact, task: str, bot: "Bot") -> NoulQuestion:
    return NoulQuestion(
        instructions=f"Would remembering this help {bot.name} with the task? Remembered: {f.text}",
        criteria={"true": "The fact bears on the task or on how this bot should do it.",
                  "false": "The fact is unrelated to the task; injecting it would only add noise."},
    )


def _promote_question(f: Fact, to_tier: Tier) -> NoulQuestion:
    where = "every bot" if to_tier in (Tier.project, Tier.user_pref) else "this bot in future sessions"
    return NoulQuestion(
        instructions=f"Should this be kept for {where}? Fact: {f.text}",
        criteria={"true": "A durable fact, preference, or decision that will matter again.",
                  "false": "Transient working state, a one-off detail, or something already known."},
    )

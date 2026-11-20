"""Catalog behind the decider.

Each route asks the scorer one `noul` question per catalog entry: does
this skill (or MCP tool) help with this task? That yields an independent
calibrated probability per entry. Skills compete: `select_band` keeps
the ones near the top score. MCP servers do not (files finds the photo,
magick edits it): `select_floor` keeps every one at or above
ZEN_MCP_FLOOR. Both under a top-k cap per entry kind. A bot's own
servers (its file's `mcps`) are not asked about and always load.

The score is cached and reused until the task text, the bot's catalog,
or the scope version changes. A scope change (p3) bumps the version.

On scorer failure the router fails open: every attached skill injects,
exactly as the p0 stub did, and the ledger records why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.memory.ledger import Kind, Ledger
from src.providers.interface import ProviderError
from src.skills.catalog import Catalog, CatalogEntry, skill_entry
from src.skills.loader import Skill

from .interface import (
    BandConfig,
    BandDecision,
    DecisionRequest,
    NoulQuestion,
    Outcome,
    Scorer,
    DecisionRefused,
    decide_and_log,
    select_band,
    select_floor,
)

if TYPE_CHECKING:
    from src.harness.bot import Bot


@dataclass
class RouterConfig:
    band: BandConfig = field(default_factory=BandConfig)
    k_by_kind: dict[str, int] = field(default_factory=dict)  # per-kind override of band.k
    fail_open: bool = True
    # kinds whose entries do not compete: every one at or above the floor wins (select_floor), not a band
    # relative to the top. MCP servers work together (files to find a photo, magick to edit it).
    floor_by_kind: dict[str, float] = field(default_factory=lambda: {"mcp": 0.5})


@dataclass
class RouteResult:
    skills: list[Skill]
    mcps: list[CatalogEntry]
    bands: dict[str, BandDecision]  # by entry kind
    outcome: Outcome
    scorer: str
    cached: bool = False
    fallback: str | None = None  # reason when failing open

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills": [s.slug for s in self.skills],
            "mcps": [m.ref for m in self.mcps],
            "bands": {k: b.to_dict() for k, b in self.bands.items()},
            "outcome": str(self.outcome),
            "scorer": self.scorer,
            "cached": self.cached,
            "fallback": self.fallback,
        }


@dataclass
class Selection:
    skills: list[Skill]
    detail: dict[str, Any] = field(default_factory=dict)


class CatalogRouter:
    kind = "catalog-router"

    def __init__(self, scorer: Scorer, catalog: Catalog, ledger: Ledger, config: RouterConfig | None = None) -> None:
        self.scorer = scorer
        self.catalog = catalog
        self.ledger = ledger
        self.config = config or RouterConfig()
        self.scope_version = 0
        self._cache: dict[tuple, RouteResult] = {}
        self.calls = 0  # scorer calls, for tests and the ledger

    # -- SkillSelector ----------------------------------------------------

    async def select(self, bot: "Bot", task: str, session_id: str = "") -> Selection:
        r = await self.route(bot, task, session_id)
        return Selection(r.skills, r.to_dict())

    # -- routing ----------------------------------------------------------

    def bump_scope(self) -> None:
        """Called after a confirmed scope change. Invalidates every cached route."""
        self.scope_version += 1

    def _key(self, bot: "Bot", task: str) -> tuple:
        skills = tuple((s.slug, s.size) for s in bot.skills)
        mcps = tuple(m.ref for m in self.catalog.mcps())
        return (bot.id, task.strip(), skills, mcps, tuple(bot.mcps), self.scope_version)

    async def route(self, bot: "Bot", task: str, session_id: str = "") -> RouteResult:
        key = self._key(bot, task)
        if key in self._cache:
            hit = self._cache[key]
            return RouteResult(hit.skills, hit.mcps, hit.bands, hit.outcome, hit.scorer, cached=True, fallback=hit.fallback)

        # the bot's own servers load on every task, so they are not asked about; a name not connected is skipped
        own = [m for ref in dict.fromkeys(bot.mcps) for m in self.catalog.mcps() if m.ref == ref]
        routed = [m for m in self.catalog.mcps() if m.ref not in set(bot.mcps)]
        entries = [skill_entry(s) for s in bot.skills] + routed
        if not entries:
            result = RouteResult([], own, {}, Outcome.act, self.scorer.kind)
            self._cache[key] = result
            return result

        questions = {_qid(e): _fit_question(e, bot) for e in entries}
        request = DecisionRequest(state={"task": task, "bot": bot.name, "role": bot.role}, questions=questions, purpose="routing")
        try:
            self.calls += 1
            decision = await decide_and_log(self.scorer, request, self.ledger, bot.id, session_id)
            scores = {_qid(e): decision.noul(_qid(e)).noul for e in entries}
        except (ProviderError, DecisionRefused) as e:  # a refused question is no answer either
            if not self.config.fail_open:
                raise
            self.ledger.append(Kind.error, bot.id, session_id, where="router", error=f"{type(e).__name__}: {e}", action="fail open: inject all")
            return RouteResult(list(bot.skills), own + routed, {}, Outcome.review, self.scorer.kind, fallback=f"{type(e).__name__}: {e}")

        by_kind: dict[str, dict[str, float]] = {}
        widths: dict[str, float] = {}
        for e in entries:
            p = scores[_qid(e)]
            by_kind.setdefault(e.kind, {})[e.ref] = p
            if e.band_width is not None:
                widths[e.ref] = e.band_width

        bands: dict[str, BandDecision] = {}
        for kind, scores in by_kind.items():
            k = self.config.k_by_kind.get(kind, self.config.band.k)
            if kind in self.config.floor_by_kind:
                bands[kind] = select_floor(scores, self.config.floor_by_kind[kind], k)
                continue
            cfg = BandConfig(self.config.band.width, k, self.config.band.low_confidence)
            bands[kind] = select_band(scores, cfg, widths)

        skill_wins = set(bands.get("skill", select_band({})).winners) if bands.get("skill", None) and bands["skill"].outcome == Outcome.act else set()
        mcp_wins = set(bands["mcp"].winners) if "mcp" in bands and bands["mcp"].outcome == Outcome.act else set()
        outcome = Outcome.act if own or any(b.outcome == Outcome.act for b in bands.values()) else Outcome.defer

        result = RouteResult(
            skills=[s for s in bot.skills if s.slug in skill_wins],
            mcps=own + [m for ref in (bands["mcp"].winners if "mcp" in bands else []) for m in routed if m.ref == ref and ref in mcp_wins],
            bands=bands,
            outcome=outcome,
            scorer=decision.kind,
        )
        self._cache[key] = result
        return result


def _qid(e: CatalogEntry) -> str:
    return f"{e.kind}:{e.ref}"


def _fit_question(e: CatalogEntry, bot: "Bot") -> NoulQuestion:
    what = "skill" if e.kind == "skill" else "tool"
    needs = f" It needs: {', '.join(e.needs)}." if e.needs else ""
    limits = f" It must not: {', '.join(e.limits)}." if e.limits else ""
    return NoulQuestion(
        instructions=f"Would loading the {what} '{e.name}' help the bot accomplish this task? Use it when: {e.use}{needs}{limits}",
        criteria={
            "true": f"The task is the kind of work this {what} is for, or the {what} is needed for a step of it.",
            "false": f"The task does not call for this {what}; loading it would only add noise to the bot's context.",
        },
    )

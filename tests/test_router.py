"""catalog-router: one noul per entry, band then cap, score reuse,
fail open, ledger with scorer kind, and the loop swap."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.decider.interface import BandConfig, DecisionRefused, NoulAnswer, Outcome, RefusalAnswer
from src.decider.router import CatalogRouter, RouterConfig
from src.harness.approvals import ApprovalStore
from src.harness.bot import Bot
from src.harness.loop import SessionState, TaskLoop
from src.harness.tools import ToolRunner
from src.memory.ledger import Ledger
from src.providers.interface import ProviderUnavailable
from src.skills.catalog import Catalog, CatalogEntry
from src.skills.loader import parse_skill

from .conftest import MockScorer, ScriptedProvider, completion


def skill(name: str, use: str, width: float | None = None) -> object:
    bw = f"band_width: {width}\n" if width is not None else ""
    return parse_skill(f"---\nname: {name}\nuse: {use}\n{bw}---\nbody of {name}\n")


@pytest.fixture
def bot() -> Bot:
    b = Bot(name="Research partner", role="Research things", id="bot1")
    for s in [skill("Brief", "research briefs"), skill("Update", "weekly updates"), skill("Plan", "project plans"), skill("Recipe", "cooking")]:
        b.attach_skill(s)
    return b


def fit_by_name(probs: dict[str, float]):
    """Answer each `skill:<slug>` or `mcp:<ref>` question from a table."""

    def answer(request):
        out = {}
        for qid in request.questions:
            ref = qid.split(":", 1)[1]
            out[qid] = NoulAnswer(probs.get(ref, 0.01))
        return out

    return answer


async def test_band_then_cap_and_ledger(bot: Bot, ledger: Ledger):
    scorer = MockScorer(fit_by_name({"brief": 0.9, "update": 0.85, "plan": 0.82, "recipe": 0.05}))
    r = CatalogRouter(scorer, Catalog(), ledger, RouterConfig(BandConfig(width=0.1, k=2)))
    res = await r.route(bot, "write the weekly research brief", "s1")

    assert [s.slug for s in res.skills] == ["brief", "update"]
    assert res.bands["skill"].capped and res.bands["skill"].floor == pytest.approx(0.8)
    assert res.outcome == Outcome.act and res.scorer == "mock-scorer" and not res.cached

    req = scorer.requests[0]
    assert req.purpose == "routing" and req.state["task"] == "write the weekly research brief"
    assert set(req.questions) == {"skill:brief", "skill:update", "skill:plan", "skill:recipe"}
    assert all(q.type == "noul" for q in req.questions.values())
    assert "research briefs" in req.questions["skill:brief"].instructions

    e = ledger.tail(kind="decider_score", session_id="s1")[0]
    assert e.data["scorer"] == "mock-scorer" and e.data["purpose"] == "routing"
    assert e.data["answers"]["skill:brief"]["noul"] == 0.9


async def test_same_band_inject_all_under_k(bot: Bot, ledger: Ledger):
    scorer = MockScorer(fit_by_name({"brief": 0.7, "update": 0.7, "plan": 0.2, "recipe": 0.1}))
    res = await CatalogRouter(scorer, Catalog(), ledger).route(bot, "t")
    assert [s.slug for s in res.skills] == ["brief", "update"]


async def test_per_skill_band_width_from_frontmatter(ledger: Ledger):
    b = Bot(name="b", role="r")
    b.attach_skill(skill("Wide", "x", width=0.5))
    b.attach_skill(skill("Other", "y"))
    scorer = MockScorer(fit_by_name({"wide": 0.6, "other": 0.2}))
    res = await CatalogRouter(scorer, Catalog(), ledger, RouterConfig(BandConfig(width=0.05, k=5))).route(b, "t")
    assert [s.slug for s in res.skills] == ["wide", "other"]


async def test_low_confidence_defers_and_injects_nothing(bot: Bot, ledger: Ledger):
    scorer = MockScorer(fit_by_name({"brief": 0.15, "update": 0.1}))
    res = await CatalogRouter(scorer, Catalog(), ledger, RouterConfig(BandConfig(low_confidence=0.2))).route(bot, "unrelated")
    assert res.skills == [] and res.outcome == Outcome.defer


async def test_mcps_scored_separately_with_their_own_k(bot: Bot, ledger: Ledger):
    cat = Catalog()
    for ref, use in [("drive:list", "list files"), ("drive:read", "read a file"), ("slack:post", "post to slack")]:
        cat.add_mcp(CatalogEntry(kind="mcp", name=ref, use=use, ref=ref))
    scorer = MockScorer(fit_by_name({"brief": 0.9, "drive:list": 0.8, "drive:read": 0.78, "slack:post": 0.1}))
    r = CatalogRouter(scorer, cat, ledger, RouterConfig(BandConfig(width=0.1), k_by_kind={"skill": 3, "mcp": 1}))
    res = await r.route(bot, "read the plan from drive")
    assert [s.slug for s in res.skills] == ["brief"]
    assert [m.ref for m in res.mcps] == ["drive:list"] and res.bands["mcp"].capped


def servers(*refs: str) -> Catalog:
    cat = Catalog()
    for ref in refs:
        cat.add_mcp(CatalogEntry(kind="mcp", name=ref, use=f"{ref} things", ref=ref))
    return cat


async def test_servers_do_not_compete_every_one_over_the_floor_loads(ledger: Ledger):
    """Editing photos on disk needs files and magick: files at 0.82 is not dropped because magick scored 0.98."""
    b = Bot(name="DarkroomBot", role="edits images", id="darkroom-bot")
    scorer = MockScorer(fit_by_name({"magick": 0.98, "files": 0.82, "toolsmonk": 0.04, "exa": 0.001}))
    r = CatalogRouter(scorer, servers("magick", "files", "toolsmonk", "exa"), ledger, RouterConfig(BandConfig(width=0.1)))
    res = await r.route(b, "optimize the jpgs in my pictures folder")
    assert [m.ref for m in res.mcps] == ["magick", "files"]
    assert res.bands["mcp"].floor == pytest.approx(0.5) and res.outcome == Outcome.act


async def test_the_server_floor_and_cap_come_from_the_config(ledger: Ledger):
    b = Bot(name="b", role="r")
    scorer = MockScorer(fit_by_name({"a": 0.9, "b": 0.7, "c": 0.6, "d": 0.4}))
    cfg = RouterConfig(BandConfig(width=0.1), k_by_kind={"mcp": 2}, floor_by_kind={"mcp": 0.55})  # a, b, c clear it; k keeps two
    res = await CatalogRouter(scorer, servers("a", "b", "c", "d"), ledger, cfg).route(b, "t")
    assert [m.ref for m in res.mcps] == ["a", "b"] and res.bands["mcp"].capped
    none = await CatalogRouter(MockScorer(fit_by_name({})), servers("a"), ledger, cfg).route(b, "unrelated")
    assert none.mcps == []


async def test_a_bots_own_servers_always_load_and_are_not_scored(ledger: Ledger):
    b = Bot(name="DarkroomBot", role="edits images", id="darkroom-bot", mcps=["files", "magick", "gone"])
    scorer = MockScorer(fit_by_name({"exa": 0.9, "files": 0.0, "magick": 0.0}))
    r = CatalogRouter(scorer, servers("files", "magick", "exa", "time"), ledger)
    res = await r.route(b, "brighten beach.jpg")
    assert [m.ref for m in res.mcps] == ["files", "magick", "exa"]  # its own first, then what routing picked
    assert set(scorer.requests[0].questions) == {"mcp:exa", "mcp:time"}  # "gone" is not connected: ignored
    assert [m.ref for m in (await r.route(b, "brighten beach.jpg")).mcps] == ["files", "magick", "exa"]  # cached


async def test_own_servers_load_when_the_scorer_fails(ledger: Ledger):
    b = Bot(name="b", role="r", mcps=["files"])
    scorer = MockScorer(script=[ProviderUnavailable("decider down")])
    res = await CatalogRouter(scorer, servers("files", "exa"), ledger).route(b, "t")
    assert [m.ref for m in res.mcps][0] == "files" and {m.ref for m in res.mcps} == {"files", "exa"} and res.fallback


async def test_own_servers_load_when_nothing_else_is_scored(ledger: Ledger):
    b = Bot(name="b", role="r", mcps=["files"])
    scorer = MockScorer(fit_by_name({}))
    res = await CatalogRouter(scorer, servers("files"), ledger).route(b, "t")
    assert [m.ref for m in res.mcps] == ["files"] and not scorer.requests


async def test_score_reused_until_task_catalog_or_scope_changes(bot: Bot, ledger: Ledger):
    scorer = MockScorer(fit_by_name({"brief": 0.9}))
    r = CatalogRouter(scorer, Catalog(), ledger)
    for _ in range(3):
        res = await r.route(bot, "same task", "s1")
    assert r.calls == 1 and res.cached is True

    await r.route(bot, "a different task", "s1")
    assert r.calls == 2

    bot.attach_skill(skill("New", "new things"))
    await r.route(bot, "same task", "s1")
    assert r.calls == 3

    r.bump_scope()
    await r.route(bot, "same task", "s1")
    assert r.calls == 4

    assert len(ledger.tail(kind="decider_score")) == 4, "cached routes do not re-log"


async def test_empty_catalog_makes_no_scorer_call(ledger: Ledger):
    scorer = MockScorer(fit_by_name({}))
    res = await CatalogRouter(scorer, Catalog(), ledger).route(Bot(name="b", role="r"), "t")
    assert res.skills == [] and scorer.requests == [] and res.outcome == Outcome.act


async def test_fail_open_injects_all_and_logs(bot: Bot, ledger: Ledger):
    scorer = MockScorer(script=[ProviderUnavailable("decisions HTTP 503")])
    res = await CatalogRouter(scorer, Catalog(), ledger).route(bot, "t", "s1")
    assert [s.slug for s in res.skills] == ["brief", "update", "plan", "recipe"]
    assert res.outcome == Outcome.review and "ProviderUnavailable" in res.fallback
    err = ledger.tail(kind="error", session_id="s1")[0]
    assert err.data["where"] == "router" and "fail open" in err.data["action"]


async def test_a_refused_question_fails_open_like_a_scorer_that_is_down(bot: Bot, ledger: Ledger):
    def one_refused(request):
        return {qid: (RefusalAnswer() if qid == "skill:update" else NoulAnswer(0.9)) for qid in request.questions}

    res = await CatalogRouter(MockScorer(one_refused), Catalog(), ledger).route(bot, "t", "s1")
    assert [s.slug for s in res.skills] == ["brief", "update", "plan", "recipe"]
    assert res.outcome == Outcome.review and "DecisionRefused" in res.fallback
    assert ledger.tail(kind="error", session_id="s1")[0].data["where"] == "router"
    with pytest.raises(DecisionRefused):
        await CatalogRouter(MockScorer(one_refused), Catalog(), ledger, RouterConfig(fail_open=False)).route(bot, "t")


async def test_fail_closed_when_configured(bot: Bot, ledger: Ledger):
    scorer = MockScorer(script=[ProviderUnavailable("down")])
    with pytest.raises(ProviderUnavailable):
        await CatalogRouter(scorer, Catalog(), ledger, RouterConfig(fail_open=False)).route(bot, "t")


async def test_loop_resolves_skills_through_the_router(bot: Bot, ledger: Ledger, tmp_path: Path):
    scorer = MockScorer(fit_by_name({"brief": 0.9, "update": 0.3}))
    router = CatalogRouter(scorer, Catalog(), ledger)
    provider = ScriptedProvider([completion("Brief written.")])
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path), selector=router)
    s = await loop.run(bot, "write the brief")

    assert s.state == SessionState.done and s.injected == ["brief"]
    assert s.route["outcome"] == "act" and s.route["scorer"] == "mock-scorer" and s.route["bands"]["skill"]["winners"] == ["brief"]
    system = provider.calls[0][-2].content  # the per-message context
    assert "## Skill: Brief" in system and "## Skill: Update" not in system
    start = ledger.tail(kind="session", session_id=s.id)[0]
    assert start.data["selector"] == "catalog-router" and start.data["route_outcome"] == "act"

"""memory graph: one local graph with a shared namespace and one private
namespace per bot; a session cache with no model call; recall in the order
cache, private, shared; retrieval routes between semantic search and a
graph walk; hits are banded by the decider; promotion is gated and in the
ledger; the exclusion filter runs at every write and every read."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.decider.interface import ConfirmState, NoulAnswer
from src.harness.bot import Bot
from src.memory.exclusion import ExclusionError, exclusion
from src.memory.graph import LocalGraph, MemoryGraph, SessionCache, Target, Tier, private_ns, SHARED
from src.memory.ledger import Ledger
from src.memory.recall import MemoryRecall, RecallConfig

from .conftest import MockScorer


def graph(ledger: Ledger, vault: Path) -> MemoryGraph:
    return MemoryGraph(LocalGraph(vault / "memory"), ledger)


def useful(p_by_text: dict[str, float], default: float = 0.3):
    def answer(request):
        out = {}
        for key, q in request.questions.items():
            p = default
            for text, prob in p_by_text.items():
                if text in q.instructions:
                    p = prob
            out[key] = NoulAnswer(p)
        return out
    return answer


# -- namespaces and tiers --------------------------------------------------


def test_shared_and_private_namespaces(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    a = g.remember("The user prefers short answers", SHARED, Tier.user_pref, bot_id="lead")
    b = g.remember("Project deadline is 2026-12-01", SHARED, Tier.project, bot_id="lead")
    c = g.remember("Lead keeps a draft outline in notes/outline.md", private_ns("lead"), Tier.private, bot_id="lead")
    assert {f.id for f in g.facts(SHARED)} == {a.id, b.id}
    assert [f.id for f in g.facts(private_ns("lead"))] == [c.id]
    assert g.facts(private_ns("writer")) == []
    assert [e.data["event"] for e in ledger.tail(kind="memory")] == ["remember"] * 3
    assert ledger.tail(kind="memory")[-1].data["namespace"] == "bot:lead"


def test_local_graph_accepts_targets(ledger: Ledger, vault: Path):
    """The local JSON graph has one store; targets are the Hindsight store's concern and are ignored."""
    g = graph(ledger, vault)
    f = g.remember("Alex's wife is Jamie.", SHARED, Tier.user_pref, targets=[Target("personal")])
    assert g.get(f.id) == f and [x.id for x in g.facts(SHARED)] == [f.id]


def test_graph_persists_across_instances(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    f = g.remember("Acme pays net 30", SHARED, Tier.project, bot_id="lead", entities=["Acme"])
    g2 = graph(ledger, vault)
    got = g2.get(f.id)
    assert got is not None and got.text == "Acme pays net 30" and got.entities == ["acme"]


def test_write_runs_the_exclusion_filter(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    with pytest.raises(ExclusionError):
        g.remember("api key is sk-abcdefghijklmnopqrstuvwxyz0123456789", SHARED, Tier.project, bot_id="lead")
    exclusion.register_secret_value("hunter2hunter2")
    with pytest.raises(ExclusionError):
        g.remember("the password is hunter2hunter2", private_ns("lead"), Tier.private, bot_id="lead")
    assert g.facts(SHARED) == [] and g.facts(private_ns("lead")) == []
    assert ledger.tail(kind="memory")[-1].data["event"] == "refused"


def test_session_cache_is_a_plain_append(ledger: Ledger, vault: Path):
    cache = SessionCache()
    cache.add("s1", "lead", "Working file is report.md")
    cache.add("s1", "lead", "Ran the tests once, 3 failed")
    cache.add("s2", "lead", "Other session")
    assert [f.text for f in cache.facts("s1")] == ["Working file is report.md", "Ran the tests once, 3 failed"]
    assert all(f.tier == Tier.session for f in cache.facts("s1"))
    with pytest.raises(ExclusionError):
        cache.add("s1", "lead", "token ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD")
    assert cache.clear("s1") == 2 and cache.facts("s1") == []


# -- retrieval routing -------------------------------------------------------


def test_semantic_search_ranks_by_overlap(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    g.remember("Invoices go out on the first of the month", SHARED, Tier.project, bot_id="lead")
    g.remember("The office plant needs water on Fridays", SHARED, Tier.project, bot_id="lead")
    hits = g.search("when do invoices go out", [SHARED])
    assert hits.route == "semantic"
    assert hits.facts[0].text.startswith("Invoices") and len(hits.facts) == 1


def test_known_entity_routes_to_a_graph_walk(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    a = g.remember("Acme is our largest client", SHARED, Tier.project, bot_id="lead", entities=["Acme"])
    b = g.remember("Dana is the contact at Acme", SHARED, Tier.project, bot_id="lead", entities=["Dana", "Acme"])
    c = g.remember("Dana prefers calls on Tuesdays", SHARED, Tier.project, bot_id="lead", entities=["Dana"])
    g.remember("Unrelated: the printer is on floor 2", SHARED, Tier.project, bot_id="lead")
    hits = g.search("what do we know about Acme", [SHARED])
    assert hits.route == "walk"
    assert [f.id for f in hits.facts] == [a.id, b.id, c.id]  # Acme, then one hop through Dana


def test_search_stays_inside_the_namespaces_asked(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    g.remember("Lead private: invoices draft", private_ns("lead"), Tier.private, bot_id="lead")
    g.remember("Writer private: invoices style", private_ns("writer"), Tier.private, bot_id="writer")
    hits = g.search("invoices", [private_ns("lead")])
    assert [f.namespace for f in hits.facts] == ["bot:lead"]


# -- recall: order, bands, filter -------------------------------------------


async def test_recall_order_is_cache_then_private_then_shared(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    cache = SessionCache()
    g.remember("Shared: the report template lives in templates/report.md", SHARED, Tier.project, bot_id="lead")
    g.remember("Private: lead's report notes are in notes/report.md", private_ns("lead"), Tier.private, bot_id="lead")
    cache.add("s1", "lead", "Cache: the report draft is half done")
    scorer = MockScorer(answer=useful({"report": 0.9}))
    r = MemoryRecall(g, cache, scorer, ledger, RecallConfig(k=5))
    facts = await r.recall(Bot(name="Lead", role="lead", id="lead"), "finish the report", session_id="s1")
    assert [f.split(":")[0] for f in facts] == ["Cache", "Private", "Shared"]
    entry = ledger.tail(kind="memory")[-1]
    assert entry.data["event"] == "recall" and entry.data["sources"] == {"cache": 1, "private": 1, "shared": 1, "graph": 0}


async def test_every_fact_above_the_floor_injects(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    g.remember("Invoices go out on the first", SHARED, Tier.project, bot_id="lead")
    g.remember("Invoices are numbered INV-", SHARED, Tier.project, bot_id="lead")
    g.remember("Invoices once went out late in May", SHARED, Tier.project, bot_id="lead")
    scorer = MockScorer(answer=useful({"on the first": 0.9, "numbered": 0.85, "late in May": 0.4}))
    r = MemoryRecall(g, SessionCache(), scorer, ledger, RecallConfig(floor=0.5, k=5))
    facts = await r.recall(Bot(name="Lead", role="lead", id="lead"), "send the invoices", session_id="s1")
    assert sorted(facts) == ["Invoices are numbered INV-", "Invoices go out on the first"]
    entry = ledger.tail(kind="memory")[-1]
    assert entry.data["band"]["outcome"] == "act" and len(entry.data["band"]["winners"]) == 2 and entry.data["band"]["floor"] == 0.5


async def test_one_obvious_fact_does_not_crowd_out_the_rest(ledger: Ledger, vault: Path):
    """The live case: "what do you know about me" scored the name 0.998 and
    the resume 0.835 and 0.798; a band 0.1 under the top dropped both resumes."""
    g = graph(ledger, vault)
    g.remember("The user's name is Alex Rivera", SHARED, Tier.project, bot_id="pa")
    g.remember("Resume: VP of Technology at Contoso", SHARED, Tier.project, bot_id="pa")
    g.remember("Full resume: AI/ML consultant at Northwind Labs", SHARED, Tier.project, bot_id="pa")
    g.remember("Works on side projects at night", SHARED, Tier.project, bot_id="pa")
    scorer = MockScorer(answer=useful({"name is": 0.998, "Full resume": 0.835, "Resume:": 0.798, "side projects": 0.438}))
    r = MemoryRecall(g, SessionCache(), scorer, ledger, RecallConfig())
    facts = await r.recall(Bot(name="PA", role="assistant", id="pa"), "what about the user: name, resume, projects", session_id="s1")  # the local store needs word overlap; Hindsight ranked all four live
    assert sorted(facts) == ["Full resume: AI/ML consultant at Northwind Labs", "Resume: VP of Technology at Contoso",
                             "The user's name is Alex Rivera"]


async def test_the_floor_still_caps_at_k(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    for i in range(4):
        g.remember(f"Invoice rule {i}", SHARED, Tier.project, bot_id="lead")
    scorer = MockScorer(answer=useful({}, default=0.9))
    r = MemoryRecall(g, SessionCache(), scorer, ledger, RecallConfig(k=2))
    facts = await r.recall(Bot(name="Lead", role="lead", id="lead"), "invoice rules", session_id="s1")
    band = ledger.tail(kind="memory")[-1].data["band"]
    assert len(facts) == 2 and band["capped"] is True


async def test_low_confidence_recall_injects_nothing(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    g.remember("Invoices go out on the first", SHARED, Tier.project, bot_id="lead")
    scorer = MockScorer(answer=useful({}, default=0.1))
    r = MemoryRecall(g, SessionCache(), scorer, ledger, RecallConfig(floor=0.2))
    facts = await r.recall(Bot(name="Lead", role="lead", id="lead"), "send the invoices", session_id="s1")
    assert facts == [] and ledger.tail(kind="memory")[-1].data["band"]["outcome"] == "defer"


async def test_scorer_failure_fails_open_to_retrieval_order(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    g.remember("Invoices go out on the first", SHARED, Tier.project, bot_id="lead")
    scorer = MockScorer(answer=lambda req: RuntimeError("scorer down"))
    r = MemoryRecall(g, SessionCache(), scorer, ledger, RecallConfig(k=1))
    facts = await r.recall(Bot(name="Lead", role="lead", id="lead"), "send the invoices", session_id="s1")
    assert facts == ["Invoices go out on the first"]
    assert ledger.tail(kind="memory")[-1].data["fallback"] is True


async def test_recall_runs_the_exclusion_filter_on_every_read(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    f = g.remember("The staging login is on the wiki", SHARED, Tier.project, bot_id="lead")
    exclusion.register_secret_value("staging login")  # became a secret after it was written
    scorer = MockScorer(answer=useful({"staging": 0.9}))
    r = MemoryRecall(g, SessionCache(), scorer, ledger, RecallConfig())
    facts = await r.recall(Bot(name="Lead", role="lead", id="lead"), "log into staging", session_id="s1")
    assert facts == [] and scorer.requests == []
    dropped = [e for e in ledger.tail(kind="memory") if e.data["event"] == "recall"][-1]
    assert dropped.data["blocked"] == [f.id]


# -- promotion ---------------------------------------------------------------


async def test_session_fact_promotes_to_private_when_the_decider_allows(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    cache = SessionCache()
    f = cache.add("s1", "lead", "Dana at Acme wants weekly updates")
    r = MemoryRecall(g, cache, MockScorer(script=[{"promote": NoulAnswer(0.9)}]), ledger, RecallConfig(promote_threshold=0.7))
    gate = await r.promote(f.id, session_id="s1", bot_id="lead")
    assert gate.allowed and gate.from_tier == "session" and gate.to_tier == "private" and gate.confirm == ConfirmState.not_needed
    assert [x.text for x in g.facts(private_ns("lead"))] == ["Dana at Acme wants weekly updates"]
    assert cache.facts("s1") == []
    e = ledger.tail(kind="promotion")[-1]
    assert e.data["allowed"] is True and e.data["from_tier"] == "session" and e.data["probability"] == 0.9


async def test_session_fact_stays_when_the_decider_declines(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    cache = SessionCache()
    f = cache.add("s1", "lead", "Ran pytest once")
    r = MemoryRecall(g, cache, MockScorer(script=[{"promote": NoulAnswer(0.2)}]), ledger, RecallConfig(promote_threshold=0.7))
    gate = await r.promote(f.id, session_id="s1", bot_id="lead")
    assert not gate.allowed and g.facts(private_ns("lead")) == [] and len(cache.facts("s1")) == 1
    assert ledger.tail(kind="promotion")[-1].data["allowed"] is False


async def test_private_to_shared_waits_for_confirm(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    f = g.remember("Acme pays net 30", private_ns("lead"), Tier.private, bot_id="lead")
    r = MemoryRecall(g, SessionCache(), MockScorer(script=[{"promote": NoulAnswer(0.95)}]), ledger, RecallConfig())
    gate = await r.promote(f.id, session_id="s1", bot_id="lead")
    assert gate.allowed and gate.to_tier == "project" and gate.confirm == ConfirmState.pending
    assert g.facts(SHARED) == []  # nothing lands until a person confirms
    assert (await r.confirm_promotion(f.id)).confirm == ConfirmState.confirmed
    assert [x.text for x in g.facts(SHARED)] == ["Acme pays net 30"] and g.facts(private_ns("lead")) == []
    assert [e.data["event"] for e in ledger.tail(kind="promotion")] == ["gated", "confirmed"]


async def test_promotion_denied_by_a_person_is_final(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    f = g.remember("Acme pays net 30", private_ns("lead"), Tier.private, bot_id="lead")
    r = MemoryRecall(g, SessionCache(), MockScorer(script=[{"promote": NoulAnswer(0.95)}]), ledger, RecallConfig())
    await r.promote(f.id, session_id="s1", bot_id="lead")
    assert r.deny_promotion(f.id).confirm == ConfirmState.denied
    assert g.facts(SHARED) == [] and len(g.facts(private_ns("lead"))) == 1
    with pytest.raises(KeyError):
        await r.confirm_promotion(f.id)


# -- harness wiring ----------------------------------------------------------


async def test_harness_memory_endpoints_and_injection(vault: Path, tmp_path: Path):
    import httpx

    from src.harness.api import Harness, create_app
    from src.harness.config import HarnessConfig
    from src.skills.catalog import Catalog
    from .conftest import ScriptedProvider, completion

    provider = ScriptedProvider([completion("done")])
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=provider, ledger=Ledger(vault), catalog=Catalog())
    h.add_bot(Bot(name="Lead", role="lead", id="lead"))
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        health = (await c.get("/health")).json()
        assert health["memory"] == "local" and health["recall"] == "graph"
        r = await c.post("/memory", json={"text": "Invoices go out on the first", "namespace": "shared", "tier": "project", "bot_id": "lead"})
        assert r.status_code == 200 and r.json()["tier"] == "project"
        fid = r.json()["id"]
        r = await c.post("/memory", json={"text": "key sk-abcdefghijklmnopqrstuvwxyz0123456789", "namespace": "shared", "tier": "project"})
        assert r.status_code == 422
        assert [f["id"] for f in (await c.get("/memory", params={"namespace": "shared"})).json()] == [fid]
        r = await c.get("/memory/recall", params={"bot": "lead", "q": "send the invoices"})
        assert r.json()["facts"] == ["Invoices go out on the first"] and r.json()["fallback"] is True  # no scorer
        r = await c.post("/bots/lead/tasks", json={"task": "send the invoices"})
        sid = r.json()["session_id"]
        for _ in range(200):
            if (await c.get(f"/sessions/{sid}")).json()["state"] == "done":
                break
            await asyncio.sleep(0.01)
        assert "Invoices go out on the first" in provider.calls[0][-2].content  # the context message remembers
        r = await c.delete(f"/memory/{fid}")
        assert r.status_code == 200 and (await c.get("/memory", params={"namespace": "shared"})).json() == []


# -- review fixes --------------------------------------------------------------


def test_multi_word_entity_routes_to_a_walk(ledger: Ledger, vault: Path):
    g = graph(ledger, vault)
    a = g.remember("Acme Corp renewed for a year", SHARED, Tier.project, bot_id="lead", entities=["Acme Corp"])
    g.remember("Dana Smith is the contact", SHARED, Tier.project, bot_id="lead", entities=["Dana Smith"])
    hits = g.search("what about Acme Corp", [SHARED])
    assert hits.route == "walk" and hits.seeds == ["acme corp"] and [f.id for f in hits.facts] == [a.id]
    assert g.search("tell me about acme", [SHARED]).route == "semantic"  # a partial name is not a seed


async def test_recall_endpoint_on_empty_memory_and_after_another_bots_recall(vault: Path, tmp_path: Path):
    import httpx

    from src.harness.api import Harness, create_app
    from src.harness.config import HarnessConfig
    from src.skills.catalog import Catalog
    from .conftest import ScriptedProvider

    h = Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path), provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    h.add_bot(Bot(name="Lead", role="lead", id="lead"))
    h.add_bot(Bot(name="Writer", role="writer", id="writer"))
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        r = await c.get("/memory/recall", params={"bot": "lead", "q": "anything"})
        assert r.status_code == 200 and r.json() == {"facts": [], "band": None, "fallback": False, "sources": {"cache": 0, "private": 0, "shared": 0, "graph": 0}, "blocked": []}
        await c.post("/memory", json={"text": "Writer private note on invoices", "namespace": "bot:writer", "tier": "private", "bot_id": "writer"})
        w = (await c.get("/memory/recall", params={"bot": "writer", "q": "invoices"})).json()
        assert w["facts"] == ["Writer private note on invoices"] and w["fallback"] is True
        l = (await c.get("/memory/recall", params={"bot": "lead", "q": "invoices"})).json()
        assert l["facts"] == [] and l["sources"] == {"cache": 0, "private": 0, "shared": 0, "graph": 0} and l["blocked"] == []


async def test_a_worker_thread_write_never_breaks_a_loop_side_read(ledger: Ledger, vault: Path):
    """A directory import writes facts from a worker thread while the API
    reads the same store on the loop."""
    import sys
    import threading

    g = graph(ledger, vault)
    errors: list[BaseException] = []

    def writer():
        try:
            for i in range(300):
                g.remember(f"fact {i}", SHARED, Tier.project, entities=[f"e{i}"])
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        t = threading.Thread(target=writer)
        t.start()
        while t.is_alive():
            g.facts(SHARED)
            g.search("fact", [SHARED])
        t.join()
    finally:
        sys.setswitchinterval(old)
    assert not errors and len(g.facts(SHARED)) == 300


# -- a slow write never blocks a read -----------------------------------------

import threading


class BlockingStore(LocalGraph):
    """A store whose put waits for a release, like a Hindsight retain mid-write."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.writing, self.release = threading.Event(), threading.Event()

    def put(self, fact, targets=None) -> None:
        self.writing.set()
        assert self.release.wait(10)
        super().put(fact, targets)


async def test_reads_answer_while_a_write_is_in_flight(ledger: Ledger, vault: Path):
    store = BlockingStore(vault / "memory")
    g = MemoryGraph(store, ledger)
    store.release.set()
    old = g.remember("Dana handles invoices.", SHARED, Tier.project, entities=["Dana"])
    store.release.clear()
    writer = threading.Thread(target=g.remember, args=("Lee runs payroll.", SHARED, Tier.project), kwargs={"entities": ["Lee"]})
    writer.start()
    try:
        assert store.writing.wait(5)

        def reads():
            return g.facts(SHARED), g.get(old.id), g.search("Dana", [SHARED]).facts
        facts, got, hits = await asyncio.wait_for(asyncio.to_thread(reads), 2)  # was: blocked until the write ended
        assert [f.id for f in facts] == [old.id] and got.id == old.id and [f.id for f in hits] == [old.id]
    finally:
        store.release.set()
        writer.join(5)
    assert len(g.facts(SHARED)) == 2


def test_a_failed_write_is_in_the_ledger_and_raises(ledger: Ledger, vault: Path):
    class Broken(LocalGraph):
        def put(self, fact, targets=None) -> None:
            raise TimeoutError("hindsight did not finish")

    g = MemoryGraph(Broken(vault / "memory"), ledger)
    with pytest.raises(TimeoutError):
        g.remember("Dana handles invoices.", SHARED, Tier.project)
    e = ledger.tail(1, kind="memory")[0]
    assert e.data["event"] == "failed" and "TimeoutError" in e.data["error"] and g.facts(SHARED) == []


# -- the store's knowledge graph in recall -------------------------------------


class GraphStore(LocalGraph):
    """A local store that also answers graph context, like Hindsight does."""

    def __init__(self, root: Path, lines: list[str] | Exception) -> None:
        super().__init__(root)
        self.lines = lines
        self.asked: list[tuple[str, list[str]]] = []

    def graph_context(self, query, namespaces, limit=12, timeout=None):
        self.asked.append((query, list(namespaces)))
        if isinstance(self.lines, Exception):
            raise self.lines
        return self.lines[:limit]


async def test_graph_sentences_are_scored_and_injected_after_the_facts(ledger: Ledger, vault: Path):
    store = GraphStore(vault / "memory", ["Alex Rivera worked at Fabrikam Group.", "Alex Rivera knows Rust.", "english is a language"])
    g = MemoryGraph(store, ledger)
    g.remember("Alex Rivera is VP of Technology at Contoso", SHARED, Tier.project, bot_id="pa")
    scorer = MockScorer(answer=useful({"VP of Technology": 0.9, "Fabrikam": 0.8, "Rust": 0.6, "english": 0.1}))
    r = MemoryRecall(g, SessionCache(), scorer, ledger, RecallConfig())
    facts = await r.recall(Bot(name="PA", role="assistant", id="pa"), "where has Alex Rivera worked", session_id="s1")
    assert facts == ["Alex Rivera is VP of Technology at Contoso", "Alex Rivera worked at Fabrikam Group.", "Alex Rivera knows Rust."]
    assert store.asked == [("where has Alex Rivera worked", ["bot:pa", SHARED])]
    e = ledger.tail(kind="memory")[-1].data
    assert e["sources"]["graph"] == 3 and e["routes"]["graph"] == "graph" and len(e["band"]["scores"]) == 4


async def test_graph_lines_have_their_own_cap(ledger: Ledger, vault: Path):
    store = GraphStore(vault / "memory", [f"Alex Rivera knows tool {i}." for i in range(6)])
    g = MemoryGraph(store, ledger)
    for i in range(3):
        g.remember(f"Alex Rivera fact {i}", SHARED, Tier.project, bot_id="pa")
    r = MemoryRecall(g, SessionCache(), MockScorer(answer=useful({}, default=0.9)), ledger, RecallConfig(k=2, graph_k=3))
    facts = await r.recall(Bot(name="PA", role="assistant", id="pa"), "Alex Rivera fact", session_id="s1")
    assert sum(f.startswith("Alex Rivera fact") for f in facts) == 2 and sum(f.startswith("Alex Rivera knows") for f in facts) == 3


async def test_a_graph_failure_recalls_the_facts_and_is_ledgered(ledger: Ledger, vault: Path):
    g = MemoryGraph(GraphStore(vault / "memory", TimeoutError("hindsight did not finish")), ledger)
    g.remember("Invoices go out on the first", SHARED, Tier.project, bot_id="lead")
    r = MemoryRecall(g, SessionCache(), MockScorer(answer=useful({}, default=0.9)), ledger, RecallConfig())
    facts = await r.recall(Bot(name="Lead", role="lead", id="lead"), "send the invoices", session_id="s1")
    assert facts == ["Invoices go out on the first"]
    err = ledger.tail(kind="error")[-1].data
    assert err["where"] == "recall_graph" and "TimeoutError" in err["error"]
    assert ledger.tail(kind="memory")[-1].data["routes"]["graph"] == "error"
    assert "ZEN_RECALL_GRAPH_TIMEOUT" in err["error"]  # the limit that ran out is recall's, not the store's own timeout


async def test_a_graph_error_that_is_not_a_timeout_names_no_timeout(ledger: Ledger, vault: Path):
    g = MemoryGraph(GraphStore(vault / "memory", RuntimeError("Could not set lock on file")), ledger)
    r = MemoryRecall(g, SessionCache(), MockScorer(answer=useful({}, default=0.9)), ledger, RecallConfig())
    await r.recall(Bot(name="Lead", role="lead", id="lead"), "send the invoices", session_id="s1")
    err = ledger.tail(kind="error")[-1].data["error"]
    assert err.startswith("RuntimeError: Could not set lock") and "TIMEOUT" not in err


async def test_graph_sentences_pass_the_exclusion_filter(ledger: Ledger, vault: Path):
    exclusion.register_secret_value("hunter2hunter2")
    g = MemoryGraph(GraphStore(vault / "memory", ["The wiki password is hunter2hunter2.", "Alex Rivera knows Rust."]), ledger)
    scorer = MockScorer(answer=useful({}, default=0.9))
    r = MemoryRecall(g, SessionCache(), scorer, ledger, RecallConfig())
    facts = await r.recall(Bot(name="PA", role="assistant", id="pa"), "what does Alex know", session_id="s1")
    assert facts == ["Alex Rivera knows Rust."]
    assert len(ledger.tail(kind="memory")[-1].data["blocked"]) == 1


async def test_the_graph_can_be_turned_off(ledger: Ledger, vault: Path):
    store = GraphStore(vault / "memory", ["Alex Rivera knows Rust."])
    r = MemoryRecall(MemoryGraph(store, ledger), SessionCache(), MockScorer(answer=useful({}, default=0.9)), ledger, RecallConfig(graph=False))
    assert await r.recall(Bot(name="PA", role="assistant", id="pa"), "what does Alex know", session_id="s1") == []
    assert store.asked == []


# -- shutdown -------------------------------------------------------------------


def test_close_releases_the_store_and_a_store_without_close_is_fine(ledger: Ledger, vault: Path):
    class Closable(LocalGraph):
        closed = 0

        def close(self) -> None:
            Closable.closed += 1

    MemoryGraph(Closable(vault / "memory"), ledger).close()
    assert Closable.closed == 1
    MemoryGraph(LocalGraph(vault / "m2"), ledger).close()  # nothing to release


def test_close_skips_rather_than_cut_off_a_running_write(ledger: Ledger, vault: Path):
    class Closable(LocalGraph):
        closed = False

        def close(self) -> None:
            Closable.closed = True

    store = BlockingStore(vault / "memory")  # a write that hangs, like a Hindsight retain mid-write
    store.close = lambda: setattr(Closable, "closed", True)
    g = MemoryGraph(store, ledger)
    writer = threading.Thread(target=g.remember, args=("Lee runs payroll.", SHARED, Tier.project))
    writer.start()
    try:
        assert store.writing.wait(5)
        g.close(wait=0.05)
        assert Closable.closed is False and ledger.tail(1, kind="memory")[0].data["event"] == "close_skipped"
    finally:
        store.release.set()
        writer.join(5)
    g.close(wait=1)  # once the write is done, close goes ahead
    assert Closable.closed is True


class BrokenSearchStore(LocalGraph):
    def search(self, query, namespaces, limit=10):
        raise RuntimeError("hindsight is down")


def test_search_falls_back_when_the_store_search_fails(ledger: Ledger, vault: Path):
    g = MemoryGraph(BrokenSearchStore(vault / "memory"), ledger)
    g.remember("Alex likes espresso", SHARED, Tier.project, bot_id="pa")
    hits = g.search("espresso", [SHARED])
    assert hits.route == "semantic" and [f.text for f in hits.facts] == ["Alex likes espresso"]
    errs = ledger.tail(5, kind="error")
    assert len(errs) == 1 and errs[0].data["where"] == "memory_search"


def test_search_falls_back_when_the_store_search_times_out(ledger: Ledger, vault: Path):
    from src.memory.hindsight_http import HindsightTimeout

    class Slow(LocalGraph):
        def search(self, query, namespaces, limit=10):
            raise HindsightTimeout("recall timed out")

    g = MemoryGraph(Slow(vault / "memory"), ledger)
    g.remember("Alex likes espresso", SHARED, Tier.project, bot_id="pa")
    assert [f.text for f in g.search("espresso", [SHARED]).facts] == ["Alex likes espresso"]
    assert ledger.tail(5, kind="error")[0].data["where"] == "memory_search"


def test_make_store_picks_local_and_rejects_an_unknown_backend(vault: Path):
    from src.memory.graph import LocalGraph, make_store

    assert isinstance(make_store(vault / "memory", "local"), LocalGraph)
    with pytest.raises(ValueError, match="hindsight.*local"):
        make_store(vault / "memory", "chroma")

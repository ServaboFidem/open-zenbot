"""learn from a crawl: a fetched page teaches memory only what touches an
organization or person from the registries, or a name an earlier page
taught. Connected discoveries (a product the known company sells, a feature
of a known product) are written; people and loose discoveries wait in
Approvals; the site itself is recorded once per organization."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.generation.generator import PageEntity, PageFact, PageLearning
from src.memory.learn import MAX_HOLDS, Hold, Write, host_of, plan_learning
from src.memory.registry import OrgRegistry, PeopleRegistry

URL = "https://northwindlabs.example/products"


@pytest.fixture
def regs(tmp_path: Path):
    orgs, people = OrgRegistry(tmp_path / "orgs.json"), PeopleRegistry(tmp_path / "people.json")
    orgs.add("Northwind Labs", "works_at", aliases=["NWL"])
    people.add("Alex Rivera", relation="owner")
    return orgs, people


def plan(learning, regs, learned=frozenset(), site_known=lambda o, h: False, denied=lambda h, n: False, url=URL):
    return plan_learning(learning, url, orgs=regs[0], people=regs[1], learned=set(learned), site_known=site_known, is_denied=denied)


# -- the gate and the plan ------------------------------------------------------


def test_unrelated_page_is_skipped_with_the_names_seen(regs):
    p = plan(PageLearning("s", [PageEntity("Acme", "organization"), PageEntity("Zed", "product", "Acme", "is sold by")], []), regs)
    assert p.status == "skipped" and p.reason == "unrelated" and p.seen == ["Acme", "Zed"] and p.writes == [] and p.holds == []


def test_plan_matches_names_case_and_space_insensitively(regs):
    p = plan(PageLearning("s", [PageEntity(" nwl ", "organization")], [PageFact("NWL is in Columbus.", "NWL")]), regs)
    assert p.matched == ["Northwind Labs"] and p.org.slug == "northwind_labs" and p.stores == ["org:northwind_labs"]
    assert p.writes[0] == Write("NWL is in Columbus.", ["Northwind Labs"])


def test_connected_product_and_feature_are_written_with_relation_facts(regs):
    ents = [PageEntity("Northwind Labs", "organization"), PageEntity("Nexus", "product", "Northwind Labs", "is sold by"),
            PageEntity("Auto-sync", "thing", "Nexus", "is a feature of")]
    facts = [PageFact("Nexus ships in 2027.", "Nexus"), PageFact("Auto-sync runs hourly.", "Auto-sync")]
    p = plan(PageLearning("NWL makes Nexus.", ents, facts), regs)
    assert p.status == "learned" and p.holds == [] and p.matched == ["Northwind Labs"]
    assert Write("Nexus is sold by Northwind Labs.", ["Nexus", "Northwind Labs"]) in p.writes
    assert Write("Auto-sync is a feature of Nexus.", ["Auto-sync", "Nexus"]) in p.writes
    assert Write("Nexus ships in 2027.", ["Nexus", "Northwind Labs"]) in p.writes
    assert Write("NWL makes Nexus.", ["Northwind Labs"]) in p.writes
    assert p.site_fact == Write("Northwind Labs's website is https://northwindlabs.example.", ["Northwind Labs"])


def test_people_and_unrelated_discoveries_are_held(regs):
    ents = [PageEntity("Northwind Labs", "organization"), PageEntity("Jane Doe", "person", "Northwind Labs", "is founder of"),
            PageEntity("Acme", "organization")]
    facts = [PageFact("Jane studied physics.", "Jane Doe"), PageFact("Acme is in Ohio.", "Acme")]
    p = plan(PageLearning("s", ents, facts), regs)
    assert [h.name for h in p.holds] == ["Jane Doe", "Acme"]
    assert p.holds[0] == Hold("Jane Doe", "person", "Jane Doe is founder of Northwind Labs.",
                              [Write("Jane studied physics.", ["Jane Doe", "Northwind Labs"]),
                               Write("Jane Doe is founder of Northwind Labs.", ["Jane Doe", "Northwind Labs"])])
    assert p.holds[1].relation_text == "" and p.holds[1].writes == [Write("Acme is in Ohio.", ["Acme"])]
    assert all("Jane" not in w.text for w in p.writes)


def test_denied_names_are_not_held_again_and_holds_overflow_at_ten(regs):
    ents = [PageEntity("Northwind Labs", "organization")] + [PageEntity(f"P{i}", "person") for i in range(12)]
    facts = [PageFact(f"P{i} works here.", f"P{i}") for i in range(12)]
    p = plan(PageLearning("s", ents, facts), regs, denied=lambda h, n: n == "p0")
    assert p.denied == ["P0"] and len(p.holds) == MAX_HOLDS and p.overflow == 1


def test_plan_drops_facts_about_unlisted_entities(regs):
    p = plan(PageLearning("s", [PageEntity("Northwind Labs", "organization")], [PageFact("Ghost is big.", "Ghost")]), regs)
    assert p.skipped_facts == 1 and all(w.text != "Ghost is big." for w in p.writes)


def test_earlier_learned_product_anchors_a_later_page(regs):
    p = plan(PageLearning("s", [PageEntity("Nexus", "product")], [PageFact("Nexus costs $9.", "Nexus")]), regs, learned={"nexus"})
    assert p.status == "learned" and p.matched == ["Nexus"] and p.org is None and p.stores == ["world"] and p.site_fact is None


def test_site_fact_needs_exactly_one_organization_and_is_written_once(regs):
    regs[0].add("Acme", "owns")
    two = plan(PageLearning("s", [PageEntity("Northwind Labs", "organization"), PageEntity("Acme", "organization")], []), regs)
    assert two.site_fact is None and two.reason == "site_ambiguous" and two.status == "learned"
    seen = plan(PageLearning("s", [PageEntity("Northwind Labs", "organization")], []), regs, site_known=lambda o, h: True)
    assert seen.site_fact is None and seen.reason == ""


def test_host_of():
    assert host_of("https://NorthwindLabs.example/x?y") == "northwindlabs.example" and host_of("northwindlabs.example") == ""


# -- the learner: redact, cap, dedup, extract, write in the background, hold, ledger -----------

import json  # noqa: E402

from src.generation.generator import Generator  # noqa: E402
from src.harness.approvals import ApprovalStore  # noqa: E402
from src.harness.tools import ToolRunner  # noqa: E402
from src.memory.graph import SHARED, LocalGraph, MemoryGraph  # noqa: E402
from src.memory.learn import LEARN_PAGE_SPEC, Learner  # noqa: E402
from src.memory.ledger import Ledger  # noqa: E402
from src.memory.profile import ProfileMemory  # noqa: E402
from src.providers.interface import ProviderError  # noqa: E402

from .conftest import ScriptedProvider, completion  # noqa: E402

PAGE = completion(json.dumps({"summary": "NWL makes Nexus.", "entities": [
    {"name": "Northwind Labs", "kind": "organization"},
    {"name": "Nexus", "kind": "product", "related_to": "Northwind Labs", "relation": "is sold by"},
    {"name": "Jane Doe", "kind": "person", "related_to": "Northwind Labs", "relation": "is founder of"}],
    "facts": [{"text": "Nexus ships in 2027.", "about": "Nexus"}, {"text": "Jane studied physics.", "about": "Jane Doe"}]}))
PAGE_ACME = completion(json.dumps({"summary": "NWL partners with Acme.", "entities": [
    {"name": "Northwind Labs", "kind": "organization"}, {"name": "Acme", "kind": "organization"}],
    "facts": [{"text": "Acme is in Ohio.", "about": "Acme"}]}))
UNRELATED = completion(json.dumps({"summary": "Acme.", "entities": [{"name": "Acme", "kind": "organization"}], "facts": []}))


class NoRouter:
    provider = None

    async def route(self, text, orgs, owner):
        raise AssertionError("no routing call expected")


def make(vault: Path, regs, script, bound=True):
    led = Ledger(vault)
    mem = MemoryGraph(LocalGraph(vault / "memory"), led)
    profile = ProfileMemory(mem, regs[1], regs[0], NoRouter(), led)
    provider = ScriptedProvider(script)
    return Learner(vault, Generator(provider), profile, None, ApprovalStore(), led, bound=lambda: bound), mem, provider, led


def learn_entries(led):
    return [e for e in led.tail(0, kind="learn") if "status" in e.data]


async def test_learn_writes_connected_facts_holds_the_person_and_records_the_site(vault, regs):
    learner, mem, provider, led = make(vault, regs, [PAGE])
    out = json.loads(await learner.learn(URL, "# Northwind Labs. Nexus ...", "products"))
    assert out == {"status": "learned", "reason": "", "matched": ["Northwind Labs"], "written": 4, "held": ["Jane Doe"], "skipped_facts": 0}
    await learner.drain()
    texts = sorted(f.text for f in mem.facts(SHARED))
    assert texts == sorted(["Northwind Labs's website is https://northwindlabs.example.", "Nexus is sold by Northwind Labs.", "Nexus ships in 2027.", "NWL makes Nexus."])
    assert all(f.source == f"web:{URL}" for f in mem.facts(SHARED))
    assert "Northwind Labs" in provider.calls[0][1].content  # the known names reach the prompt
    (req,) = learner.approvals.list("pending")
    assert req.tool == "learn_page" and req.rule == "learn:loose" and req.title == "Remember Jane Doe (person) from northwindlabs.example?"
    assert "Jane studied physics." in req.detail and "is founder of" in req.detail
    assert learner.holds.pop(req.id)[1]["writes"][0]["text"] == "Jane studied physics."
    assert learner.log.site_recorded("Northwind Labs", "northwindlabs.example") and learner.log.get(URL).names == ["Northwind Labs", "Nexus"]
    assert "nexus" in learner.log.learned() and len(learner.log.get(URL).fact_ids) == 3  # the site fact is kept apart
    assert learner.log.site_fact_id("Northwind Labs", "northwindlabs.example") in {f.id for f in mem.facts(SHARED)}
    e = learn_entries(led)[0].data
    assert e["status"] == "learned" and e["matched"] == ["Northwind Labs"] and e["held"] == ["Jane Doe"] and e["chars"] > 0


async def test_learn_skips_unrelated_unchanged_unparsed_and_errors(vault, regs):
    learner, mem, provider, led = make(vault, regs, [UNRELATED, PAGE, completion("prose"), ProviderError("boom")])
    assert json.loads(await learner.learn("https://acme.com", "Acme"))["reason"] == "unrelated"
    assert json.loads(await learner.learn(URL, "page"))["status"] == "learned"
    assert json.loads(await learner.learn(URL, "page"))["reason"] == "unchanged" and len(provider.calls) == 2
    assert json.loads(await learner.learn(URL, "page v2"))["reason"] == "unparsed"
    assert json.loads(await learner.learn(URL, "page v3"))["reason"] == "error"
    assert [e.data["reason"] for e in learn_entries(led)] == ["unrelated", "", "unchanged", "unparsed", "error"]
    assert any(e.data.get("where") == "learn_page" for e in led.tail(0, kind="error"))
    await learner.drain()


async def test_learn_refuses_a_url_without_a_host(vault, regs):
    learner, *_ = make(vault, regs, [])
    assert json.loads(await learner.learn("northwindlabs.example", "x"))["reason"] == "bad_url"


async def test_learn_skips_empty_markdown(vault, regs):
    learner, _, provider, _ = make(vault, regs, [])
    assert json.loads(await learner.learn(URL, "    "))["reason"] == "empty" and provider.calls == []


async def test_learn_says_unbound_without_a_provider(vault, regs):
    learner, *_ = make(vault, regs, [], bound=False)
    assert json.loads(await learner.learn(URL, "x"))["reason"] == "unbound"


async def test_learn_redacts_secrets_and_caps_the_page(vault, regs):
    learner, _, provider, led = make(vault, regs, [PAGE])
    big = "sk-ant-api03-" + "a" * 40 + " " + "Northwind Labs " * 5000
    await learner.learn(URL, big)
    sent = provider.calls[0][1].content
    assert "sk-ant-api03" not in sent and "[document continues;" in sent and len(sent) < 20_000
    assert learn_entries(led)[0].data["redactions"] == 1
    await learner.drain()


async def test_a_changed_page_replaces_its_facts_but_keeps_approved_ones(vault, regs):
    learner, mem, provider, led = make(vault, regs, [PAGE, PAGE])
    await learner.learn(URL, "v1")
    await learner.drain()
    first = set(learner.log.get(URL).fact_ids)
    kept = mem.remember("Jane studied physics.", SHARED, "project", entities=["jane doe"], source=f"web:{URL}")
    learner.log.add_approved(URL, [kept.id])
    await learner.learn(URL, "v2")
    await learner.drain()
    ids = {f.id for f in mem.facts(SHARED)}
    assert first.isdisjoint(ids) and kept.id in ids and len(ids) == 5  # 3 new + the approved one + the site fact, which survives
    assert learner.log.get(URL).approved_fact_ids == [kept.id]
    assert "Nexus is sold by Northwind Labs." in {f.text for f in mem.facts(SHARED)}  # Nexus is a learned name now; the relation still lands


async def test_holds_are_capped_at_ten_per_page(vault, regs):
    many = completion(json.dumps({"summary": "s", "entities": [{"name": "Northwind Labs", "kind": "organization"}] +
                                  [{"name": f"P{i}", "kind": "person"} for i in range(12)],
                                  "facts": [{"text": f"P{i} works here.", "about": f"P{i}"} for i in range(12)]}))
    learner, _, _, led = make(vault, regs, [many])
    out = json.loads(await learner.learn(URL, "x"))
    assert len(out["held"]) == 10 and len(learner.approvals.list("pending")) == 10
    assert any(e.data.get("event") == "held_overflow" and e.data["count"] == 2 for e in led.tail(0, kind="learn"))
    await learner.drain()


def test_learn_page_registers_as_a_built_in_tool(vault, regs):
    learner, *_ = make(vault, regs, [])
    runner = ToolRunner()
    learner.register(runner)
    spec = next(s for s in runner.specs(set()) if s.name == "learn_page")  # an empty group set still lists built-ins
    assert spec.parameters == LEARN_PAGE_SPEC and LEARN_PAGE_SPEC["required"] == ["url", "markdown"]


# -- the review's fix pass -------------------------------------------------------


def test_site_fact_only_on_the_orgs_own_host(regs):
    foreign = plan(PageLearning("s", [PageEntity("Northwind Labs", "organization")], []), regs, url="https://techcrunch.com/story")
    assert foreign.status == "learned" and foreign.site_fact is None and foreign.reason == "site_foreign"
    alias = plan(PageLearning("s", [PageEntity("Northwind Labs", "organization")], []), regs, url="https://www.nwl.example/")
    assert alias.site_fact == Write("Northwind Labs's website is https://www.nwl.example.", ["Northwind Labs"])


def test_hold_with_nothing_to_write_is_skipped(regs):
    p = plan(PageLearning("s", [PageEntity("Northwind Labs", "organization"), PageEntity("Acme", "organization")], []), regs)
    assert p.holds == [] and p.overflow == 0


def test_chains_stop_at_one_level_and_only_products_anchor_later_pages(regs):
    ents = [PageEntity("Northwind Labs", "organization"), PageEntity("Nexus", "product", "Northwind Labs", "is sold by"),
            PageEntity("Auto-sync", "thing", "Nexus", "is a feature of"), PageEntity("Gadget", "thing", "Auto-sync", "is part of"),
            PageEntity("GitHub", "organization", "Northwind Labs", "is a partner of")]
    p = plan(PageLearning("s", ents, [PageFact("Gadget is small.", "Gadget")]), regs)
    assert p.connected == ["Nexus", "Auto-sync", "GitHub"] and [h.name for h in p.holds] == ["Gadget"]
    assert p.learned_names == ["Northwind Labs", "Nexus"]  # only matched names and products anchor a later crawl


async def test_site_fact_survives_a_changed_page(vault, regs):
    learner, mem, provider, led = make(vault, regs, [PAGE, PAGE])
    await learner.learn(URL, "v1")
    await learner.drain()
    await learner.learn(URL, "v2")
    await learner.drain()
    texts = [f.text for f in mem.facts(SHARED)]
    assert texts.count("Northwind Labs's website is https://northwindlabs.example.") == 1
    assert learner.log.site_fact_id("Northwind Labs", "northwindlabs.example") in {f.id for f in mem.facts(SHARED)}


async def test_a_logged_page_whose_facts_are_not_in_the_store_is_learned_again(vault, regs):
    # An upgrade from cognee keeps the learn log: the page looks unchanged, with facts the new store lacks.
    learner, mem, provider, led = make(vault, regs, [PAGE, PAGE])
    await learner.learn(URL, "v1")
    await learner.drain()
    for f in mem.facts(SHARED):
        mem.store.delete(f.id)
    out = json.loads(await learner.learn(URL, "v1"))
    assert out["status"] == "learned" and out["written"] == 4 and len(provider.calls) == 2  # the site fact too
    await learner.drain()
    assert len(mem.facts(SHARED)) == 4
    assert learner.log.site_fact_id("Northwind Labs", "northwindlabs.example") in {f.id for f in mem.facts(SHARED)}


async def test_a_page_with_one_fact_forgotten_on_purpose_is_not_learned_again(vault, regs):
    learner, mem, provider, led = make(vault, regs, [PAGE, PAGE])
    await learner.learn(URL, "v1")
    await learner.drain()
    mem.store.delete(learner.log.get(URL).fact_ids[0])
    assert json.loads(await learner.learn(URL, "v1"))["reason"] == "unchanged" and len(provider.calls) == 1


class SlowProvider(ScriptedProvider):
    async def complete(self, messages, *, tools=None, model=None, session_id=None):
        import asyncio
        await asyncio.sleep(0.05)
        return await super().complete(messages, tools=tools, model=model, session_id=session_id)


async def test_overlapping_learns_of_one_url_write_once(vault, regs):
    import asyncio
    led = Ledger(vault)
    mem = MemoryGraph(LocalGraph(vault / "memory"), led)
    profile = ProfileMemory(mem, regs[1], regs[0], NoRouter(), led)
    learner = Learner(vault, Generator(SlowProvider([PAGE, PAGE])), profile, None, ApprovalStore(), led, bound=lambda: True)
    a, b = await asyncio.gather(learner.learn(URL, "same"), learner.learn(URL, "same"))
    assert {json.loads(a)["status"], json.loads(b)["status"]} == {"learned", "skipped"}
    await learner.drain()
    texts = [f.text for f in mem.facts(SHARED)]
    assert len(texts) == len(set(texts)) == 4


PAGE2 = completion(json.dumps({"summary": "NWL makes Nexus v2.", "entities": [
    {"name": "Northwind Labs", "kind": "organization"},
    {"name": "Nexus", "kind": "product", "related_to": "Northwind Labs", "relation": "is sold by"}],
    "facts": [{"text": "Nexus ships in 2028.", "about": "Nexus"}]}))


async def test_a_quick_relearn_still_replaces_the_first_facts(vault, regs):
    learner, mem, provider, led = make(vault, regs, [PAGE, PAGE2])
    await learner.learn(URL, "v1")  # its writes are still in flight
    await learner.learn(URL, "v2")
    await learner.drain()
    texts = sorted(f.text for f in mem.facts(SHARED))
    assert texts == sorted(["Northwind Labs's website is https://northwindlabs.example.", "Nexus is sold by Northwind Labs.",
                            "Nexus ships in 2028.", "NWL makes Nexus v2."])
    site = learner.log.site_fact_id("Northwind Labs", "northwindlabs.example")
    assert learner.log.get(URL).done and sorted(learner.log.get(URL).fact_ids) == sorted(f.id for f in mem.facts(SHARED) if f.id != site)


async def test_an_unfinished_entry_is_learned_again(vault, regs):
    learner, mem, provider, led = make(vault, regs, [PAGE, PAGE])
    await learner.learn(URL, "v1")
    await learner.drain()
    e = learner.log.get(URL)
    e.done = False  # as a crash in the middle of the writes leaves it
    learner.log.put(e)
    assert json.loads(await learner.learn(URL, "v1"))["status"] == "learned" and len(provider.calls) == 2
    await learner.drain()
    assert len({f.text for f in mem.facts(SHARED)}) == len(mem.facts(SHARED)) == 4


async def test_a_pending_hold_is_not_asked_again_from_another_page(vault, regs):
    learner, mem, provider, led = make(vault, regs, [PAGE, PAGE])
    first = json.loads(await learner.learn("https://northwindlabs.example/team", "page"))
    second = json.loads(await learner.learn("https://northwindlabs.example/about", "page"))
    assert first["held"] == ["Jane Doe"] and second["held"] == [] and len(learner.approvals.list("pending")) == 1
    assert any(e.data.get("event") == "held_pending" for e in led.tail(0, kind="learn"))
    await learner.drain()


async def test_learn_page_returns_json(vault, regs):
    learner, *_ = make(vault, regs, [])
    assert json.loads(await learner.learn("nope", "x"))["reason"] == "bad_url"

"""Store routing and ProfileMemory: which shared stores a text lands in, with
which entity hints, and the owner rule. Offline: a scripted provider answers
the router, and a recording store stands in for Hindsight."""

from __future__ import annotations

import json

import pytest

from src.memory.exclusion import ExclusionError
from src.memory.graph import SHARED, Fact, LocalGraph, MemoryGraph, Target, Tier
from src.memory.profile import OwnerUnknown, ProfileMemory
from src.memory.registry import OrgRegistry, PeopleRegistry
from src.memory.router import Route, StoreRouter
from src.memory.stores import PERSONAL, WORLD, org_store
from src.providers.interface import ProviderUnavailable
from tests.conftest import ScriptedProvider, completion


def answer(stores, new_orgs=()):
    return completion(json.dumps({"stores": stores, "new_orgs": [{"name": n, "relation": r} for n, r in new_orgs]}))


class RecordingStore(LocalGraph):
    """LocalGraph that also records the targets of each put and every attach,
    the way HindsightGraph receives them."""

    kind = "recording"

    def __init__(self, root):
        super().__init__(root)
        self.puts: list[list[Target] | None] = []
        self.attached: list[tuple[str, str, Target]] = []
    def put(self, fact, targets=None):
        self.puts.append(targets)
        super().put(fact, targets)

        self.fail_on: set[int] = set()  # attach call numbers (1-based) that raise

    def attach(self, fact_id, text, target):
        self.calls = getattr(self, "calls", 0) + 1
        if self.calls in self.fail_on:
            raise RuntimeError("retain failed")
        self.attached.append((fact_id, text, target))

    def has_item(self, fact_id, store):
        return any(f == fact_id and t.store == store for f, _, t in self.attached)


@pytest.fixture
def people(tmp_path):
    return PeopleRegistry(tmp_path / "people.json")


@pytest.fixture
def orgs(tmp_path):
    return OrgRegistry(tmp_path / "orgs.json")


def make(tmp_path, ledger, people, orgs, script, store=None):
    provider = ScriptedProvider(script)
    store = store or RecordingStore(tmp_path / "graph")
    router = StoreRouter(provider, "route-model", ledger=ledger)
    return ProfileMemory(MemoryGraph(store, ledger), people, orgs, router, ledger), provider, store


# -- the router -------------------------------------------------------------


async def test_route_parses_stores_and_new_orgs(ledger, orgs):
    orgs.add("Zen Labs", "works_at")
    provider = ScriptedProvider([answer(["personal", "org:zen_labs", "org:acme_widgets"], [("Acme Widgets", "owns")])])
    route = await StoreRouter(provider, "m", ledger=ledger).route("I started Acme Widgets.", orgs.all(), "Alex Rivera")
    assert route == Route([PERSONAL, org_store("zen_labs"), org_store("acme_widgets")], [("Acme Widgets", "owns")])
    sent = "\n".join(m.content for m in provider.calls[0])
    assert "Alex Rivera" in sent and "zen_labs" in sent and "Zen Labs" in sent


async def test_route_drops_unknown_org_slugs(ledger, orgs):
    provider = ScriptedProvider([answer(["org:nobody", "world"]), answer(["org:nobody", "bogus"])])
    router = StoreRouter(provider, "m", ledger=ledger)
    assert (await router.route("text", orgs.all(), "Alex")).stores == [WORLD]
    assert (await router.route("text", orgs.all(), "Alex")).stores == [WORLD]  # nothing valid left


async def test_route_does_not_open_a_store_for_a_vendor(ledger, orgs):
    provider = ScriptedProvider([answer(["org:bolt"], [("Bolt", "vendor")])])
    route = await StoreRouter(provider, "m", ledger=ledger).route("Paid Bolt.", orgs.all(), "Alex")
    assert route.stores == [WORLD]


async def test_route_error_falls_back(ledger, orgs):
    provider = ScriptedProvider([ProviderUnavailable("down")])
    route = await StoreRouter(provider, "m", ledger=ledger).route("text", orgs.all(), "Alex")
    assert route == Route([PERSONAL, WORLD], [])
    errors = [e for e in ledger.iter() if e.kind == "error"]
    assert len(errors) == 1 and errors[0].data["where"] == "route" and "down" in errors[0].data["error"]


async def test_route_unreadable_answer_falls_back(ledger, orgs):
    provider = ScriptedProvider([completion("personal, I think")])
    route = await StoreRouter(provider, "m", ledger=ledger).route("text", orgs.all(), "Alex")
    assert route.stores == [PERSONAL, WORLD]
    assert [e.data["where"] for e in ledger.iter() if e.kind == "error"] == ["route"]


async def test_route_accepts_a_single_store_as_a_string(ledger, orgs):
    provider = ScriptedProvider([completion(json.dumps({"stores": "personal", "new_orgs": []}))])
    assert (await StoreRouter(provider, "m", ledger=ledger).route("text", orgs.all(), "Alex")).stores == [PERSONAL]


async def test_route_caps_its_input(ledger, orgs):
    provider = ScriptedProvider([answer(["world"])])
    await StoreRouter(provider, "m", ledger=ledger, chunk_chars=100).route("x" * 5000, orgs.all(), "Alex")
    sent = "\n".join(m.content for m in provider.calls[0])
    assert "x" * 100 in sent and "x" * 101 not in sent


# -- ProfileMemory ------------------------------------------------------------


async def test_remember_shared_skips_routing_when_stores_given(tmp_path, ledger, people, orgs):
    people.set_owner("Alex Rivera", ["Lexi"])
    pm, provider, store = make(tmp_path, ledger, people, orgs, [])
    fact = await pm.remember_shared("I like tea.", bot_id="b", source="s", stores=[PERSONAL])
    assert provider.calls == []
    assert fact.namespace == SHARED and [t.store for t in store.puts[0]] == [PERSONAL]


async def test_remember_shared_builds_targets(tmp_path, ledger, people, orgs):
    people.set_owner("Alex Rivera", ["Lexi"])
    orgs.add("Zen Labs", "runs")
    pm, _, store = make(tmp_path, ledger, people, orgs, [answer(["personal", "org:zen_labs", "world"])])
    await pm.remember_shared("I run Zen Labs.", bot_id="b", source="s")
    personal, org, world = store.puts[0]
    assert personal == Target(PERSONAL, hints=("Alex Rivera",))
    assert org == Target(org_store("zen_labs"), hints=("Alex Rivera", "Zen Labs"))
    assert world == Target(WORLD, hints=("Alex Rivera",))


async def test_personal_without_owner_raises(tmp_path, ledger, people, orgs):
    pm, _, store = make(tmp_path, ledger, people, orgs, [answer(["personal"])])
    with pytest.raises(OwnerUnknown):
        await pm.remember_shared("I like tea.", bot_id="b", source="s")
    with pytest.raises(OwnerUnknown):
        await pm.remember_shared("I like tea.", bot_id="b", source="s", stores=[PERSONAL])
    assert store.puts == []


async def test_world_needs_no_owner(tmp_path, ledger, people, orgs):
    pm, _, store = make(tmp_path, ledger, people, orgs, [])
    await pm.remember_shared("Water boils at 100 C.", bot_id="b", source="s", stores=[WORLD])
    assert store.puts == [[Target(WORLD)]]


async def test_unrouted_text_without_owner_spends_no_routing_call(tmp_path, ledger, people, orgs):
    """Routing could pick the personal store, which waits for the owner: the
    text waits before the routing call, not after it."""
    pm, provider, store = make(tmp_path, ledger, people, orgs, [answer(["world"])])
    with pytest.raises(OwnerUnknown):
        await pm.plan_stores("Water boils at 100 C.")
    with pytest.raises(OwnerUnknown):
        await pm.remember_shared("Water boils at 100 C.", bot_id="b", source="s")
    assert provider.calls == [] and store.puts == []


async def test_new_org_registered_only_with_owner_relation(tmp_path, ledger, people, orgs):
    people.set_owner("Alex Rivera", [])
    script = [answer(["personal", "org:acme"], [("Acme", "owns"), ("Bolt", "vendor")])]
    pm, _, store = make(tmp_path, ledger, people, orgs, script)
    await pm.remember_shared("I own Acme; Bolt supplies us.", bot_id="b", source="s")
    assert [o.slug for o in orgs.all()] == ["acme"] and orgs.get("acme").relation == "owns"
    assert [t.store for t in store.puts[0]] == [PERSONAL, org_store("acme")]


def summary_fact(store) -> str:
    f = Fact(text="summary", namespace=SHARED, tier=Tier.project)
    store.put(f)
    return f.id


async def test_extract_attaches_each_routed_store(tmp_path, ledger, people, orgs):
    people.set_owner("Alex Rivera", [])
    pm, _, store = make(tmp_path, ledger, people, orgs, [answer(["personal", "world"])])
    fid = summary_fact(store)
    written = await pm.extract(fid, "My notes.", None)
    assert written == [PERSONAL, WORLD]
    assert [(f, t, tg.store) for f, t, tg in store.attached] == [(fid, "My notes.", PERSONAL), (fid, "My notes.", WORLD)]
    assert ledger.tail(kind="memory")[-1].data["event"] == "attach"


async def test_extract_retry_attaches_each_store_once(tmp_path, ledger, people, orgs):
    people.set_owner("Alex Rivera", [])
    pm, _, store = make(tmp_path, ledger, people, orgs, [])
    fid = summary_fact(store)
    store.fail_on = {2}
    with pytest.raises(RuntimeError, match="retain failed"):
        await pm.extract(fid, "My notes.", [PERSONAL, WORLD])
    failed = ledger.tail(kind="memory")[-1].data
    assert failed["event"] == "failed" and failed["stores"] == [PERSONAL]
    assert await pm.extract(fid, "My notes.", [PERSONAL, WORLD]) == [PERSONAL, WORLD]
    assert [tg.store for _, _, tg in store.attached] == [PERSONAL, WORLD]  # one attach per store overall


async def test_extract_stops_between_stores(tmp_path, ledger, people, orgs):
    """Shutdown: the store being written finishes, the next is never started,
    and a later run writes only what is missing."""
    import threading

    from src.memory.graph import Stopped

    people.set_owner("Alex Rivera", [])
    pm, _, store = make(tmp_path, ledger, people, orgs, [])
    fid = summary_fact(store)
    stop = threading.Event()
    real = store.attach

    def attach_then_stop(fact_id, text, target):
        real(fact_id, text, target)
        stop.set()  # shutdown arrives while the first store is writing

    store.attach = attach_then_stop
    with pytest.raises(Stopped):
        await pm.extract(fid, "My notes.", [PERSONAL, WORLD], stop=stop)
    assert [tg.store for _, _, tg in store.attached] == [PERSONAL]
    last = ledger.tail(kind="memory")[-1].data
    assert last["event"] == "attach_stopped" and last["stores"] == [PERSONAL]
    store.attach = real
    assert await pm.extract(fid, "My notes.", [PERSONAL, WORLD]) == [PERSONAL, WORLD]
    assert [tg.store for _, _, tg in store.attached] == [PERSONAL, WORLD]


async def test_extract_runs_the_exclusion_filter_before_routing(tmp_path, ledger, people, orgs):
    people.set_owner("Alex Rivera", [])
    pm, provider, store = make(tmp_path, ledger, people, orgs, [answer(["personal"])])
    fid = summary_fact(store)
    with pytest.raises(ExclusionError):
        await pm.extract(fid, "api key is sk-abcdefghijklmnopqrstuvwxyz0123456789", None)
    assert store.attached == [] and provider.calls == []
    assert ledger.tail(kind="memory")[-1].data["event"] == "refused"


async def test_remember_shared_runs_the_exclusion_filter_before_routing(tmp_path, ledger, people, orgs):
    people.set_owner("Alex Rivera", [])
    pm, provider, store = make(tmp_path, ledger, people, orgs, [answer(["personal"])])
    with pytest.raises(ExclusionError):
        await pm.remember_shared("api key is sk-abcdefghijklmnopqrstuvwxyz0123456789", bot_id="b", source="s")
    assert store.puts == [] and provider.calls == []


async def test_extract_unknown_fact_raises_before_routing(tmp_path, ledger, people, orgs):
    pm, provider, store = make(tmp_path, ledger, people, orgs, [answer(["world"])])
    with pytest.raises(KeyError):
        await pm.extract("nope", "text", None)
    assert provider.calls == [] and store.attached == []


async def test_extract_refuses_a_store_without_attach(tmp_path, ledger, people, orgs):
    pm, provider, _ = make(tmp_path, ledger, people, orgs, [], store=LocalGraph(tmp_path / "g"))
    with pytest.raises(TypeError, match="attach"):
        await pm.extract("f1", "text", None)
    assert provider.calls == []  # fails before spending a routing call


async def test_preflight_checks_store_fact_and_text_without_routing(tmp_path, ledger, people, orgs):
    pm, provider, store = make(tmp_path, ledger, people, orgs, [answer(["world"])])
    fid = summary_fact(store)
    pm.preflight(fid, "fine text")
    with pytest.raises(KeyError):
        pm.preflight("nope", "fine text")
    with pytest.raises(ExclusionError):
        pm.preflight(fid, "api key is sk-abcdefghijklmnopqrstuvwxyz0123456789")
    assert provider.calls == []
    pm2, _, _ = make(tmp_path, ledger, people, orgs, [], store=LocalGraph(tmp_path / "g2"))
    with pytest.raises(TypeError, match="attach"):
        pm2.preflight("f1", "text")


def test_targets_carry_people_and_org_hints(tmp_path, ledger, people, orgs):
    """The targets carry the entity names the store should link the text to."""
    people.set_owner("Alex Rivera", ["Lexi"])
    people.add("Dana Reyes")
    people.add("Someone Else")
    orgs.add("Acme Widgets", "owns")
    pm, _, _ = make(tmp_path, ledger, people, orgs, [])
    personal, org, world = pm.targets_for([PERSONAL, org_store("acme_widgets"), WORLD], "Lunch with Dana Reyes.")
    assert personal == Target(PERSONAL, hints=("Alex Rivera", "Dana Reyes"))
    assert org == Target(org_store("acme_widgets"), hints=("Alex Rivera", "Dana Reyes", "Acme Widgets"))
    assert world == Target(WORLD, hints=("Alex Rivera", "Dana Reyes"))

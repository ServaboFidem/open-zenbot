import json
import threading
import time
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest

from src.memory.graph import SHARED, Fact, MemoryGraph, Target, Tier, private_ns
from src.memory.hindsight_http import HindsightHTTP
from src.memory.hindsight_store import HindsightGraph, retain_mission, text_hash
from src.memory.registry import PersonEntry
from src.memory.stores import PERSONAL, SHARED_TAG, WORLD, org_store

BANK = "/v1/default/banks/zen"


class FakeHindsight:
    """Records every request; the test sets operation states and outages."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, object]] = []
        self.ops: dict[str, str] = {}
        self.new_op_state = "pending"
        self.down = False
        self.cancel_status = 200
        self.recall_results: list[dict] = []
        self.recall_hook = None  # called inside a recall request
        self.recall_error = False
        self.recall_truncated = False
        self.banks: set[str] = set()
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.http = HindsightHTTP("http://hs", client=client)

    def handle(self, r: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=r)
        path = r.url.path.removeprefix(BANK)
        body = json.loads(r.content) if r.content else None
        self.requests.append((r.method, path, body))
        if path == "/v1/default/banks":
            return httpx.Response(200, json={"banks": [{"bank_id": b} for b in sorted(self.banks)]})
        if path == "" and r.method == "PUT":
            self.banks.add("zen")
        if path == "/memories/recall":
            if self.recall_hook is not None:
                self.recall_hook()
            if self.recall_error:
                return httpx.Response(500, json={"detail": "boom"})
            return httpx.Response(200, json={"results": self.recall_results,
                                             "source_facts_truncated": self.recall_truncated})
        if r.method == "POST" and path == "/memories":
            op = body["operation_id"]
            self.ops.setdefault(op, self.new_op_state)
            return httpx.Response(200, json={"success": True, "operation_id": op})
        if path.startswith("/operations/"):
            op = path.rsplit("/", 1)[1]
            if r.method == "GET":
                return httpx.Response(200, json={"status": self.ops.get(op, "not_found")})
            if self.cancel_status != 200:
                return httpx.Response(self.cancel_status, json={"detail": "already terminal"})
            self.ops[op] = "cancelled"
        return httpx.Response(200, json={})

    def calls(self, method: str, prefix: str) -> list:
        return [(p, b) for m, p, b in self.requests if m == method and p.startswith(prefix)]

    def retains(self) -> list[dict]:
        return [b["items"][0] | {"operation_id": b["operation_id"]} for _, b in self.calls("POST", "/memories")]

    def patches(self) -> list[list[str]]:
        return [b["tags"] for _, b in self.calls("PATCH", "/documents/")]

    def configs(self) -> list[dict]:
        return [b["updates"] for _, b in self.calls("PATCH", "/config")]

    def settle(self, state: str = "completed") -> None:
        for op, s in self.ops.items():
            if s in ("pending", "processing"):
                self.ops[op] = state


class Ledger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict]] = []

    def append(self, kind, bot_id, session_id, **data):
        self.entries.append((kind, data))


@pytest.fixture
def fake():
    return FakeHindsight()


@pytest.fixture
def store(tmp_path, fake):
    s = HindsightGraph(tmp_path, http=fake.http)
    s.ledger = Ledger()
    return s


def records(root) -> dict:
    return json.loads((root / "hindsight" / "retained.json").read_text(encoding="utf-8"))


def shared(text="Dana likes tea", **kw) -> Fact:
    return Fact(text=text, namespace=SHARED, tier=Tier.project, **kw)


def test_private_fact_is_tagged_with_its_bot(store, fake):
    f = Fact(text="my scratch note", namespace=private_ns("writer"), tier=Tier.private, bot_id="writer")
    store.put(f, [Target(PERSONAL)])  # targets never widen a private fact
    assert store.tags_for(f) == ["bot:writer"]
    assert [r["tags"] for r in fake.retains()] == [["bot:writer"]]
    with pytest.raises(ValueError):
        store.attach(f.id, "more text", Target(WORLD))
    assert len(fake.retains()) == 1 and fake.patches() == []
    assert store.stores() == []


def test_shared_fact_takes_one_tag_per_target(store, fake):
    f = shared()
    store.put(f, [Target(PERSONAL), Target(org_store("acme")), Target(WORLD)])
    assert fake.retains()[0]["tags"] == ["personal", "org:acme", "world"]
    assert store.stores() == ["org:acme", "personal", "world"]
    with pytest.raises(ValueError):
        store.put(shared(), [Target("bot:writer")])  # a shared fact never lands in a bot's tag


def test_untargeted_shared_fact_is_tagged_shared(store, fake):
    f = shared()
    store.put(f)
    assert store.tags_for(f) == [SHARED_TAG]
    assert fake.retains()[0]["tags"] == ["shared"]
    assert store.stores() == []


def test_put_retains_once_with_hints_and_metadata(store, fake):
    f = shared(entities=["dana lee"], bot_id="writer", source="sess-1")
    store.put(f, [Target(PERSONAL, hints=("Alex Rivera",))])
    (item,) = fake.retains()
    assert item["document_id"] == f.id and item["content"] == f.text
    assert item["entities"] == [{"text": "dana lee"}, {"text": "Alex Rivera"}]
    assert item["metadata"] == {"namespace": "shared", "tier": "project", "bot_id": "writer", "source": "sess-1", "at": f.at}
    assert item["operation_id"] == str(uuid5(NAMESPACE_URL, f"{f.id}:{text_hash(f.text)}:0"))
    rec = records(store.root)["facts"][f.id]
    assert rec["state"] == "pending" and rec["op"] == item["operation_id"] and rec["sent_tags"] == ["personal"]


def test_attach_same_text_patches_tags_only(store, fake):
    f = shared()
    store.put(f, [Target(PERSONAL)])
    fake.settle()
    assert store.reconcile()["completed"] == 1
    store.attach(f.id, f.text, Target(WORLD))
    assert len(fake.retains()) == 1
    assert fake.patches() == [["personal", "world"]]
    assert store.has_item(f.id, WORLD)


def test_attach_document_text_retains_again(store, fake):
    f = shared("summary of the doc")
    store.put(f, [Target(PERSONAL)])
    fake.settle()
    store.reconcile()
    store.attach(f.id, "the full markdown body", Target(WORLD))
    first, second = fake.retains()
    assert second["content"] == "the full markdown body"
    assert second["tags"] == ["personal", "world"]
    assert second["operation_id"] != first["operation_id"]
    assert fake.patches() == []
    assert store.get(f.id).text == "summary of the doc"  # the index keeps the summary


def test_ingested_summary_moves_from_shared_to_its_stores(store, fake):
    f = shared("summary")
    store.put(f)
    store.attach(f.id, "body", Target(PERSONAL))
    store.attach(f.id, "body", Target(WORLD))
    assert [r["tags"] for r in fake.retains()] == [["shared"], ["personal"]]
    assert fake.patches() == []  # deferred while the body's retain is pending
    fake.settle()
    store.reconcile()
    assert fake.patches() == [["personal", "world"]]
    assert records(store.root)["facts"][f.id]["tags"] == ["personal", "world"]
    assert store.stores() == ["personal", "world"]


def test_attach_while_pending_defers_the_patch(store, fake):
    f = shared()
    store.put(f, [Target(PERSONAL)])
    store.attach(f.id, f.text, Target(WORLD))
    assert fake.patches() == [] and len(fake.retains()) == 1
    fake.settle()
    out = store.reconcile()
    assert out["completed"] == 1 and out["patched"] == 1
    assert fake.patches() == [["personal", "world"]]
    store.reconcile()
    assert len(fake.patches()) == 1


def test_put_while_hindsight_is_down_is_sent_by_the_reconciler(store, fake):
    fake.down = True
    f = shared()
    store.put(f, [Target(PERSONAL)])  # nothing raises
    assert store.get(f.id) == f
    assert records(store.root)["facts"][f.id]["state"] == "unsent"
    assert store.reconcile()["sent"] == 0  # still down: nothing lost, nothing raised
    fake.down = False
    assert store.reconcile()["sent"] == 1
    (item,) = fake.retains()
    assert item["tags"] == ["personal"]
    assert records(store.root)["facts"][f.id]["state"] == "pending"


def test_forget_a_pending_fact_cancels_and_is_never_resent(store, fake):
    f = shared()
    store.put(f, [Target(PERSONAL)])
    op = fake.retains()[0]["operation_id"]
    before = len(fake.requests)
    assert store.delete(f.id) is True
    assert [(m, p) for m, p, _ in fake.requests[before:]] == [
        ("DELETE", f"/operations/{op}"), ("DELETE", f"/documents/{f.id}"), ("GET", f"/operations/{op}")]
    assert store.get(f.id) is None and f.id not in records(store.root)["facts"]
    after = len(fake.requests)
    fake.settle("failed")
    assert sum(store.reconcile().values()) == 0
    assert len(fake.requests) == after
    assert store.delete(f.id) is False


def test_forget_deletes_the_document_even_when_cancel_is_refused(store, fake):
    f = shared()
    store.put(f, [Target(PERSONAL)])
    fake.settle()  # the operation finished first: cancel answers 409
    fake.cancel_status = 409
    assert store.delete(f.id) is True
    assert fake.calls("DELETE", "/documents/") == [(f"/documents/{f.id}", None)]
    assert f.id not in records(store.root)["facts"]


def test_forget_of_a_never_sent_fact_works_offline(store, fake):
    fake.down = True
    f = shared()
    store.put(f)
    assert store.delete(f.id) is True
    assert store.get(f.id) is None and f.id not in records(store.root)["facts"]
    fake.down = False
    assert sum(store.reconcile().values()) == 0 and fake.requests == []


def test_forget_while_hindsight_is_down_is_finished_by_the_reconciler(store, fake):
    f = shared("a secret")
    store.put(f, [Target(PERSONAL)])
    op = fake.retains()[0]["operation_id"]
    fake.down = True
    assert store.delete(f.id) is True  # forget never waits on Hindsight
    assert store.get(f.id) is None
    assert not store.has_item(f.id, PERSONAL) and store.stores() == []
    tomb = records(store.root)["facts"][f.id]
    assert tomb["state"] == "deleting" and tomb["tags"] == [] and "text" not in tomb
    assert store.delete(f.id) is False
    assert store.reconcile()["forgotten"] == 0  # still down: the tombstone stays
    fake.down = False
    before = len(fake.requests)
    out = store.reconcile()
    assert out["forgotten"] == 1 and out["sent"] == out["retried"] == 0
    assert [(m, p) for m, p, _ in fake.requests[before:]] == [
        ("DELETE", f"/operations/{op}"), ("DELETE", f"/documents/{f.id}"), ("GET", f"/operations/{op}")]
    assert len(fake.retains()) == 1
    assert f.id not in records(store.root)["facts"]


def test_a_tombstone_without_its_fact_is_deleted_not_dropped_after_a_restart(tmp_path, store, fake):
    f = shared()
    store.put(f, [Target(PERSONAL)])
    fake.settle()
    store.reconcile()
    fake.down = True
    store.delete(f.id)  # tombstone saved, fact gone from the index, remote delete failed
    fake.down = False
    again = HindsightGraph(tmp_path, http=fake.http)
    assert again.get(f.id) is None
    assert again.reconcile()["forgotten"] == 1
    assert fake.calls("DELETE", "/documents/") == [(f"/documents/{f.id}", None)]
    assert len(fake.retains()) == 1 and f.id not in records(tmp_path)["facts"]


def test_forget_after_a_timed_out_retain_takes_the_tombstone_path(tmp_path, fake):
    taken: list[str] = []

    def slow(r: httpx.Request) -> httpx.Response:
        if r.method == "POST":  # the server takes the retain, but the answer never arrives
            taken.append(json.loads(r.content)["operation_id"])
            fake.ops[taken[-1]] = "pending"
            raise httpx.ReadTimeout("timed out", request=r)
        return fake.handle(r)

    store = HindsightGraph(tmp_path, http=HindsightHTTP("http://hs", client=httpx.Client(transport=httpx.MockTransport(slow))))
    f = shared()
    store.put(f, [Target(PERSONAL)])
    assert records(tmp_path)["facts"][f.id]["state"] == "unsent"
    assert store.delete(f.id) is True
    assert [(m, p) for m, p, _ in fake.requests] == [
        ("DELETE", f"/operations/{taken[0]}"), ("DELETE", f"/documents/{f.id}"), ("GET", f"/operations/{taken[0]}")]
    assert f.id not in records(tmp_path)["facts"]


def test_forget_keeps_the_tombstone_while_the_retain_still_runs(store, fake):
    f = shared()
    store.put(f, [Target(PERSONAL)])
    fake.cancel_status = 409  # cancel refused while the operation is processing
    fake.settle("processing")
    assert store.delete(f.id) is True
    assert records(store.root)["facts"][f.id]["state"] == "deleting"  # it could still write the document
    fake.settle()
    assert store.reconcile()["forgotten"] == 1
    assert len(fake.calls("DELETE", "/documents/")) == 2


def test_owner_rename_bumps_the_mission_and_attach_retains_again(store, fake):
    owner = {"p": PersonEntry("Alex", aliases=["Lexi"])}
    store.owner_source = lambda: owner["p"]
    f = shared()
    store.put(f, [Target(PERSONAL)])
    (cfg,) = fake.configs()
    assert cfg == {"retain_mission": retain_mission("Alex", ["Lexi"])} and "Alex" in cfg["retain_mission"]
    fake.settle()
    store.reconcile()
    store.put(shared("another"), [Target(PERSONAL)])
    assert len(fake.configs()) == 1  # unchanged owner: the mission is not sent again
    assert store.has_item(f.id, PERSONAL)
    owner["p"] = PersonEntry("Alex Rivera", aliases=["Lexi"])
    assert not store.has_item(f.id, PERSONAL)  # a rename is pending: the next attach must re-retain
    store.attach(f.id, f.text, Target(PERSONAL))
    assert len(fake.configs()) == 2 and "Alex Rivera" in fake.configs()[1]["retain_mission"]
    assert len(fake.retains()) == 3
    assert fake.retains()[-1]["document_id"] == f.id
    assert records(store.root)["mission"] == 2
    assert store.has_item(f.id, PERSONAL)


def test_failed_retain_is_retried_three_times_then_ledgered(store, fake):
    fake.new_op_state = "failed"
    f = shared()
    store.put(f, [Target(PERSONAL)])
    for _ in range(3):
        assert store.reconcile()["retried"] == 1
    out = store.reconcile()
    assert out["failed"] == 1 and out["retried"] == 0
    retains = fake.retains()
    assert len(retains) == 4  # the first send and three retries
    assert len({r["operation_id"] for r in retains}) == 4  # Hindsight dedupes by operation id
    assert records(store.root)["facts"][f.id]["state"] == "failed"
    errors = [d for k, d in store.ledger.entries if k == "error"]
    assert len(errors) == 1 and errors[0]["where"] == "hindsight_retain" and errors[0]["fact_id"] == f.id
    assert sum(store.reconcile().values()) == 0
    assert len(fake.retains()) == 4


def test_pruned_operation_is_resent(store, fake):
    f = shared()
    store.put(f, [Target(PERSONAL)])
    fake.ops.clear()  # the server no longer knows the operation
    assert store.reconcile()["retried"] == 1
    assert len(fake.retains()) == 2


def test_rebuild_marks_shared_facts_for_retain(store, fake):
    a, b = shared("a fact"), shared("b fact")
    p = Fact(text="private", namespace=private_ns("writer"), tier=Tier.private, bot_id="writer")
    store.put(a, [Target(PERSONAL)])
    store.put(b, [Target(WORLD)])
    store.put(p)
    fake.settle()
    store.reconcile()
    priv_before = records(store.root)["facts"][p.id]
    assert store.drop_shared_datasets() == 2
    assert not store.has_item(a.id, PERSONAL) and not store.has_item(b.id, WORLD)
    assert records(store.root)["facts"][p.id] == priv_before
    store.attach(a.id, a.text, Target(PERSONAL))
    assert len(fake.retains()) == 4 and fake.retains()[-1]["document_id"] == a.id
    first, again = fake.retains()[0]["operation_id"], fake.retains()[-1]["operation_id"]
    assert again != first  # a reused id would get the old, completed operation back and extract nothing
    assert fake.ops[again] == "pending"
    assert store.has_item(a.id, PERSONAL)


def test_rebuild_keeps_an_unsent_document_body(store, fake):
    f = shared("summary")
    store.put(f, [Target(PERSONAL)])
    fake.settle()
    store.reconcile()
    fake.down = True
    store.attach(f.id, "the full body", Target(PERSONAL))  # body retain left unsent by the outage
    assert store.drop_shared_datasets() == 1
    fake.down = False
    assert store.reconcile()["sent"] == 1
    assert fake.retains()[-1]["content"] == "the full body"  # not the summary
    assert store.has_item(f.id, PERSONAL)


def test_index_survives_restart(tmp_path, store, fake):
    a = shared()
    p = Fact(text="private", namespace=private_ns("writer"), tier=Tier.private, bot_id="writer")
    store.put(a, [Target(PERSONAL)])
    store.put(p)
    again = HindsightGraph(tmp_path, http=fake.http)
    assert again.get(a.id) == a and again.facts(private_ns("writer")) == [p]
    assert again.stores() == ["personal"]
    assert again.has_item(a.id, PERSONAL)
    fake.settle()
    assert again.reconcile()["completed"] == 2


def test_attach_to_a_forgotten_fact_is_a_key_error(store):
    with pytest.raises(KeyError):
        store.attach("nope", "text", Target(PERSONAL))


def test_memory_graph_hands_its_ledger_to_the_store(tmp_path, fake):
    s = HindsightGraph(tmp_path, http=fake.http)
    ledger = Ledger()
    MemoryGraph(s, ledger)
    assert s.ledger is ledger


# -- recall ----------------------------------------------------------------


def recalls(fake) -> list:
    return fake.calls("POST", "/memories/recall")


def hit(fact: Fact | None = None, **kw) -> dict:
    return {"id": "r", "text": fact.text if fact else "", "type": "world",
            "document_id": fact.id if fact else None, "tags": []} | kw


def test_scope_for_a_bot_reads_its_own_tag_and_every_shared_store(store):
    store.put(shared("a"), [Target(PERSONAL)])
    store.put(shared("b"), [Target(org_store("acme"))])
    store.put(Fact(text="w", namespace=private_ns("writer"), tier=Tier.private, bot_id="writer"))
    got = store.scope(["bot:research-partner", SHARED])
    assert sorted(got) == sorted(["bot:research-partner", SHARED_TAG, "org:acme", "personal"])
    assert "bot:writer" not in got
    assert store.scope(["bot:x"]) == ["bot:x"]


def test_recall_maps_documents_to_facts_and_observations_to_sentences(store, fake):
    a, b = shared("Dana likes tea"), shared("Dana owns a cat")
    store.put(a, [Target(PERSONAL)])
    store.put(b, [Target(PERSONAL)])
    fake.recall_results = [hit(b), hit(a), hit(a), hit(None, type="observation", text="Dana is a tea drinker", tags=["personal"])]
    assert [f.id for f in store.search("dana", [SHARED], limit=10)] == [b.id, a.id]
    assert [f.id for f in store.search("dana", [SHARED], limit=1)] == [b.id]
    assert store.graph_context("dana", [SHARED]) == ["Dana is a tea drinker"]
    assert len(recalls(fake)) == 1
    assert recalls(fake)[0][1]["tags"] == [SHARED_TAG, "personal"]


def test_unknown_document_is_dropped_and_ledgered(store, fake):
    a = shared()
    store.put(a, [Target(PERSONAL)])
    fake.recall_results = [hit(None, document_id="ghost", text="stray"), hit(a)]
    assert [f.id for f in store.search("q", [SHARED])] == [a.id]
    (kind, data), = [e for e in store.ledger.entries if e[0] == "memory"]
    assert data["event"] == "hindsight_unknown_document" and data["document_id"] == "ghost"


def test_another_bots_fact_is_dropped_even_if_returned(store, fake):
    mine = Fact(text="mine", namespace=private_ns("a"), tier=Tier.private, bot_id="a")
    theirs = Fact(text="theirs", namespace=private_ns("b"), tier=Tier.private, bot_id="b")
    store.put(mine)
    store.put(theirs)
    fake.recall_results = [hit(theirs), hit(mine)]
    assert [f.id for f in store.search("q", [private_ns("a")])] == [mine.id]


def test_observation_with_a_foreign_tag_is_dropped(store, fake):
    store.put(shared(), [Target(PERSONAL)])
    obs = lambda text, tags: hit(None, type="observation", text=text, tags=tags)  # noqa: E731
    fake.recall_results = [obs("ok", ["personal"]), obs("leak", ["personal", "bot:b"]), obs("untagged", []),
                           obs("other org", ["org:zzz"])]
    assert store.graph_context("q", [SHARED]) == ["ok"]


def obs_of(*facts: Fact, tags: list[str], text: str = "an observation", source_tags: list[str] | None = None) -> dict:
    """An observation result with its source facts attached (as HindsightHTTP.recall hands them over)."""
    return hit(None, type="observation", text=text, tags=tags,
               source_facts=[{"id": f"src-{f.id}", "text": f.text, "document_id": f.id, "type": "world",
                              "tags": tags if source_tags is None else source_tags} for f in facts])


def test_an_observation_surfaces_the_facts_behind_it(store, fake):
    # Once consolidated, Hindsight answers with the observation alone: its source facts stand in its place.
    a, b, c = shared("Dana likes tea"), shared("Dana owns a cat"), shared("Dana lives in Ohio")
    for f in (a, b, c):
        store.put(f, [Target(PERSONAL)])
    fake.recall_results = [hit(c), obs_of(a, b, tags=["personal"], text="Dana is a tea drinker with a cat")]
    assert [f.id for f in store.search("dana", [SHARED], limit=10)] == [c.id, a.id, b.id]
    assert store.graph_context("dana", [SHARED]) == ["Dana is a tea drinker with a cat"]
    assert len(recalls(fake)) == 1


def test_a_private_observation_surfaces_its_bots_fact(store, fake):
    mine = Fact(text="The draft codename is Pelican.", namespace=private_ns("writer"), tier=Tier.private,
                bot_id="writer")
    store.put(mine)
    fake.recall_results = [obs_of(mine, tags=["bot:writer"])]
    assert [f.id for f in store.search("codename", [private_ns("writer")])] == [mine.id]


def test_an_out_of_scope_source_fact_is_not_surfaced(store, fake):
    ok = shared("Dana likes tea")
    store.put(ok, [Target(PERSONAL)])
    theirs = Fact(text="secret", namespace=private_ns("b"), tier=Tier.private, bot_id="b")
    store.put(theirs)
    other = shared("Zed's org plan")
    store.put(other, [Target(org_store("zzz"))])
    fake.recall_results = [
        obs_of(ok, theirs, tags=["personal"]),  # a stray source of another bot: its namespace is not asked for
        obs_of(other, tags=["personal"], source_tags=["org:zzz", "bot:b"]),  # source tags outside the scope
        obs_of(ok, tags=["personal", "bot:b"]),  # the observation itself is out of scope: none of its sources
    ]
    assert [f.id for f in store.search("q", [SHARED])] == [ok.id]
    assert [f.id for f in store.search("q", [private_ns("a")])] == []


def test_a_fact_returned_directly_and_behind_an_observation_is_not_doubled(store, fake):
    a, b = shared("Dana likes tea"), shared("Dana owns a cat")
    store.put(a, [Target(PERSONAL)])
    store.put(b, [Target(PERSONAL)])
    fake.recall_results = [obs_of(a, b, tags=["personal"]), hit(a), obs_of(b, tags=["personal"])]
    assert [f.id for f in store.search("dana", [SHARED], limit=10)] == [a.id, b.id]
    assert [f.id for f in store.search("dana", [SHARED], limit=1)] == [a.id]


def test_empty_scope_makes_no_call(store, fake):
    assert store.search("q", []) == [] and store.graph_context("q", ["other"]) == []
    assert recalls(fake) == []


def until(cond, what: str, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not cond():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.005)


class WatchedEvent(threading.Event):
    waiters = 0

    def wait(self, timeout=None):
        type(self).waiters += 1
        return super().wait(timeout)


def test_parallel_search_and_graph_context_share_one_call(store, fake, monkeypatch):
    import src.memory.hindsight_store as hs
    store.put(shared(), [Target(PERSONAL)])
    monkeypatch.setattr(hs, "RECALL_CACHE_S", 0.0)  # a cache hit cannot fake the pass: only the in-flight wait shares
    WatchedEvent.waiters = 0
    orig = hs._Recall

    class Watched(orig):
        def __init__(self):
            super().__init__()
            self.done = WatchedEvent()

    monkeypatch.setattr(hs, "_Recall", Watched)
    release = threading.Event()
    fake.recall_hook = lambda: release.wait(5)
    ns = [private_ns("writer"), SHARED]  # what recall.py passes to both
    out: dict = {}
    t1 = threading.Thread(target=lambda: out.setdefault("s", store.search("q", ns)))
    t2 = threading.Thread(target=lambda: out.setdefault("g", store.graph_context("q", ns)))
    t1.start()
    until(lambda: len(recalls(fake)) == 1, "the first recall to reach the transport")
    t2.start()
    until(lambda: WatchedEvent.waiters == 1, "the second caller to wait on the in-flight call")
    release.set()
    t1.join(5)
    t2.join(5)
    assert "s" in out and "g" in out and len(recalls(fake)) == 1


def test_a_failed_in_flight_call_wakes_waiters_with_the_error(store, fake):
    from src.memory.hindsight_http import HindsightError
    store.put(shared(), [Target(PERSONAL)])
    release = threading.Event()
    fake.recall_hook = lambda: release.wait(5)
    fake.recall_error = True
    errs: list = []

    def run(fn):
        try:
            fn()
        except HindsightError as e:
            errs.append(e)

    t1 = threading.Thread(target=run, args=(lambda: store.search("q", [SHARED]),))
    t1.start()
    until(lambda: recalls(fake), "the first recall to reach the transport")
    t2 = threading.Thread(target=run, args=(lambda: store.graph_context("q", [SHARED]),))
    t2.start()
    threading.Event().wait(0.1)
    release.set()
    t1.join(5)
    t2.join(5)
    assert len(errs) == 2 and len(recalls(fake)) == 1
    fake.recall_error = False
    fake.recall_hook = None
    assert store.graph_context("q", [SHARED]) == [] and len(recalls(fake)) == 2  # an error is not cached


def test_cache_expires(store, fake):
    from src.memory.hindsight_store import RECALL_CACHE_S
    store.put(shared(), [Target(PERSONAL)])
    now = [100.0]
    store._clock = lambda: now[0]
    store.search("q", [SHARED])
    now[0] += RECALL_CACHE_S - 0.1
    store.graph_context("q", [SHARED])
    assert len(recalls(fake)) == 1
    now[0] += 0.2
    store.search("q", [SHARED])
    assert len(recalls(fake)) == 2
    store.search("other", [SHARED])
    assert len(recalls(fake)) == 3


def test_search_without_a_timeout_uses_the_recall_graph_timeout(store, fake, monkeypatch):
    seen = []
    orig = store.http.recall
    monkeypatch.setattr(store.http, "recall", lambda q, tags, **kw: seen.append(kw.get("timeout")) or orig(q, tags, **kw))
    store.put(shared(), [Target(PERSONAL)])
    assert store.search_timeout_s == 20.0
    store.search("q", [SHARED])
    store.search("q2", [SHARED], timeout=3.0)
    assert seen == [20.0, 3.0]


def test_recall_makes_one_hindsight_request_per_turn(store, fake, vault):
    import asyncio
    from src.harness.bot import Bot
    from src.memory.ledger import Ledger as RealLedger
    from src.memory.recall import MemoryRecall, RecallConfig
    from src.memory.graph import SessionCache
    ledger = RealLedger(vault)
    g = MemoryGraph(store, ledger)
    mine = Fact(text="private note", namespace=private_ns("pa"), tier=Tier.private, bot_id="pa")
    pub = shared("Dana likes tea")
    store.put(mine)
    store.put(pub, [Target(PERSONAL)])
    fake.recall_results = [hit(mine), hit(pub), hit(None, type="observation", text="Dana drinks tea", tags=["personal"])]
    r = MemoryRecall(g, SessionCache(), None, ledger, RecallConfig())
    got = asyncio.run(r.recall_detail(Bot(name="PA", role="assistant", id="pa"), "dana tea", "s1"))
    assert len(recalls(fake)) == 1
    assert got.sources["private"] == 1 and got.sources["shared"] == 1 and got.sources["graph"] == 1


# -- final fix wave ---------------------------------------------------------


def test_a_failed_retain_given_a_new_round_goes_out_under_a_new_operation_id(store, fake):
    fake.new_op_state = "failed"
    f = shared()
    store.put(f, [Target(PERSONAL)])
    for _ in range(4):
        store.reconcile()
    assert records(store.root)["facts"][f.id]["state"] == "failed"
    fake.new_op_state = "pending"
    assert store._requeue_failed() == 1
    assert store.reconcile()["sent"] == 1
    ids = [r["operation_id"] for r in fake.retains()]
    assert len(ids) == 5 and len(set(ids)) == 5  # a reused id would get the failed operation back
    fake.settle()
    assert store.reconcile()["completed"] == 1


def settled(store, fake, *facts):
    for f in facts:
        store.put(f, [Target(PERSONAL)])
    fake.settle()
    store.reconcile()
    assert all(records(store.root)["facts"][f.id]["state"] == "ok" for f in facts)


def test_a_new_bank_sends_every_record_again_and_drops_tombstones(store, fake):
    a, b, gone = shared("a fact"), shared("b fact"), shared("forgotten")
    settled(store, fake, a, b, gone)
    fake.down = True
    store.delete(gone.id)  # a tombstone, waiting for Hindsight
    fake.down = False
    n = len(fake.retains())
    store.start()  # the bank did not exist: an emptied ~/.pg0, a moved vault
    try:
        recs = records(store.root)["facts"]
        assert gone.id not in recs  # nothing to delete in a new bank
        assert ("DELETE", f"/documents/{gone.id}", None) not in fake.requests
        assert len(fake.retains()) == n + 2 and {r["document_id"] for r in fake.retains()[n:]} == {a.id, b.id}
        resync = [d for k, d in store.ledger.entries if k == "memory" and d.get("event") == "store_resync"]
        assert len(resync) == 1 and resync[0]["count"] == 2 and resync[0]["tombstones_dropped"] == 1
        assert records(store.root)["identity"] == {"server": "http://hs", "bank": "zen"}
    finally:
        store.close()


def test_another_database_sends_every_record_again_but_keeps_tombstones(tmp_path, store, fake):
    a, gone = shared("a fact"), shared("forgotten")
    settled(store, fake, a, gone)
    fake.banks.add("zen")
    store.start()  # the identity is recorded
    store.close()
    fake.down = True
    store.delete(gone.id)
    fake.down = False
    d = records(tmp_path)
    d["identity"] = {"server": "pg0:zen-00000000", "bank": "zen"}  # the records were about another database
    (tmp_path / "hindsight" / "retained.json").write_text(json.dumps(d), encoding="utf-8")
    again = HindsightGraph(tmp_path, http=fake.http)
    again.ledger = Ledger()
    n = len(fake.retains())
    again.start()
    try:
        assert [r["document_id"] for r in fake.retains()[n:]] == [a.id]
        assert gone.id not in records(tmp_path)["facts"]  # the tombstone was kept, then its delete went through
        assert ("DELETE", f"/documents/{gone.id}", None) in fake.requests
        (entry,) = [d for k, d in again.ledger.entries if d.get("event") == "store_resync"]
        assert entry["count"] == 1 and entry["tombstones_dropped"] == 0
        assert records(tmp_path)["identity"] == {"server": "http://hs", "bank": "zen"}
    finally:
        again.close()


def test_the_same_database_with_its_bank_changes_nothing(store, fake):
    a = shared("a fact")
    settled(store, fake, a)
    fake.banks.add("zen")
    store.start()
    store.close()
    before, n = records(store.root)["facts"], len(fake.retains())
    store.start()
    try:
        assert records(store.root)["facts"] == before and len(fake.retains()) == n
        assert not [d for k, d in store.ledger.entries if d.get("event") == "store_resync"]
    finally:
        store.close()


def test_a_resync_sends_the_mission_again_to_a_new_bank(store, fake):
    store.owner_source = lambda: PersonEntry("Alex Rivera", ["Alex"])
    a = shared("a fact")
    settled(store, fake, a)
    assert len(fake.configs()) == 1
    store.start()  # a new bank: it has no retain mission
    try:
        assert len(fake.configs()) == 2 and "Alex Rivera" in fake.configs()[1]["retain_mission"]
    finally:
        store.close()


def test_a_completed_retain_drops_its_text_and_a_resync_reads_the_body_again(store, fake):
    f = shared("summary of the doc", source="vault:docs/plan.md")
    store.put(f, [Target(PERSONAL)])
    store.attach(f.id, "# the full body", Target(PERSONAL))
    assert records(store.root)["facts"][f.id]["text"] == "# the full body"  # until it lands
    fake.settle()
    store.reconcile()
    rec = records(store.root)["facts"][f.id]
    assert "text" not in rec and rec["body"] is True  # retained.json does not keep every document forever
    store.text_source = lambda fact: "# the full body, from the vault" if fact.id == f.id else None
    n = len(fake.retains())
    store.start()  # a new bank: resend
    try:
        assert fake.retains()[n]["content"] == "# the full body, from the vault"
    finally:
        store.close()


def test_a_resync_without_a_vault_copy_sends_the_facts_own_text(store, fake):
    f = shared("summary of the doc", source="vault:docs/gone.md")
    store.put(f, [Target(PERSONAL)])
    store.attach(f.id, "# the full body", Target(PERSONAL))
    fake.settle()
    store.reconcile()
    n = len(fake.retains())
    store.start()
    try:
        assert fake.retains()[n]["content"] == "summary of the doc"
    finally:
        store.close()


def test_an_observation_citing_a_forgotten_document_is_dropped(store, fake):
    a, b = shared("Dana likes tea"), shared("Dana owns a cat")
    store.put(a, [Target(PERSONAL)])
    store.put(b, [Target(PERSONAL)])
    fake.down = True
    store.delete(b.id)  # tombstoned: Hindsight still has it
    fake.down = False
    fake.recall_results = [
        obs_of(a, tags=["personal"], text="Dana drinks tea"),
        obs_of(a, b, tags=["personal"], text="Dana has a cat and tea"),  # b is forgotten
        hit(None, type="observation", text="never ours", tags=["personal"],
            source_facts=[{"id": "s", "text": "x", "document_id": "not-indexed", "tags": ["personal"]}]),
        hit(None, type="observation", text="sources not returned", tags=["personal"]),
    ]
    assert store.graph_context("dana", [SHARED]) == ["Dana drinks tea", "sources not returned"]


def test_truncated_source_facts_are_ledgered_at_most_once_a_minute(store, fake):
    now = [100.0]
    store._clock = lambda: now[0]
    fake.recall_truncated = True
    fake.recall_results = [hit(None, type="observation", text="o", tags=["personal"])]
    store.put(shared(), [Target(PERSONAL)])

    def truncated():
        return [d for k, d in store.ledger.entries if d.get("event") == "source_facts_truncated"]

    store.search("one", [SHARED])
    store.search("two", [SHARED])
    assert len(truncated()) == 1 and truncated()[0]["observations"] == 1
    now[0] += 61
    store.search("three", [SHARED])
    assert len(truncated()) == 2
    fake.recall_truncated = False
    now[0] += 61
    store.search("four", [SHARED])
    assert len(truncated()) == 2


def test_a_missing_document_body_is_logged_once_by_fact_id(store, fake, caplog):
    f = shared("summary of the doc", source="vault:docs/gone.md")
    store.put(f, [Target(PERSONAL)])
    store.attach(f.id, "# the secret body", Target(PERSONAL))
    fake.settle()
    store.reconcile()
    caplog.set_level("INFO", logger="src.memory.hindsight_store")
    for _ in range(2):
        store._resync(True)
        store.reconcile()
    lines = [r.getMessage() for r in caplog.records if "no document body" in r.getMessage()]
    assert len(lines) == 1 and f.id in lines[0] and "secret" not in lines[0] and "summary" not in lines[0]

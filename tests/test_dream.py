import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from src.generation.generator import Generator
from src.harness.conversations import Conversation, ConversationStore, Conversations, Exchange
from src.memory import dream as dream_mod
from src.memory.dream import (DreamFact, Dreamer, DreamMarks, DreamSettings, Mark, batches, clean_facts, error_kind, unread,
                              unread_marked)
from src.memory.exclusion import exclusion
from src.memory.graph import SHARED, LocalGraph, MemoryGraph, Tier
from src.memory.ledger import Ledger, ledger_path_for
from src.memory.profile import ProfileMemory
from src.memory.registry import OrgRegistry, PeopleRegistry
from src.providers.interface import Completion, Message, ProviderAuthError, ProviderError, ProviderUnavailable
from src.providers.store import ProviderStoreError
from src.vault_settings import settings_dir


def ex(user, outcome="done", **kw):
    return Exchange(user=user, outcome=outcome, reply="BOT REPLY", addendum="COMMENT BLOCK", **kw)


ok = lambda text: True  # noqa: E731


def test_reads_only_user_text_past_the_mark():
    c = Conversation(owner="b", exchanges=[ex("old"), ex("I live in Columbus"), ex("I prefer metric")])
    assert unread(c, 1, ok) == (["I live in Columbus", "I prefer metric"], 3)


def _skipping():
    return Conversation(owner="b", exchanges=[
        ex("to a room", outcome="routed"), ex("make it blue"), ex("make it blue"), ex("redo", set_aside=True),
        ex("- Size: large", answers_to="s1"), ex("Form answers (q1):\n- Size: large"), ex("   "), ex("keep me")])


def test_skips_but_counts_routed_set_aside_forms_repeats_and_blank():
    assert unread(_skipping(), 0, ok) == (["make it blue", "keep me"], 8)


def test_unread_marked_gives_the_mark_after_each_message():
    assert unread_marked(_skipping(), 0, ok) == ([("make it blue", 2), ("keep me", 8)], 8)


def test_repeat_of_a_message_before_the_mark_is_skipped():
    c = Conversation(owner="b", exchanges=[ex("same"), ex(" same ")])
    assert unread(c, 1, ok) == ([], 2)


def test_stops_before_a_pending_exchange():
    c = Conversation(owner="b", exchanges=[ex("a"), ex("b", outcome="pending"), ex("c")])
    assert unread(c, 0, ok) == (["a"], 1)


def test_refused_message_is_left_out_and_long_one_is_cut():
    c = Conversation(owner="b", exchanges=[ex("sk-secret"), ex("x" * 100_000)])
    msgs, mark = unread(c, 0, lambda t: "secret" not in t)
    assert [len(m) for m in msgs] == [4000] and mark == 2


def test_nothing_to_read_still_moves_the_mark():
    assert unread(Conversation(owner="b", exchanges=[ex("  ")]), 0, ok) == ([], 1)


def test_batches_keep_order_and_the_limit():
    assert batches(["a" * 7000, "b" * 7000, "c" * 100]) == [["a" * 7000], ["b" * 7000, "c" * 100]]


def test_begin_saves_answers_to(tmp_path, ledger):
    convs = Conversations(ConversationStore(tmp_path / "conv"), ledger)
    convs.load()
    _, exch = convs.begin("bot", "- Size: large", session_id="s2", answers_to="s1")
    assert exch.answers_to == "s1"


def test_settings_defaults_save_and_range(tmp_path):
    assert DreamSettings.load(tmp_path) == DreamSettings(False, 5)
    DreamSettings.checked(True, 120).save(tmp_path)
    assert DreamSettings.load(tmp_path) == DreamSettings(True, 120)
    for bad in [(True, 0), (True, 121), (True, 5.5), (True, True), ("yes", 5)]:
        with pytest.raises(ValueError):
            DreamSettings.checked(*bad)


def test_settings_file_is_beside_the_vault(tmp_path):
    DreamSettings(True, 5).save(tmp_path)
    assert (settings_dir(tmp_path) / "dream.json").is_file() and not list(tmp_path.rglob("dream.json"))


def test_damaged_settings_give_the_defaults(tmp_path):
    d = settings_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "dream.json").write_text('{"enabled": "yes", "idle_minutes": 999}', encoding="utf-8")
    assert DreamSettings.load(tmp_path) == DreamSettings(False, 5)


def test_marks_persist_and_retry_schedule(tmp_path):
    m = DreamMarks(tmp_path / "dreams.json")
    m.fail("c1", now=1000.0, seen=4)
    assert m.get("c1").not_before == 1600.0
    m.fail("c1", now=2000.0, seen=4)
    assert m.get("c1").not_before == 5600.0
    m.fail("c1", now=9000.0, seen=4)
    assert m.skipped("c1")
    m.set_read("c1", 4)
    assert DreamMarks(tmp_path / "dreams.json").get("c1") == Mark(read=4, seen=4)
    m.add_learned(3, "2026-11-19T03:12:00Z")
    again = DreamMarks(tmp_path / "dreams.json")
    assert again.learned == 3 and again.last_dream_at == "2026-11-19T03:12:00Z"


def test_grown_unskips_and_keeps_read(tmp_path):
    m = DreamMarks(tmp_path / "dreams.json")
    m.set_read("c1", 2)
    for t in (1.0, 2.0, 3.0):
        m.fail("c1", now=t, seen=5)
    assert m.skipped("c1") and m.get("c1").seen == 5
    m.grown("c1")
    assert not m.skipped("c1") and m.get("c1") == Mark(read=2, seen=5)


def test_damaged_marks_file_is_kept_aside(tmp_path):
    p = tmp_path / "dreams.json"
    p.write_text("{not json", encoding="utf-8")
    assert DreamMarks(p).get("c1") == Mark() and (tmp_path / "dreams.json.bad").is_file()


STORES = {"personal", "world", "org:acme"}


def test_clean_keeps_shape_caps_and_stores():
    raw = {"facts": [{"text": " Gordon lives in Columbus. ", "store": "personal"}, {"text": "Acme ships on Fridays.", "store": "org:acme"},
                     {"text": "Water boils at 100 C.", "store": "org:unknown"}, "Gordon prefers metric units.",
                     {"text": "x" * 301, "store": "personal"}, {"text": "line\nbreak", "store": "personal"}, {"text": "", "store": "personal"}, 7]}
    assert clean_facts(raw, STORES, set()) == [DreamFact("Gordon lives in Columbus.", "personal"), DreamFact("Acme ships on Fridays.", "org:acme"),
                                                DreamFact("Water boils at 100 C.", "personal"), DreamFact("Gordon prefers metric units.", "personal")]


def test_clean_drops_known_and_repeated_text_and_keeps_eight():
    raw = {"facts": [{"text": "Gordon lives in Columbus."}, {"text": "gordon lives in columbus."}] + [{"text": f"Fact {i}."} for i in range(12)]}
    got = clean_facts(raw, STORES, {"GORDON LIVES IN COLUMBUS."})
    assert [f.text for f in got] == [f"Fact {i}." for i in range(8)]


@pytest.mark.parametrize("raw", [None, [], "facts", {"facts": "none"}, {"fact": []}])
def test_clean_refuses_a_wrong_shape(raw):
    with pytest.raises(ValueError):
        clean_facts(raw, STORES, set())


@pytest.mark.parametrize("body", [
    {"conversations": {"c1": {"read": "x", "attempts": 0, "not_before": 0.0, "seen": 0}}, "learned": 0, "last_dream_at": ""},
    {"conversations": {"c1": {"read": True, "attempts": 0, "not_before": 0.0, "seen": 0}}, "learned": 0, "last_dream_at": ""},
    {"conversations": {"c1": {"read": 1, "attempts": 0, "not_before": "soon", "seen": 0}}, "learned": 0, "last_dream_at": ""},
    {"conversations": {}, "learned": "3", "last_dream_at": ""},
    {"conversations": {}, "learned": 3, "last_dream_at": 7},
])
def test_marks_with_values_of_the_wrong_type_are_kept_aside(tmp_path, body):
    p = tmp_path / "dreams.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    m = DreamMarks(p)
    assert m.get("c1") == Mark() and m.learned == 0 and (tmp_path / "dreams.json.bad").is_file()


def test_marks_start_empty_when_the_damaged_file_cannot_be_moved(tmp_path, monkeypatch):
    p = tmp_path / "dreams.json"
    p.write_text("{not json", encoding="utf-8")

    def refuse(self, target):
        raise PermissionError("in use")

    monkeypatch.setattr(type(p), "replace", refuse)
    m = DreamMarks(p)
    assert m.get("c1") == Mark() and m.learned == 0


# -- the dreamer ---------------------------------------------------------------------------------------------

NOW = 1_800_000_000.0


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def answer(*facts) -> str:
    return json.dumps({"facts": [{"text": t, "store": s} for t, s in facts]})


class FakeProvider:
    """Stands in for the dreaming model. `answers` holds, per call, a reply text, an exception to raise, or
    BLOCK (wait on `gate`, then answer nothing); `calls` keeps each call's whole prompt."""

    kind = "fake"
    BLOCK = object()

    def __init__(self):
        self.answers: list = []
        self.calls: list[str] = []
        self.gate = asyncio.Event()
        self.cancelled = 0

    async def complete(self, messages, *, tools=None, model=None, session_id=None):
        self.calls.append("\n".join(m.content for m in messages))
        a = self.answers.pop(0) if self.answers else answer()
        if a is FakeProvider.BLOCK:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
            a = answer()
        if isinstance(a, BaseException):
            raise a
        return Completion(message=Message("assistant", a), finish_reason="stop", model="fake")

    async def probe(self):
        return True


class RecordingGraph(LocalGraph):
    """The local store, remembering which stores each fact was sent to."""

    def __init__(self, root):
        super().__init__(root)
        self.stores: dict[str, list[str]] = {}

    def put(self, fact, targets=None):
        self.stores[fact.id] = [t.store for t in targets or []]
        super().put(fact, targets)


class NoRouter:
    provider = None

    async def route(self, text, orgs, owner):
        raise AssertionError("a dream names its stores: no routing call")


class Rig:
    def __init__(self, vault, ledger, *, owner=True):
        self.vault, self.ledger = vault, ledger
        self.now = NOW
        self.convs: list[Conversation] = []
        self.provider = FakeProvider()
        self.has_role = True
        self.role_broken = False
        self.is_busy = False
        self.store = RecordingGraph(vault / "memory")
        self.graph = MemoryGraph(self.store, ledger)
        root = vault / "memory" / "local"
        self.profile = ProfileMemory(self.graph, PeopleRegistry(root / "people.json"), OrgRegistry(root / "orgs.json"),
                                     NoRouter(), ledger)
        if owner:
            self.profile.people.set_owner("Gordon", [])
        self.marks = DreamMarks(ledger_path_for(vault) / "dreams.json")
        gen = Generator(self.provider)

        def generator():
            if self.role_broken:
                raise ProviderStoreError("no provider 'gone'")
            return gen if self.has_role else None

        self.dreamer = Dreamer(vault_root=vault, marks=self.marks, ledger=ledger, conversations=lambda: list(self.convs),
                               generator=generator, profile=lambda: self.profile, queue=lambda: None,
                               busy=lambda: self.is_busy, clock=lambda: self.now)
        self.dreamer.set_settings(DreamSettings(True, 5))

    def conv(self, *users, age=3600.0):
        c = Conversation(owner="b", exchanges=[ex(u) for u in users], updated_at=iso(self.now - age))
        self.convs.append(c)
        return c

    def dreamt(self):
        return {f.text: f for f in self.graph.facts(SHARED)}

    def entries(self):
        return [e for e in self.ledger.iter() if e.kind == "dream"]


@pytest.fixture
def d(vault, ledger):
    return Rig(vault, ledger)


async def test_writes_facts_with_the_dream_source_and_moves_the_mark(d):
    c = d.conv("I live in Columbus", "I prefer metric")
    d.provider.answers = [answer(("Gordon lives in Columbus.", "personal"))]
    got = await d.dreamer.dream(manual=True)
    assert got == {"conversations": 1, "facts": 1, "dropped": 0, "refused": 0}
    fact = d.dreamt()["Gordon lives in Columbus."]
    assert fact.source == f"dream:{c.id}" and fact.bot_id == ""
    assert d.marks.get(c.id).read == 2 and d.marks.learned == 1 and d.marks.last_dream_at == "2027-01-15T08:00:00Z"
    assert "I live in Columbus" in d.provider.calls[0] and "I prefer metric" in d.provider.calls[0]


async def test_prompt_never_holds_reply_or_addendum(d):
    d.conv("I live in Columbus", "I prefer metric")
    d.conv("My sister is Ann", age=4000)
    await d.dreamer.dream(manual=True)
    assert len(d.provider.calls) == 2
    assert not any("BOT REPLY" in p or "COMMENT BLOCK" in p for p in d.provider.calls)


async def test_not_enabled_no_role_or_no_owner_does_nothing(d, vault, tmp_path):
    d.conv("I live in Columbus")
    d.dreamer.set_settings(DreamSettings(False, 5))
    assert not d.dreamer.ready() and await d.dreamer.dream(manual=True) == {"conversations": 0, "facts": 0, "dropped": 0, "refused": 0}
    d.dreamer.set_settings(DreamSettings(True, 5))
    d.has_role = False
    assert not d.dreamer.ready()
    await d.dreamer.dream(manual=True)
    other = tmp_path / "other-vault"
    other.mkdir()
    nobody = Rig(other, Ledger(other), owner=False)
    nobody.conv("I live in Columbus")
    assert not nobody.dreamer.ready()
    await nobody.dreamer.dream(manual=True)
    assert d.provider.calls == [] and nobody.provider.calls == [] and d.entries() == []
    assert DreamSettings.load(vault) == DreamSettings(True, 5)  # set_settings saves


def test_a_broken_role_is_not_ready_and_reads_as_a_setup_error(d):
    d.role_broken = True
    assert not d.dreamer.ready() and d.dreamer.status()["error"] == "setup"


def test_idle_reported_only_while_the_newest_report_is_fresh(d):
    d.dreamer.report_idle(0)  # any idle value counts as a report
    assert d.dreamer.status()["idle_reported"] is True
    d.now += 149
    assert d.dreamer.status()["idle_reported"] is True
    d.now += 1  # the app closed: no report for 150 s
    assert d.dreamer.status()["idle_reported"] is False
    d.dreamer.report_idle(10)
    d.now -= 10  # a clock set back does not make a report fresh
    assert d.dreamer.status()["idle_reported"] is False


async def test_idle_needs_a_fresh_long_enough_report(d):
    assert not d.dreamer.machine_idle() and d.dreamer.status()["idle_reported"] is False
    d.dreamer.report_idle(299)
    assert not d.dreamer.machine_idle() and d.dreamer.status()["idle_reported"] is True
    d.dreamer.report_idle(300)
    assert d.dreamer.machine_idle()
    d.now += 149
    assert d.dreamer.machine_idle()
    d.now += 1
    assert not d.dreamer.machine_idle()
    d.dreamer.report_idle(300)
    d.now -= 10
    assert not d.dreamer.machine_idle()


async def test_manual_pass_skips_the_idle_rule_only(d):
    d.conv("I live in Columbus")
    await d.dreamer.dream()
    assert d.provider.calls == []
    d.is_busy = True
    await d.dreamer.dream(manual=True)
    assert d.provider.calls == []
    d.is_busy = False
    await d.dreamer.dream(manual=True)
    assert len(d.provider.calls) == 1


async def test_an_idle_machine_dreams_without_manual(d):
    d.conv("I live in Columbus")
    d.dreamer.report_idle(600)
    await d.dreamer.dream()
    assert len(d.provider.calls) == 1


async def test_recent_conversation_waits_thirty_minutes(d):
    c = d.conv("I live in Columbus", age=1799)
    assert d.dreamer.waiting() == 0
    await d.dreamer.dream(manual=True)
    assert d.provider.calls == [] and d.marks.get(c.id).read == 0
    d.now += 1
    assert d.dreamer.waiting() == 1
    await d.dreamer.dream(manual=True)
    assert len(d.provider.calls) == 1 and d.marks.get(c.id).read == 1


async def test_newest_conversation_is_read_first(d):
    d.conv("older", age=9000)
    d.conv("newer", age=3600)
    await d.dreamer.dream(manual=True)
    assert ["newer" in p for p in d.provider.calls] == [True, False]


async def test_user_returning_cancels_the_model_call_and_keeps_the_mark(d, monkeypatch):
    monkeypatch.setattr(dream_mod, "GUARD_S", 0.05)
    c = d.conv("I live in Columbus")
    d.provider.answers = [FakeProvider.BLOCK]
    run = asyncio.ensure_future(d.dreamer.dream(manual=True))
    while not d.provider.calls:
        await asyncio.sleep(0.01)
    d.is_busy = True
    got = await asyncio.wait_for(run, 1.0)
    assert d.provider.cancelled == 1 and got["conversations"] == 0
    assert d.marks.get(c.id) == Mark() and d.dreamer.status()["error"] == ""


async def test_pass_resumes_after_an_interruption(d, monkeypatch):
    monkeypatch.setattr(dream_mod, "GUARD_S", 0.05)
    d.conv("first message", age=3600)
    second = d.conv("second message", age=3700)
    d.conv("third message", age=3800)
    d.provider.answers = [answer(("Gordon likes tea.", "personal")), FakeProvider.BLOCK]
    run = asyncio.ensure_future(d.dreamer.dream(manual=True))
    while len(d.provider.calls) < 2:
        await asyncio.sleep(0.01)
    d.is_busy = True
    await asyncio.wait_for(run, 1.0)
    assert d.marks.get(second.id).read == 0
    d.is_busy = False
    d.provider.calls.clear()
    await d.dreamer.dream(manual=True)
    assert len(d.provider.calls) == 2
    assert "second message" in d.provider.calls[0] and "third message" in d.provider.calls[1]
    assert not any("first message" in p for p in d.provider.calls)


async def test_bad_answer_counts_one_attempt_and_the_pass_goes_on(d):
    bad = d.conv("I live in Columbus", age=3600)
    good = d.conv("I prefer metric", age=3700)
    d.provider.answers = ["no json at all", answer(("Gordon prefers metric units.", "personal"))]
    got = await d.dreamer.dream(manual=True)
    assert got["conversations"] == 1 and got["facts"] == 1
    assert d.marks.get(bad.id) == Mark(read=0, attempts=1, not_before=NOW + 600, seen=1)
    assert d.marks.get(good.id).read == 1 and d.dreamer.status()["error"] == "model"
    d.dreamer.report_idle(600)
    await d.dreamer.dream()  # still waiting out its retry
    assert len(d.provider.calls) == 2


async def test_wrong_shape_counts_as_a_failed_attempt(d):
    c = d.conv("I live in Columbus")
    d.provider.answers = [json.dumps({"facts": "none"})]
    await d.dreamer.dream(manual=True)
    assert d.marks.get(c.id).attempts == 1 and d.marks.get(c.id).read == 0


async def idle_pass(d):
    """An automatic pass, the machine reported idle just now."""
    d.dreamer.report_idle(600)
    return await d.dreamer.dream()


async def test_three_failures_skip_until_the_conversation_grows(d):
    c = d.conv("I live in Columbus")
    d.provider.answers = ["no json"] * 3
    for wait in (0, 600, 3600):
        d.now += wait
        await idle_pass(d)
    assert d.marks.skipped(c.id) and len(d.provider.calls) == 3
    d.now += 10_000
    assert d.dreamer.waiting() == 0
    await idle_pass(d)
    assert len(d.provider.calls) == 3
    c.exchanges.append(ex("I prefer metric"))
    assert d.dreamer.waiting() == 1
    await idle_pass(d)
    assert len(d.provider.calls) == 4 and d.marks.get(c.id) == Mark(read=2, seen=2)


async def test_dream_now_tries_a_skipped_conversation_again(d):
    c = d.conv("I live in Columbus")
    d.provider.answers = ["no json"] * 3
    for wait in (0, 600, 3600):
        d.now += wait
        await idle_pass(d)
    assert d.marks.skipped(c.id) and len(d.provider.calls) == 3
    retry = d.conv("I prefer metric", age=4000)
    d.marks.fail(retry.id, d.now, 1)  # waiting out its first retry
    assert d.dreamer.waiting() == 0
    d.provider.answers = [answer(("Gordon lives in Columbus.", "personal")), answer()]
    await d.dreamer.dream(manual=True)  # every mark's attempts and wait are cleared first
    assert len(d.provider.calls) == 5 and "Gordon lives in Columbus." in d.dreamt()
    assert d.marks.get(c.id) == Mark(read=1, seen=1) and d.marks.get(retry.id) == Mark(read=1, seen=1)


def test_reset_attempts_keeps_read_seen_and_the_totals(tmp_path):
    m = DreamMarks(tmp_path / "dreams.json")
    m.set_read("c1", 2)
    for t in (1.0, 2.0, 3.0):
        m.fail("c1", now=t, seen=5)
    m.fail("c2", now=10.0, seen=1)
    m.add_learned(4, "2026-11-19T03:12:00Z")
    m.reset_attempts()
    again = DreamMarks(tmp_path / "dreams.json")
    assert again.get("c1") == Mark(read=2, seen=5) and again.get("c2") == Mark(seen=1)
    assert again.learned == 4 and again.last_dream_at == "2026-11-19T03:12:00Z"


def test_dreamer_reset_attempts_clears_the_marks_the_pause_and_the_error(d):
    c = d.conv("I live in Columbus")
    d.marks.fail(c.id, NOW, 1)
    d.dreamer._paused_until, d.dreamer._error = NOW + 300, "provider"
    d.dreamer.reset_attempts()
    assert d.marks.get(c.id) == Mark(seen=1) and not d.dreamer._paused() and d.dreamer.status()["error"] == ""


async def test_unreachable_provider_pauses_ten_minutes(d):
    c = d.conv("I live in Columbus", age=3600)
    other = d.conv("I prefer metric", age=3700)
    d.provider.answers = [httpx.ConnectError("refused")]
    d.dreamer.report_idle(600)
    await d.dreamer.dream()
    assert len(d.provider.calls) == 1 and d.dreamer.status()["error"] == "unreachable"
    assert d.marks.get(c.id) == Mark() and d.marks.get(other.id) == Mark()  # the pass ended; no attempt counted
    d.now += 599
    d.dreamer.report_idle(600)
    await d.dreamer.dream()
    assert len(d.provider.calls) == 1
    d.now += 1
    d.dreamer.report_idle(600)
    await d.dreamer.dream()
    assert len(d.provider.calls) == 3 and d.dreamer.status()["error"] == ""


async def test_dream_now_tries_the_model_inside_the_pause(d):
    d.conv("I live in Columbus")
    d.provider.answers = [ProviderUnavailable("down", cause="network")]
    d.dreamer.report_idle(600)
    await d.dreamer.dream()
    assert len(d.provider.calls) == 1 and d.dreamer.status()["error"] == "unreachable"
    d.now += 60
    d.dreamer.report_idle(600)
    await d.dreamer.dream()  # automatic: still waiting out the pause
    assert len(d.provider.calls) == 1
    d.provider.answers = [ProviderUnavailable("still down", cause="network")]
    await d.dreamer.dream(manual=True)  # manual: clears the pause and tries
    assert len(d.provider.calls) == 2 and d.dreamer.status()["error"] == "unreachable"
    d.now += 60
    d.dreamer.report_idle(600)
    await d.dreamer.dream()  # that failure started the pause again
    assert len(d.provider.calls) == 2
    await d.dreamer.dream(manual=True)
    assert len(d.provider.calls) == 3 and d.dreamer.status()["error"] == ""


async def test_a_model_too_slow_for_a_batch_is_skipped_after_three_tries(d):
    slow = d.conv("I live in Columbus", age=3600)
    older = d.conv("I prefer metric", age=3700)
    d.provider.answers = [ProviderUnavailable("slow", cause="timeout"), answer(),
                          httpx.ReadTimeout("slow"), ProviderUnavailable("slow", cause="timeout")]
    for wait in (0, 600, 3600):
        d.now += wait
        await idle_pass(d)
    assert d.marks.skipped(slow.id) and d.marks.get(slow.id).read == 0
    assert d.marks.get(older.id).read == 1  # it did not wait behind the slow one
    assert d.dreamer.status()["error"] == "model" and len(d.provider.calls) == 4
    await idle_pass(d)
    assert len(d.provider.calls) == 4


def test_error_kind_tells_the_conversation_from_an_outage():
    assert error_kind(httpx.ConnectError("refused")) == "unreachable"
    assert error_kind(ConnectionRefusedError()) == "unreachable"
    assert error_kind(ProviderUnavailable("down", cause="network")) == "unreachable"
    assert error_kind(ProviderUnavailable("slow", cause="timeout")) == "model"
    assert error_kind(httpx.ReadTimeout("slow")) == "model"
    assert error_kind(httpx.ConnectTimeout("slow")) == "model"
    assert error_kind(TimeoutError()) == "model"
    assert error_kind(ValueError("no JSON")) == "model"
    assert error_kind(ProviderUnavailable("busy", cause="rate_limit", status=429)) == "provider"
    assert error_kind(ProviderUnavailable("down", cause="server", status=503)) == "provider"
    assert error_kind(ProviderAuthError("rejected the key (HTTP 401)")) == "provider"
    assert error_kind(ProviderAuthError("rejected the key (HTTP 403)")) == "provider"
    # about this request (a client error, or a provider error that cannot be told): counted against the conversation
    for status in (400, 404, 413, 422):
        assert error_kind(ProviderError(f"HTTP {status}", status=status)) == "model"
    assert error_kind(ProviderError("malformed completion: KeyError")) == "model"
    assert error_kind(ProviderUnavailable("odd", cause="something new")) == "model"


OUTAGES = [
    pytest.param(ProviderAuthError("rejected the key (HTTP 401)"), "provider", id="auth-401"),
    pytest.param(ProviderAuthError("rejected the key (HTTP 403)"), "provider", id="auth-403"),
    pytest.param(ProviderUnavailable("HTTP 429", cause="rate_limit", status=429), "provider", id="rate-limit"),
    pytest.param(ProviderUnavailable("HTTP 502", cause="server", status=502), "provider", id="server"),
    pytest.param(httpx.ConnectError("refused"), "unreachable", id="unreachable"),
]


async def test_a_request_the_model_refuses_counts_and_the_older_conversation_still_dreams(d):
    newest = d.conv("a message too long for this model", age=3600)
    older = d.conv("I prefer metric", age=3700)
    too_big = ProviderError("HTTP 400: context length exceeded", status=400)
    d.provider.answers = [too_big, answer(("Gordon prefers metric units.", "personal"))]
    await idle_pass(d)
    assert d.marks.get(newest.id).attempts == 1 and d.marks.get(newest.id).read == 0
    assert d.marks.get(older.id).read == 1 and "Gordon prefers metric units." in d.dreamt()  # the pass went on
    assert d.dreamer.status()["error"] == "model" and not d.dreamer._paused()
    for wait in (600, 3600):
        d.now += wait
        d.provider.answers = [too_big]
        await idle_pass(d)
    assert d.marks.skipped(newest.id) and len(d.provider.calls) == 4
    d.now += 10_000
    await idle_pass(d)  # skipped until it grows: dreaming is not held behind it
    assert len(d.provider.calls) == 4 and d.dreamer.waiting() == 0


@pytest.mark.parametrize("exc, kind", OUTAGES)
async def test_an_outage_counts_no_attempt_and_pauses_automatic_dreaming(d, exc, kind):
    convs = [d.conv("I live in Columbus", age=3600), d.conv("I prefer metric", age=3700)]
    for _ in range(8):  # well past the time three counted failures would take: the backlog is never burnt out
        d.provider.answers = [exc]
        calls = len(d.provider.calls)
        await idle_pass(d)
        assert len(d.provider.calls) == calls + 1  # the pass ended at the first conversation
        assert d.dreamer.status()["error"] == kind
        assert all(d.marks.get(c.id) == Mark() for c in convs)
        d.now += 599
        await idle_pass(d)  # paused: no call
        assert len(d.provider.calls) == calls + 1
        d.now += 1
    end = [e.data for e in d.entries() if e.data["event"] == "pass_end"][-1]
    assert end["error"] == kind and end["conversations"] == 0


async def test_a_failing_store_counts_no_attempt_and_shows_as_store(d, monkeypatch):
    c = d.conv("I live in Columbus", age=3600)
    other = d.conv("I prefer metric", age=3700)
    d.marks.fail(other.id, NOW - 10_000, 1)  # an earlier failure, due again now

    async def broken(*a, **kw):
        raise RuntimeError("store down")

    monkeypatch.setattr(dream_mod, "remember_shared", broken)
    d.provider.answers = [answer(("Gordon lives in Columbus.", "personal"))]
    await idle_pass(d)
    assert len(d.provider.calls) == 1 and d.dreamer.status()["error"] == "store"
    assert d.marks.get(c.id) == Mark() and d.marks.get(other.id).attempts == 1  # nothing counted, nothing moved
    end = [e.data for e in d.entries() if e.data["event"] == "pass_end"][-1]
    assert end["error"] == "store"
    conv_entry = [e.data for e in d.entries() if e.data["event"] == "conversation"][-1]
    assert conv_entry["error"] == "store" and conv_entry["exception"] == "RuntimeError"
    d.now += 300
    await idle_pass(d)  # paused like an unreachable provider
    assert len(d.provider.calls) == 1


async def test_a_store_failure_after_a_model_error_keeps_an_error(d, monkeypatch):
    d.conv("I live in Columbus", age=3600)
    d.conv("I prefer metric", age=3700)

    async def broken(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(dream_mod, "remember_shared", broken)
    d.provider.answers = ["no json", answer(("Gordon prefers metric units.", "personal"))]
    await d.dreamer.dream(manual=True)
    assert d.dreamer.status()["error"] == "store"


async def test_unknown_store_goes_to_personal_and_no_org_is_registered(d):
    d.profile.orgs.add("Acme", "works_at")
    d.conv("We ship on Fridays at Acme")
    d.provider.answers = [answer(("Gordon's team ships on Fridays.", "org:newco"), ("Acme ships on Fridays.", "org:acme"),
                                 ("Water boils at 100 C.", "world"))]
    await d.dreamer.dream(manual=True)
    prompt = d.provider.calls[0]
    assert "personal:" in prompt and "world:" in prompt and "org:acme: Acme" in prompt
    facts = d.dreamt()
    assert d.store.stores[facts["Gordon's team ships on Fridays."].id] == ["personal"]
    assert d.store.stores[facts["Acme ships on Fridays."].id] == ["org:acme"]
    assert d.store.stores[facts["Water boils at 100 C."].id] == ["world"]
    assert [o.slug for o in d.profile.orgs.all()] == ["acme"]


async def test_refused_fact_is_dropped_and_the_rest_written(d):
    exclusion.register_secret_value("hunter2-secret")
    c = d.conv("I live in Columbus")
    d.provider.answers = [answer(("Gordon's password is hunter2-secret.", "personal"), ("Gordon lives in Columbus.", "personal"),
                                 ("Gordon lives in Columbus.", "personal"))]
    got = await d.dreamer.dream(manual=True)
    assert got == {"conversations": 1, "facts": 1, "dropped": 1, "refused": 1}
    assert list(d.dreamt()) == ["Gordon lives in Columbus."] and d.marks.get(c.id).read == 1


async def test_a_secret_message_never_reaches_the_model(d):
    exclusion.register_secret_value("hunter2-secret")
    c = d.conv("my key is hunter2-secret", "I live in Columbus")
    await d.dreamer.dream(manual=True)
    assert "hunter2-secret" not in d.provider.calls[0] and d.marks.get(c.id).read == 2


async def test_known_fact_is_not_written_again(d):
    d.conv("I live in Columbus")
    d.graph.remember("Gordon lives in Columbus.", SHARED, Tier.project)
    d.provider.answers = [answer(("Gordon lives in Columbus.", "personal"))]
    got = await d.dreamer.dream(manual=True)
    assert got["facts"] == 0 and got["dropped"] == 1 and len(d.graph.facts(SHARED)) == 1


async def test_long_conversation_moves_the_mark_batch_by_batch(d):
    c = d.conv("a" * 4000, "b" * 4000, "c" * 4000, "d" * 4000)
    d.provider.answers = [answer(), "no json"]
    await d.dreamer.dream(manual=True)
    assert len(d.provider.calls) == 2
    assert d.marks.get(c.id).read == 3 and d.marks.get(c.id).attempts == 1


async def test_nothing_to_read_still_moves_the_mark_without_a_call(d):
    c = d.conv("   ", "to a room")
    c.exchanges[1].outcome = "routed"
    got = await d.dreamer.dream(manual=True)
    assert d.provider.calls == [] and d.marks.get(c.id).read == 2 and got["conversations"] == 1


async def test_ledger_entries_carry_no_text(d):
    d.conv("I live in Columbus", "I prefer metric")
    bad = d.conv("My sister is Ann", age=4000)
    d.provider.answers = [answer(("Gordon lives in Columbus.", "personal")), ValueError("Ann is my sister")]
    await d.dreamer.dream(manual=True)
    entries = d.entries()
    assert [e.data["event"] for e in entries] == ["pass_start", "conversation", "conversation", "pass_end"]
    blob = json.dumps([e.data for e in entries])
    for text in ("Columbus", "metric", "Ann", "sister"):
        assert text not in blob
    assert entries[0].data["manual"] is True
    first = entries[1].data
    assert (first["exchanges"], first["messages"], first["facts"], first["dropped"], first["refused"]) == (2, 2, 1, 0, 0)
    assert entries[2].data["conversation"] == bad.id and entries[2].data["exception"] == "ValueError"
    end = entries[3].data
    assert (end["conversations"], end["facts"], end["dropped"], end["refused"], end["error"]) == (1, 1, 0, 0, "model")


async def test_second_start_while_dreaming_is_refused(d):
    d.conv("I live in Columbus")
    d.provider.answers = [FakeProvider.BLOCK]
    assert d.dreamer.start(manual=True)
    while not d.provider.calls:
        await asyncio.sleep(0.01)
    assert d.dreamer.status()["dreaming"] is True
    assert not d.dreamer.start(manual=True)
    assert await d.dreamer.dream(manual=True) == {"conversations": 0, "facts": 0, "dropped": 0, "refused": 0}
    d.provider.gate.set()
    await d.dreamer.close()
    assert d.dreamer.status()["dreaming"] is False and len(d.provider.calls) == 1


async def test_close_cancels_the_model_call_and_keeps_the_mark(d):
    c = d.conv("I live in Columbus")
    d.provider.answers = [FakeProvider.BLOCK]
    assert d.dreamer.start(manual=True)
    while not d.provider.calls:
        await asyncio.sleep(0.01)
    await asyncio.wait_for(d.dreamer.close(), 1.0)
    assert d.provider.cancelled == 1 and d.marks.get(c.id) == Mark()
    assert not d.dreamer.start(manual=True)


async def test_close_lets_a_write_in_flight_finish(d, monkeypatch):
    c = d.conv("I live in Columbus")
    d.provider.answers = [answer(("Gordon lives in Columbus.", "personal"), ("Gordon likes tea.", "personal"))]
    entered, release = asyncio.Event(), asyncio.Event()
    real = dream_mod.remember_shared

    async def slow(*a, **kw):
        entered.set()
        await release.wait()
        return await real(*a, **kw)

    monkeypatch.setattr(dream_mod, "remember_shared", slow)
    assert d.dreamer.start(manual=True)
    await entered.wait()
    closing = asyncio.ensure_future(d.dreamer.close())
    await asyncio.sleep(0.05)
    assert not closing.done()
    release.set()
    await asyncio.wait_for(closing, 1.0)
    assert list(d.dreamt()) == ["Gordon lives in Columbus."]  # the write finished; the next one never started
    assert d.marks.get(c.id).read == 0 and d.marks.learned == 1


async def test_run_starts_a_pass_when_idle_and_stops(d, monkeypatch):
    monkeypatch.setattr(dream_mod, "TICK_S", 0.02)
    d.conv("I live in Columbus")
    stop = asyncio.Event()
    loop = asyncio.ensure_future(d.dreamer.run(stop))
    await asyncio.sleep(0.1)
    assert d.provider.calls == [] and d.entries() == []  # not idle: no pass, nothing logged
    d.dreamer.report_idle(600)
    while not d.provider.calls:
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(loop, 1.0)
    await d.dreamer.close()
    assert len(d.provider.calls) == 1 and len([e for e in d.entries() if e.data["event"] == "pass_start"]) == 1


def test_waiting_ignores_marks_for_conversations_that_are_gone(d):
    d.conv("I live in Columbus", "I prefer metric")
    done = d.conv("read already")
    d.conv("too recent", age=60)
    retry = d.conv("waiting out a retry")
    d.marks.set_read(done.id, 1)
    d.marks.fail(retry.id, NOW, 1)
    d.marks.fail("gone", NOW - 10_000, 9)
    d.marks.set_read("also-gone", 0)
    assert d.dreamer.waiting() == 1
    s = d.dreamer.status()
    assert s["waiting"] == 1 and s["enabled"] is True and s["idle_minutes"] == 5 and s["ready"] is True
    assert s["idle"] is False and s["dreaming"] is False and s["learned"] == 0 and s["last_dream_at"] == ""


async def test_a_pass_whose_first_ledger_entry_fails_does_not_block_later_passes(d, monkeypatch):
    c = d.conv("I live in Columbus")
    real, failed = d.ledger.append, []

    def append(*a, **kw):
        if kw.get("event") == "pass_start" and not failed:
            failed.append(True)
            raise OSError("disk full")
        return real(*a, **kw)

    monkeypatch.setattr(d.ledger, "append", append)
    with pytest.raises(OSError):
        await d.dreamer.dream(manual=True)
    assert d.dreamer.status()["dreaming"] is False
    d.provider.answers = [answer(("Gordon lives in Columbus.", "personal"))]
    got = await d.dreamer.dream(manual=True)
    assert got["facts"] == 1 and d.marks.get(c.id).read == 1


async def test_a_conversation_touched_during_the_pass_waits_for_a_later_one(d):
    first = d.conv("I live in Columbus", age=3600)
    later = d.conv("I prefer metric", age=3700)
    real = d.provider.complete

    async def complete(messages, **kw):
        later.updated_at = iso(d.now)  # a turn there ended while the first conversation was being read
        return await real(messages, **kw)

    d.provider.complete = complete
    got = await d.dreamer.dream(manual=True)
    assert len(d.provider.calls) == 1 and got["conversations"] == 1
    assert d.marks.get(first.id).read == 1 and d.marks.get(later.id) == Mark()


async def test_reset_attempts_ends_the_old_models_call_so_it_cannot_pause_dreaming(d):
    c = d.conv("I live in Columbus")
    d.provider.answers = [FakeProvider.BLOCK]
    run = asyncio.ensure_future(d.dreamer.dream(manual=True))
    while not d.provider.calls:
        await asyncio.sleep(0.01)
    d.dreamer.reset_attempts()  # a new dreaming model was chosen while the old one was still answering
    got = await asyncio.wait_for(run, 1.0)
    assert d.provider.cancelled == 1 and got["conversations"] == 0
    assert d.marks.get(c.id) == Mark() and not d.dreamer._paused() and d.dreamer.status()["error"] == ""


# -- SkillTuner's turn in the loop -------------------------------------------------------------------------

class StubTuner:
    """Stands in for SkillTuner: says it is ready with `waiting` things, records each pass and its guard."""

    def __init__(self, waiting=1, raises=None):
        self._waiting, self.raises = waiting, raises
        self.passes = 0
        self.went: list[bool] = []

    def ready(self):
        return True

    def waiting(self):
        return self._waiting

    async def tune(self, *, may_go, guarded):
        self.passes += 1
        self.went.append(may_go())
        if self.raises:
            raise self.raises
        await guarded(asyncio.sleep(0))
        return {}


async def test_dream_now_never_runs_the_tuner(d):
    d.dreamer.tuner = stub = StubTuner()
    d.conv("I live in Columbus")
    await d.dreamer.dream(manual=True)
    assert stub.passes == 0 and len(d.provider.calls) == 1


async def test_an_automatic_pass_runs_the_tuner_after_the_facts(d):
    d.dreamer.tuner = stub = StubTuner()
    d.conv("I live in Columbus")
    d.dreamer.report_idle(600)
    got = await d.dreamer.dream()
    assert got["conversations"] == 1 and stub.passes == 1 and stub.went == [True]


async def test_the_tuner_runs_when_dreaming_has_nothing_waiting(d):
    d.dreamer.tuner = stub = StubTuner()
    d.dreamer.report_idle(600)
    await d.dreamer.dream()
    assert stub.passes == 1 and d.entries() == []  # no empty fact pass logged


async def test_a_systemic_fact_pass_skips_the_tuner(d):
    d.dreamer.tuner = stub = StubTuner()
    d.conv("I live in Columbus")
    d.provider.answers = [ProviderUnavailable("down")]
    d.dreamer.report_idle(600)
    await d.dreamer.dream()
    assert stub.passes == 0 and d.dreamer.status()["error"] == "unreachable"


async def test_a_tuner_failure_leaves_the_fact_pass_alone(d):
    d.dreamer.tuner = StubTuner(raises=RuntimeError("tuner bug"))
    d.conv("I live in Columbus")
    d.provider.answers = [answer(("Gordon lives in Columbus.", "personal"))]
    d.dreamer.report_idle(600)
    got = await d.dreamer.dream()
    assert got == {"conversations": 1, "facts": 1, "dropped": 0, "refused": 0} and d.dreamer.status()["error"] == ""
    errors = [e.data for e in d.ledger.iter() if e.kind == "error"]
    assert errors == [{"where": "skilltuner", "error": "RuntimeError"}]


async def test_the_loop_starts_a_tuner_pass_when_dreaming_has_nothing_waiting(d, monkeypatch):
    monkeypatch.setattr(dream_mod, "TICK_S", 0.02)
    d.dreamer.tuner = stub = StubTuner()
    stop = asyncio.Event()
    loop = asyncio.ensure_future(d.dreamer.run(stop))
    await asyncio.sleep(0.1)
    assert stub.passes == 0  # not idle
    d.dreamer.report_idle(600)

    async def started():
        while not stub.passes:
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(started(), 1.0)
    finally:
        stop.set()
    await asyncio.wait_for(loop, 1.0)
    await d.dreamer.close()
    assert d.provider.calls == []


async def test_the_loop_starts_nothing_when_neither_is_waiting(d, monkeypatch):
    monkeypatch.setattr(dream_mod, "TICK_S", 0.02)
    d.dreamer.tuner = stub = StubTuner(waiting=0)
    d.dreamer.report_idle(600)
    stop = asyncio.Event()
    loop = asyncio.ensure_future(d.dreamer.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(loop, 1.0)
    assert stub.passes == 0

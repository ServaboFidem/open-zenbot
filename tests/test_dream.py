from src.harness.conversations import Conversation, ConversationStore, Conversations, Exchange
import pytest

from src.memory.dream import DreamFact, DreamMarks, DreamSettings, Mark, batches, clean_facts, unread, unread_marked
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

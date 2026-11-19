from src.harness.conversations import Conversation, ConversationStore, Conversations, Exchange
from src.memory.dream import batches, unread, unread_marked


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

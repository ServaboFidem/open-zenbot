import json

import pytest

from src.harness.conversations import (
    EDIT_ACK,
    SUMMARY_PREFIX,
    ConversationStore,
    Conversations,
    reply_text,
    title_of,
)


def make(tmp_path, ledger):
    store = ConversationStore(tmp_path / "conv")
    convs = Conversations(store, ledger)
    convs.load()
    return convs


def test_begin_creates_and_titles_a_conversation(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, ex = convs.begin("me", "hello there\nmore", session_id="s1")
    assert conv.title == title_of("hello there\nmore") == "hello there"
    assert convs.current("me").id == conv.id
    assert ex.outcome == "pending" and ex.session_id == "s1"
    assert (tmp_path / "conv" / f"{conv.id}.json").exists()


def test_finish_completes_the_exchange_by_session(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("me", "q", session_id="s1")
    convs.finish(conv.id, "s1", "done", "answer")
    ex = convs.get(conv.id).exchanges[0]
    assert (ex.outcome, ex.reply) == ("done", "answer")
    on_disk = json.loads((tmp_path / "conv" / f"{conv.id}.json").read_text())
    assert on_disk["exchanges"][0]["outcome"] == "done"


def test_history_is_alternating_user_assistant(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("me", "q1", session_id="s1")
    convs.finish(conv.id, "s1", "done", "a1")
    convs.begin("me", "q2", session_id="s2")
    convs.finish(conv.id, "s2", "done", "a2")
    convs.begin("me", "q3", session_id="s3")
    c = convs.get(conv.id)
    assert [(m.role, m.content) for m in c.history(exclude="s3")] == [
        ("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2"),
    ]


def test_history_uses_summary_and_skips_folded(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("me", "q1", session_id="s1")
    convs.finish(conv.id, "s1", "done", "a1")
    convs.begin("me", "q2", session_id="s2")
    convs.finish(conv.id, "s2", "done", "a2")
    c = convs.get(conv.id)
    c.summary, c.folded = "sum", 1
    h = c.history()
    assert h[0].role == "system" and h[0].content == SUMMARY_PREFIX + "sum"
    assert [m.content for m in h[1:]] == ["q2", "a2"]


def test_reply_text_copy(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, ex = convs.begin("me", "q", session_id="s1")
    assert reply_text(ex) == "(Still in progress.)"
    for outcome, reply, want in [
        ("failed", "boom", "(This task failed: boom)"),
        ("stopped", "", "(Stopped before finishing.)"),
        ("routed", "(Sent to the room 'X'.)", "(Sent to the room 'X'.)"),
    ]:
        ex.outcome, ex.reply = outcome, reply
        assert reply_text(ex) == want


def test_pending_exchanges_load_as_interrupted(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("me", "q", session_id="s1")
    again = make(tmp_path, ledger)
    ex = again.get(conv.id).exchanges[0]
    assert ex.outcome == "interrupted"
    assert reply_text(ex) == "(Interrupted: the app closed before this finished.)"
    saved = json.loads((tmp_path / "conv" / f"{conv.id}.json").read_text())
    assert saved["exchanges"][0]["outcome"] == "interrupted"


def test_a_bad_file_is_skipped(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    convs.begin("me", "q", session_id="s1")
    (tmp_path / "conv" / "junk.json").write_text("{not json")
    again = make(tmp_path, ledger)
    assert len(again.for_owner("me")) == 1
    errs = [e for e in ledger.iter() if e.kind == "error"]
    assert errs and errs[0].data["where"] == "conversation_store"


def test_current_survives_a_reload(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    a, _ = convs.begin("me", "one")
    b = convs.start_new("me")
    convs.select("me", a.id)
    assert make(tmp_path, ledger).current("me").id == a.id
    assert b.id != a.id


def test_select_rejects_another_owners_conversation(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("me", "q")
    with pytest.raises(KeyError):
        convs.select("you", conv.id)


def test_for_owner_lists_newest_first(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    a, _ = convs.begin("me", "one")
    b = convs.start_new("me")
    a.updated_at, b.updated_at = "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"
    assert [c.id for c in convs.for_owner("me")] == [b.id, a.id]
    assert convs.for_owner("you") == []


def test_record_adds_a_complete_exchange_and_brief_counts(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    ex = convs.record("me", "hi", "routed", "(Sent to the room 'X'.)")
    assert ex.outcome == "routed"
    c = convs.current("me")
    assert c.brief()["exchanges"] == 1 and "exchanges" in c.brief()


def test_title_is_cut_to_60_characters():
    t = title_of("word " * 40)
    assert len(t) == 60 and t.endswith("…")


class FakeGenerator:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.compacted: list[tuple[str, str | None]] = []

    async def compact(self, rendered, *, goal=None):
        if self.fail:
            raise RuntimeError("summary model down")
        self.compacted.append((rendered, goal))
        return "LATEST"

    async def fold(self, previous, latest):
        return f"{previous}+{latest}" if previous else latest


def _fill(convs, n):
    conv = None
    for i in range(n):
        conv, ex = convs.begin("me", f"question {i} " + "x" * 200, session_id=f"s{i}")
        convs.finish(conv.id, f"s{i}", "done", f"answer {i} " + "y" * 200)
    return conv


async def test_history_folds_past_the_budget(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv = _fill(convs, 20)
    gen = FakeGenerator()
    assert await convs.fold_if_needed(conv, gen, budget_chars=6000, keep=6) is True
    assert conv.folded == 14 and conv.summary == "LATEST"
    rendered, goal = gen.compacted[0]
    assert "question 0" in rendered and "answer 13" in rendered and "question 14" not in rendered
    assert goal == conv.title
    h = conv.history()
    assert h[0].content == SUMMARY_PREFIX + "LATEST"
    assert [m.content.split()[1] for m in h[1:]] == [str(i) for i in range(14, 20) for _ in (0, 1)]
    assert len(conv.exchanges) == 20  # kept in the record for display
    on_disk = json.loads((tmp_path / "conv" / f"{conv.id}.json").read_text())
    assert on_disk["folded"] == 14 and on_disk["summary"] == "LATEST"
    entry = ledger.tail(kind="conversation_fold")[-1]
    assert entry.data["ok"] is True and entry.data["folded"] == 14
    assert entry.data["chars_after"] < entry.data["chars_before"]


async def test_under_the_budget_nothing_folds(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv = _fill(convs, 3)
    gen = FakeGenerator()
    assert await convs.fold_if_needed(conv, gen, budget_chars=100_000, keep=6) is False
    assert conv.folded == 0 and gen.compacted == []


async def test_a_second_fold_carries_the_summary_forward(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv = _fill(convs, 20)
    gen = FakeGenerator()
    await convs.fold_if_needed(conv, gen, budget_chars=6000, keep=6)
    _fill(convs, 4)  # continues the current conversation: 10 unfolded, under the budget
    assert await convs.fold_if_needed(conv, gen, budget_chars=6000, keep=6) is False
    _fill(convs, 5)  # 15 unfolded, past it
    assert await convs.fold_if_needed(conv, gen, budget_chars=6000, keep=6) is True
    assert conv.folded == 23 and conv.summary == "LATEST+LATEST"


async def test_a_failed_fold_keeps_the_history(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv = _fill(convs, 20)
    assert await convs.fold_if_needed(conv, FakeGenerator(fail=True), budget_chars=1000, keep=6) is False
    assert conv.folded == 0 and conv.summary == ""
    entry = ledger.tail(kind="conversation_fold")[-1]
    assert entry.data["ok"] is False and "summary model down" in entry.data["error"]


def test_finish_with_no_session_id_touches_nothing(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("me", "q")  # recorded without a session
    convs.finish(conv.id, "", "done", "wrong")
    assert conv.exchanges[0].outcome == "pending"


def test_for_owner_breaks_ties_newest_first(tmp_path, ledger, monkeypatch):
    import src.harness.conversations as mod

    monkeypatch.setattr(mod, "_now", lambda: "2026-11-09T00:00:00+00:00")
    convs = make(tmp_path, ledger)
    a = convs.start_new("me")
    b = convs.start_new("me")
    c = convs.start_new("me")
    assert [x.id for x in convs.for_owner("me")] == [c.id, b.id, a.id]


async def test_kept_exchanges_fold_too_when_they_do_not_fit_half_the_budget(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv = _fill(convs, 20)
    assert await convs.fold_if_needed(conv, FakeGenerator(), budget_chars=1000, keep=6) is True
    unfolded = conv.exchanges[conv.folded :]
    assert sum(len(e.user) + len(reply_text(e)) for e in unfolded) <= 500
    assert conv.folded == 19  # the newest is never folded


async def test_long_replies_do_not_fold_on_every_message(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    gen = FakeGenerator()
    folds = []
    for i in range(30):  # as a task runs: begin (pending), fold, finish
        conv, _ = convs.begin("me", f"question {i}", session_id=f"s{i}")
        folds.append(await convs.fold_if_needed(conv, gen, budget_chars=3000, keep=6))
        convs.finish(conv.id, f"s{i}", "done", "y" * 900)  # 6 kept replies alone pass the budget
        assert sum(len(e.user) + len(reply_text(e)) for e in conv.exchanges[conv.folded :]) <= 3000 + 1000
    assert any(folds)
    assert not any(a and b for a, b in zip(folds, folds[1:])), folds


async def test_a_pending_exchange_stops_the_fold(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv = _fill(convs, 5)
    conv.exchanges.append(type(conv.exchanges[0])(user="still running " + "z" * 300, session_id="p"))  # pending
    _fill(convs, 10)
    assert await convs.fold_if_needed(conv, FakeGenerator(), budget_chars=1000, keep=6) is True
    assert conv.folded == 5 and conv.exchanges[5].outcome == "pending"


def test_saved_files_are_redacted_but_memory_keeps_the_text(tmp_path, ledger):
    from src.memory.exclusion import exclusion

    exclusion.register_secret_value("hunter2hunter2")  # conftest clears it after the test
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("me", "my password is hunter2hunter2", session_id="s1")
    convs.finish(conv.id, "s1", "done", "noted hunter2hunter2")
    raw = (tmp_path / "conv" / f"{conv.id}.json").read_text()
    assert "hunter2hunter2" not in raw
    assert "[redacted:" in raw
    ex = convs.get(conv.id).exchanges[0]
    assert ex.user == "my password is hunter2hunter2"  # the current run keeps the real text
    assert ex.reply == "noted hunter2hunter2"


def test_finish_keeps_attachments_and_old_records_load(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("me", "q", session_id="s1")
    path = tmp_path / "conv" / f"{conv.id}.json"
    old = json.loads(path.read_text())
    old["exchanges"][0].pop("attachments", None)  # a record written before attachments existed
    path.write_text(json.dumps(old))
    convs = make(tmp_path, ledger)
    assert convs.get(conv.id).exchanges[0].attachments == []
    convs.finish(conv.id, "s1", "done", "here", attachments=[{"id": "a1", "title": "Flow", "path": "/w/flow.html"}])
    assert convs.attachment(conv.id, "a1") == {"id": "a1", "title": "Flow", "path": "/w/flow.html"}
    assert convs.attachment(conv.id, "zz") is None
    assert convs.attachment("nope", "a1") is None
    assert make(tmp_path, ledger).get(conv.id).exchanges[0].attachments[0]["title"] == "Flow"


def test_history_names_attached_files(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("me", "draw it", session_id="s1")
    convs.finish(conv.id, "s1", "done", "Here it is.", attachments=[{"id": "a1", "title": "Flow", "path": "/w/flow.html"}])
    assert convs.get(conv.id).history()[-1].content == "Here it is.\n(Attached: Flow (flow.html))"


def test_a_helper_exchange_records_its_bot_and_reads_back_with_a_prefix_and_a_suffix(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, ex = convs.begin("designer", "hi", session_id="s1", bot="copywriter")
    assert ex.bot == "copywriter" and not ex.set_aside
    convs.finish(conv.id, "s1", "done", "Rewrote it.", attachments=[{"id": "a1", "title": "Site", "path": "/v/2.index.html"}])
    assert convs.get(conv.id).history()[-1].content == "(copywriter changed the design) Rewrote it.\n(Attached: Site (2.index.html))"
    convs.set_aside(conv.id, "s1")
    assert convs.get(conv.id).history()[-1].content.endswith("(Attached: Site (2.index.html)) (These changes were set aside.)")
    again = make(tmp_path, ledger)
    assert again.get(conv.id).exchanges[0].set_aside is True


@pytest.mark.parametrize("outcome, attachments", [("done", []), ("failed", [{"id": "a1", "title": "Site", "path": "/v/2.index.html"}]),
                                                   ("stopped", [])])
def test_a_helper_exchange_that_changed_nothing_reads_back_without_the_prefix(tmp_path, ledger, outcome, attachments):
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("designer", "hi", session_id="s1", bot="copywriter")
    convs.finish(conv.id, "s1", outcome, "No luck.", attachments=attachments)
    assert "changed the design" not in convs.get(conv.id).history()[-1].content


def test_an_old_record_without_bot_or_set_aside_loads_and_the_owners_reply_is_plain(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv, _ = convs.begin("designer", "hi", session_id="s1")
    convs.finish(conv.id, "s1", "done", "ok")
    path = tmp_path / "conv" / f"{conv.id}.json"
    rec = json.loads(path.read_text())
    rec["exchanges"][0].pop("bot", None)
    rec["exchanges"][0].pop("set_aside", None)
    path.write_text(json.dumps(rec))
    ex = make(tmp_path, ledger).get(conv.id).exchanges[0]
    assert (ex.bot, ex.set_aside) == ("", False)
    assert reply_text(ex) == "ok"


def test_an_edit_exchange_is_one_note_to_the_model(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    c = convs.start_new("designer")
    ex = convs.record_edit(c.id, "You changed 2 texts.", {"id": "a1", "title": "index.html", "path": "/v/designs/x/.versions/5.index.html"})
    assert (ex.user, ex.outcome, ex.reply) == ("", "edit", "You changed 2 texts.")
    assert ex.session_id.startswith("edit-") and ex.attachments[0]["id"] == "a1"
    user, bot = convs.get(c.id).history()[-2:]
    assert user.role == "user" and user.content == "(The user changed the design in the app, not through you: You changed 2 texts.)"
    assert bot.role == "assistant" and bot.content.startswith(EDIT_ACK) and "5.index.html" in bot.content


def test_an_edit_exchange_survives_a_reload(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    c = convs.start_new("designer")
    convs.record_edit(c.id, "You changed 2 texts.", {"id": "a1", "title": "index.html", "path": "/v/5.index.html"})
    ex = make(tmp_path, ledger).get(c.id).exchanges[-1]
    assert ex.outcome == "edit" and ex.attachments[0]["id"] == "a1"


async def test_a_fold_keeps_the_note_of_an_edit_exchange(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    conv = _fill(convs, 2)
    convs.record_edit(conv.id, "You changed 2 texts.", {"id": "a1", "title": "index.html", "path": "/v/5.index.html"})
    _fill(convs, 6)
    gen = FakeGenerator()
    assert await convs.fold_if_needed(conv, gen, budget_chars=1000, keep=2) is True
    rendered, _ = gen.compacted[0]
    assert "(The user changed the design in the app, not through you: You changed 2 texts.)" in rendered


def test_files_only_begin_takes_its_title_from_the_first_file(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    files = [{"id": "a" * 32, "name": "report.pdf", "kind": "document", "path": "uploads/chat/" + "a" * 32 + "/report.pdf"}]
    conv, ex = convs.begin("me", "", session_id="s1", files=files)
    assert conv.title == "report.pdf" and ex.user == "" and ex.files == files


def test_history_names_files_by_line_and_never_their_content(tmp_path, ledger):
    convs = make(tmp_path, ledger)
    files = [{"id": "a" * 32, "name": "a.webp", "kind": "image", "path": f"uploads/chat/{'a' * 32}/a.webp"},
             {"id": "b" * 32, "name": "n.txt", "kind": "document", "path": f"uploads/chat/{'b' * 32}/n.txt"}]
    conv, _ = convs.begin("me", "look", session_id="s1", files=files)
    convs.finish(conv.id, "s1", "done", "seen")
    [user, _] = conv.history()
    assert user.content == (f"look\n\nAttached: a.webp (uploads/chat/{'a' * 32}/a.webp)\n"
                            f"Attached: n.txt (uploads/chat/{'b' * 32}/n.txt)")
    assert user.images == []

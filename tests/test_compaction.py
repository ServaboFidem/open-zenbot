"""compaction: under budget unchanged; over budget system + pinned block +
one summary + recent turns, cut only between whole turns; fold forward;
fail safe on generator error; protected block never summarized; scope
change waits for confirm."""

from __future__ import annotations

import pytest

from src.context.compaction import CompactionConfig, Compactor, render_transcript
from src.context.protected import ProtectedBlock, ScopeGuard
from src.decider.interface import ConfirmState, NoulAnswer
from src.generation.generator import Generator
from src.memory.ledger import Ledger
from src.providers.interface import Message, ProviderUnavailable, ToolCall

from .conftest import MockScorer, ScriptedProvider, completion

SYSTEM = Message("system", "You are the research partner.")
BLOCK = ProtectedBlock(task="write the weekly brief", constraints=["no external sends", "cite sources"])


def turn(i: int, with_tool: bool = True) -> list[Message]:
    """One whole turn: an assistant tool call plus its tool result."""
    if not with_tool:
        return [Message("assistant", f"thought {i} " + "x" * 200)]
    return [
        Message("assistant", f"step {i}", tool_calls=[ToolCall(id=f"c{i}", name="shell", arguments={"command": f"echo {i}"})]),
        Message("tool", f"out {i} " + "y" * 200, tool_call_id=f"c{i}", name="shell"),
    ]


def transcript(n_turns: int) -> list[Message]:
    msgs = [SYSTEM, Message("user", BLOCK.task)]
    for i in range(1, n_turns + 1):
        msgs += turn(i)
    return msgs


def make(provider, ledger, budget=1_000, keep=2) -> Compactor:
    return Compactor(Generator(provider), ledger, CompactionConfig(budget_chars=budget, keep_recent_turns=keep))


async def test_under_budget_returns_unchanged(ledger: Ledger):
    provider = ScriptedProvider([])
    msgs = transcript(2)
    res = await make(provider, ledger, budget=100_000).maybe_compact(msgs, BLOCK, bot_id="b", session_id="s")
    assert res.messages is msgs and not res.compacted and res.summary == ""
    assert provider.calls == []


async def test_over_budget_is_system_pinned_summary_recent(ledger: Ledger):
    provider = ScriptedProvider([completion("SUMMARY: steps 1-4 ran echo")])
    msgs = transcript(6)
    res = await make(provider, ledger, budget=1_000, keep=2).maybe_compact(msgs, BLOCK, bot_id="b", session_id="s")

    assert res.compacted and res.summary == "SUMMARY: steps 1-4 ran echo"
    roles = [m.role for m in res.messages]
    assert res.messages[0] is SYSTEM
    assert res.messages[1].role == "system" and BLOCK.render() == res.messages[1].content
    assert res.messages[2].role == "system" and "SUMMARY: steps 1-4 ran echo" in res.messages[2].content
    # a user turn precedes the kept window; recent = last 2 whole turns = 4 messages, order preserved
    assert res.messages[3].role == "user"
    assert roles[4:] == ["assistant", "tool", "assistant", "tool"]
    assert res.messages[4].content == "step 5" and res.messages[-1].tool_call_id == "c6"

    # the generator saw the old turns rendered with their tool calls, plus the goal
    sent = provider.calls[0][-1].content
    assert "shell" in sent and "echo 1" in sent and "out 4" in sent and "step 5" not in sent
    assert BLOCK.task in sent

    e = ledger.tail(kind="compaction", session_id="s")[0]
    assert e.data["before"] == len(msgs) and e.data["after"] == len(res.messages) and e.data["ok"] is True


async def test_cut_only_between_whole_turns_keeps_tool_result_with_its_call(ledger: Ledger):
    provider = ScriptedProvider([completion("sum")])
    # last message is a bare assistant text turn; keep=2 must take [call, result] + [text], never split c3
    msgs = transcript(3) + turn(4, with_tool=False)
    res = await make(provider, ledger, budget=800, keep=2).maybe_compact(msgs, BLOCK, bot_id="b", session_id="s")
    recent = res.messages[4:]
    assert [m.role for m in recent] == ["assistant", "tool", "assistant"]
    assert recent[0].tool_calls[0].id == "c3" and recent[1].tool_call_id == "c3"
    # no tool result ever appears without its call directly before it
    for i, m in enumerate(res.messages):
        if m.role == "tool":
            assert res.messages[i - 1].role == "assistant" and res.messages[i - 1].tool_calls


async def test_summary_folds_forward_on_next_round(ledger: Ledger):
    provider = ScriptedProvider([completion("first summary"), completion("second summary"), completion("folded summary")])
    c = make(provider, ledger, budget=1_000, keep=1)
    res1 = await c.maybe_compact(transcript(4), BLOCK, bot_id="b", session_id="s")
    assert res1.summary == "first summary"

    # grow again past budget: the summary message plus new turns
    msgs = res1.messages + turn(5) + turn(6) + turn(7)
    res2 = await c.maybe_compact(msgs, BLOCK, bot_id="b", session_id="s", summary=res1.summary)
    assert res2.summary == "folded summary"
    fold_call = provider.calls[2]
    assert "first summary" in fold_call[-1].content and "second summary" in fold_call[-1].content
    # exactly one summary message, the earlier one is not summarized twice
    assert sum(1 for m in res2.messages if m.role == "system" and "folded summary" in m.content) == 1
    assert not any("first summary" in m.content for m in res2.messages)


async def test_generator_error_keeps_full_history(ledger: Ledger):
    provider = ScriptedProvider([ProviderUnavailable("offline")])
    msgs = transcript(6)
    res = await make(provider, ledger, budget=1_000).maybe_compact(msgs, BLOCK, bot_id="b", session_id="s")
    assert res.messages is msgs and not res.compacted and res.error and "offline" in res.error
    e = ledger.tail(kind="compaction", session_id="s")[0]
    assert e.data["ok"] is False and "offline" in e.data["error"]


async def test_protected_block_never_summarized(ledger: Ledger):
    provider = ScriptedProvider([completion("s1"), completion("s2"), completion("f")])
    c = make(provider, ledger, budget=1_000, keep=1)
    res1 = await c.maybe_compact(transcript(4), BLOCK, bot_id="b", session_id="s")
    msgs = res1.messages + turn(5) + turn(6) + turn(7)
    await c.maybe_compact(msgs, BLOCK, bot_id="b", session_id="s", summary=res1.summary)
    for call in provider.calls:
        assert BLOCK.render() not in call[-1].content
        assert "cite sources" not in call[-1].content
    assert render_transcript(msgs, skip_pinned=True).count(BLOCK.render()) == 0


# -- scope change ---------------------------------------------------------


async def test_scope_change_waits_for_confirm_then_bumps(ledger: Ledger):
    scorer = MockScorer(answer=lambda r: {"new_goal": NoulAnswer(0.9), "new_constraints": NoulAnswer(0.1)})
    bumped = []
    guard = ScopeGuard(scorer, ledger, BLOCK, on_confirm=lambda: bumped.append(1), threshold=0.7)

    change = await guard.check("actually, forget the brief, build me a dashboard instead", bot_id="b", session_id="s")
    assert change.changed and change.new_goal and not change.new_constraints
    assert change.confirm == ConfirmState.pending
    assert guard.block == BLOCK and guard.block.task == "write the weekly brief"  # unchanged until confirm
    assert bumped == []
    assert scorer.requests[0].purpose == "scope"
    assert ledger.tail(kind="scope_change", session_id="s")[0].data["confirm"] == "pending"

    guard.confirm(task="build me a dashboard")
    assert guard.block.task == "build me a dashboard" and guard.block.constraints == BLOCK.constraints
    assert guard.pending is None and bumped == [1]


async def test_scope_change_rejected_keeps_block(ledger: Ledger):
    scorer = MockScorer(answer=lambda r: {"new_goal": NoulAnswer(0.2), "new_constraints": NoulAnswer(0.95)})
    guard = ScopeGuard(scorer, ledger, BLOCK, on_confirm=lambda: None)
    change = await guard.check("from now on never touch the prod db", bot_id="b", session_id="s")
    assert change.changed and change.new_constraints
    guard.reject()
    assert guard.block == BLOCK and guard.pending is None


async def test_no_scope_change_needs_no_confirm(ledger: Ledger):
    scorer = MockScorer(answer=lambda r: {"new_goal": NoulAnswer(0.05), "new_constraints": NoulAnswer(0.05)})
    guard = ScopeGuard(scorer, ledger, BLOCK, on_confirm=lambda: None)
    change = await guard.check("ok go on", bot_id="b", session_id="s")
    assert not change.changed and change.confirm == ConfirmState.not_needed and guard.pending is None


async def test_scope_check_error_fails_open(ledger: Ledger):
    scorer = MockScorer(answer=lambda r: ProviderUnavailable("down"))
    guard = ScopeGuard(scorer, ledger, BLOCK, on_confirm=lambda: None)
    change = await guard.check("whatever", bot_id="b", session_id="s")
    assert not change.changed and guard.block == BLOCK


async def test_confirm_without_pending_is_noop():
    guard = ScopeGuard(MockScorer(), None, BLOCK, on_confirm=lambda: pytest.fail("must not bump"))
    guard.confirm(task="x")
    assert guard.block == BLOCK


# -- loop hook -----------------------------------------------------------


async def test_loop_compacts_before_the_call(ledger: Ledger, tmp_path):
    from src.harness.approvals import ApprovalStore
    from src.harness.bot import VOICE, Bot
    from src.harness.loop import SessionState, TaskLoop
    from src.harness.tools import ToolRunner

    bot = Bot(name="Worker", role="Do the thing", id="bot1")
    steps = [completion(calls=[(f"c{i}", "shell", {"command": f"echo {i} " + "z" * 300})]) for i in range(1, 7)]
    provider = ScriptedProvider(steps + [completion("done")])
    summarizer = ScriptedProvider([completion("SUMMARY of 1-3"), completion("SUMMARY folded"), completion("SUMMARY folded again")])
    compactor = Compactor(Generator(summarizer), ledger, CompactionConfig(budget_chars=1_500 + len(VOICE), keep_recent_turns=1))  # the shared voice rides in every system prompt
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path, timeout=10), max_steps=20, compactor=compactor)

    s = await loop.run(bot, "echo a lot")
    assert s.state == SessionState.done and s.compactions >= 1 and s.summary.startswith("SUMMARY")
    assert s.protected == {"task": "echo a lot", "constraints": []}
    # the first provider call after a compaction saw: system, pinned block, summary, then whole turns
    after = [c for c in provider.calls if len(c) > 1 and c[1].content.startswith("## Protected task")]
    assert after, "no call saw the pinned block"
    first = after[0]
    assert first[0].role == "system" and "Worker" in first[0].content
    assert first[2].content.startswith("## Summary of earlier work") and "SUMMARY of 1-3" in first[2].content
    assert summarizer.calls and "echo 1" in summarizer.calls[0][-1].content
    for i, m in enumerate(first):
        if m.role == "tool":
            assert first[i - 1].role == "assistant" and first[i - 1].tool_calls
    e = ledger.tail(kind="compaction", session_id=s.id)[0]
    assert e.data["ok"] is True and e.data["chars_after"] < e.data["chars"]


# -- review fixes ---------------------------------------------------------


async def test_over_budget_with_nothing_old_is_not_a_compaction(ledger: Ledger):
    # a big system prompt and one turn: nothing to summarize, so nothing changes and no call is made
    provider = ScriptedProvider([])
    msgs = [Message("system", "S" * 600), Message("user", BLOCK.task)] + turn(1)
    res = await make(provider, ledger, budget=500, keep=6).maybe_compact(msgs, BLOCK, bot_id="b", session_id="s")
    assert res.messages is msgs and not res.compacted and provider.calls == []


async def test_kept_window_shrinks_to_fit_budget(ledger: Ledger):
    # keep=6 but only ~2 turns fit: the first compaction must already land under budget
    provider = ScriptedProvider([completion("sum")])
    msgs = transcript(8)
    budget = 3 * 230 + len(SYSTEM.content) + len(BLOCK.render()) + 120
    res = await make(provider, ledger, budget=budget, keep=6).maybe_compact(msgs, BLOCK, bot_id="b", session_id="s")
    assert res.compacted
    e = ledger.tail(kind="compaction", session_id="s")[0]
    assert e.data["chars_after"] <= budget and e.data["kept_turns"] < 6
    assert len(provider.calls) == 1


async def test_post_compaction_context_starts_with_a_user_message(ledger: Ledger):
    provider = ScriptedProvider([completion("sum")])
    res = await make(provider, ledger, budget=1_000, keep=2).maybe_compact(transcript(6), BLOCK, bot_id="b", session_id="s")
    first = next(m for m in res.messages if m.role != "system")
    assert first.role == "user"
    # and the whole turns after it are intact
    assert [m.role for m in res.messages[4:]] == ["assistant", "tool", "assistant", "tool"]


async def test_context_message_survives_in_task_compaction(ledger: Ledger, tmp_path):
    from src.harness.approvals import ApprovalStore
    from src.harness.bot import VOICE, Bot
    from src.harness.loop import SessionState, TaskLoop
    from src.harness.stubs import NoRecall
    from src.harness.tools import ToolRunner
    from src.providers.interface import Message

    class Facts(NoRecall):
        async def recall(self, bot, query, session_id):
            return ["the user keeps bees"]

    bot = Bot(name="Worker", role="Do the thing", id="bot1")
    steps = [completion(calls=[(f"c{i}", "shell", {"command": f"echo {i} " + "z" * 300})]) for i in range(1, 7)]
    provider = ScriptedProvider(steps + [completion("done")])
    summarizer = ScriptedProvider([completion("SUMMARY a"), completion("SUMMARY b"), completion("SUMMARY c")])
    compactor = Compactor(Generator(summarizer), ledger, CompactionConfig(budget_chars=1_500 + len(VOICE), keep_recent_turns=1))
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path, timeout=10), max_steps=20, compactor=compactor)
    loop.recall = Facts()

    s = await loop.run(bot, "echo a lot", history=[Message("user", "earlier q"), Message("assistant", "earlier a")])
    assert s.state == SessionState.done and s.compactions >= 1
    for call in provider.calls[1:]:
        assert sum("the user keeps bees" in m.content for m in call) == 1
        ctx = next(m for m in call if "the user keeps bees" in m.content)
        assert ctx.role == "user"  # still a user message after the strip and reinsert
    assert not any("the user keeps bees" in m.content for c in summarizer.calls for m in c)

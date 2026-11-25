"""task-loop: one task end to end on the mock provider, approvals from
frontmatter, ledger coverage, stop."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.harness.approvals import ApprovalState, ApprovalStore, needs_approval
from src.harness.bot import Bot
from src.harness.loop import SessionState, TaskLoop, offered_groups
from src.harness.tools import ToolRunner
from src.memory.ledger import Ledger
from src.providers.interface import ToolCall
from src.skills.loader import Reason, SkillState, load_skill, parse_skill

from .conftest import ScriptedProvider, completion


@pytest.fixture
def bot(example_skill_dir: Path) -> Bot:
    b = Bot(name="Research partner", role="Turn the noise into something useful.")
    b.attach_skill(load_skill(example_skill_dir).skill)
    return b


def make_loop(provider, ledger, tmp_path, approvals=None) -> TaskLoop:
    return TaskLoop(provider, ledger, approvals or ApprovalStore(), ToolRunner(tmp_path, timeout=10), max_steps=10)


async def test_one_task_end_to_end(bot: Bot, ledger: Ledger, tmp_path: Path):
    first = completion(calls=[("c1", "shell", {"command": "echo hello from the shell"})])
    first.usage.cached_tokens = 7
    provider = ScriptedProvider([first, completion("Findings: the shell said hello.")])
    s = await make_loop(provider, ledger, tmp_path).run(bot, "say hello via the shell")

    assert s.state == SessionState.done
    assert s.result == "Findings: the shell said hello."
    assert s.steps == 2 and s.injected == ["research-brief"]

    # context: system prompt carries role and the injected skill; tool result fed back
    system = provider.calls[0][0]
    context = provider.calls[0][-2]
    assert system.role == "system" and "Research partner" in system.content
    # the per-message context rides as its own user message, outside the cached system prefix
    assert context.role == "user" and "## Skill: Research brief" in context.content
    second_call = provider.calls[1]
    assert second_call[-1].role == "tool" and "hello from the shell" in second_call[-1].content

    kinds = [e.kind for e in ledger.tail(session_id=s.id)]
    assert kinds == ["session", "turn", "tool_start", "tool_call", "turn", "session"]
    turn = ledger.tail(kind="turn", session_id=s.id)[0]
    assert turn.data["provider"] == "mock" and turn.data["tool_calls"] == ["shell"] and turn.data["cost"] == 0.0001
    assert turn.data["cached_tokens"] == 7
    started = ledger.tail(kind="tool_start", session_id=s.id)[0]
    assert started.data == {"tool": "shell", "arguments": {"command": "echo hello from the shell"}, "step": 1}


async def test_approval_gate_reads_frontmatter_not_prompt(bot: Bot, ledger: Ledger, tmp_path: Path):
    approvals = ApprovalStore()
    provider = ScriptedProvider([
        completion(calls=[("c1", "shell", {"command": "git push origin main"})]),
        completion("Pushed."),
    ])
    loop = make_loop(provider, ledger, tmp_path, approvals)
    # the prompt says "send" and "publish"; the gate must ignore that and match the shell rule
    task = asyncio.create_task(loop.run(bot, "send and publish the update"))

    for _ in range(50):
        await asyncio.sleep(0.01)
        if approvals.list(ApprovalState.pending):
            break
    pending = approvals.list(ApprovalState.pending)
    assert len(pending) == 1 and pending[0].rule == "shell:git push*" and pending[0].tool == "shell"
    assert not task.done()

    approvals.decide(pending[0].id, approved=True)
    s = await task
    assert s.state == SessionState.done and s.approvals == [pending[0].id]
    kinds = [e.kind for e in ledger.tail(session_id=s.id)]
    assert "approval_request" in kinds and "approval_decision" in kinds
    assert ledger.tail(kind="approval_decision", session_id=s.id)[0].data["decision"] == "approved"


async def test_rejection_tells_the_model_and_runs_nothing(bot: Bot, ledger: Ledger, tmp_path: Path):
    approvals = ApprovalStore()
    marker = tmp_path / "should-not-exist"
    provider = ScriptedProvider([
        completion(calls=[("c1", "shell", {"command": f"rm -rf {marker}; touch {marker}"})]),
        completion("Understood, I did not run it."),
    ])
    loop = make_loop(provider, ledger, tmp_path, approvals)
    task = asyncio.create_task(loop.run(bot, "clean up"))
    while not approvals.list(ApprovalState.pending):
        await asyncio.sleep(0.01)
    approvals.decide(approvals.list(ApprovalState.pending)[0].id, approved=False)
    s = await task
    assert s.state == SessionState.done
    assert not marker.exists()
    assert "rejected" in provider.calls[1][-1].content
    assert not [e for e in ledger.tail(kind="tool_call", session_id=s.id)]


async def test_no_approval_without_a_matching_rule(bot: Bot, ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([
        completion(calls=[("c1", "shell", {"command": "git status"})]),
        completion("Clean tree."),
    ])
    s = await make_loop(provider, ledger, tmp_path).run(bot, "check git")
    assert s.state == SessionState.done and s.approvals == []


def test_rule_matching_forms():
    skills = [parse_skill("---\nname: R\nuse: u\napprovals:\n  - 'shell:rm *'\n  - send_email\n  - 'mcp_drive:delete*'\n---\n")]
    assert needs_approval(skills, ToolCall("1", "shell", {"command": "rm -rf /tmp/x"})) == "shell:rm *"
    assert needs_approval(skills, ToolCall("1", "shell", {"command": "ls"})) is None
    assert needs_approval(skills, ToolCall("1", "send_email", {"to": "a@b"})) == "send_email"
    assert needs_approval(skills, ToolCall("1", "mcp_drive", {"op": "delete file"})) == "mcp_drive:delete*"
    assert needs_approval(skills, ToolCall("1", "mcp_drive", {"op": "list"})) is None
    star = [parse_skill("---\nname: S\nuse: u\napprovals: ['*']\n---\n")]
    assert needs_approval(star, ToolCall("1", "anything", {})) == "*"


async def test_unknown_tool_is_reported_to_model(bot: Bot, ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([
        completion(calls=[("c1", "teleport", {"to": "mars"})]),
        completion("No such tool, sorry."),
    ])
    s = await make_loop(provider, ledger, tmp_path).run(bot, "go")
    assert s.state == SessionState.done
    assert "unknown tool" in provider.calls[1][-1].content


async def test_max_steps_fails_cleanly(bot: Bot, ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion(calls=[("c", "shell", {"command": "true"})])] * 10)
    s = await make_loop(provider, ledger, tmp_path).run(bot, "loop forever")
    assert s.state == SessionState.failed and "max steps" in s.error


async def test_stop_while_waiting_for_approval(bot: Bot, ledger: Ledger, tmp_path: Path):
    approvals = ApprovalStore()
    provider = ScriptedProvider([completion(calls=[("c1", "shell", {"command": "git push"})])])
    loop = make_loop(provider, ledger, tmp_path, approvals)
    task = asyncio.create_task(loop.run(bot, "push"))
    while not approvals.list(ApprovalState.pending):
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger.tail(session_id=loop.ledger.tail(1)[0].session_id)[-1].data == {"event": "stopped", "by": "cancel"}


def _delegating_loop(provider, ledger, tmp_path, approvals, check) -> tuple[TaskLoop, list[dict]]:
    """A loop with a stand-in `delegate` tool that records each run."""
    ran: list[dict] = []
    loop = TaskLoop(provider, ledger, approvals, ToolRunner(tmp_path, timeout=10), max_steps=10, delegate_check=check)

    async def delegate(agent: str, task: str, folder: str | None = None) -> str:
        ran.append({"agent": agent, "task": task, "folder": folder})
        return "Handed off."

    loop.tools.register("delegate", "d", {"type": "object", "properties": {}}, delegate)
    return loop, ran


async def _pending(approvals: ApprovalStore):
    while not approvals.list(ApprovalState.pending):
        await asyncio.sleep(0.01)
    return approvals.list(ApprovalState.pending)


async def test_delegate_waits_for_approval_and_runs_when_approved(bot: Bot, ledger: Ledger, tmp_path: Path):
    approvals, asked = ApprovalStore(), []
    args = {"agent": "claude-code", "task": "Write the report."}
    provider = ScriptedProvider([completion(calls=[("c1", "delegate", args)]), completion("Done.")])
    loop, ran = _delegating_loop(provider, ledger, tmp_path, approvals,
                                 lambda a, forced, session_id: asked.append((a, forced, session_id)) or
                                 ("delegate:claude-code", "delegate: Claude Code: Write", "Hand this to Claude Code?"))
    task = asyncio.create_task(loop.run(bot, "write it"))
    pending = await _pending(approvals)
    assert len(pending) == 1 and (pending[0].rule, pending[0].tool) == ("delegate:claude-code", "delegate")
    assert pending[0].detail == "Hand this to Claude Code?" and pending[0].title == "delegate: Claude Code: Write"
    assert asked == [(args, False, pending[0].session_id)]
    assert ran == [] and not task.done()
    approvals.decide(pending[0].id, approved=True)
    s = await task
    assert s.state == SessionState.done and s.approvals == [pending[0].id]
    assert ran == [{**args, "folder": None}]
    req = ledger.tail(kind="approval_request", session_id=s.id)[0].data
    assert (req["rule"], req["tool"]) == ("delegate:claude-code", "delegate")


async def test_delegate_rejected_runs_nothing_and_tells_the_bot(bot: Bot, ledger: Ledger, tmp_path: Path):
    from src.harness.delegation import REJECTED

    approvals = ApprovalStore()
    provider = ScriptedProvider([completion(calls=[("c1", "delegate", {"agent": "codex", "task": "t"})]),
                                 completion("I will do it myself.")])
    loop, ran = _delegating_loop(provider, ledger, tmp_path, approvals,
                                 lambda a, forced, session_id: ("delegate:codex", "delegate: Codex: t", "Codex?"))
    task = asyncio.create_task(loop.run(bot, "write it"))
    approvals.decide((await _pending(approvals))[0].id, approved=False)
    s = await task
    assert s.state == SessionState.done and ran == []
    assert provider.calls[1][-1].content == REJECTED
    assert not ledger.tail(kind="tool_call", session_id=s.id)


async def test_delegate_without_a_check_result_runs_at_once(bot: Bot, ledger: Ledger, tmp_path: Path):
    approvals = ApprovalStore()
    provider = ScriptedProvider([completion(calls=[("c1", "delegate", {"agent": "claude-code", "task": "t"})]),
                                 completion("Done.")])
    loop, ran = _delegating_loop(provider, ledger, tmp_path, approvals, lambda a, forced, session_id: None)
    s = await loop.run(bot, "write it")
    assert s.state == SessionState.done and s.approvals == [] and approvals.list() == []
    assert len(ran) == 1 and provider.calls[1][-1].content == "Handed off."


def _real_delegator(tmp_path: Path, enabled: bool, ask: bool, in_routine=lambda session_id: False):
    """A Delegator whose Claude Code is found and usable, with the switches given; its runner only records."""
    from src.harness.agent_clis import Agents, AgentSettings, save_settings
    from src.harness.allowed_folders import Folders
    from src.harness.delegation import Delegator, Runner

    class Recording(Runner):
        async def run(self, *a, **kw):
            self.calls.append(a)
            raise AssertionError("nothing may be launched")

    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    agents = Agents(vault)
    agents.statuses["claude-code"] = {"path": "claude", "usable": True}
    save_settings(vault, "claude-code", AgentSettings(enabled=enabled, ask=ask))
    runner = Recording(agents, None)
    runner.calls = []
    return Delegator(vault, agents, lambda: Folders(full=[vault]), runner, in_routine=in_routine)


async def test_a_skill_rule_on_delegate_still_shows_the_hand_off(ledger: Ledger, tmp_path: Path):
    from src.harness.delegation import REJECTED

    b = Bot(name="Writer", role="Writes.")
    b.attach_skill(parse_skill("---\nname: Careful\nuse: u\napprovals:\n  - delegate\n---\nAsk first.\n"))
    d = _real_delegator(tmp_path, enabled=True, ask=False)  # the setting alone would not ask
    task = "Write\nthe quarterly report " + "z" * 100
    approvals = ApprovalStore()
    provider = ScriptedProvider([completion(calls=[("c1", "delegate", {"agent": "claude-code", "task": task})]),
                                 completion("Fine.")])
    loop, ran = _delegating_loop(provider, ledger, tmp_path, approvals, d.needs_approval)
    running = asyncio.create_task(loop.run(b, "write it"))
    pending = (await _pending(approvals))[0]
    assert pending.rule == "delegate"  # the skill's rule keeps its name
    assert "Hand this job to Claude Code?" in pending.detail and task in pending.detail
    assert pending.title == "delegate: Claude Code: " + ("Write the quarterly report " + "z" * 100)[:80]
    approvals.decide(pending.id, approved=False)
    await running
    assert ran == [] and provider.calls[1][-1].content == REJECTED


async def test_an_agent_switched_off_is_refused_without_an_approval(bot: Bot, ledger: Ledger, tmp_path: Path):
    d = _real_delegator(tmp_path, enabled=False, ask=True)
    approvals = ApprovalStore()
    provider = ScriptedProvider([completion(calls=[("c1", "delegate", {"agent": "claude-code", "task": "t"})]),
                                 completion("Understood.")])
    loop = TaskLoop(provider, ledger, approvals, ToolRunner(tmp_path, timeout=10), max_steps=10,
                    delegate_check=d.needs_approval)
    # the bot was offered the tool before the switch went off: the function is still registered
    loop.tools.register("delegate", "d", {"type": "object", "properties": {}}, d.delegate)
    s = await loop.run(bot, "hand it off")
    assert s.state == SessionState.done and approvals.list() == [] and s.approvals == []
    assert "Claude Code is switched off in Settings." in provider.calls[1][-1].content
    assert d.runner.calls == []


async def test_a_routine_is_refused_before_any_approval_opens(ledger: Ledger, tmp_path: Path):
    """Nobody is there to approve in a routine: not the hand-off's own approval, nor a skill rule's."""
    b = Bot(name="Writer", role="Writes.")
    b.attach_skill(parse_skill("---\nname: Careful\nuse: u\napprovals:\n  - delegate\n---\nAsk first.\n"))
    seen: list[str] = []
    d = _real_delegator(tmp_path, enabled=True, ask=True, in_routine=lambda session_id: seen.append(session_id) or True)
    approvals = ApprovalStore()
    provider = ScriptedProvider([completion(calls=[("c1", "delegate", {"agent": "claude-code", "task": "t",
                                                                        "folder": "x"})]),
                                 completion("Understood.")])
    loop = TaskLoop(provider, ledger, approvals, ToolRunner(tmp_path, timeout=10), max_steps=10,
                    delegate_check=d.needs_approval)
    loop.tools.register("delegate", "d", {"type": "object", "properties": {}}, d.delegate)
    s = await asyncio.wait_for(loop.run(b, "hand it off"), 10)
    assert s.state == SessionState.done and approvals.list() == [] and s.approvals == []
    assert "delegate works only in a conversation or a room turn." in provider.calls[1][-1].content
    assert d.runner.calls == [] and seen and set(seen) == {s.id}


def test_bot_skill_cap_is_config():
    b = Bot(name="x", role="y")
    a = parse_skill("---\nname: A\nuse: u\n---\n")
    c = parse_skill("---\nname: C\nuse: u\n---\n")
    assert b.attach_skill(a, max_per_bot=1) == (SkillState.added, None)
    assert b.attach_skill(a, max_per_bot=1) == (SkillState.nothing, Reason.duplicate)
    assert b.attach_skill(c, max_per_bot=1) == (SkillState.nothing, Reason.too_many_skills)
    assert b.detach_skill("a") and b.skills == []


async def test_tools_see_the_calling_bot_and_session(bot: Bot, ledger: Ledger, tmp_path: Path):
    """A built-in tool (learn_page) needs to know which bot and session called it: the loop sets a
    context variable around the call and resets it after."""
    from src.harness.tools import current_call

    seen = []
    provider = ScriptedProvider([completion(calls=[("c1", "peek", {})]), completion("done")])
    loop = make_loop(provider, ledger, tmp_path)
    loop.tools.register("peek", "p", {"type": "object", "properties": {}}, lambda: seen.append(current_call.get()) or "ok")
    s = await loop.run(bot, "peek")
    assert s.state == SessionState.done and seen == [(bot.id, s.id)]
    assert current_call.get() == ("", "")  # reset after the call


class _Facts:
    kind = "test"

    def __init__(self, *per_task):
        self.per_task = list(per_task)

    async def recall(self, bot, query, session_id):
        return self.per_task.pop(0)


async def test_loop_message_order_is_system_history_context_user(bot: Bot, ledger: Ledger, tmp_path: Path):
    from src.harness.loop import Session
    from src.providers.interface import Message

    history = [Message("user", "earlier q"), Message("assistant", "earlier a")]
    provider = ScriptedProvider([completion("ok")])
    loop = make_loop(provider, ledger, tmp_path)
    loop.recall = _Facts(["the user keeps bees"])
    session = Session(bot_id=bot.id, task="new q")
    await loop.run(bot, "new q", session, history=history)
    call = provider.calls[0]
    assert call[0].content == bot.base_prompt()
    assert call[1:3] == history
    assert call[-1] == Message("user", "new q")
    assert call[-2].role == "user" and "Things you remember" in call[-2].content
    assert [m.role for m in call].count("system") == 1  # only the cacheable base prompt is a system message
    assert "## Skill: Research brief" in call[-2].content


async def test_two_tasks_share_an_identical_prefix(bot: Bot, ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("a"), completion("b")])
    loop = make_loop(provider, ledger, tmp_path)
    loop.recall = _Facts(["fact one"], ["fact two"])
    await loop.run(bot, "q1")
    await loop.run(bot, "q2")
    assert provider.calls[0][0].content == provider.calls[1][0].content
    assert "fact one" not in provider.calls[0][0].content


async def test_session_id_is_the_conversation_id(bot: Bot, ledger: Ledger, tmp_path: Path):
    from src.harness.loop import Session

    provider = ScriptedProvider([completion("a"), completion("b")])
    loop = make_loop(provider, ledger, tmp_path)
    s = await loop.run(bot, "q", Session(bot_id=bot.id, task="q", conversation_id="c1"))
    await loop.run(bot, "q")
    assert provider.session_ids == ["c1", None]
    assert s.to_dict()["conversation_id"] == "c1"


class _NoSelect:
    kind = "never"

    async def select(self, bot, task, session_id=""):
        raise AssertionError("a pinned run must not ask the selector")


async def test_pinned_skills_bypass_selector(bot: Bot, ledger: Ledger, tmp_path: Path):
    from src.harness.loop import Session

    provider = ScriptedProvider([completion("ok")])
    loop = make_loop(provider, ledger, tmp_path)
    loop.selector = _NoSelect()
    s = await loop.run(bot, "weekly digest", Session(bot_id=bot.id, task="weekly digest", pinned_skills=["research-brief"]))
    assert s.state == SessionState.done
    assert s.injected == ["research-brief"]
    assert s.route == {"skills": ["research-brief"], "mcps": [], "outcome": "act", "scorer": None, "pinned": True}
    assert "## Skill: Research brief" in provider.calls[0][-2].content


async def test_a_type_error_inside_a_tool_is_not_called_bad_arguments(tmp_path: Path):
    def broken(n: int = 8):
        import re
        return re.sub("a", "b", {"n": n})  # a bug in the tool, not in the call

    tools = ToolRunner(tmp_path, timeout=10)
    tools.register("broken", "b", {"type": "object", "properties": {}}, broken)
    inner = await tools.run(ToolCall("c1", "broken", {"n": 4}))
    assert not inner.ok and inner.error.startswith("TypeError:") and "bad arguments" not in inner.error
    wrong = await tools.run(ToolCall("c2", "broken", {"count": 4}))
    assert not wrong.ok and wrong.error.startswith("bad arguments:")


async def test_pinned_run_offers_only_its_bots_servers_and_the_skills_tools(bot: Bot, ledger: Ledger, tmp_path: Path):
    # a routine skips the router; it must not fall back to offering every connected server's tools
    from src.harness.loop import Session

    provider = ScriptedProvider([completion("ok")])
    loop = make_loop(provider, ledger, tmp_path)
    loop.selector = _NoSelect()
    obj = {"type": "object", "properties": {}}
    for name, group in [("music_profile", "music"), ("image.generate", "image"), ("pexels.photos_search", "pexels"),
                        ("exa.search", "exa")]:
        loop.tools.register(name, name, obj, lambda: "ok", group=group)
    bot.mcps = ["music"]
    bot.skills[0].access = ["exa.search"]
    s = await loop.run(bot, "weekly digest", Session(bot_id=bot.id, task="weekly digest", pinned_skills=["research-brief"]))

    assert s.route["mcps"] == ["music", "exa"]
    offered = set(provider.tools[0])
    assert {"music_profile", "exa.search", "shell"} <= offered
    assert not offered & {"image.generate", "pexels.photos_search"}


async def test_missing_pinned_skill_fails_session(bot: Bot, ledger: Ledger, tmp_path: Path):
    from src.harness.loop import Session

    provider = ScriptedProvider([completion("ok")])
    loop = make_loop(provider, ledger, tmp_path)
    loop.selector = _NoSelect()
    s = await loop.run(bot, "t", Session(bot_id=bot.id, task="t", pinned_skills=["nope"]))
    assert s.state == SessionState.failed
    assert s.error == f"Routine skill 'nope' is not attached to {bot.id}."
    assert provider.calls == []


def test_offered_groups_for_a_closed_bot_ignores_the_route():
    bot = Bot(name="S", role="r", mcps=["events"], closed=True)
    for route in (None, {}, {"bands": {"mcp": 1}, "mcps": ["browser"]}, {"pinned": True, "mcps": ["events", "files"]}):
        assert offered_groups(bot, route) == ({"events"}, False)
    open_bot = Bot(name="O", role="r")
    assert offered_groups(open_bot, None) == (None, True)
    assert offered_groups(open_bot, {"pinned": True, "mcps": ["a"]}) == ({"a"}, True)


async def test_a_closed_bot_is_offered_only_its_own_tools(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("ok")])
    loop = make_loop(provider, ledger, tmp_path)
    obj = {"type": "object", "properties": {}}
    loop.tools.register("events_x", "d", obj, lambda: "ok", group="events")
    loop.tools.register("music_x", "d", obj, lambda: "ok", group="music")
    loop.tools.register("attach_file", "d", obj, lambda: "ok")
    await loop.run(Bot(name="S", role="r", mcps=["events"], closed=True), "go")
    assert set(provider.tools[0]) == {"events_x"}


async def test_a_closed_bot_cannot_run_a_tool_it_was_not_offered(ledger: Ledger, tmp_path: Path):
    ran: list[str] = []
    provider = ScriptedProvider([
        completion(calls=[("c1", "shell", {"command": "echo hi"}), ("c2", "music_x", {}), ("c3", "events_x", {})]),
        completion("done"),
    ])
    loop = make_loop(provider, ledger, tmp_path)
    obj = {"type": "object", "properties": {}}
    loop.tools.register("music_x", "d", obj, lambda: ran.append("music") or "ok", group="music")
    loop.tools.register("events_x", "d", obj, lambda: ran.append("events") or "ok", group="events")
    loop.tools.register("shell", "d", {"type": "object", "properties": {"command": {"type": "string"}}},
                        lambda command: ran.append("shell") or "ok")
    s = await loop.run(Bot(name="S", role="r", mcps=["events"], closed=True), "go")
    assert s.state == SessionState.done
    results = {m.name: m.content for m in provider.calls[1] if m.role == "tool"}
    assert "not available to this bot" in results["shell"] and "not available to this bot" in results["music_x"]
    assert "ok" in results["events_x"]
    assert ran == ["events"]
    assert not [e for e in ledger.tail(kind="tool_start", session_id=s.id) if e.data["tool"] != "events_x"]


async def test_pinned_routine_gets_bot_mcps_sonos(bot: Bot, ledger: Ledger, tmp_path: Path):
    # a routine of a bot with mcps [sonos] is offered the Sonos tools while they are registered
    from src.harness.loop import Session

    provider = ScriptedProvider([completion("ok")])
    loop = make_loop(provider, ledger, tmp_path)
    loop.selector = _NoSelect()
    loop.tools.register("sonos_rooms", "rooms", {"type": "object", "properties": {}}, lambda: "ok", group="sonos")
    bot.mcps = ["sonos"]
    s = await loop.run(bot, "morning music", Session(bot_id=bot.id, task="morning music", pinned_skills=["research-brief"]))
    assert s.route["mcps"] == ["sonos"]
    assert "sonos_rooms" in set(provider.tools[0])


class _Spy:
    kind = "spy"

    def __init__(self):
        self.tasks: list[str] = []

    async def select(self, bot, task, session_id=""):
        from src.harness.stubs import Selection
        self.tasks.append(task)
        return Selection([], {"skills": [], "outcome": "act", "scorer": None})

    async def recall(self, bot, task, session_id=""):
        self.tasks.append(task)
        return []


async def test_turn_extra_and_images_reach_the_last_message_but_not_routing(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([completion("done"), completion("done")])
    spy = _Spy()
    loop = make_loop(provider, ledger, tmp_path)
    loop.selector = spy
    loop.recall = spy
    s = await loop.run(Bot(name="B", role="r"), "read it", turn_extra="FILE TEXT", images=["data:image/webp;base64,AA"],
                       files=2)
    last = provider.calls[0][-1]
    assert last.content == "read it\n\nFILE TEXT" and last.images == ["data:image/webp;base64,AA"]
    assert spy.tasks == ["read it", "read it"]
    start = ledger.tail(kind="session", session_id=s.id)[0]
    assert start.data["task"] == "read it" and start.data["images"] == 1
    assert start.data["files"] == 2 and start.data["images_sent"] is True
    s = await loop.run(Bot(name="B", role="r"), "plain")
    start = ledger.tail(kind="session", session_id=s.id)[0]
    assert start.data["files"] == 0 and start.data["images_sent"] is False


async def test_images_dropped_clears_images_for_later_steps(ledger: Ledger, tmp_path: Path):
    first = completion(calls=[("c1", "shell", {"command": "echo hi"})])
    first.images_dropped = True
    provider = ScriptedProvider([first, completion("ok")])
    s = await make_loop(provider, ledger, tmp_path).run(Bot(name="B", role="r"), "look", images=["data:image/webp;base64,AA"])
    second = provider.calls[1]
    assert all(m.images == [] for m in second)
    assert provider.calls[0][-1].images == ["data:image/webp;base64,AA"]  # the first call's messages are untouched
    user = [m for m in second if m.role == "user"][-1]
    assert user.content.endswith("(The picture could not be shown to this model; use your tools on the file.)")
    ev = ledger.tail(kind="upload", session_id=s.id)
    assert ev and ev[0].data["event"] == "images_refused"

"""loop-supervisor: every CHECK_EVERY steps ask run_state, is_repeating,
completion over the rendered transcript. Halt pauses for confirm, nudge
injects a user message, errors fail open, protected task untouched."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.decider.interface import ChoiceAnswer, NoulAnswer, ScoreAnswer, SupervisorThresholds
from src.decider.supervisor import LoopSupervisor, SupervisorConfig
from src.harness.approvals import ApprovalStore
from src.harness.bot import Bot
from src.harness.loop import Session, SessionState, TaskLoop
from src.harness.pause import PauseController
from src.harness.tools import ToolRunner
from src.memory.ledger import Ledger
from src.providers.interface import ProviderUnavailable

from .conftest import MockScorer, ScriptedProvider, completion


def answers(looping: float, repeating: float, stuck: float = 0.0, done: float = 1.0) -> dict:
    prog = max(0.0, 1.0 - looping - stuck)
    return {
        "run_state": ChoiceAnswer(choice="looping" if looping >= 0.5 else "progressing", probabilities={"progressing": prog, "stuck": stuck, "looping": looping}),
        "is_repeating": NoulAnswer(repeating),
        "completion": ScoreAnswer(score=done, probabilities={"1": 0.5, "2": 0.3, "3": 0.2}),
    }


CONTINUE = answers(0.1, 0.1)
NUDGE = answers(0.6, 0.3)
HALT = answers(0.9, 0.9)


@pytest.fixture
def bot() -> Bot:
    return Bot(name="Worker", role="Do the thing", id="bot1")


def steps(n: int, final: str = "done") -> list:
    return [completion(calls=[(f"c{i}", "shell", {"command": f"echo {i}"})]) for i in range(1, n + 1)] + [completion(final)]


def make(provider, ledger, tmp_path, scorer, every=5, thresholds=None) -> tuple[TaskLoop, PauseController]:
    pause = PauseController(ledger, provider)
    sup = LoopSupervisor(scorer, ledger, pause, SupervisorConfig(check_every=every, thresholds=thresholds or SupervisorThresholds()))
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path, timeout=10), pause, max_steps=30, supervisor=sup)
    return loop, pause


async def wait_paused(pause: PauseController, sid: str) -> None:
    for _ in range(200):
        if pause.is_paused(sid):
            return
        await asyncio.sleep(0.01)
    pytest.fail("session never paused")


async def test_checks_every_n_steps_with_rendered_transcript(bot: Bot, ledger: Ledger, tmp_path: Path):
    scorer = MockScorer(script=[CONTINUE, CONTINUE])
    loop, _ = make(ScriptedProvider(steps(10)), ledger, tmp_path, scorer, every=5)
    s = await loop.run(bot, "count to ten")
    assert s.state == SessionState.done and s.steps == 11
    assert len(scorer.requests) == 2  # steps 5 and 10, not 11
    req = scorer.requests[0]
    assert req.purpose == "supervise" and set(req.questions) == {"run_state", "is_repeating", "completion"}
    rendered = req.state["transcript"]
    assert "[tool call shell c5]" in rendered and "echo 5" in rendered and "[tool result shell c5]" in rendered
    assert "echo 6" not in rendered
    entries = ledger.tail(kind="supervisor", session_id=s.id)
    assert len(entries) == 2 and entries[0].data["action"] == "continue" and entries[0].data["step"] == 5
    assert entries[0].data["looping"] == pytest.approx(0.1) and entries[0].data["cost"] == pytest.approx(0.000002)


async def test_halt_pauses_and_waits_for_confirm(bot: Bot, ledger: Ledger, tmp_path: Path):
    scorer = MockScorer(script=[HALT, CONTINUE])
    loop, pause = make(ScriptedProvider(steps(7)), ledger, tmp_path, scorer, every=5)
    session = Session(bot_id=bot.id, task="loop forever")
    task = asyncio.create_task(loop.run(bot, session.task, session))

    await wait_paused(pause, session.id)
    assert not task.done() and session.state == SessionState.paused
    entry = ledger.tail(kind="pause", session_id=session.id)[0]
    assert "supervisor" in entry.data["reason"] and "looping" in entry.data["reason"]
    assert ledger.tail(kind="supervisor", session_id=session.id)[0].data["action"] == "halt"

    # the user confirms by resuming; the loop carries on and finishes
    assert pause.resume(session.id, reason="user")
    s = await task
    assert s.state == SessionState.done and s.steps == 8 and s.halts == 1
    assert ledger.tail(kind="resume", session_id=session.id)[0].data["reason"] == "user"


async def test_halt_then_stop_while_paused_stops(bot: Bot, ledger: Ledger, tmp_path: Path):
    scorer = MockScorer(script=[HALT])
    loop, pause = make(ScriptedProvider(steps(7)), ledger, tmp_path, scorer, every=5)
    session = Session(bot_id=bot.id, task="loop forever")
    task = asyncio.create_task(loop.run(bot, session.task, session))
    await wait_paused(pause, session.id)
    # what the API's stop() does
    session.stop_requested = True
    pause.release_for_stop(session.id)
    s = await task
    assert s.state == SessionState.stopped


async def test_nudge_injects_user_message_without_confirm(bot: Bot, ledger: Ledger, tmp_path: Path):
    scorer = MockScorer(script=[NUDGE, CONTINUE])
    provider = ScriptedProvider(steps(10))
    loop, _ = make(provider, ledger, tmp_path, scorer, every=5)
    s = await loop.run(bot, "try again differently")
    assert s.state == SessionState.done and s.nudges == 1
    # the call for step 6 saw the nudge as the latest user turn
    sixth = provider.calls[5]
    assert sixth[-1].role == "user" and "looping" in sixth[-1].content.lower()
    assert any(m.role == "user" and "looping" in m.content.lower() for m in s.messages)
    assert ledger.tail(kind="supervisor", session_id=s.id)[0].data["action"] == "nudge"
    assert ledger.tail(kind="pause", session_id=s.id) == []


async def test_check_error_fails_open_and_logs(bot: Bot, ledger: Ledger, tmp_path: Path):
    scorer = MockScorer(script=[ProviderUnavailable("decider down"), CONTINUE])
    loop, _ = make(ScriptedProvider(steps(10)), ledger, tmp_path, scorer, every=5)
    s = await loop.run(bot, "keep going")
    assert s.state == SessionState.done and s.steps == 11
    entries = ledger.tail(kind="supervisor", session_id=s.id)
    assert entries[0].data["ok"] is False and "decider down" in entries[0].data["error"] and entries[0].data["action"] == "continue"
    assert entries[1].data["ok"] is True


async def test_thresholds_are_config(bot: Bot, ledger: Ledger, tmp_path: Path):
    # with a stricter nudge threshold the same answers do not nudge
    scorer = MockScorer(script=[NUDGE])
    loop, _ = make(ScriptedProvider(steps(5)), ledger, tmp_path, scorer, every=5, thresholds=SupervisorThresholds(nudge_looping=0.95, nudge_stuck=0.95))
    s = await loop.run(bot, "x")
    assert s.nudges == 0 and ledger.tail(kind="supervisor", session_id=s.id)[0].data["action"] == "continue"


async def test_supervisor_never_rewrites_protected_task(bot: Bot, ledger: Ledger, tmp_path: Path):
    scorer = MockScorer(script=[NUDGE, HALT, CONTINUE])
    loop, pause = make(ScriptedProvider(steps(15)), ledger, tmp_path, scorer, every=5)
    session = Session(bot_id=bot.id, task="the one true task")
    task = asyncio.create_task(loop.run(bot, session.task, session))
    await wait_paused(pause, session.id)
    pause.resume(session.id)
    s = await task
    assert s.state == SessionState.done and s.nudges == 1 and s.halts == 1
    assert s.protected == {"task": "the one true task", "constraints": []}
    assert s.messages[1].role == "user" and s.messages[1].content == "the one true task"


def test_config_reads_environment(monkeypatch):
    monkeypatch.setenv("ZEN_SUPERVISE_EVERY", "3")
    monkeypatch.setenv("ZEN_HALT_LOOPING", "0.7")
    monkeypatch.setenv("ZEN_NUDGE_STUCK", "0.3")
    c = SupervisorConfig()
    assert c.check_every == 3 and c.thresholds.halt_looping == 0.7 and c.thresholds.nudge_stuck == 0.3
    assert c.thresholds.halt_repeating == 0.8  # default kept


async def test_stop_during_a_check_stops_instead_of_hanging_paused(bot: Bot, ledger: Ledger, tmp_path: Path):
    thinking = asyncio.Event()

    class SlowHaltScorer(MockScorer):
        async def decide(self, request):
            thinking.set()
            await asyncio.sleep(0.05)
            return await super().decide(request)

    scorer = SlowHaltScorer(script=[HALT])
    loop, pause = make(ScriptedProvider(steps(7)), ledger, tmp_path, scorer, every=5)
    session = Session(bot_id=bot.id, task="loop forever")
    task = asyncio.create_task(loop.run(bot, session.task, session))
    await asyncio.wait_for(thinking.wait(), 2)
    # the API's stop(), fired while the decider is still thinking
    session.stop_requested = True
    pause.release_for_stop(session.id)
    s = await asyncio.wait_for(task, 2)
    assert s.state == SessionState.stopped and not pause.is_paused(session.id)

"""pause-fallback: provider drops, session pauses, resumes on user or on a
user-triggered probe that succeeds. Never on a timer. Local is a no-op."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.harness.approvals import ApprovalStore
from src.harness.bot import Bot
from src.harness.loop import SessionState, TaskLoop
from src.harness.pause import PauseController, local_available
from src.harness.tools import ToolRunner
from src.memory.ledger import Ledger
from src.providers.interface import ProviderAuthError, ProviderUnavailable

from .conftest import ScriptedProvider, completion


def make(provider, ledger, tmp_path):
    pause = PauseController(ledger, provider)
    loop = TaskLoop(provider, ledger, ApprovalStore(), ToolRunner(tmp_path), pause, max_steps=5)
    return loop, pause


async def wait_paused(pause: PauseController, session_id_holder: list, loop: TaskLoop):
    for _ in range(100):
        await asyncio.sleep(0.01)
        if session_id_holder and pause.is_paused(session_id_holder[0]):
            return
    raise AssertionError("never paused")


async def test_pause_then_user_resume(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([ProviderUnavailable("openrouter HTTP 503"), completion("back online")])
    loop, pause = make(provider, ledger, tmp_path)
    bot = Bot(name="b", role="r")
    from src.harness.loop import Session

    session = Session(bot_id=bot.id, task="go")
    task = asyncio.create_task(loop.run(bot, "go", session))

    for _ in range(100):
        await asyncio.sleep(0.01)
        if pause.is_paused(session.id):
            break
    assert session.state == SessionState.paused
    assert "ProviderUnavailable" in session.pause_reason
    assert not task.done()
    await asyncio.sleep(0.05)
    assert not task.done(), "must not resume on its own"

    assert pause.resume(session.id, reason="user")
    s = await task
    assert s.state == SessionState.done and s.result == "back online" and s.pause_reason is None
    kinds = [e.kind for e in ledger.tail(session_id=s.id)]
    assert kinds == ["session", "pause", "resume", "turn", "session"]
    assert ledger.tail(kind="resume")[0].data["reason"] == "user"


async def test_retry_resumes_only_when_provider_answers(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([ProviderUnavailable("down"), completion("ok")], probe=False)
    loop, pause = make(provider, ledger, tmp_path)
    bot = Bot(name="b", role="r")
    from src.harness.loop import Session

    session = Session(bot_id=bot.id, task="go")
    task = asyncio.create_task(loop.run(bot, "go", session))
    while not pause.is_paused(session.id):
        await asyncio.sleep(0.01)

    assert await pause.retry(session.id) is False
    assert pause.is_paused(session.id)

    provider.probe_result = True
    assert await pause.retry(session.id) is True
    s = await task
    assert s.state == SessionState.done
    assert ledger.tail(kind="resume")[0].data["reason"] == "provider"


async def test_auth_error_pauses_too(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([ProviderAuthError("rejected the key"), completion("ok")])
    loop, pause = make(provider, ledger, tmp_path)
    bot = Bot(name="b", role="r")
    from src.harness.loop import Session

    session = Session(bot_id=bot.id, task="go")
    task = asyncio.create_task(loop.run(bot, "go", session))
    while not pause.is_paused(session.id):
        await asyncio.sleep(0.01)
    assert "ProviderAuthError" in session.pause_reason
    pause.resume(session.id)
    assert (await task).state == SessionState.done


async def test_stop_while_paused(ledger: Ledger, tmp_path: Path):
    provider = ScriptedProvider([ProviderUnavailable("down")])
    loop, pause = make(provider, ledger, tmp_path)
    bot = Bot(name="b", role="r")
    from src.harness.loop import Session

    session = Session(bot_id=bot.id, task="go")
    task = asyncio.create_task(loop.run(bot, "go", session))
    while not pause.is_paused(session.id):
        await asyncio.sleep(0.01)
    session.stop_requested = True
    pause.release_for_stop(session.id)
    s = await task
    assert s.state == SessionState.stopped
    assert provider.script == [], "no further provider calls after stop"
    assert [e.kind for e in ledger.tail(session_id=s.id)] == ["session", "pause", "session"]


def test_resume_on_unpaused_session_is_false(ledger: Ledger):
    pause = PauseController(ledger, ScriptedProvider())
    assert pause.resume("nope") is False


def test_local_branch_is_a_documented_no_op():
    assert local_available() is False


async def test_cancelled_hold_clears_the_pause(ledger):
    import asyncio

    from src.harness.loop import Session
    from src.harness.pause import PauseController

    from .conftest import ScriptedProvider

    pause = PauseController(ledger, ScriptedProvider())
    session = Session(bot_id="b", task="t")
    task = asyncio.create_task(pause.hold_reason(session, "supervisor halt"))
    await asyncio.sleep(0.01)
    assert pause.is_paused(session.id)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert not pause.is_paused(session.id) and session.pause_reason is None

"""Hand a job to Claude Code: resolve the job folder, launch the CLI with a fixed argument list and the job on
stdin, check its first event, read its event stream into the ledger, and kill its whole process tree on timeout
or stop. The bot-facing tool and its approval are elsewhere."""

from __future__ import annotations

import asyncio
import codecs
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.memory.ledger import Kind

from .agent_clis import AGENTS, MODEL, Agents, run_env
from .allowed_folders import Folders, _norm, _within

CLAUDE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")
# Verified by hand: confined to the working folder, no command tool, no user MCP servers, plugins or hooks.
_CLAUDE_ARGS = ("-p", "--restricted", "--strict-mcp-config", "--tools", ",".join(CLAUDE_TOOLS),
                "--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose",
                "--no-session-persistence")
CHANGES_LISTED = 200
ERROR_TAIL = 2_000
_LINE_LIMIT = 32 * 1024 * 1024  # one event can carry a whole file the agent wrote
_EXIT_GRACE = 10.0              # how long a CLI that has ended gets to exit, and a killed one to be reaped
_FILE_TOOLS = ("Write", "Edit")


class DelegationError(Exception):
    """A run that cannot start: no program, a bad model name, or the run limits."""


class FolderRefused(ValueError):
    """The job folder breaks a rule; the message names it."""


def claude_argv(path: str, model: str = "") -> list[str]:
    if model and not MODEL.fullmatch(model):
        raise DelegationError("The model name in Settings may hold only letters, digits, dots, dashes and underscores.")
    return [path, *_CLAUDE_ARGS, *(("--model", model) if model else ())]


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(_norm(a))) == os.path.normcase(str(_norm(b)))


def check_init(event: dict, folder: Path, subscription: bool) -> str | None:
    """What is wrong with the CLI's first event, or None. A CLI update that widens what a run can do shows here
    and stops the run, rather than going unnoticed."""
    if event.get("type") != "system" or event.get("subtype") != "init":
        return "its first event was not the start event."
    tools = event.get("tools")
    if not isinstance(tools, list):
        return "its start event did not list its tools."
    extra = [str(t) for t in tools if t not in CLAUDE_TOOLS]
    if extra:
        return f"it started with tools beyond the five it may have: {', '.join(extra)}."
    servers = event.get("mcp_servers")
    if servers:
        names = [str(s.get("name")) for s in servers if isinstance(s, dict)] if isinstance(servers, list) else []
        return f"it started with MCP servers ({', '.join(names) or 'unnamed'}); a run may have none."
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd or not _same(Path(cwd), folder):
        return f"it started in another folder ({cwd}), not {folder}."
    if subscription and event.get("apiKeySource") != "none":
        return f"it used {event.get('apiKeySource')} instead of the subscription sign-in Settings showed."
    return None


# -- the folder ----------------------------------------------------------------------------------------------

def _holds(folder: Path, roots: list[Path]) -> bool:
    return any(r == folder or r.is_relative_to(folder) for r in (_norm(Path(x)) for x in roots))


def _refusal(folder: Path, folders: Folders) -> str | None:
    if _within(folder, folders.private):
        return f"{folder} is inside a folder the harness keeps private."
    if _holds(folder, folders.private):
        return f"{folder} holds a folder the harness keeps private."
    if _within(folder, folders.read_only):
        return f"{folder} is inside a Read-Only folder, and a run writes."
    if _holds(folder, folders.read_only):  # the agent's own write check knows nothing of Read-Only folders
        return f"{folder} holds a Read-Only folder."
    if not _within(folder, folders.full):
        return f"{folder} is outside the folders bots may write."
    return None


def resolve_folder(vault_root: Path, folders: Folders, raw: str | None, stamp: str) -> tuple[Path, bool]:
    """The job folder, resolved (links followed), and whether the harness made it. Empty `raw` makes
    `<vault>/delegated/<stamp>/`; otherwise `raw` is absolute or relative to the vault and must already exist.
    Raises FolderRefused naming the rule."""
    made = not (raw or "").strip()
    if made:
        if not stamp or Path(stamp).name != stamp or stamp in (".", ".."):
            raise ValueError(f"bad stamp {stamp!r}")
        target = Path(vault_root) / "delegated" / stamp
    else:
        target = Path(raw)
        if not target.is_absolute():
            target = Path(vault_root) / target
        if not target.exists():
            raise FolderRefused(f"{raw} does not exist. Name a folder that does, or leave the folder out.")
        if not target.is_dir():
            raise FolderRefused(f"{raw} is not a folder.")
    folder = _norm(target)
    why = _refusal(folder, folders)
    if why:
        raise FolderRefused(why)
    if made:  # checked before it exists, so a link in the way cannot place it somewhere refused
        folder.mkdir(parents=True, exist_ok=True)
    return folder, made


# -- the job and what it changed -----------------------------------------------------------------------------

PREAMBLE = ("You are doing a job handed over by another assistant. Work only in this folder: {folder}\n"
            "Read and write files there and nowhere else. Everything after the line below is the whole job; there "
            "is no one to ask. When you finish, say what you made or changed and where, by file name.\n"
            "--- the job ---\n")


def job_text(folder: Path, task: str) -> str:
    return PREAMBLE.format(folder=folder) + task  # the task is never formatted: braces in it stay as written


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """Every file under the folder: relative posix path to (size, mtime_ns). Links are not followed."""
    out: dict[str, tuple[int, int]] = {}
    for root, _dirs, files in os.walk(folder):
        base = Path(root)
        for name in files:
            p = base / name
            try:
                st = p.lstat()
            except OSError:
                continue
            out[p.relative_to(folder).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def changed(before: dict, after: dict) -> list[str]:
    """Files added, changed or removed, sorted."""
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def changes_text(names: list[str]) -> str:
    if not names:
        return "No files were added, changed or removed."
    lines = ["Files added, changed or removed:", *(f"- {n}" for n in names[:CHANGES_LISTED])]
    if len(names) > CHANGES_LISTED:
        lines.append(f"and {len(names) - CHANGES_LISTED} more")
    return "\n".join(lines)


# -- running -------------------------------------------------------------------------------------------------

@dataclass
class RunResult:
    ok: bool
    text: str
    error: str = ""
    usage: dict = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)


@dataclass
class _Live:
    agent: str
    folder: Path
    stop: asyncio.Event = field(default_factory=asyncio.Event)


class _Tail:
    """The last characters a run printed, stdout and stderr together, in the order they arrived."""

    def __init__(self) -> None:
        self.text = ""

    def add(self, s: str) -> None:
        self.text = (self.text + s)[-ERROR_TAIL:]


def _new_group() -> dict[str, Any]:
    """A process group of its own, so the whole tree can be killed and a Ctrl+C to the harness is not shared."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """The CLI and everything it started."""
    if os.name == "nt":
        if proc.returncode is None:  # taskkill finds the children through a live parent
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            try:
                k = await asyncio.create_subprocess_exec(str(taskkill), "/T", "/F", "/PID", str(proc.pid),
                                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                         stderr=subprocess.DEVNULL)
                await k.wait()
            except OSError:
                pass
    else:
        try:  # the group outlives its leader, so this also reaches children left behind by a CLI that exited
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _exited(proc: asyncio.subprocess.Process, within: float) -> bool:
    """Wait for the process and its pipes (an orphan holding a pipe would otherwise hang the run)."""
    try:
        await asyncio.wait_for(proc.wait(), within)
        return True
    except TimeoutError:
        return False


async def _feed(proc: asyncio.subprocess.Process, text: str) -> None:
    assert proc.stdin is not None
    try:
        proc.stdin.write(text.encode("utf-8"))
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass  # it exited before reading; its stream says why
    finally:
        proc.stdin.close()


async def _drain(stream: asyncio.StreamReader, tail: _Tail) -> None:
    decode = codecs.getincrementaldecoder("utf-8")("replace")
    while chunk := await stream.read(65536):
        tail.add(decode.decode(chunk))


def _relative(file_path: str, folder: Path) -> str:
    p = Path(file_path)
    p = _norm(p if p.is_absolute() else folder / p)
    try:
        return p.relative_to(folder).as_posix()
    except ValueError:
        return p.name


class Runner:
    """Runs an agent CLI on one job in one folder: at most one run per session and `max_runs` in the harness."""

    def __init__(self, agents: Agents, ledger: Any, timeout: float = 900.0, max_runs: int = 2) -> None:
        self.agents = agents
        self.ledger = ledger
        self.timeout = timeout
        self.max_runs = max_runs
        self.live: dict[str, _Live] = {}

    def command(self, agent: str, path: str, model: str) -> list[str]:
        if agent != "claude-code":
            raise DelegationError(f"{AGENTS[agent].program.name} runs are not built yet.")
        return claude_argv(path, model)

    def stop(self, session_id: str) -> bool:
        """End that session's run; its `run` returns "Stopped by the user." False when it has none."""
        live = self.live.get(session_id)
        if live is None:
            return False
        live.stop.set()
        return True

    async def run(self, agent: str, folder: Path, task: str, bot_id: str, session_id: str) -> RunResult:
        if agent not in AGENTS:
            raise DelegationError(f"There is no agent called {agent}.")
        name = AGENTS[agent].program.name
        st = self.agents.statuses.get(agent) or {}
        if not st.get("path"):
            raise DelegationError(f"{name} was not found. Check Settings > Harness Tools.")
        if session_id in self.live:
            raise DelegationError("This conversation already has a run going. Wait for it to end.")
        if len(self.live) >= self.max_runs:
            raise DelegationError(f"{self.max_runs} runs are going already. Try again when one ends.")
        argv = self.command(agent, st["path"], self.agents.settings(agent).model)
        live = self.live[session_id] = _Live(agent, folder)  # claimed before the first await: no two calls both pass
        try:
            return await self._run(live, argv, name, _norm(Path(folder)), task, bot_id, session_id,
                                   subscription=st.get("auth") == "subscription")
        finally:
            self.live.pop(session_id, None)

    async def _run(self, live: _Live, argv: list[str], name: str, folder: Path, task: str, bot_id: str,
                   session_id: str, subscription: bool) -> RunResult:
        before = await asyncio.to_thread(snapshot, folder)
        self.ledger.append(Kind.delegate, bot_id, session_id, event="start", agent=live.agent, folder=str(folder))
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=folder, env=run_env(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=_LINE_LIMIT, **_new_group())
        except OSError as e:
            result = RunResult(False, "", error=f"{name} could not start: {e}")
        else:
            result = await self._watch(proc, live, name, folder, task, bot_id, session_id, subscription)
        result.changed = changed(before, await asyncio.to_thread(snapshot, folder))  # a failed run may have written too
        self.ledger.append(Kind.delegate, bot_id, session_id, event="end", ok=result.ok,
                           seconds=round(time.monotonic() - t0, 1), changed=len(result.changed), usage=result.usage)
        return result

    async def _watch(self, proc: asyncio.subprocess.Process, live: _Live, name: str, folder: Path, task: str,
                     bot_id: str, session_id: str, subscription: bool) -> RunResult:
        assert proc.stdout is not None and proc.stderr is not None
        tail = _Tail()
        tasks = [asyncio.create_task(_feed(proc, job_text(folder, task))), asyncio.create_task(_drain(proc.stderr, tail))]
        reader = asyncio.create_task(self._read(proc.stdout, tail, name, folder, bot_id, session_id, subscription))
        stopper = asyncio.create_task(live.stop.wait())
        tasks += [reader, stopper]
        how, result = "", RunResult(False, "")
        try:
            done, _ = await asyncio.wait({reader, stopper}, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                how, result = reader.result()
            elif stopper in done:
                how, result = "stopped", RunResult(False, "", error="Stopped by the user.")
            else:
                how, result = "timeout", RunResult(False, "", error=f"Timed out after {self.timeout:g} s.")
        finally:
            reader.cancel()
            stopper.cancel()
            if how == "result":  # nothing more is expected, but read what comes so a full pipe cannot block its exit
                tasks.append(asyncio.create_task(_drain(proc.stdout, tail)))
            # a CLI that ended on its own gets a moment to exit; anything else (refused start, timeout, stop,
            # cancellation) is killed with everything it started
            if not (how in ("result", "eof") and await _exited(proc, _EXIT_GRACE)):
                await _kill_tree(proc)
                await _exited(proc, _EXIT_GRACE)
            _, pending = await asyncio.wait(tasks, timeout=_EXIT_GRACE)
            for t in pending:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if how == "eof":  # no result: what it printed last, stderr included, says why
            result = RunResult(False, "", error=tail.text or f"{name} ended without a result.")
        return result

    async def _read(self, stdout: asyncio.StreamReader, tail: _Tail, name: str, folder: Path, bot_id: str,
                    session_id: str, subscription: bool) -> tuple[str, RunResult]:
        """Read events to the result: ("result", ...), ("refused", ...) for a bad first event, or ("eof", ...)."""
        first = True
        while True:
            try:
                raw = await stdout.readline()
            except ValueError:  # a line over the limit: skipped like any line that is not an event
                continue
            if not raw:
                return "eof", RunResult(False, "")
            line = raw.decode("utf-8", "replace")
            tail.add(line)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if first:
                first = False
                problem = check_init(event, folder, subscription)
                if problem:
                    return "refused", RunResult(False, "", error=f"{name} was stopped as it started: {problem}")
                continue
            kind = event.get("type")
            if kind == "assistant":
                self._files(event, folder, bot_id, session_id)
            elif kind == "result":
                usage = event.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                text = event.get("result")
                text = text if isinstance(text, str) else ""
                if event.get("is_error"):
                    return "result", RunResult(False, "", error=text or f"{name} reported an error.", usage=usage)
                return "result", RunResult(True, text, usage=usage)

    def _files(self, event: dict, folder: Path, bot_id: str, session_id: str) -> None:
        """Each file the agent writes or edits goes to the ledger, so the activity feed shows the run moving."""
        msg = event.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        for block in content if isinstance(content, list) else []:
            if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in _FILE_TOOLS):
                continue
            args = block.get("input")
            fp = args.get("file_path") if isinstance(args, dict) else None
            if isinstance(fp, str) and fp:
                self.ledger.append(Kind.delegate, bot_id, session_id, event="file", tool=block["name"],
                                   path=_relative(fp, folder))

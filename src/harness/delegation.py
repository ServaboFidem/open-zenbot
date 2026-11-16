"""Hand a job to Claude Code: resolve the job folder, launch the CLI with a fixed argument list and the job on
stdin, check its first event, read its event stream into the ledger, and kill its whole process tree on timeout
or stop. The bot-facing `delegate` tool and when it asks the user (`Delegator`) are at the end."""

from __future__ import annotations

import asyncio
import codecs
import functools
import json
import os
import re
import signal
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.memory.ledger import Kind

from .agent_clis import AGENTS, MODEL, Agents, run_env
from .allowed_folders import Folders, _norm, _within
from .tools import current_call
from .write_guard import named_protected

CLAUDE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")
# Verified by hand: confined to the working folder, no command tool, no user MCP servers, plugins or hooks.
_CLAUDE_ARGS = ("-p", "--restricted", "--strict-mcp-config", "--tools", ",".join(CLAUDE_TOOLS),
                "--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose",
                "--no-session-persistence")
CHANGES_LISTED = 200
ERROR_TAIL = 2_000
_LINE_LIMIT = 32 * 1024 * 1024  # one event can carry a whole file the agent wrote
_EXIT_GRACE = 10.0              # how long a CLI that has ended gets to exit, and a killed one to be reaped
_DRAIN_GRACE = 2.0              # after the CLI exits, how long its last output gets to arrive
SNAPSHOT_LIMIT = 50_000         # entries walked before a folder counts as too large to compare in full
_FILE_TOOLS = ("Write", "Edit")
_LOGGED_PATH = 500              # a path the agent names is cut to this in the ledger


class DelegationError(Exception):
    """A run that cannot start (no program, a bad model name, the run limits), or one that failed."""


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
    # resolved again now, not compared with itself: a folder swapped for a link since it was checked fails here
    now = _norm(folder)
    if os.path.normcase(str(now)) != os.path.normcase(str(folder)):
        return f"its folder {folder} now leads to {now}."
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd or not _same(Path(cwd), folder):
        return f"it started in another folder ({cwd}), not {folder}."
    if "permissionMode" in event and event["permissionMode"] != "acceptEdits":
        return f"it started in permission mode {event['permissionMode']}, not acceptEdits."
    if (why := _plugins(event.get("plugins"))) is not None:
        return why
    if subscription and event.get("apiKeySource") != "none":
        return f"it used {event.get('apiKeySource')} instead of the subscription sign-in Settings showed."
    return None


def _plugins(plugins: Any) -> str | None:
    """Only Claude Code's own plugins may load (2.1.325 lists them as `"path": "builtin"`, source `<name>@builtin`):
    one from the user's setup could add tools or hooks the other checks do not see."""
    if not plugins:
        return None
    if not isinstance(plugins, list):
        return "its start event listed its plugins in a form the harness does not know."
    other = [p for p in plugins if not (isinstance(p, dict) and p.get("path") == "builtin"
                                        and str(p.get("source", "")).endswith("@builtin"))]
    if other:
        names = [str(p.get("name") or p.get("source") or "unnamed") if isinstance(p, dict) else str(p) for p in other]
        return f"it started with plugins that are not built in: {', '.join(names)}."
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


class Snapshot(dict):
    """Relative posix path to (size, mtime_ns); `complete` is False when the walk stopped at the limit."""

    complete = True


_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: a link, a junction, or another kind of redirect on Windows


def _redirect(p: Path) -> bool:
    """A symlink, a junction or any other reparse point: something a walk must not follow. Unreadable counts."""
    try:
        st = os.lstat(p)
    except OSError:
        return True
    return stat.S_ISLNK(st.st_mode) or p.is_junction() or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def snapshot(folder: Path, limit: int = SNAPSHOT_LIMIT) -> Snapshot:
    """Every file under the folder, up to `limit` entries (files and folders) walked. Links are not followed."""
    out, seen = Snapshot(), 0
    for root, dirs, files in os.walk(folder):
        base = Path(root)
        # os.walk takes a junction for an ordinary folder: without this a junction planted in the job folder would
        # list another folder's file names (or loop until the limit)
        dirs[:] = [d for d in dirs if not _redirect(base / d)]
        seen += len(dirs)
        for name in files:
            seen += 1
            if seen > limit:
                out.complete = False
                return out
            p = base / name
            try:
                st = p.lstat()
            except OSError:
                continue
            out[p.relative_to(folder).as_posix()] = (st.st_size, st.st_mtime_ns)
        if seen > limit:
            out.complete = False
            return out
    return out


def leads_out(folder: Path, limit: int = SNAPSHOT_LIMIT) -> str | None:
    """Why a folder the bot named must not be handed over, or None: the first link or junction in it that resolves
    outside it, or the first file with more than one name (a hard link: its other name may be outside, and Claude
    Code reads through one). Links are not followed. Up to `limit` entries; a larger folder is refused, since what
    was not looked at could hold a way out."""
    seen, stack = 0, [folder]
    while stack:
        here = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue  # unreadable: the agent cannot list it either
        for entry in entries:
            seen += 1
            if seen > limit:
                return f"it holds more than {limit:,} entries, too many to check for links that lead out of it."
            p = Path(entry.path)
            name = p.relative_to(folder).as_posix()
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if entry.is_symlink() or entry.is_junction() or getattr(st, "st_file_attributes", 0) & _REPARSE:
                try:
                    target = p.resolve()
                except (OSError, RuntimeError):
                    return f"{name} is a link the harness cannot follow."
                if not (target == folder or target.is_relative_to(folder)):
                    return f"{name} is a link or junction that leads outside the folder."
                continue
            if stat.S_ISDIR(st.st_mode):
                stack.append(p)
            elif stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                return f"{name} is a hard link: the same file has another name, which may be outside the folder."
    return None


def changed(before: dict, after: dict) -> list[str]:
    """Files added, changed or removed, sorted."""
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def changes_text(names: list[str], complete: bool = True) -> str:
    lines = ["Files added, changed or removed:", *(f"- {n}" for n in names[:CHANGES_LISTED])] if names else \
        ["No files were added, changed or removed."]
    if len(names) > CHANGES_LISTED:
        lines.append(f"and {len(names) - CHANGES_LISTED} more")
    if not complete:
        lines.append(f"The folder was too large to compare in full (over {SNAPSHOT_LIMIT:,} entries), so this list "
                     "may be incomplete or wrong.")
    return "\n".join(lines)


# -- killing the whole tree ----------------------------------------------------------------------------------

if os.name == "nt":
    import ctypes
    from ctypes import wintypes as _w

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", _w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", _w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", _w.DWORD), ("SchedulingClass", _w.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes",
                                                     "OtherBytes")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [("dwSize", _w.DWORD), ("cntUsage", _w.DWORD), ("th32ThreadID", _w.DWORD),
                    ("th32OwnerProcessID", _w.DWORD), ("tpBasePri", _w.LONG), ("tpDeltaPri", _w.LONG),
                    ("dwFlags", _w.DWORD)]

    @functools.cache
    def _kernel32() -> Any:
        k = ctypes.WinDLL("kernel32", use_last_error=True)  # its own instance: argtypes here touch no one else's
        for fn, res, args in (
                ("CreateJobObjectW", _w.HANDLE, [ctypes.c_void_p, _w.LPCWSTR]),
                ("SetInformationJobObject", _w.BOOL, [_w.HANDLE, ctypes.c_int, ctypes.c_void_p, _w.DWORD]),
                ("AssignProcessToJobObject", _w.BOOL, [_w.HANDLE, _w.HANDLE]),
                ("TerminateJobObject", _w.BOOL, [_w.HANDLE, _w.UINT]),
                ("OpenProcess", _w.HANDLE, [_w.DWORD, _w.BOOL, _w.DWORD]),
                ("OpenThread", _w.HANDLE, [_w.DWORD, _w.BOOL, _w.DWORD]),
                ("ResumeThread", _w.DWORD, [_w.HANDLE]),
                ("CreateToolhelp32Snapshot", _w.HANDLE, [_w.DWORD, _w.DWORD]),
                ("Thread32First", _w.BOOL, [_w.HANDLE, ctypes.c_void_p]),
                ("Thread32Next", _w.BOOL, [_w.HANDLE, ctypes.c_void_p]),
                ("CloseHandle", _w.BOOL, [_w.HANDLE])):
            f = getattr(k, fn)
            f.restype, f.argtypes = res, args
        return k


class _Job:
    """A Windows Job Object set to kill on close: every process the CLI starts joins it (none may break away),
    so ending the job ends the whole tree, including children left behind by a CLI that exited, and closing
    the harness ends it too."""

    def __init__(self, handle: int) -> None:
        self.handle: int | None = handle

    @classmethod
    def create(cls) -> _Job | None:
        k = _kernel32()
        h = k.CreateJobObjectW(None, None)
        if not h:
            return None
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):  # extended limits
            k.CloseHandle(h)
            return None
        return cls(h)

    def adopt(self, pid: int) -> bool:
        k = _kernel32()
        p = k.OpenProcess(0x0100 | 0x0001, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not p:
            return False
        try:
            return bool(k.AssignProcessToJobObject(self.handle, p))
        finally:
            k.CloseHandle(p)

    def close(self) -> None:
        """Kill everything in the job, then let go of it."""
        if self.handle:
            k = _kernel32()
            k.TerminateJobObject(self.handle, 1)
            k.CloseHandle(self.handle)
            self.handle = None


def _resume(pid: int) -> bool:
    """Resume a process started suspended (Popen does not keep its thread handle)."""
    k = _kernel32()
    snap = k.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
    if not snap or snap == ctypes.c_void_p(-1).value:
        return False
    resumed = 0
    try:
        te = _ThreadEntry()
        te.dwSize = ctypes.sizeof(te)
        more = k.Thread32First(snap, ctypes.byref(te))
        while more:
            if te.th32OwnerProcessID == pid:
                t = k.OpenThread(0x0002, False, te.th32ThreadID)  # THREAD_SUSPEND_RESUME
                if t:
                    if k.ResumeThread(t) != 0xFFFFFFFF:
                        resumed += 1
                    k.CloseHandle(t)
            more = k.Thread32Next(snap, ctypes.byref(te))
    finally:
        k.CloseHandle(snap)
    return resumed > 0


# -- npm shims ----------------------------------------------------------------------------------------------

# the script an npm .cmd shim runs: `"%dp0%\node_modules\...\cli.js"` (or `%~dp0`, in older npm versions)
_SHIM_SCRIPT = re.compile(r'"%~?dp0%?\\(node_modules\\[^"%]+?\.[cm]?js)"', re.IGNORECASE)
SHIM_REFUSED = ("{name} is installed as a command script ({path}), which would run through the command prompt in the "
                "job folder, and the harness could not find the program and script it starts. Install the native "
                "{name}, or set the path to it in Settings > Harness Tools > Coding agents.")


def _node_on_path() -> Path | None:
    """node.exe from the harness's own PATH: absolute folders only, never the current one, never a script."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip().strip('"')
        if d and Path(d).is_absolute() and (Path(d) / "node.exe").is_file():
            return Path(d) / "node.exe"
    return None


def _outside(p: Path, folder: Path) -> bool:
    p, folder = _norm(p), _norm(folder)
    return not (p == folder or p.is_relative_to(folder))


def direct(argv: list[str], folder: Path, name: str = "Claude Code") -> list[str]:
    """The command with a Windows .cmd or .bat program (an npm shim) replaced by what it starts: node and its script.
    A shim runs through cmd.exe, and its bare `node` is looked up in the current folder, the job folder the bot may
    have filled, before PATH. Raises DelegationError when it cannot be resolved that way."""
    shim = Path(argv[0])
    if os.name != "nt" or shim.suffix.lower() not in (".cmd", ".bat"):
        return argv
    refused = DelegationError(SHIM_REFUSED.format(name=name, path=shim))
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")[:65_536]
    except OSError:
        raise refused from None
    m = _SHIM_SCRIPT.search(text)
    beside = shim.parent / "node.exe"  # the shim's own first choice
    node = beside if beside.is_file() else _node_on_path()
    if m is None or node is None:
        raise refused
    script = _norm(shim.parent / m.group(1))
    node = _norm(node)
    if not (node.is_absolute() and node.is_file() and node.suffix.lower() == ".exe" and _outside(node, folder)
            and script.is_file() and _outside(script, folder)):
        raise refused
    return [str(node), str(script), *argv[1:]]


async def _launch(argv: list[str], folder: Path) -> tuple[asyncio.subprocess.Process, _Job | None]:
    """Start the CLI in a process group of its own. On Windows it starts suspended inside a kill-on-close job, so
    nothing it starts can slip out before it is in the job; without a job, taskkill is the fallback."""
    env = run_env()
    if os.name == "nt":  # a bare program name the CLI starts is never looked up in the job folder
        env["NoDefaultCurrentDirectoryInExePath"] = "1"
    job = _Job.create() if os.name == "nt" else None
    if os.name == "nt":
        group: dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
                                 | (0x00000004 if job else 0)}  # CREATE_SUSPENDED
    else:
        group = {"start_new_session": True}
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=folder, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=_LINE_LIMIT, **group)
    except BaseException:
        if job:
            job.close()
        raise
    if job:
        if not job.adopt(proc.pid):
            job.close()
            job = None
        if not _resume(proc.pid):  # suspended for good would hang the run until the timeout
            if job:
                job.close()
            else:
                proc.kill()
            await _exited(proc, _EXIT_GRACE)
            raise OSError("it could not be resumed after starting")
    return proc, job


async def _kill_tree(proc: asyncio.subprocess.Process, job: _Job | None) -> None:
    """End the CLI and everything it started, whether or not the CLI itself is still running: the job on Windows
    (taskkill /T, which needs a live parent to find the children, when there is no job), the process group on
    POSIX (the group outlives its leader, so this reaches children left behind too)."""
    if job is not None:
        job.close()
    elif os.name == "nt" and proc.returncode is None:
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            k = await asyncio.create_subprocess_exec(str(taskkill), "/T", "/F", "/PID", str(proc.pid),
                                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                     stderr=subprocess.DEVNULL)
            await k.wait()
        except OSError:
            pass
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _gone(proc: asyncio.subprocess.Process) -> None:
    """Until the process itself exits. Not `wait()`: that also waits for its pipes, which a child it left
    behind may hold open."""
    while proc.returncode is None:
        await asyncio.sleep(0.1)


async def _exited(proc: asyncio.subprocess.Process, within: float) -> bool:
    """Wait for the process and its pipes, bounded."""
    try:
        await asyncio.wait_for(proc.wait(), within)
        return True
    except TimeoutError:
        return False


# -- running -------------------------------------------------------------------------------------------------

@dataclass
class RunResult:
    ok: bool
    text: str
    error: str = ""
    usage: dict = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)
    complete: bool = True  # False when the folder was too large to compare in full


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


def _where(file_path: str, folder: Path) -> tuple[str, bool]:
    """The path relative to the folder, or the full path and True when it is outside (or cannot be resolved)."""
    if "\0" in file_path:  # names no file; resolve() raises on it on POSIX but not on Windows
        return file_path, True
    if file_path == "~" or file_path.startswith(("~/", "~\\")):  # the agent may read it as the home folder
        return file_path, True
    try:
        p = Path(file_path)
        p = (p if p.is_absolute() else folder / p).resolve()
    except (OSError, ValueError):
        return file_path, True
    try:
        return p.relative_to(folder).as_posix(), False
    except ValueError:
        return str(p), True


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

    async def stop_all(self, within: float = 15.0) -> None:
        """Stop every run (the harness is shutting down) and wait, bounded, for them to end and be logged."""
        for live in list(self.live.values()):
            live.stop.set()
        end = time.monotonic() + within
        while self.live and time.monotonic() < end:
            await asyncio.sleep(0.05)

    async def run(self, agent: str, folder: Path, task: str, bot_id: str, session_id: str,
                  named: bool = False) -> RunResult:
        """`named`: the bot named the folder (so it may have filled it), and it is checked for links just before
        the launch. A folder the harness made starts empty."""
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
        argv = direct(self.command(agent, st["path"], self.agents.settings(agent).model), folder, name)
        live = self.live[session_id] = _Live(agent, folder)  # claimed before the first await: no two calls both pass
        try:
            return await self._run(live, argv, name, _norm(Path(folder)), task, bot_id, session_id,
                                   subscription=st.get("auth") == "subscription", named=named)
        finally:
            self.live.pop(session_id, None)

    async def _run(self, live: _Live, argv: list[str], name: str, folder: Path, task: str, bot_id: str,
                   session_id: str, subscription: bool, named: bool) -> RunResult:
        before = await asyncio.to_thread(snapshot, folder)
        self.ledger.append(Kind.delegate, bot_id, session_id, event="start", agent=live.agent, folder=str(folder))
        t0 = time.monotonic()
        result = RunResult(False, "", error="The run was cancelled.")
        stopped = RunResult(False, "", error="Stopped by the user.")
        try:
            # read last, just before the launch, so a link put there while the folder was read is caught too
            why = await asyncio.to_thread(leads_out, folder) if named and not live.stop.is_set() else None
            if live.stop.is_set():  # Stop came while the folder was read: nothing is launched
                result = stopped
            elif why:
                result = RunResult(False, "", error=f"The folder cannot be handed over: {why}")
            else:
                try:
                    proc, job = await _launch(argv, folder)
                except OSError as e:
                    result = RunResult(False, "", error=f"{name} could not start: {e}")
                else:
                    if live.stop.is_set():  # Stop came while it was starting: killed before it is given the job
                        if proc.stdin is not None:
                            proc.stdin.close()
                        await _kill_tree(proc, job)
                        await _exited(proc, _EXIT_GRACE)
                        result = stopped
                    else:
                        result = await self._watch(proc, job, live, name, folder, task, bot_id, session_id,
                                                   subscription)
        finally:  # the end is logged however the run ends, a cancelled one included
            try:
                after = await asyncio.to_thread(snapshot, folder)  # a failed run may have written too
                result.changed = changed(before, after)
                result.complete = before.complete and after.complete
            finally:
                self.ledger.append(Kind.delegate, bot_id, session_id, event="end", ok=result.ok,
                                   seconds=round(time.monotonic() - t0, 1), changed=len(result.changed),
                                   usage=result.usage)
        return result

    async def _watch(self, proc: asyncio.subprocess.Process, job: _Job | None, live: _Live, name: str, folder: Path,
                     task: str, bot_id: str, session_id: str, subscription: bool) -> RunResult:
        assert proc.stdout is not None and proc.stderr is not None
        tail = _Tail()
        tasks = [asyncio.create_task(_feed(proc, job_text(folder, task))), asyncio.create_task(_drain(proc.stderr, tail))]
        reader = asyncio.create_task(self._read(proc.stdout, tail, name, folder, bot_id, session_id, subscription))
        stopper = asyncio.create_task(live.stop.wait())
        exited = asyncio.create_task(_gone(proc))
        tasks += [reader, stopper, exited]
        how, result = "", RunResult(False, "")
        try:
            done, _ = await asyncio.wait({reader, stopper, exited}, timeout=self.timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if exited in done and reader not in done and stopper not in done:
                # it exited: read what is left in the pipe, but not past a short grace, since a child it left
                # behind may hold the pipe open
                done |= (await asyncio.wait({reader}, timeout=_DRAIN_GRACE))[0]
            if reader in done:
                try:
                    how, result = reader.result()
                except Exception as e:  # a reader fault fails the run; it must not leave the tree running
                    how, result = "fault", RunResult(False, "", error=f"Reading {name}'s output failed: {e}")
            elif stopper in done:
                how, result = "stopped", RunResult(False, "", error="Stopped by the user.")
            elif exited in done:
                how = "eof"
            else:
                how, result = "timeout", RunResult(False, "", error=f"Timed out after {self.timeout:g} s.")
        finally:
            for t in (reader, stopper, exited):
                t.cancel()
            if how == "result":  # nothing more is expected, but read what comes so a full pipe cannot block its exit
                tasks.append(asyncio.create_task(_drain(proc.stdout, tail)))
            if how in ("result", "eof"):  # it ended on its own: a moment to exit before the rest is killed
                try:
                    await asyncio.wait_for(_gone(proc), _EXIT_GRACE)
                except TimeoutError:
                    pass
            await _kill_tree(proc, job)  # always: whatever it left behind goes with it
            await _exited(proc, _EXIT_GRACE)
            _, pending = await asyncio.wait(tasks, timeout=_EXIT_GRACE)
            for t in pending:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if how == "eof":  # no result: what it printed last, stderr included, says why
            result = RunResult(False, "", error=tail.text or f"{name} ended without a result.")
        elif how == "refused_said":  # it said something other than its start event; show that
            said = tail.text.strip()
            result.error += f" It said: {said}" if said else ""
        return result

    async def _read(self, stdout: asyncio.StreamReader, tail: _Tail, name: str, folder: Path, bot_id: str,
                    session_id: str, subscription: bool) -> tuple[str, RunResult]:
        """Read events to the result: ("result", ...); ("refused", ...) or ("refused_said", ...) for a bad first
        event (the latter when it was not a start event and the output tail should follow); or ("eof", ...)."""
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
                problem = await asyncio.to_thread(check_init, event, folder, subscription)  # it resolves paths
                if problem:
                    error = f"{name} was stopped as it started: {problem}"
                    if event.get("type") == "system" and event.get("subtype") == "init":
                        return "refused", RunResult(False, "", error=error)
                    said = event.get("result")
                    if event.get("type") == "result" and isinstance(said, str) and said.strip():
                        return "refused", RunResult(False, "", error=f"{error} It said: {said.strip()}")
                    return "refused_said", RunResult(False, "", error=error)
                continue
            kind = event.get("type")
            if kind == "assistant":
                if out := self._files(event, folder, bot_id, session_id):
                    return "outside", RunResult(False, "", error=f"{name} reached for {out[:_LOGGED_PATH]}, outside its "
                                                                 "folder, so the harness ended the run.")
            elif kind == "result":
                usage = event.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                text = event.get("result")
                text = text if isinstance(text, str) else ""
                if event.get("is_error"):
                    return "result", RunResult(False, "", error=text or f"{name} reported an error.", usage=usage)
                return "result", RunResult(True, text, usage=usage)

    def _files(self, event: dict, folder: Path, bot_id: str, session_id: str) -> str | None:
        """Each file the agent writes or edits goes to the ledger, so the activity feed shows the run moving. Returns
        the first path any tool names (`file_path` or `path`) that is outside the folder, logged too, or None."""
        msg = event.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        for block in content if isinstance(content, list) else []:
            if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                continue
            args = block.get("input")
            tool = str(block.get("name"))[:40]
            for key in ("file_path", "path"):
                fp = args.get(key) if isinstance(args, dict) else None
                if not (isinstance(fp, str) and fp):
                    continue
                path, outside = _where(fp, folder)
                if outside or (key == "file_path" and tool in _FILE_TOOLS):
                    self.ledger.append(Kind.delegate, bot_id, session_id, event="file", tool=tool,
                                       path=path[:_LOGGED_PATH], **({"outside": True} if outside else {}))
                if outside:
                    return path
        return None


# -- the tool ------------------------------------------------------------------------------------------------

TOOL = "delegate"
MAX_TASK = 20_000
REJECTED = "The user rejected this hand-off. Do not retry it. Do the work yourself, or say what you would have handed off."
CODEX_READS = "Codex can read any file your account can, not only this folder."


class Delegator:
    """The `delegate` tool: who may be asked, when the user must approve, and the run itself. `folders` is read on
    every call (Settings may change); `in_routine(session_id)` is true for a session with nobody to approve."""

    def __init__(self, vault_root: Path, agents: Agents, folders: Callable[[], Folders], runner: Runner,
                 in_routine: Callable[[str], bool] = lambda session_id: False) -> None:
        self.vault_root = Path(vault_root)
        self.agents = agents
        self.folders = folders
        self.runner = runner
        self.in_routine = in_routine

    def needs_approval(self, arguments: dict, forced: bool = False, session_id: str = "") -> tuple[str, str, str] | None:
        """(rule, title, detail) when this call must wait for the user, else None. Codex always (it can read
        outside its folder); Claude Code when Settings asks, and always for a folder the bot named, so only a run
        in a fresh folder the harness makes can go unasked. An agent that is not on asks nothing: the tool refuses
        it. `forced` (a skill rule already asks) gives the title and detail whatever the agent and switches. In a
        routine nothing is asked, not even when forced: nobody is there to answer, and the tool refuses it."""
        if session_id and self.in_routine(session_id):
            return None
        agent = arguments.get("agent")
        known = isinstance(agent, str) and agent in AGENTS
        raw = "" if arguments.get("folder") is None else str(arguments["folder"]).strip()
        settings = self.agents.settings(agent) if known else None
        if not forced:
            if not known or agent not in self.agents.on():
                return None
            if agent == "claude-code" and not settings.ask and not raw:
                return None
        task = str(arguments.get("task") or "")
        name = AGENTS[agent].program.name if known else " ".join(str(agent).split())[:40]  # bot-written: one short line
        model = (settings.model if settings else "") or "the agent's default"
        lines = [f"Hand this job to {name}?", f"Folder: {self._shown(raw)}", f"Model: {model}"]
        if agent == "codex":
            if named := named_protected(task, self.folders().private):
                lines.insert(0, f"The task names {named}, a folder the harness keeps private "
                                "(its settings and keys, ledger or music data).")
            lines.append(CODEX_READS)
        lines += ["", "The task:", task]
        title = f"{TOOL}: {name}: {' '.join(task.split())[:80]}"
        return f"delegate:{agent}" if known else TOOL, title, "\n".join(lines)

    def _relative(self, full: Path) -> str | None:
        """The resolved folder relative to the vault, with forward slashes, when it is strictly inside it."""
        vault = _norm(self.vault_root)
        return full.relative_to(vault).as_posix() if full != vault and full.is_relative_to(vault) else None

    def _shown(self, raw: str) -> str:
        """The folder as the user should see it: relative to the vault when it lands inside it, else in full."""
        if not raw:
            return "a new folder under delegated/"
        p = Path(raw)
        full = _norm(p if p.is_absolute() else self.vault_root / p)
        if full == _norm(self.vault_root):
            return "the whole vault"
        return self._relative(full) or str(full)

    def _refusal(self, agent: str) -> str | None:
        """Why this agent cannot take a job now, or None when it is on."""
        if agent in self.agents.on():
            return None
        if not isinstance(agent, str) or agent not in AGENTS:
            return f"There is no agent called {agent}."
        name = AGENTS[agent].program.name
        if not self.agents.settings(agent).enabled:
            return f"{name} is switched off in Settings."
        return ((self.agents.statuses.get(agent) or {}).get("reason")
                or f"{name} cannot run now. Check Settings > Harness Tools > Coding agents.")

    async def delegate(self, agent: str, task: str, folder: str | None = None) -> str:
        bot_id, session_id = current_call.get()
        if self.in_routine(session_id):
            raise DelegationError("delegate works only in a conversation or a room turn.")
        if why := self._refusal(agent):
            raise DelegationError(why)
        if not task.strip():
            raise DelegationError("The task is empty. Write the whole job into it.")
        if len(task) > MAX_TASK:
            raise DelegationError(f"The task is {len(task):,} characters; the most is {MAX_TASK:,}. Shorten it.")
        stamp = f"{date.today().isoformat()}-{uuid4().hex[:6]}"
        try:
            path, made = resolve_folder(self.vault_root, self.folders(), (folder or "").strip(), stamp)
        except FolderRefused as e:
            raise DelegationError(str(e)) from None
        # the runner adds the preamble, and checks a folder the bot named for links just before the launch
        result = await self.runner.run(agent, path, task, bot_id, session_id, named=not made)
        # the folder first (the changes are listed relative to it, and a harness-made one has a fresh name), then the
        # changes, so a long answer cannot push either past the tool output cap; a failed run says them too
        where = self._relative(_norm(path)) or str(path)
        done = f"Folder: {where}\n{changes_text(result.changed, result.complete)}"
        if not result.ok:
            raise DelegationError(f"{result.error}\n\n{done}")  # the loop's tool_call entry records it
        return f"{done}\n\n{result.text}"

    def register(self, tools: Any) -> bool:
        """Offer the tool while an agent is on, naming those agents; take it away otherwise. Safe to call again."""
        on = self.agents.on()
        if not on:
            tools.unregister(TOOL)
            return False
        names = " or ".join(AGENTS[k].program.name for k in on)
        description = (
            f"Hand a job to another AI agent ({names}) and get back its answer and the list of files it added, "
            "changed or removed. It sees only `task`, never this conversation, so write the job to stand on its own: "
            "what to make or change, from which files, and what to say when done. It works in one folder: leave "
            "`folder` out for a new one under delegated/ in the vault, or name a folder that exists (relative to the "
            "vault, or absolute) to work on the files there. Read what it made with your file tools and show it with "
            "attach_file. A run may wait for the user's approval and can take many minutes.")
        if "claude-code" in on:
            description += (" Claude Code reads and writes files only in that folder; it cannot run commands, browse "
                            "the web or use other tools.")
        if "codex" in on:
            description += " Codex writes only in that folder but can read any file the user's account can."
        spec = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": on, "description": "which agent does the job"},
                "task": {"type": "string", "maxLength": MAX_TASK,
                         "description": f"the whole job, standing on its own (at most {MAX_TASK:,} characters)"},
                "folder": {"type": "string", "description": "an existing folder to work in; leave out for a new one"},
            },
            "required": ["agent", "task"],
        }
        tools.register(TOOL, description, spec, self.delegate)
        return True

    def stop(self, session_id: str) -> bool:
        return self.runner.stop(session_id)

    async def stop_all(self) -> None:
        await self.runner.stop_all()

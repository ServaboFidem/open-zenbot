"""The delegation runner: Claude Code's argument list, the first-event check, the job folder rules, and runs of a
fake CLI (tests/fixtures/fake_agent_cli.py). Nothing here starts the real `claude`."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest

from src.harness import delegation
from src.harness.agent_clis import RUNNABLE, Agents, AgentSettings, save_settings
from src.harness.allowed_folders import Folders
from src.harness.delegation import (CLAUDE_TOOLS, MAX_TASK, TOOL, DelegationError, Delegator, FolderRefused, Runner,
                                    changed, changes_text, check_init, claude_argv, direct, job_text, leads_out,
                                    resolve_folder, snapshot)
from src.harness.tools import ToolRunner, current_call
from src.memory.ledger import Kind, Ledger

FAKE = Path(__file__).parent / "fixtures" / "fake_agent_cli.py"
APPROVED = ["-p", "--restricted", "--strict-mcp-config", "--tools", "Read,Write,Edit,Glob,Grep", "--permission-mode",
            "acceptEdits", "--output-format", "stream-json", "--verbose", "--no-session-persistence"]


# -- the argument list and the first event -------------------------------------------------------------------

def test_claude_argv_is_exactly_the_approved_list():
    assert claude_argv("C:/bin/claude.exe") == ["C:/bin/claude.exe", *APPROVED]
    assert CLAUDE_TOOLS == ("Read", "Write", "Edit", "Glob", "Grep")


def test_claude_argv_adds_only_the_model():
    assert claude_argv("claude", "claude-opus-4.5") == ["claude", *APPROVED, "--model", "claude-opus-4.5"]
    with pytest.raises(DelegationError):
        claude_argv("claude", "opus; rm -rf /")


def _init(folder: Path, **over) -> dict:
    return {"type": "system", "subtype": "init", "cwd": str(folder), "tools": ["Edit", "Glob", "Grep", "Read", "Write"],
            "mcp_servers": [], "apiKeySource": "none", **over}


@pytest.mark.parametrize("over, subscription, names", [
    ({}, True, None),
    ({"tools": ["Read", "Write", "Bash"]}, True, "Bash"),
    ({"mcp_servers": [{"name": "gmail", "status": "connected"}]}, True, "MCP"),
    ({"cwd": "ELSEWHERE"}, True, "folder"),
    ({"apiKeySource": "ANTHROPIC_API_KEY"}, True, "ANTHROPIC_API_KEY"),
    ({"apiKeySource": "ANTHROPIC_API_KEY"}, False, None),
])
def test_check_init(tmp_path, over, subscription, names):
    if over.get("cwd") == "ELSEWHERE":
        over = {"cwd": str(tmp_path.parent)}
    problem = check_init(_init(tmp_path, **over), tmp_path, subscription)
    if names is None:
        assert problem is None
    else:
        assert problem and names in problem
    if os.name == "nt" and names is None:  # Windows paths compare without case
        assert check_init(_init(Path(str(tmp_path).upper()), **over), tmp_path, subscription) is None


BUILTIN = [{"name": "cc-plugin-privacy", "path": "builtin", "source": "cc-plugin-privacy@builtin"}]


@pytest.mark.parametrize("over, names", [
    ({"permissionMode": "acceptEdits", "plugins": BUILTIN}, None),  # as Claude Code 2.1.325 printed it
    ({"permissionMode": "bypassPermissions"}, "bypassPermissions"),
    ({"permissionMode": "default"}, "default"),
    ({"plugins": [*BUILTIN, {"name": "gmail-tools", "path": "C:/Users/x/.claude/plugins/gmail", "source": "gmail@market"}]},
     "gmail-tools"),
    ({"plugins": [{"name": "odd"}]}, "odd"),
    ({"plugins": "everything"}, "plugins"),
])
def test_check_init_mode_and_plugins(tmp_path, over, names):
    problem = check_init(_init(tmp_path, **over), tmp_path, True)
    assert problem is None if names is None else (problem and names in problem)


def _junction(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("cannot make a link here")
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except OSError:
            pytest.skip("cannot make a link or a junction here")


def test_check_init_fails_when_the_folder_was_swapped_for_a_link(tmp_path):
    folder, elsewhere = tmp_path / "job", tmp_path / "elsewhere"
    folder.mkdir()
    elsewhere.mkdir()
    resolved = folder.resolve()
    folder.rmdir()  # swapped after resolve_folder: the same name now leads elsewhere
    _junction(elsewhere, folder)
    assert check_init(_init(folder), resolved, True)
    assert check_init(_init(elsewhere), resolved, True)


# -- the folder ----------------------------------------------------------------------------------------------

def _folders(full: list[Path], read_only: list[Path] | None = None, private: list[Path] | None = None) -> Folders:
    return Folders(full=full, read_only=read_only or [], private=private or [])


def test_resolve_folder_makes_one_under_delegated(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    folder, made = resolve_folder(vault, _folders([vault]), "", "2026-11-16-ab12")
    assert made is True
    assert folder == (vault / "delegated" / "2026-11-16-ab12").resolve() and folder.is_dir()


def test_resolve_folder_takes_a_folder_relative_to_the_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "proj").mkdir(parents=True)
    folder, made = resolve_folder(vault, _folders([vault]), "proj", "unused")
    assert (folder, made) == ((vault / "proj").resolve(), False)
    assert not (vault / "delegated").exists()


def _refused(vault: Path, folders: Folders, raw: str) -> str:
    with pytest.raises(FolderRefused) as e:
        resolve_folder(vault, folders, raw, "stamp")
    return str(e.value)


def test_refuses_a_private_folder(tmp_path):
    (tmp_path / "secret" / "x").mkdir(parents=True)
    why = _refused(tmp_path, _folders([tmp_path], private=[tmp_path / "secret"]), str(tmp_path / "secret" / "x"))
    assert "inside" in why and "private" in why


def test_refuses_a_folder_holding_a_private_folder(tmp_path):
    (tmp_path / "outer" / "secret").mkdir(parents=True)
    why = _refused(tmp_path, _folders([tmp_path], private=[tmp_path / "outer" / "secret"]), "outer")
    assert "holds" in why and "private" in why


def test_refuses_a_read_only_folder(tmp_path):
    (tmp_path / "ro" / "sub").mkdir(parents=True)
    why = _refused(tmp_path, _folders([tmp_path], read_only=[tmp_path / "ro"]), str(tmp_path / "ro" / "sub"))
    assert "inside" in why and "Read-Only" in why


def test_refuses_a_folder_holding_a_read_only_folder(tmp_path):
    (tmp_path / "a" / "ro").mkdir(parents=True)
    why = _refused(tmp_path, _folders([tmp_path], read_only=[tmp_path / "a" / "ro"]), "a")
    assert "holds" in why and "Read-Only" in why


def test_refuses_outside_the_writable_folders(tmp_path):
    vault, elsewhere = tmp_path / "vault", tmp_path / "elsewhere"
    vault.mkdir()
    elsewhere.mkdir()
    assert "outside" in _refused(vault, _folders([vault]), str(elsewhere))


def test_refuses_a_missing_folder(tmp_path):
    assert "does not exist" in _refused(tmp_path, _folders([tmp_path]), "nope")


def test_refuses_a_link_that_leads_out(tmp_path):
    vault, elsewhere = tmp_path / "vault", tmp_path / "elsewhere"
    vault.mkdir()
    elsewhere.mkdir()
    _junction(elsewhere, vault / "link")
    assert "outside" in _refused(vault, _folders([vault]), "link")


# -- what a bot-named folder may hold ------------------------------------------------------------------------

def test_leads_out_passes_a_plain_folder_and_a_link_that_stays_inside(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("a")
    _junction(tmp_path / "sub", tmp_path / "again")
    assert leads_out(tmp_path.resolve()) is None


def test_leads_out_names_a_junction_that_leads_out(tmp_path):
    job, outside = tmp_path / "job", tmp_path / "outside"
    (job / "deep").mkdir(parents=True)
    outside.mkdir()
    _junction(outside, job / "deep" / "door")
    why = leads_out(job.resolve())
    assert why and "deep/door" in why and "outside" in why


def test_leads_out_names_a_file_link_that_leads_out(tmp_path):
    job, outside = tmp_path / "job", tmp_path / "outside"
    job.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    try:
        os.symlink(outside / "secret.txt", job / "s.txt")
    except (OSError, NotImplementedError):
        pytest.skip("cannot make a file link here")
    why = leads_out(job.resolve())
    assert why and "s.txt" in why


def test_leads_out_names_a_hard_link(tmp_path):
    job, outside = tmp_path / "job", tmp_path / "outside"
    job.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    os.link(outside / "secret.txt", job / "hard.txt")
    why = leads_out(job.resolve())
    assert why and "hard.txt" in why and "hard link" in why


def test_leads_out_refuses_a_folder_too_large_to_check(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("a")
    assert leads_out(tmp_path.resolve(), limit=20) is None
    assert "too many" in leads_out(tmp_path.resolve(), limit=5)


# -- what changed --------------------------------------------------------------------------------------------

def test_changed_finds_added_modified_and_removed(tmp_path):
    (tmp_path / "keep.txt").write_text("a")
    (tmp_path / "edit.txt").write_text("a")
    (tmp_path / "gone.txt").write_text("a")
    before = snapshot(tmp_path)
    (tmp_path / "edit.txt").write_text("longer")
    (tmp_path / "gone.txt").unlink()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "new.txt").write_text("a")
    assert changed(before, snapshot(tmp_path)) == ["edit.txt", "gone.txt", "sub/new.txt"]


def test_changes_text_caps_at_200():
    text = changes_text([f"f{i}.txt" for i in range(250)])
    lines = text.splitlines()
    listed = [ln for ln in lines if ln.startswith("- ")]
    assert len(listed) == 200 and listed[-1] == "- f199.txt"
    assert lines[-1] == "and 50 more"
    assert "f200.txt" not in text


# -- runs of the fake CLI ------------------------------------------------------------------------------------

class FakeRunner(Runner):
    """Runs the fake through this interpreter; the argument list after it is the production one."""

    def command(self, agent: str, path: str, model: str) -> list[str]:
        return [sys.executable, *super().command(agent, path, model)]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    record = tmp_path / "record.json"
    state = {"mode": "ok"}
    real = delegation.run_env
    monkeypatch.setattr(delegation, "run_env",
                        lambda base=None: {**real(base), "FAKE_AGENT_RECORD": str(record), "FAKE_AGENT_MODE": state["mode"]})
    ledger = Ledger(vault)

    def make(mode: str = "ok", auth: str = "subscription", timeout: float = 30.0, max_runs: int = 2,
             folder: str = "job") -> tuple[Runner, Path]:
        state["mode"] = mode
        agents = Agents(vault)
        agents.statuses["claude-code"] = {"path": str(FAKE), "auth": auth, "usable": True, "model": ""}
        target = vault / folder
        target.mkdir(exist_ok=True)
        return FakeRunner(agents, ledger, timeout=timeout, max_runs=max_runs), target.resolve()

    make.ledger, make.record = ledger, record
    return make


def _delegate_entries(ledger: Ledger) -> list[dict]:
    return [{"event": e.data.get("event"), **e.data} for e in ledger.iter() if e.kind == Kind.delegate]


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _all_dead(pids: list[int], within: float = 5.0) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not any(_alive(p) for p in pids):
            return True
        await asyncio.sleep(0.1)
    return False


async def _child_recorded(record: Path, within: float = 15.0) -> dict:
    end = time.monotonic() + within
    while time.monotonic() < end:
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            if "child" in data:
                return data
        except (OSError, ValueError):
            pass
        await asyncio.sleep(0.05)
    raise AssertionError("the fake never recorded its child")


async def test_run_returns_text_and_changed_files(rig):
    runner, folder = rig("ok")
    r = await runner.run("claude-code", folder, "Write hi to out.txt.", "b1", "s1")
    assert r.ok and r.error == ""
    assert r.text == "Made out.txt in the folder."
    assert r.changed == ["out.txt"] and (folder / "out.txt").read_text() == "hi"
    assert r.usage["output_tokens"] == 1955
    events = _delegate_entries(rig.ledger)
    assert [e["event"] for e in events] == ["start", "file", "end"]
    assert events[0]["agent"] == "claude-code" and Path(events[0]["folder"]) == folder
    assert events[1]["tool"] == "Write" and events[1]["path"] == "out.txt"
    assert events[2]["ok"] is True and events[2]["changed"] == 1 and events[2]["usage"]["input_tokens"] == 10
    assert isinstance(events[2]["seconds"], (int, float))
    assert runner.live == {}


async def test_task_text_is_never_an_argument(rig):
    runner, folder = rig("ok")
    task = "UNIQUE-TASK-TEXT: write a poem about {braces} into out.txt"
    await runner.run("claude-code", folder, task, "b1", "s1")
    rec = json.loads(rig.record.read_text(encoding="utf-8"))
    assert rec["argv"] == APPROVED
    assert not any("UNIQUE-TASK-TEXT" in a for a in rec["argv"])
    assert rec["stdin"] == job_text(folder, task)
    assert rec["stdin"].endswith(task) and rec["stdin"].index(str(folder)) < rec["stdin"].index(task)
    assert Path(rec["cwd"]).resolve() == folder


async def test_child_environment_has_no_keys(rig, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                 "CLAUDE_CODE_USE_BEDROCK", "CODEX_SANDBOX", "OPENROUTER_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "C:/claude-config")
    runner, folder = rig("ok")
    await runner.run("claude-code", folder, "task", "b1", "s1")
    names = {n.upper() for n in json.loads(rig.record.read_text(encoding="utf-8"))["env"]}
    assert "CLAUDE_CONFIG_DIR" in names
    for n in names:
        assert not n.startswith(("ANTHROPIC_", "OPENAI_", "CLAUDE_CODE_", "CODEX_")), n
        assert not n.endswith(("_API_KEY", "_TOKEN")), n


@pytest.mark.parametrize("mode, names", [("extra_tool", "Bash"), ("mcp", "MCP"), ("api_key", "ANTHROPIC_API_KEY")])
async def test_run_stops_on_a_bad_first_event(rig, mode, names):
    runner, folder = rig(mode, auth="subscription")
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert not r.ok and names in r.error
    assert not (folder / "out.txt").exists()
    assert _delegate_entries(rig.ledger)[-1]["ok"] is False


async def test_a_non_json_line_is_skipped(rig):
    runner, folder = rig("garbage_line")
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert r.ok and r.changed == ["out.txt"]


async def test_a_cut_off_stream_fails_with_the_tail(rig):
    runner, folder = rig("cut_off")
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert not r.ok and "Something broke: connection reset" in r.error
    assert len(r.error) <= 2000


async def test_an_error_result_passes_the_cli_message_through(rig):
    runner, folder = rig("error_result")
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert not r.ok and r.error == "Credit balance is too low"
    assert r.usage["input_tokens"] == 3


async def test_timeout_kills_the_tree_and_returns_a_plain_failure(rig):
    runner, folder = rig("hang", timeout=1)
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert not r.ok and r.error == "Timed out after 1 s."
    rec = json.loads(rig.record.read_text(encoding="utf-8"))
    assert "child" in rec, "the fake was killed before it started its child; nothing proved about the tree"
    assert await _all_dead([rec["pid"], rec["child"]])
    assert runner.live == {}


async def test_stop_kills_the_run(rig):
    runner, folder = rig("hang")
    task = asyncio.create_task(runner.run("claude-code", folder, "task", "b1", "s1"))
    rec = await _child_recorded(rig.record)
    assert runner.stop("other") is False
    assert runner.stop("s1") is True
    r = await task
    assert not r.ok and r.error == "Stopped by the user."
    assert await _all_dead([rec["pid"], rec["child"]])
    assert runner.live == {}


async def test_stop_while_the_folder_is_read_launches_nothing(rig, monkeypatch):
    runner, folder = rig("ok")
    reading, release = threading.Event(), threading.Event()
    real, calls = delegation.snapshot, []

    def slow(path, limit=delegation.SNAPSHOT_LIMIT):  # the before-snapshot waits until the test has pressed Stop
        calls.append(path)
        if len(calls) == 1:
            reading.set()
            release.wait(10)
        return real(path, limit)

    monkeypatch.setattr(delegation, "snapshot", slow)
    task = asyncio.create_task(runner.run("claude-code", folder, "task", "b1", "s1"))
    while not reading.is_set():
        await asyncio.sleep(0.01)
    assert runner.stop("s1") is True
    release.set()
    r = await task
    assert not r.ok and r.error == "Stopped by the user."
    assert not rig.record.exists() and not (folder / "out.txt").exists()  # the fake never ran
    assert [e["event"] for e in _delegate_entries(rig.ledger)] == ["start", "end"]
    assert runner.live == {}


async def test_stop_while_it_starts_kills_it_before_it_gets_the_job(rig, monkeypatch):
    runner, folder = rig("ok")
    real, pids = delegation._launch, []

    async def launch(argv, where):  # Stop lands between the launch and the first byte of the job
        proc, job = await real(argv, where)
        pids.append(proc.pid)
        runner.stop("s1")
        return proc, job

    monkeypatch.setattr(delegation, "_launch", launch)
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert not r.ok and r.error == "Stopped by the user."
    assert pids and await _all_dead(pids)
    assert not rig.record.exists() and not (folder / "out.txt").exists()  # it records only once it has the job
    assert runner.live == {}


async def test_one_run_per_session(rig):
    runner, folder = rig("hang")
    first = asyncio.create_task(runner.run("claude-code", folder, "task", "b1", "s1"))
    await asyncio.sleep(0)
    with pytest.raises(DelegationError):
        await runner.run("claude-code", folder, "task", "b1", "s1")
    runner.stop("s1")
    assert (await first).error == "Stopped by the user."


async def test_two_runs_in_the_harness(rig):
    runner, folder = rig("hang")
    _, other = rig("hang", folder="job2")
    runs = [asyncio.create_task(runner.run("claude-code", f, "task", "b1", s)) for f, s in ((folder, "s1"), (other, "s2"))]
    await asyncio.sleep(0)
    with pytest.raises(DelegationError):
        await runner.run("claude-code", folder, "task", "b1", "s3")
    assert runner.stop("s1") and runner.stop("s2")
    assert [r.error for r in await asyncio.gather(*runs)] == ["Stopped by the user."] * 2
    assert runner.live == {}


async def test_a_folder_with_spaces_and_non_ascii_runs(rig):
    runner, folder = rig("ok", folder="my job ü 日本")
    r = await runner.run("claude-code", folder, "Écris « hi » dans out.txt.", "b1", "s1")
    assert r.ok and r.changed == ["out.txt"]
    rec = json.loads(rig.record.read_text(encoding="utf-8"))
    assert rec["stdin"].endswith("Écris « hi » dans out.txt.")


async def test_a_missing_program_is_refused(rig):
    runner, folder = rig("ok")
    runner.agents.statuses["claude-code"]["path"] = None
    with pytest.raises(DelegationError):
        await runner.run("claude-code", folder, "task", "b1", "s1")
    assert runner.live == {}


# -- review fixes: leftovers, cancellation, odd paths, a bad start, big folders --------------------------------

async def test_a_child_left_behind_dies_and_the_run_ends_promptly(rig):
    runner, folder = rig("cut_off_child", timeout=30)
    t0 = time.monotonic()
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert time.monotonic() - t0 < 15, "the run waited on a pipe the leftover child holds"
    assert not r.ok and "Something broke: connection reset" in r.error
    rec = json.loads(rig.record.read_text(encoding="utf-8"))
    assert await _all_dead([rec["pid"], rec["child"]])


async def test_a_cancelled_run_still_logs_end_and_kills_the_tree(rig):
    runner, folder = rig("hang")
    task = asyncio.create_task(runner.run("claude-code", folder, "task", "b1", "s1"))
    rec = await _child_recorded(rig.record)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    events = _delegate_entries(rig.ledger)
    assert [e["event"] for e in events] == ["start", "end"] and events[-1]["ok"] is False
    assert await _all_dead([rec["pid"], rec["child"]])
    assert runner.live == {}


async def test_a_reader_fault_fails_the_run_and_logs_end(rig, monkeypatch):
    runner, folder = rig("ok")

    def boom(*a, **k):
        raise RuntimeError("bad event")

    monkeypatch.setattr(runner, "_files", boom)
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert not r.ok and "bad event" in r.error
    assert _delegate_entries(rig.ledger)[-1]["event"] == "end"


async def test_a_write_outside_the_folder_ends_the_run_and_is_logged_capped(rig):
    runner, folder = rig("odd_paths")
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert not r.ok and "outside" in r.error
    files = [e for e in _delegate_entries(rig.ledger) if e["event"] == "file"]
    assert len(files) == 1 and files[0]["outside"] is True and files[0]["tool"] == "Write"
    assert len(files[0]["path"]) == 500 and files[0]["path"].startswith(str(folder.parent.resolve()))


def test_where_counts_odd_paths_as_outside(tmp_path):
    assert delegation._where("bad\0name.txt", tmp_path) == ("bad\0name.txt", True)  # names no file
    assert delegation._where("~/notes.txt", tmp_path)[1] is True  # the agent may expand it to the home folder
    assert delegation._where("~\\notes.txt", tmp_path)[1] is True
    assert delegation._where("~$draft.docx", tmp_path.resolve()) == ("~$draft.docx", False)  # an ordinary name
    assert delegation._where("sub/a.txt", tmp_path.resolve()) == ("sub/a.txt", False)


@pytest.mark.parametrize("mode, tool", [("reach_read", "Read"), ("reach_glob", "Glob")])
async def test_any_tool_reaching_outside_ends_the_run_and_kills_the_tree(rig, mode, tool):
    runner, folder = rig(mode, timeout=60)
    t0 = time.monotonic()
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert time.monotonic() - t0 < 20, "the run went on after the agent reached outside its folder"
    assert not r.ok and "outside its folder" in r.error
    rec = json.loads(rig.record.read_text(encoding="utf-8"))
    assert await _all_dead([rec["pid"], rec["child"]])
    files = [e for e in _delegate_entries(rig.ledger) if e["event"] == "file"]
    assert files[-1]["tool"] == tool and files[-1]["outside"] is True
    assert runner.live == {}


async def test_child_environment_turns_off_the_current_folder_search_on_windows(rig, monkeypatch):
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)  # whatever the harness was started with
    runner, folder = rig("ok")
    await runner.run("claude-code", folder, "task", "b1", "s1")
    names = {n.upper() for n in json.loads(rig.record.read_text(encoding="utf-8"))["env"]}
    assert ("NODEFAULTCURRENTDIRECTORYINEXEPATH" in names) == (os.name == "nt")


# -- a bot-named folder is checked for links just before the launch ------------------------------------------

async def test_a_named_folder_with_a_link_out_is_refused_before_launch(rig, tmp_path, monkeypatch):
    runner, folder = rig("ok")
    outside = tmp_path / "outside"
    outside.mkdir()
    real = delegation.snapshot

    def planting(path, limit=delegation.SNAPSHOT_LIMIT):  # the link lands after the folder was first read
        out = real(path, limit)
        if not (folder / "door").exists():
            _junction(outside, folder / "door")
        return out

    monkeypatch.setattr(delegation, "snapshot", planting)
    r = await runner.run("claude-code", folder, "task", "b1", "s1", named=True)
    assert not r.ok and "door" in r.error and "outside" in r.error
    assert not rig.record.exists()  # nothing was launched
    assert [e["event"] for e in _delegate_entries(rig.ledger)] == ["start", "end"] and runner.live == {}


async def test_a_harness_made_folder_is_not_scanned(rig, monkeypatch):
    runner, folder = rig("ok")
    monkeypatch.setattr(delegation, "leads_out", lambda *a, **k: "should not be asked")
    assert (await runner.run("claude-code", folder, "task", "b1", "s1")).ok


async def test_a_named_folder_holding_a_hard_link_is_refused(rig, tmp_path):
    runner, folder = rig("ok")
    (tmp_path / "secret.txt").write_text("s")
    os.link(tmp_path / "secret.txt", folder / "copy.txt")
    r = await runner.run("claude-code", folder, "task", "b1", "s1", named=True)
    assert not r.ok and "copy.txt" in r.error and not rig.record.exists()


# -- npm shims: never through cmd.exe in the job folder -------------------------------------------------------

windows_only = pytest.mark.skipif(os.name != "nt", reason="npm .cmd shims are a Windows thing")

# what `npm install -g @anthropic-ai/claude-code` writes as claude.cmd
SHIM = ('@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\nCALL :find_dp0\r\n\r\n'
        'IF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n  SET "_prog=node"\r\n'
        '  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n)\r\n\r\n'
        'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
        '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\cli.js" %*\r\n')


def _npm(tmp_path: Path, script: bool = True) -> Path:
    """An npm global folder holding claude.cmd; its cli.js is the fake CLI (run here by Python as `node`)."""
    npm = tmp_path / "npm"
    pkg = npm / "node_modules" / "@anthropic-ai" / "claude-code"
    pkg.mkdir(parents=True)
    if script:
        (pkg / "cli.js").write_text(FAKE.read_text(encoding="utf-8"), encoding="utf-8")
    shim = npm / "claude.cmd"
    shim.write_text(SHIM, encoding="utf-8")
    return shim


def _plain_runner(fake: Runner, shim: Path) -> Runner:
    fake.agents.statuses["claude-code"]["path"] = str(shim)
    return Runner(fake.agents, fake.ledger, timeout=30)


@windows_only
async def test_a_shim_never_runs_a_node_planted_in_the_job_folder(rig, tmp_path, monkeypatch):
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)  # as in a harness started from Explorer
    fake, folder = rig("ok")
    marker = tmp_path / "planted-ran.txt"
    for name in ("node.bat", "node.cmd"):
        (folder / name).write_text(f'@echo off\r\necho ran> "{marker}"\r\n', encoding="utf-8")
    monkeypatch.setattr(delegation, "_node_on_path", lambda: Path(sys.executable), raising=False)
    runner = _plain_runner(fake, _npm(tmp_path))
    r = await runner.run("claude-code", folder, "Write hi to out.txt.", "b1", "s1")
    assert not marker.exists(), "the node.bat planted in the job folder ran"
    assert r.ok and r.changed == ["out.txt"]
    assert json.loads(rig.record.read_text(encoding="utf-8"))["argv"] == APPROVED


@windows_only
def test_direct_resolves_a_shim_to_node_and_its_script(tmp_path, monkeypatch):
    shim = _npm(tmp_path)
    job = tmp_path / "job"
    job.mkdir()
    monkeypatch.setattr(delegation, "_node_on_path", lambda: Path(sys.executable))
    script = str((shim.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js").resolve())
    assert direct([str(shim), *APPROVED], job) == [str(Path(sys.executable).resolve()), script, *APPROVED]
    (shim.parent / "node.exe").write_bytes(b"")  # the one beside the shim wins, as in the shim itself
    assert direct([str(shim), *APPROVED], job) == [str((shim.parent / "node.exe").resolve()), script, *APPROVED]
    assert direct([sys.executable, "-p"], job) == [sys.executable, "-p"]  # not a shim: as it is


@windows_only
@pytest.mark.parametrize("case", ["no_script", "no_target", "no_node", "node_in_job", "node_not_exe"])
async def test_an_unresolvable_shim_is_refused_and_nothing_runs(rig, tmp_path, monkeypatch, case):
    fake, folder = rig("ok")
    shim = _npm(tmp_path, script=case != "no_script")
    if case == "no_target":
        shim.write_text("@echo off\r\nnode %*\r\n", encoding="utf-8")
    node = {"no_node": None, "node_in_job": folder / "node.exe",
            "node_not_exe": tmp_path / "node.cmd"}.get(case, Path(sys.executable))
    if node is not None and not node.exists():
        node.write_bytes(b"")
    monkeypatch.setattr(delegation, "_node_on_path", lambda: node, raising=False)
    runner = _plain_runner(fake, shim)
    with pytest.raises(DelegationError, match="Install the native Claude Code"):
        await runner.run("claude-code", folder, "task", "b1", "s1")
    assert not rig.record.exists() and runner.live == {} and _delegate_entries(rig.ledger) == []


@windows_only
def test_node_on_path_never_looks_in_the_current_folder(tmp_path, monkeypatch):
    here, found = tmp_path / "here", tmp_path / "bin"
    here.mkdir()
    found.mkdir()
    (here / "node.exe").write_bytes(b"")
    (here / "node.cmd").write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.chdir(here)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", "here", str(found)]))
    assert delegation._node_on_path() is None
    (found / "node.cmd").write_text("@echo off\r\n", encoding="utf-8")
    assert delegation._node_on_path() is None  # a script is not node
    (found / "node.exe").write_bytes(b"")
    assert delegation._node_on_path() == found / "node.exe"


async def test_a_first_event_that_is_not_the_start_says_what_the_cli_said(rig):
    runner, folder = rig("error_first")
    r = await runner.run("claude-code", folder, "task", "b1", "s1")
    assert not r.ok and "start event" in r.error and r.error.endswith("It said: Not logged in. Please run /login")
    runner, folder = rig("garbage_first")
    r = await runner.run("claude-code", folder, "task", "b1", "s2")
    assert not r.ok and "start event" in r.error and "Fatal: config file is broken" in r.error


def test_snapshot_stops_at_the_limit(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("a")
    assert snapshot(tmp_path).complete
    part = snapshot(tmp_path, limit=5)
    assert not part.complete and len(part) <= 5
    assert "too large to compare in full" in changes_text(["f1.txt"], complete=False)
    assert "too large" not in changes_text(["f1.txt"])


# -- the tool and its approval -------------------------------------------------------------------------------

CODEX_READS_TEXT = "Codex can read any file your account can, not only this folder."


def _switch(vault: Path, key: str = "claude-code", **kw) -> None:
    save_settings(vault, key, AgentSettings(**kw))


def _delegator(runner: Runner, vault: Path, private: list[Path] | None = None,
               in_routine=lambda session_id: False) -> Delegator:
    return Delegator(vault, runner.agents, lambda: _folders([vault], private=private), runner, in_routine=in_routine)


def _plain(tmp_path: Path, private: list[Path] | None = None) -> tuple[Delegator, Path]:
    """A delegator for the approval checks, both agents found and switched on: nothing runs, so the runner is
    never asked."""
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    agents = Agents(vault)
    for key in ("claude-code", "codex"):
        agents.statuses[key] = {"path": key, "usable": True}
        _switch(vault, key, enabled=True)
    return _delegator(Runner(agents, Ledger(vault)), vault, private), vault


def test_needs_approval_codex_always(tmp_path):
    d, vault = _plain(tmp_path)
    _switch(vault, "codex", enabled=True, ask=False)
    rule, title, detail = d.needs_approval({"agent": "codex", "task": "Tidy the notes."})
    assert rule == "delegate:codex" and "Codex" in detail and title == "delegate: Codex: Tidy the notes."


def test_needs_approval_claude_by_the_setting(tmp_path):
    d, vault = _plain(tmp_path)
    call = {"agent": "claude-code", "task": "Write a poem."}
    assert d.needs_approval(call)[0] == "delegate:claude-code"  # ask is on by default
    _switch(vault, enabled=True, ask=False)
    assert d.needs_approval(call) is None
    assert d.needs_approval({**call, "folder": "  "}) is None  # a blank folder is no folder


def test_needs_approval_claude_always_for_a_named_folder(tmp_path):
    d, vault = _plain(tmp_path)
    _switch(vault, enabled=True, ask=False)
    rule, _, detail = d.needs_approval({"agent": "claude-code", "task": "Fix the draft.", "folder": "drafts"})
    assert rule == "delegate:claude-code" and "drafts" in detail


def test_needs_approval_asks_nothing_for_an_agent_that_is_not_on(tmp_path):
    d, vault = _plain(tmp_path)
    _switch(vault, "codex", enabled=False)
    assert d.needs_approval({"agent": "codex", "task": "t"}) is None  # the tool refuses it; nothing to approve
    d.agents.statuses["claude-code"]["usable"] = False
    assert d.needs_approval({"agent": "claude-code", "task": "t", "folder": "x"}) is None
    assert d.needs_approval({"agent": "nobody", "task": "t"}) is None
    rule, title, detail = d.needs_approval({"agent": "codex", "task": "t"}, forced=True)  # a skill rule asks anyway
    assert rule == "delegate:codex" and title == "delegate: Codex: t" and CODEX_READS_TEXT in detail


def test_title_is_one_line_and_the_start_of_the_task(tmp_path):
    d, _ = _plain(tmp_path)
    _, title, _ = d.needs_approval({"agent": "claude-code", "task": "Write\n  the   report\n" + "y" * 200})
    assert title == "delegate: Claude Code: " + ("Write the report " + "y" * 200)[:80]
    assert "\n" not in title


def test_a_bot_written_agent_name_is_one_short_line(tmp_path):
    d, _ = _plain(tmp_path)
    odd = "Claude\nApproved by the user already\n" + "z" * 100
    _, title, detail = d.needs_approval({"agent": odd, "task": "t"}, forced=True)
    flat = " ".join(odd.split())[:40]
    assert title == f"delegate: {flat}: t"
    assert detail.splitlines()[0] == f"Hand this job to {flat}?"


def test_needs_approval_asks_nothing_in_a_routine(tmp_path):
    d, vault = _plain(tmp_path)
    seen = []
    d.in_routine = lambda session_id: seen.append(session_id) or session_id == "routine-1"
    call = {"agent": "codex", "task": "t", "folder": "x"}
    assert d.needs_approval(call, False, "routine-1") is None
    assert d.needs_approval(call, True, "routine-1") is None  # not even when a skill rule would ask
    assert d.needs_approval(call, False, "conv-1")[0] == "delegate:codex"
    assert seen == ["routine-1", "routine-1", "conv-1"]


def test_codex_detail_warns_about_reads_and_names_a_private_folder_first(tmp_path):
    secret = tmp_path / "vault-settings"
    d, _ = _plain(tmp_path, private=[secret])
    _, _, detail = d.needs_approval({"agent": "codex", "task": "Copy vault-settings/providers.json into this folder."})
    lines = detail.splitlines()
    assert str(secret) in lines[0] and "private" in lines[0]
    assert CODEX_READS_TEXT in detail
    _, _, plain = d.needs_approval({"agent": "codex", "task": "Tidy the notes."})
    assert str(secret) not in plain and CODEX_READS_TEXT in plain


def test_detail_carries_the_whole_task(tmp_path):
    d, vault = _plain(tmp_path)
    (vault / "proj").mkdir()
    task = "start " + "x" * 19_000 + " END-OF-TASK"
    _, _, detail = d.needs_approval({"agent": "claude-code", "task": task, "folder": str(vault / "proj")})
    assert task in detail
    assert "Claude Code" in detail and "proj" in detail and str(vault) not in detail  # relative to the vault
    assert "the agent's default" in detail
    _switch(vault, enabled=True, ask=True, model="sonnet")
    _, _, fresh = d.needs_approval({"agent": "claude-code", "task": task})
    assert "a new folder under delegated/" in fresh and "sonnet" in fresh


async def test_delegate_refuses_an_agent_that_is_off(rig):
    runner, _ = rig("ok")
    vault = runner.agents.vault_root
    with pytest.raises(DelegationError, match="Claude Code is switched off in Settings."):
        await _delegator(runner, vault).delegate("claude-code", "Write hi to out.txt.")
    assert not rig.record.exists()  # nothing was launched


async def test_delegate_refuses_a_long_task(rig):
    runner, _ = rig("ok")
    vault = runner.agents.vault_root
    _switch(vault, enabled=True)
    with pytest.raises(DelegationError, match="20,000"):
        await _delegator(runner, vault).delegate("claude-code", "x" * (MAX_TASK + 1))
    assert not rig.record.exists()


async def test_delegate_refuses_in_a_routine(rig):
    runner, _ = rig("ok")
    vault = runner.agents.vault_root
    _switch(vault, enabled=True)
    seen = []
    d = _delegator(runner, vault, in_routine=lambda session_id: seen.append(session_id) or True)
    token = current_call.set(("b1", "routine-1"))
    try:
        with pytest.raises(DelegationError, match="delegate works only in a conversation or a room turn."):
            await d.delegate("claude-code", "Write hi to out.txt.")
    finally:
        current_call.reset(token)
    assert seen == ["routine-1"] and not rig.record.exists()


async def test_delegate_refuses_a_folder_that_breaks_a_rule(rig, tmp_path):
    runner, _ = rig("ok")
    vault = runner.agents.vault_root
    _switch(vault, enabled=True)
    with pytest.raises(DelegationError, match="outside the folders bots may write"):
        await _delegator(runner, vault).delegate("claude-code", "Write hi.", folder=str(tmp_path))
    assert not rig.record.exists()


async def test_delegate_returns_the_folder_and_changes_then_the_answer(rig):
    runner, _ = rig("ok")
    vault = runner.agents.vault_root
    _switch(vault, enabled=True)
    task = "Write hi to out.txt."
    token = current_call.set(("b1", "s1"))
    try:
        out = await _delegator(runner, vault).delegate("claude-code", task)
    finally:
        current_call.reset(token)
    made = list((vault / "delegated").iterdir())
    assert len(made) == 1 and (made[0] / "out.txt").read_text() == "hi"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{6}", made[0].name)
    # the answer last, so a long one cannot push the folder and the changes past the tool output cap
    assert out == (f"Folder: delegated/{made[0].name}\nFiles added, changed or removed:\n- out.txt\n\n"
                   "Made out.txt in the folder.")
    assert json.loads(rig.record.read_text(encoding="utf-8"))["stdin"] == job_text(made[0].resolve(), task)  # wrapped once
    assert [e.session_id for e in rig.ledger.iter() if e.kind == Kind.delegate] == ["s1"] * 3


async def test_delegate_names_a_folder_outside_the_vault_in_full(rig, tmp_path):
    runner, _ = rig("ok")
    vault = runner.agents.vault_root
    _switch(vault, enabled=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    d = Delegator(vault, runner.agents, lambda: _folders([vault, outside]), runner)
    out = await d.delegate("claude-code", "Write hi.", folder=str(outside))
    assert out.splitlines()[0] == f"Folder: {outside.resolve()}"
    out = await d.delegate("claude-code", "Write hi.", folder="job")
    assert out.splitlines()[0] == "Folder: job"


async def test_delegate_gives_the_reason_an_enabled_agent_cannot_run(rig):
    runner, _ = rig("ok")
    vault = runner.agents.vault_root
    _switch(vault, enabled=True)
    runner.agents.statuses["claude-code"].update(usable=False, reason="Claude Code is not signed in. Run `claude`.")
    with pytest.raises(DelegationError, match="^Claude Code is not signed in"):
        await _delegator(runner, vault).delegate("claude-code", "Write hi.")
    with pytest.raises(DelegationError, match="^There is no agent called nobody."):
        await _delegator(runner, vault).delegate("nobody", "Write hi.")
    assert not rig.record.exists()


async def test_delegate_raises_the_reason_a_run_failed(rig):
    runner, _ = rig("error_result")
    vault = runner.agents.vault_root
    _switch(vault, enabled=True)
    with pytest.raises(DelegationError, match="^Credit balance is too low\n\nFolder: delegated/"):
        await _delegator(runner, vault).delegate("claude-code", "Write hi.")


@pytest.mark.parametrize("mode, timeout, error, listed", [
    ("write_then_fail", 30, "Ran out of turns", "- out.txt"),
    ("hang", 1, "Timed out after 1 s.", "No files were added, changed or removed."),
])
async def test_a_failed_run_still_says_where_the_work_is(rig, mode, timeout, error, listed):
    runner, _ = rig(mode, timeout=timeout)
    vault = runner.agents.vault_root
    _switch(vault, enabled=True)
    with pytest.raises(DelegationError) as e:
        await _delegator(runner, vault).delegate("claude-code", "Write hi.")
    made = next((vault / "delegated").iterdir())
    assert str(e.value) == f"{error}\n\nFolder: delegated/{made.name}\n" + (
        "Files added, changed or removed:\n- out.txt" if listed.startswith("- ") else listed)


async def test_delegate_scans_only_a_folder_the_bot_named(rig, tmp_path):
    runner, folder = rig("ok")
    vault = runner.agents.vault_root
    _switch(vault, enabled=True)
    (tmp_path / "secret.txt").write_text("s")
    os.link(tmp_path / "secret.txt", folder / "copy.txt")
    with pytest.raises(DelegationError, match="copy.txt") as e:
        await _delegator(runner, vault).delegate("claude-code", "Tidy.", folder="job")
    assert "Folder: job" in str(e.value) and not rig.record.exists()
    assert (await _delegator(runner, vault).delegate("claude-code", "Write hi.")).startswith("Folder: delegated/")


async def test_delegate_points_at_the_settings_page_for_an_agent_that_cannot_run(rig):
    runner, _ = rig("ok")
    vault = runner.agents.vault_root
    _switch(vault, enabled=True)
    runner.agents.statuses["claude-code"].update(usable=False)
    with pytest.raises(DelegationError, match=r"Check Settings > Harness Tools > Coding agents\.$"):
        await _delegator(runner, vault).delegate("claude-code", "Write hi.")


def test_register_follows_the_switch(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    agents = Agents(vault)
    agents.statuses["claude-code"] = {"path": "claude", "usable": True}
    d = Delegator(vault, agents, lambda: _folders([vault]), Runner(agents, Ledger(vault)))
    tools = ToolRunner(tmp_path, timeout=10)
    _switch(vault, enabled=True)
    d.register(tools)
    spec = next(s for s in tools.specs() if s.name == TOOL)
    assert spec.parameters["properties"]["agent"]["enum"] == ["claude-code"]
    assert "Claude Code" in spec.description and spec.parameters["required"] == ["agent", "task"]
    _switch(vault, enabled=False)
    d.register(tools)
    assert TOOL not in tools.names()
    _switch(vault, enabled=True)
    d.register(tools)
    d.register(tools)  # again: still one tool
    assert tools.names().count(TOOL) == 1


# -- Codex ---------------------------------------------------------------------------------------------------

async def test_codex_stays_detected_only(rig):
    """Codex's sandbox left the network open when checked on Windows
    (docs/superpowers/specs/2026-11-16-codex-confinement-notes.md), so Codex is found but never run."""
    assert RUNNABLE["codex"] == ("Codex is detected only: in testing on Windows its sandbox did not switch off network "
                                 "access, so a run could send files out.")
    runner, folder = rig("ok")
    runner.agents.statuses["codex"] = {"path": str(FAKE), "auth": "subscription", "usable": True, "model": ""}
    with pytest.raises(DelegationError):
        await runner.run("codex", folder, "task", "b1", "s1")
    assert runner.live == {} and not rig.record.exists() and _delegate_entries(rig.ledger) == []

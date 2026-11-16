"""A stand-in for `claude -p ... --output-format stream-json`: tests/test_delegation.py runs it as
`python fake_agent_cli.py <argv...>`. It reads the job from stdin, records its argv, environment names, stdin,
cwd and pid to FAKE_AGENT_RECORD, then prints one event stream chosen by FAKE_AGENT_MODE."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

TOOLS = ["Edit", "Glob", "Grep", "Read", "Write"]


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def record(data: dict) -> None:
    path = os.environ.get("FAKE_AGENT_RECORD")
    if path:
        Path(path).write_text(json.dumps(data), encoding="utf-8")


def init(**over) -> dict:
    return {"type": "system", "subtype": "init", "cwd": os.getcwd(), "tools": TOOLS, "mcp_servers": [],
            "apiKeySource": "none", "model": "fake-model", "permissionMode": "acceptEdits", **over}


def write_out() -> None:
    target = Path(os.getcwd()) / "out.txt"
    emit({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Writing the file."},
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": str(target), "content": "hi"}}]}})
    target.write_text("hi", encoding="utf-8")
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}})
    emit({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}})


def success() -> None:
    emit({"type": "result", "subtype": "success", "is_error": False, "result": "Made out.txt in the folder.",
          "usage": {"input_tokens": 10, "output_tokens": 1955}, "duration_ms": 1200})


def main() -> int:
    job = sys.stdin.buffer.read().decode("utf-8")
    data = {"argv": sys.argv[1:], "env": sorted(os.environ), "stdin": job, "cwd": os.getcwd(), "pid": os.getpid()}
    record(data)
    mode = os.environ.get("FAKE_AGENT_MODE", "ok")
    if mode == "ok":
        emit(init())
        write_out()
        success()
    elif mode in ("extra_tool", "mcp", "api_key"):
        over = {"extra_tool": {"tools": [*TOOLS, "Bash"]},
                "mcp": {"mcp_servers": [{"name": "gmail", "status": "connected"}]},
                "api_key": {"apiKeySource": "ANTHROPIC_API_KEY"}}[mode]
        emit(init(**over))
        time.sleep(3)  # a runner that does not stop at the first event sees the write below
        write_out()
        success()
    elif mode == "garbage_line":
        emit(init())
        sys.stdout.write("this is not json {\n")
        sys.stdout.flush()
        write_out()
        success()
    elif mode == "cut_off":
        emit(init())
        sys.stderr.write("Something broke: connection reset\n")
        sys.stderr.flush()
        return 1
    elif mode == "cut_off_child":
        # a child left behind that holds this process's stdout and stderr open after it exits
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdin=subprocess.DEVNULL,
                                 stdout=sys.stdout.fileno(), stderr=sys.stderr.fileno(), creationflags=flags)
        record({**data, "child": child.pid})
        emit(init())
        sys.stderr.write("Something broke: connection reset\n")
        sys.stderr.flush()
        os._exit(1)  # without waiting for the child
    elif mode == "error_first":
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "result": "Not logged in. Please run /login"})
        return 1
    elif mode == "garbage_first":
        sys.stdout.write("Fatal: config file is broken\n")
        emit({"type": "system", "subtype": "status", "status": "starting"})
        return 1
    elif mode == "odd_paths":
        emit(init())
        outside = str(Path(os.getcwd()).parent / ("elsewhere-" + "x" * 1000 + ".txt"))  # long: the ledger caps it
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": outside, "content": "x"}},
            {"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "bad\u0000name.txt"}}]}})
        success()
    elif mode in ("reach_read", "reach_glob"):
        # asks for a path outside its folder, then hangs with a child: the runner must end the run itself
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        record({**data, "child": child.pid})
        emit(init())
        up = Path(os.getcwd()).parent
        name, args = (("Read", {"file_path": str(up / "secret.txt")}) if mode == "reach_read"
                      else ("Glob", {"pattern": "**/*", "path": str(up)}))
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": name, "input": args}]}})
        time.sleep(120)
    elif mode == "write_then_fail":
        emit(init())
        write_out()
        emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "Ran out of turns",
              "usage": {"input_tokens": 3, "output_tokens": 1}})
    elif mode == "hang":
        # the child first, so even a 1 s timeout finds a tree to kill
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        record({**data, "child": child.pid})
        emit(init())
        time.sleep(120)
    elif mode == "error_result":
        emit(init())
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "result": "Credit balance is too low", "usage": {"input_tokens": 3, "output_tokens": 0}})
    return 0


if __name__ == "__main__":
    sys.exit(main())

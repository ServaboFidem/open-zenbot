"""Tool runner.

Skills bind to neutral tools: shell, files, browser, MCP. Day one ships
shell. MCP tools register here as async callables by name; a real MCP
client that discovers and registers them lands with connected apps.
"""

from __future__ import annotations

import asyncio
import os
import inspect
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.providers.interface import ToolCall, ToolSpec

ToolFn = Callable[..., Awaitable[str] | str]

# (bot_id, session_id) of the call a tool is serving: the task loop sets it around each tool run, so a built-in
# tool that writes memory or opens an approval can attribute the work without the model passing ids
current_call: ContextVar[tuple[str, str]] = ContextVar("current_call", default=("", ""))

OUTPUT_CAP = 20_000


@dataclass
class ToolResult:
    name: str
    output: str
    ok: bool = True
    error: str | None = None

    def as_content(self) -> str:
        if self.ok:
            return self.output
        return f"error: {self.error}\n{self.output}".strip()


@dataclass
class _Tool:
    spec: ToolSpec
    fn: ToolFn
    group: str | None = None  # None: built in; else the MCP server that owns it
    room_only: bool = False  # offered only to a room turn's session


SECRET_ENV_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
SECRET_ENV_PREFIXES = ("VAULT_", "ZEN_GUI_KEY")


def scrubbed_env() -> dict[str, str]:
    """The environment a shell child sees: provider keys, vault tokens, and
    the GUI-entered key are dropped, so a model cannot read them back."""
    return {k: v for k, v in os.environ.items()
            if not (k.upper().endswith(SECRET_ENV_SUFFIXES) or k.upper().startswith(SECRET_ENV_PREFIXES))}


class ToolRunner:
    def __init__(self, cwd: Path | None = None, timeout: float = 60.0) -> None:
        self.cwd = Path(cwd or ".").resolve()
        self.timeout = timeout
        self._tools: dict[str, _Tool] = {}
        self.register(
            "shell",
            f"Run a shell command on the local machine and return its output. It starts in your working folder, "
            f"{self.cwd}: save files you make there (or elsewhere in the vault). A command that may write outside "
            "the folders you may use waits for the user's approval.",
            {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            self._shell,
        )

    def register(self, name: str, description: str, parameters: dict[str, Any], fn: ToolFn, group: str | None = None,
                 room_only: bool = False) -> None:
        self._tools[name] = _Tool(ToolSpec(name, description, parameters), fn, group, room_only)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def names(self) -> list[str]:
        return list(self._tools)

    def group_of(self, name: str) -> str | None:
        """The group (MCP server) a registered tool belongs to; None for a built-in or unknown tool."""
        tool = self._tools.get(name)
        return tool.group if tool else None

    def fn(self, name: str) -> ToolFn | None:
        """The function behind a registered tool, so one built-in tool can call another."""
        tool = self._tools.get(name)
        return tool.fn if tool else None

    def specs(self, groups: set[str] | None = None, room: bool = False) -> list[ToolSpec]:
        """Built-in tools always; grouped (MCP) tools only when their group is in `groups` (None means every
        group); room-only tools only when `room` is true."""
        return [t.spec for t in self._tools.values()
                if (groups is None or t.group is None or t.group in groups) and (room or not t.room_only)]

    async def run(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(call.name, "", ok=False, error=f"unknown tool {call.name!r}")
        # bad arguments only when the call does not fit the signature: a TypeError raised inside the tool is a bug
        # in the tool, and calling it "bad arguments" sends the model off retrying arguments that were fine
        try:
            inspect.signature(tool.fn).bind(**call.arguments)
        except TypeError as e:
            return ToolResult(call.name, "", ok=False, error=f"bad arguments: {e}")
        except ValueError:
            pass  # no readable signature: let the call itself say
        try:
            out = tool.fn(**call.arguments)
            if inspect.isawaitable(out):
                out = await out
            text = str(out)
            if len(text) > OUTPUT_CAP:
                text = text[:OUTPUT_CAP] + f"\n[truncated {len(text) - OUTPUT_CAP} chars]"
            return ToolResult(call.name, text)
        except asyncio.TimeoutError:
            return ToolResult(call.name, "", ok=False, error=f"timed out after {self.timeout}s")
        except Exception as e:  # noqa: BLE001 - tool failures are data for the model
            return ToolResult(call.name, "", ok=False, error=f"{type(e).__name__}: {e}")

    async def _shell(self, command: str) -> str:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=self.cwd,
            env=scrubbed_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        text = out.decode("utf-8", errors="replace")
        if proc.returncode:
            text += f"\n[exit {proc.returncode}]"
        return text

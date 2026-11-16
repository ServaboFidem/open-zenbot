"""Claude Code and Codex as agents the harness can hand work to: where they are, whether they are signed in
and can be confined, and the switches (on, ask, model) saved in vault-settings/agents.json. Running them is
elsewhere; this module only detects and records."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.vault_settings import settings_dir

from . import tool_paths
from .tools import scrubbed_env

FILE = "agents.json"  # in the settings folder, never the vault: a bot that could write it could switch itself on
MODEL = re.compile(r"[A-Za-z0-9._-]{1,64}")


@dataclass(frozen=True)
class AgentCli:
    key: str
    program: tool_paths.Program
    vendor: str
    auth_args: tuple[str, ...]   # prints the sign-in state
    help_args: tuple[str, ...]   # prints the options this version has
    help_needs: tuple[str, ...]  # options a version must list for the harness to confine a run
    sign_in: str                 # the command the user runs in a terminal


AGENTS: dict[str, AgentCli] = {
    "claude-code": AgentCli("claude-code", tool_paths.CLAUDE_CODE, "Anthropic", ("auth", "status"), ("--help",),
                            ("--restricted", "--strict-mcp-config"), "claude"),
    "codex": AgentCli("codex", tool_paths.CODEX, "OpenAI", ("login", "status"), ("exec", "--help"),
                      ("--sandbox",), "codex login"),
}

# Non-empty: why this agent can be found but not run yet. Codex: its sandbox left direct network connections open
# (docs/superpowers/specs/2026-11-16-codex-confinement-notes.md).
RUNNABLE: dict[str, str] = {
    "claude-code": "",
    "codex": "Codex is detected only: in testing on Windows its sandbox did not switch off network access, so a run "
             "could send files out.",
}

_DROPPED = ("ANTHROPIC_", "OPENAI_", "CLAUDE_CODE_", "CODEX_")
_KEPT = ("CLAUDE_CONFIG_DIR", "CODEX_HOME")  # compared upper-case, like the prefixes


def run_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment a CLI child gets: the scrubbed one, minus every vendor variable (keys, base URLs, tokens),
    so only its own sign-in (CLAUDE_CONFIG_DIR, CODEX_HOME) decides who it is."""
    env = dict(scrubbed_env() if base is None else base)
    return {k: v for k, v in env.items() if k.upper() in _KEPT or not k.upper().startswith(_DROPPED)}


def read_auth(key: str, output: str | None) -> str:
    """`subscription`, `api_key`, `none` or `unknown` from what the sign-in command printed."""
    if not output:
        return "unknown"
    if key == "claude-code":
        start, end = output.find("{"), output.rfind("}")
        try:
            data = json.loads(output[start:end + 1]) if 0 <= start < end else None
        except ValueError:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("loggedIn"), bool):
            return "unknown"
        if not data["loggedIn"]:
            return "none"
        return "subscription" if data.get("authMethod") == "claude.ai" else "api_key"
    if key == "codex":
        if "Logged in using ChatGPT" in output:
            return "subscription"
        if "Logged in using" in output:
            return "api_key"
        if "Not logged in" in output:
            return "none"
    return "unknown"


# -- the switches -------------------------------------------------------------------------------------------

@dataclass
class AgentSettings:
    enabled: bool = False
    ask: bool = True
    model: str = ""


def _read(vault_root: Path) -> dict[str, Any]:
    try:
        data = json.loads((settings_dir(vault_root) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_settings(vault_root: Path) -> dict[str, AgentSettings]:
    data = _read(vault_root)
    out: dict[str, AgentSettings] = {}
    for key in AGENTS:
        e = data.get(key)
        e = e if isinstance(e, dict) else {}
        model = e.get("model")
        out[key] = AgentSettings(
            enabled=e["enabled"] if isinstance(e.get("enabled"), bool) else False,
            ask=e["ask"] if isinstance(e.get("ask"), bool) else True,
            model=model if isinstance(model, str) and MODEL.fullmatch(model) else "")
    return out


def save_settings(vault_root: Path, key: str, settings: AgentSettings) -> None:
    """Save one agent's switches, keeping the other agent's. A model name must fully match MODEL (it ends up on
    a command line)."""
    if key not in AGENTS:
        raise ValueError(f"unknown agent {key}")
    if settings.model and not MODEL.fullmatch(settings.model):
        raise ValueError("A model name may hold only letters, digits, dots, dashes and underscores (64 at most).")
    target = settings_dir(vault_root) / FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    data = _read(vault_root)
    data[key] = asdict(settings)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(target)


# -- probing ------------------------------------------------------------------------------------------------

def _run(path: str, args: tuple[str, ...], timeout: float) -> str | None:
    """Like host_deps._run_version, but with the vendor-free environment. Output of both streams, or None."""
    try:
        r = subprocess.run([path, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                           env=run_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout or "") + "\n" + (r.stderr or "")


def probe(vault_root: Path, key: str, timeout: float = 10.0) -> dict[str, Any]:
    agent = AGENTS[key]
    st = tool_paths.status(vault_root, key)
    path, name = st["path"], agent.program.name
    found = bool(path) and bool(st["ok"])
    confinable, auth = False, "unknown"
    if found:
        out = _run(path, agent.help_args, timeout)
        confinable = out is not None and all(n in out for n in agent.help_needs)
        auth = read_auth(key, _run(path, agent.auth_args, timeout))
    why_not_run = RUNNABLE.get(key, "")
    sw = load_settings(vault_root)[key]
    if not path:
        reason = f"{name} was not found. Install it, or save its path in Settings > Harness Tools."
    elif not st["ok"]:
        reason = st["problem"] or f"{name} did not answer its version check."
    elif not confinable:
        reason = f"This version of {name} cannot be confined; update it."
    elif why_not_run:
        reason = why_not_run
    elif auth == "none":
        reason = f"{name} is not signed in. Run `{agent.sign_in}` in a terminal."
    elif auth == "unknown":
        reason = f"The harness could not tell whether {name} is signed in. Run `{agent.sign_in}` in a terminal to check."
    else:
        reason = ""
    return {"id": key, "name": name, "vendor": agent.vendor, "path": path, "source": st["source"],
            "version": st["version"], "auth": auth, "confinable": confinable, "runnable": not why_not_run,
            "enabled": sw.enabled, "ask": sw.ask, "model": sw.model, "usable": not reason, "reason": reason,
            "sign_in": agent.sign_in}


class Agents:
    """The last probe of each agent, and which are on."""

    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root
        self.statuses: dict[str, dict[str, Any]] = {}

    async def refresh(self, key: str | None = None) -> list[dict[str, Any]]:
        """Probe in a thread (the programs may be slow); all agents, or just `key`."""
        keys = [key] if key else list(AGENTS)
        # looked up here, not bound at import, so a test can swap `probe`
        for k, st in zip(keys, await asyncio.gather(*(asyncio.to_thread(probe, self.vault_root, k) for k in keys))):
            self.statuses[k] = st
        return [self.statuses[k] for k in AGENTS if k in self.statuses]

    def settings(self, key: str) -> AgentSettings:
        return load_settings(self.vault_root)[key]

    def on(self) -> list[str]:
        sw = load_settings(self.vault_root)
        return [k for k in AGENTS if sw[k].enabled and self.statuses.get(k, {}).get("usable")]

"""MCP client. Servers named in `mcp.json` connect when the harness starts.

Each server's tools register in the tool runner as `<server>.<tool>` with
the server's own description and input schema, grouped by server so the
loop can offer only the servers the decider routed to. The server itself
enters the catalog as one `mcp` entry (its `use` text from the config, or
the tool descriptions when the config has none), which is what the decider
scores. A server that fails to start is reported in `status()` and skipped;
the others still connect. A server whose `requires` (host binaries, see
`host_deps`) are unmet is held back the same way, in state `needs_install`,
and is probed again when it reconnects.

Config, the shape Claude Desktop and Cursor use. `${VAR}` in a url,
header, or env value expands from the harness environment; a header or
env entry whose variable is unset is dropped, so a keyed server degrades
to keyless access instead of sending an empty credential:

    {"mcpServers": {
        "files":  {"command": "npx", "args": ["-y", "@x/server"], "env": {"ROOT": "."}, "use": "Read local files."},
        "remote": {"url": "http://127.0.0.1:9000/mcp", "headers": {"Authorization": "Bearer ..."}}
    }}
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.skills.catalog import Catalog, CatalogEntry

from .host_deps import Requirement, check, current_host, parse_requires
from .tools import ToolRunner, scrubbed_env

DEFAULT_TIMEOUT = 60.0

# A gate on one server's calls: (tool, arguments, call) -> result. `call(tool, arguments)` reaches the server
# itself; the gate raises to refuse. Example: magick_guard.MagickGuard.
Guard = Callable[[str, dict[str, Any], Callable[[str, dict[str, Any]], Awaitable[str]]], Awaitable[str]]


@dataclass
class ServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    use: str = ""
    timeout: float = DEFAULT_TIMEOUT
    keys: list[str] = field(default_factory=list)  # the ${VAR}s its env, headers and url name: what the page asks for
    requires: list[Requirement] = field(default_factory=list)  # host binaries it needs before it is spawned
    arg_vars: list[str] = field(default_factory=list)  # the ${VAR}s its args name: a change to one reconnects it
    guard: str = ""  # the harness gate every call goes through first (McpRegistry guards); none named: none run

    @property
    def transport(self) -> str:
        return "http" if self.url else "stdio"


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# What the harness hands mcp.json: a string, or a list for a variable that is a whole arg on its own
# (`${ZEN_ALLOWED_FOLDERS}`: one arg per folder). A list anywhere else counts as unset.
Vars = Mapping[str, "str | list[str]"]


def _expand(value: str, vars: Vars | None = None) -> str | None:
    """`${VAR}` -> its value, from `vars` first and the environment second;
    None when any referenced variable is unset."""
    missing = False

    def sub(m: re.Match) -> str:
        nonlocal missing
        v = (vars or {}).get(m.group(1), os.environ.get(m.group(1)))
        if not isinstance(v, str):
            missing = True
            return ""
        return v
    out = _VAR.sub(sub, value)
    return None if missing else out


def _expand_map(d: dict, vars: Vars | None = None) -> dict[str, str]:
    out = {}
    for k, v in (d or {}).items():
        e = _expand(str(v), vars)
        if e is not None:
            out[str(k)] = e
    return out


def _expand_list(items: list, vars: Vars | None = None) -> list[str]:
    out = []
    for a in items or []:
        whole = _VAR.fullmatch(str(a))
        many = (vars or {}).get(whole.group(1)) if whole else None
        if isinstance(many, list):  # a list variable that is the whole arg: one arg per item
            out.extend(str(x) for x in many)
            continue
        e = _expand(str(a), vars)
        if e is not None:  # an arg naming an unset variable is left out, like a header
            out.append(e)
    return out


def arg_vars(spec: dict) -> list[str]:
    """The ${VAR}s a server's args name, in order, once."""
    out: list[str] = []
    for a in spec.get("args") or []:
        for m in _VAR.finditer(str(a)):
            if m.group(1) not in out:
                out.append(m.group(1))
    return out


def key_vars(spec: dict, vars: Vars | None = None) -> list[str]:
    """The keys a server takes: each `${VAR}` its env, headers and url name, in order, once. Args hold
    paths, not keys, and a variable the harness hands in (`vars`) is never the user's to enter."""
    texts = [*map(str, (spec.get("env") or {}).values()), *map(str, (spec.get("headers") or {}).values()), str(spec.get("url") or "")]
    out: list[str] = []
    for t in texts:
        for m in _VAR.finditer(t):
            if m.group(1) not in out and m.group(1) not in (vars or {}):
                out.append(m.group(1))
    return out


def load_config(path: Path, vars: Vars | None = None,
                saved: dict[str, dict[str, str]] | None = None) -> list[ServerConfig]:
    """`mcp.json` -> server configs. A missing file is no servers; a broken
    one is an error. `${VAR}` in args, env, url, and headers expands from
    `vars` (what the harness knows: `ZEN_VAULT_ROOT`, and `ZEN_ALLOWED_FOLDERS`,
    a list that becomes one arg per folder), then the keys saved on the MCP
    servers page for that server (`saved`), then the environment."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: {e}") from None
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: expected a top-level mcpServers object")
    out: list[ServerConfig] = []
    for name, spec in servers.items():
        if name.startswith("_") or not isinstance(spec, dict):
            continue  # "_comment" style keys are allowed
        if not spec.get("command") and not spec.get("url"):
            raise ValueError(f"{path}: server {name!r} needs a command or a url")
        keys = key_vars(spec, vars)
        v: dict[str, str | list[str]] = {**{k: val for k, val in ((saved or {}).get(name) or {}).items() if k in keys}, **(vars or {})}
        out.append(ServerConfig(
            name=name, command=spec.get("command"), args=_expand_list(spec.get("args", []), v),
            env=_expand_map(spec.get("env"), v), url=_expand(spec["url"], v) if spec.get("url") else None,
            headers=_expand_map(spec.get("headers"), v),
            use=str(spec.get("use", "")), timeout=float(spec.get("timeout", DEFAULT_TIMEOUT)), keys=keys,
            requires=parse_requires(spec.get("requires"), f"{path}: server {name!r}"), arg_vars=arg_vars(spec),
            guard=str(spec.get("guard") or ""),
        ))
    return out


@dataclass
class _Server:
    config: ServerConfig
    state: str = "pending"  # pending | connected | failed | needs_install | closed
    error: str | None = None
    tools: list[str] = field(default_factory=list)
    use: str = ""  # the one line the decider scores; config's, else built from the tool descriptions
    session: Any = None
    stack: AsyncExitStack | None = None
    deps: list[dict] = field(default_factory=list)  # host_deps.check results for config.requires


def _text_of(result: Any) -> str:
    """The text a model gets back: text blocks joined, else the structured content as JSON."""
    parts = [c.text for c in getattr(result, "content", []) or [] if getattr(c, "type", "") == "text"]
    if parts:
        return "\n".join(parts)
    structured = getattr(result, "structured_content", None)
    return json.dumps(structured) if structured is not None else ""


class McpRegistry:
    kind = "mcp"

    def __init__(self, servers: list[ServerConfig], guards: Mapping[str, Guard] | None = None,
                 known_bins: Callable[[], dict[str, str]] | None = None) -> None:
        self._servers: dict[str, _Server] = {s.name: _Server(s) for s in servers}
        self._tools: ToolRunner | None = None
        self._catalog: Catalog | None = None
        self.guards: dict[str, Guard] = dict(guards or {})  # mcp.json "guard": name -> the gate its calls go through
        self.known_bins = known_bins  # bin -> full path saved in Settings > Programs; `requires` probes try it first

    # -- lifecycle --------------------------------------------------------

    async def connect(self, tools: ToolRunner, catalog: Catalog) -> None:
        """Connect every server; failures are recorded, not raised."""
        self._tools, self._catalog = tools, catalog
        for s in self._servers.values():
            await self._connect_safely(s)

    async def reconnect(self, config: ServerConfig) -> None:
        """Swap one server for a new config (a key saved on the MCP servers page): close it, drop its
        tools and catalog entry, and connect it again. The other servers are not touched."""
        old = self._servers.get(config.name)
        if old is not None:
            await self._close_one(old)
            if self._catalog is not None:
                self._catalog.remove_mcp(config.name)
        s = self._servers[config.name] = _Server(config)
        if self._tools is not None:
            await self._connect_safely(s)

    async def _connect_safely(self, s: _Server) -> None:
        try:
            if s.config.requires:
                child_path = s.config.env.get("PATH")
                host = current_host(path=child_path) if child_path else current_host()
                if self.known_bins is not None:
                    host.known = self.known_bins()
                s.deps = await asyncio.to_thread(check, s.config.requires, host)
                unmet = next((d for d in s.deps if not d["ok"]), None)
                if unmet is not None:
                    s.state = "needs_install"
                    s.error = f"{unmet['bin']}: {unmet['problem'].replace('_', ' ')}" + (f" — {unmet['fix']}" if unmet["fix"] else "")
                    return
            await self._connect_one(s)
        except Exception as e:  # noqa: BLE001 - one bad server must not stop the rest
            s.state, s.error = "failed", f"{type(e).__name__}: {e}"[:500]
            if s.stack is not None:
                try:
                    await s.stack.aclose()
                except Exception:  # noqa: BLE001
                    pass
                s.stack = s.session = None

    async def _connect_one(self, s: _Server) -> None:
        from mcp.client.session import ClientSession

        c = s.config
        s.stack = AsyncExitStack()
        await s.stack.__aenter__()
        if c.url:
            from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

            client = create_mcp_http_client(headers=c.headers or None)
            streams = await s.stack.enter_async_context(streamable_http_client(c.url, http_client=client))
            read, write = streams[0], streams[1]
        else:
            from mcp.client.stdio import StdioServerParameters, stdio_client

            assert c.command is not None
            params = StdioServerParameters(command=c.command, args=c.args, env={**scrubbed_env(), **c.env})
            read, write = await s.stack.enter_async_context(stdio_client(params))
        session = await s.stack.enter_async_context(ClientSession(read, write, read_timeout_seconds=c.timeout))
        await asyncio.wait_for(session.initialize(), c.timeout)
        listed = (await session.list_tools()).tools
        s.session = session
        s.tools = sorted(t.name for t in listed)
        s.state, s.error = "connected", None
        assert self._tools is not None and self._catalog is not None
        for t in listed:
            self._tools.register(f"{c.name}.{t.name}", t.description or t.name, t.input_schema or {"type": "object", "properties": {}},
                                 self._caller(c.name, t.name), group=c.name)
        s.use = c.use or "; ".join(f"{t.name}: {t.description}" for t in listed if t.description) or f"Tools from the {c.name} MCP server."
        self._catalog.add_mcp(CatalogEntry(kind="mcp", name=c.name, use=s.use, inputs=list(s.tools), ref=c.name))

    def _caller(self, server: str, tool: str):
        async def call(**arguments: Any) -> str:
            return await self.call(server, tool, arguments)
        return call

    async def call(self, server: str, tool: str, arguments: dict[str, Any] | None = None) -> str:
        """Call one tool. A server whose config names a guard goes through it first; the guard may refuse
        (it raises) or make calls of its own on the same server. A named guard the harness lacks refuses all."""
        s = self._servers.get(server)
        if s is None or s.session is None:
            raise RuntimeError(f"MCP server {server!r} is not connected")
        if s.config.guard:
            gate = self.guards.get(s.config.guard)
            if gate is None:
                raise RuntimeError(f"{server}.{tool}: the harness has no {s.config.guard!r} guard, so it runs none of this server's tools")
            return await gate(tool, dict(arguments or {}), lambda t, a: self._call_raw(server, t, a))
        return await self._call_raw(server, tool, arguments or {})

    async def _call_raw(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        s = self._servers.get(server)
        if s is None or s.session is None:
            raise RuntimeError(f"MCP server {server!r} is not connected")
        result = await s.session.call_tool(tool, arguments)
        if getattr(result, "is_error", False):
            raise RuntimeError(_text_of(result) or f"{server}.{tool} returned an error")
        return _text_of(result)

    async def close(self) -> None:
        for s in self._servers.values():
            await self._close_one(s)

    async def _close_one(self, s: _Server) -> None:
        if self._tools is not None:
            for t in s.tools:
                self._tools.unregister(f"{s.config.name}.{t}")
        if s.stack is not None:
            try:
                await s.stack.aclose()
            except Exception:  # noqa: BLE001 - a server that died mid-close is still closed
                pass
        s.stack = s.session = None
        if s.state == "connected":
            s.state = "closed"

    # -- introspection ----------------------------------------------------

    def connected(self) -> list[str]:
        return [n for n, s in self._servers.items() if s.state == "connected"]

    def has(self, server: str) -> bool:
        s = self._servers.get(server)
        return s is not None and s.state == "connected"

    def status(self) -> dict[str, dict[str, Any]]:
        return {
            n: {"state": s.state, "transport": s.config.transport, "tools": list(s.tools), "error": s.error, "use": s.use or s.config.use,
                "key_vars": list(s.config.keys), "deps": list(s.deps),
                "target": s.config.url or " ".join([s.config.command or "", *s.config.args]).strip()}
            for n, s in self._servers.items()
        }

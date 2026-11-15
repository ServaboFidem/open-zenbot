"""API keys for MCP servers, entered on the MCP servers page. Kept in `vault-settings/mcp_keys.json`
(owner-only: it holds secrets), by server and variable name:

    {"servers": {"pexels": {"PEXELS_API_KEY": "..."}, "toolsmonk": {"TOOLSMONK_API_KEY": "..."}}}

A server's keys are the `${VAR}`s its `mcp.json` entry names in `env`, `headers` or `url` (not in
`args`, which hold paths, and never a variable the harness supplies itself, like `ZEN_VAULT_ROOT`).
A key saved here wins over `.env` for that server only; clearing it hands the variable back to `.env`.
The page never gets a key back, only where it comes from and its last four characters."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.vault_settings import settings_dir

FILE = "mcp_keys.json"


@dataclass
class McpKeys:
    servers: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, vault_root: Path) -> "McpKeys":
        try:
            raw = json.loads((settings_dir(vault_root) / FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        servers = raw.get("servers") if isinstance(raw, dict) else None
        if not isinstance(servers, dict):
            return cls()
        out: dict[str, dict[str, str]] = {}
        for name, keys in servers.items():
            if isinstance(keys, dict):
                kept = {str(k): v.strip() for k, v in keys.items() if isinstance(v, str) and v.strip()}
                if kept:
                    out[str(name)] = kept
        return cls(servers=out)

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner only: it holds secret keys
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"servers": {n: k for n, k in self.servers.items() if k}}, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def for_server(self, server: str) -> dict[str, str]:
        return dict(self.servers.get(server, {}))

    def with_keys(self, server: str, values: Mapping[str, str]) -> "McpKeys":
        """Set these keys on one server; a blank value keeps what is saved, since the page never holds it."""
        servers = {n: dict(k) for n, k in self.servers.items()}
        cur = servers.setdefault(server, {})
        for var, value in values.items():
            if (value or "").strip():
                cur[var] = value.strip()
        return McpKeys(servers={n: k for n, k in servers.items() if k})

    def without_key(self, server: str, var: str) -> "McpKeys":
        servers = {n: {k: v for k, v in keys.items() if not (n == server and k == var)} for n, keys in self.servers.items()}
        return McpKeys(servers={n: k for n, k in servers.items() if k})


def hint(value: str) -> str:
    """Enough to tell keys apart, never enough to use one: the last four characters of a long key."""
    return value[-4:] if len(value) >= 8 else ("set" if value else "")


def public(key_vars: Iterable[str], saved: Mapping[str, str] | None, env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """One row per key a server takes: where its value comes from (saved, .env, or nowhere) and its hint."""
    env = os.environ if env is None else env
    saved = saved or {}
    out = []
    for var in key_vars:
        if saved.get(var):
            out.append({"name": var, "source": "saved", "hint": hint(saved[var])})
        elif env.get(var):
            out.append({"name": var, "source": ".env", "hint": hint(env[var])})
        else:
            out.append({"name": var, "source": "", "hint": ""})
    return out

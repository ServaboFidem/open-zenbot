"""Tracing settings from Settings > Tracing: the Sentry (GlitchTip) DSN and Langfuse's keys, server and
environment. Kept in `vault-settings/tracing.json` (owner-only: it holds a secret key).

A saved section wins over `.env`; a section that is not saved leaves `.env` in charge, so clearing one
hands it back. The desktop app reads the same file's DSN at launch (src/electron/tracing-settings.cjs)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.vault_settings import settings_dir

FILE = "tracing.json"
DSN_ENV = "ZEN_GLITCHTIP_DSN"
LANGFUSE_FIELDS = ("public_key", "secret_key", "base_url", "environment")
LANGFUSE_ENV = {"public_key": "LANGFUSE_PUBLIC_KEY", "secret_key": "LANGFUSE_SECRET_KEY",
                "base_url": "LANGFUSE_BASE_URL", "environment": "LANGFUSE_TRACING_ENVIRONMENT"}


@dataclass
class TracingSettings:
    sentry: dict[str, str] | None = None  # {"dsn"}; "" saved means off
    langfuse: dict[str, str] | None = None  # LANGFUSE_FIELDS
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, vault_root: Path) -> "TracingSettings":
        try:
            raw = json.loads((settings_dir(vault_root) / FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        s, lf = raw.get("sentry"), raw.get("langfuse")
        return cls(
            sentry={"dsn": str(s.get("dsn") or "").strip()} if isinstance(s, dict) else None,
            langfuse={k: str(lf.get(k) or "").strip() for k in LANGFUSE_FIELDS} if isinstance(lf, dict) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.sentry is not None:
            out["sentry"] = self.sentry
        if self.langfuse is not None:
            out["langfuse"] = self.langfuse
        return out

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner only: it holds a secret key
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def with_langfuse(self, values: Mapping[str, str | None], *, current_secret: str = "") -> "TracingSettings":
        """A Langfuse save: a blank secret keeps the one in force (saved, else .env), since the page never holds it."""
        new = {k: (values.get(k) or "").strip() for k in LANGFUSE_FIELDS}
        if not new["secret_key"]:
            new["secret_key"] = (self.langfuse or {}).get("secret_key", "") or current_secret
        return TracingSettings(sentry=self.sentry, langfuse=new)

    def with_sentry(self, dsn: str | None) -> "TracingSettings":
        """None clears the saved DSN (back to .env); "" saves Sentry as off."""
        return TracingSettings(sentry=None if dsn is None else {"dsn": dsn.strip()}, langfuse=self.langfuse)

    def without_langfuse(self) -> "TracingSettings":
        return TracingSettings(sentry=self.sentry, langfuse=None)


def effective(vault_root: Path, env: Mapping[str, str] | None = None) -> dict[str, dict[str, str]]:
    """What is in force for each: saved values over the environment, with where they came from."""
    env = os.environ if env is None else env
    saved = TracingSettings.load(vault_root)
    if saved.sentry is not None:
        sentry = {"dsn": saved.sentry["dsn"], "source": "saved"}
    else:
        dsn = (env.get(DSN_ENV) or "").strip()
        sentry = {"dsn": dsn, "source": ".env" if dsn else "off"}
    if saved.langfuse is not None:
        lf = {**saved.langfuse, "source": "saved"}
    else:
        lf = {k: (env.get(name) or "").strip() for k, name in LANGFUSE_ENV.items()}
        if not lf["base_url"]:
            lf["base_url"] = (env.get("LANGFUSE_HOST") or "").strip()
        lf["source"] = ".env" if lf["public_key"] and lf["secret_key"] else "off"
    return {"sentry": sentry, "langfuse": lf}


def langfuse_env(lf: Mapping[str, str]) -> dict[str, str]:
    """The LANGFUSE_* mapping `tracing.init_langfuse` reads."""
    return {name: lf.get(k, "") for k, name in LANGFUSE_ENV.items()}


def public(eff: Mapping[str, Mapping[str, str]], *, sentry_on: bool, langfuse_on: bool) -> dict[str, Any]:
    """For the page: never the secret key, only its last four characters."""
    lf = dict(eff["langfuse"])
    secret = lf.pop("secret_key", "")
    lf["secret_key_hint"] = secret[-4:] if len(secret) >= 8 else ("set" if secret else "")
    return {"sentry": {**eff["sentry"], "live": sentry_on}, "langfuse": {**lf, "live": langfuse_on}}

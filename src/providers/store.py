"""The model providers the harness can use, kept in `vault-settings/providers.json`.

Each provider has an id, a name, a kind (`openrouter` or `openai-compatible`), a base URL, its key,
and the models enabled on it. The key is either stored in the file (pasted in Settings) or named by a
reference (`env:NAME` or `vault:NAME`) whose value stays out of the file. The file is written owner-read
only, and nothing that reads it through the API ever gets a key back: `public()` says only whether a
key is set and where it comes from.

A stored key reaches the rest of the harness as an environment reference: it is put in this process's
environment as `ZEN_PROVIDER_<ID>_API_KEY` (a name the shell tool's scrubbed environment drops) and
registered with the exclusion filter, so the ledger redacts it if it ever appears in text.

Each enabled model keeps a snapshot of what the provider said about it (kinds, reasoning, cost), so the
Models page's pickers work without fetching the provider's whole list.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.memory.exclusion import exclusion
from src.vault_settings import settings_dir

from .models import ModelInfo
from .secrets import Secret, SecretNotFound, resolve_secret

FILE = "providers.json"
KINDS = ("openrouter", "openai-compatible")
OPENROUTER_BASE = "https://openrouter.ai/api"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")  # Tailscale assigns peers addresses from the CGNAT range
FREE_HOSTS = {"integrate.api.nvidia.com"}  # NVIDIA's hosted NIM endpoints: free to call with a build.nvidia.com key
FREE = "Free"  # the cost shown for a model on a free provider that reports no price
REF = re.compile(r"^(env|vault):[A-Za-z_][A-Za-z0-9_]*$")

# Ready-made providers for the add form: it fills in the name and base URL (and whether a key is needed).
# The local servers' URLs are their usual defaults on this machine; change them to where yours runs.
PRESETS: list[dict[str, Any]] = [
    {"id": "nvidia-nim", "name": "NVIDIA NIM", "kind": "openai-compatible", "base_url": "https://integrate.api.nvidia.com/v1",
     "no_key": False, "local": False, "note": "Hosted NIM endpoints. Get a key at build.nvidia.com."},
    {"id": "vllm", "name": "vLLM", "kind": "openai-compatible", "base_url": "http://localhost:8000/v1",
     "no_key": True, "local": True, "note": "Example URL: vLLM's OpenAI-compatible server on its default port. Needs a key only if you started it with --api-key."},
    {"id": "lmstudio", "name": "LM Studio", "kind": "openai-compatible", "base_url": "http://localhost:1234/v1",
     "no_key": True, "local": True, "note": "Example URL: LM Studio's local server on its default port."},
    {"id": "ollama", "name": "Ollama", "kind": "openai-compatible", "base_url": "http://localhost:11434/v1",
     "no_key": True, "local": True, "note": "Example URL: Ollama's OpenAI-compatible endpoint on its default port."},
]


class ProviderStoreError(ValueError):
    """A request the store refuses; the message is safe to show (it never holds a key)."""


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "provider"


def _env_name(provider_id: str) -> str:
    return f"ZEN_PROVIDER_{re.sub(r'[^A-Z0-9]', '_', provider_id.upper())}_API_KEY"


def check_base_url(kind: str, base_url: str | None) -> str:
    if kind == "openrouter":
        return (base_url or OPENROUTER_BASE).rstrip("/")
    url = (base_url or "").strip().rstrip("/")
    parts = urlsplit(url)
    if not parts.hostname or parts.scheme not in ("https", "http") or (parts.scheme == "http" and not _nearby(parts.hostname)):
        raise ProviderStoreError("base URL must be an https address, like https://api.groq.com/openai/v1 "
                                 "(plain http only for a server on this machine, your local network or your tailnet)")
    return url


def _nearby(host: str) -> bool:
    """This machine, the local network or the Tailscale tailnet: where plain http to a self-hosted server is
    reasonable. Tailnet traffic is WireGuard-encrypted end to end, so http to a peer does not cross the internet
    in the clear. A peer is a MagicDNS name (`<machine>.<tailnet>.ts.net`; a bare `<machine>` already passes as a
    single label) or a Tailscale address: 100.64.0.0/10 (not private to Python, it is the carrier-grade NAT range)
    or fd7a:115c:a1e0::/48 (unique-local, so already private)."""
    host = host.rstrip(".")
    if host in LOOPBACK or host.endswith((".local", ".ts.net")) or "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip in TAILSCALE_V4


@dataclass
class ProviderEntry:
    id: str
    name: str
    kind: str
    base_url: str
    key: str | None = None  # stored in the file; never sent back through the API
    key_ref: str | None = None  # or a reference whose value lives elsewhere
    models: list[ModelInfo] = field(default_factory=list)  # the enabled ones, with what the provider said about each
    no_key: bool = False  # a server that needs no key (LM Studio, Ollama, a local vLLM); requests go without one
    unusable: list[str] = field(default_factory=list)  # listed, but a test found this key can't run them (HTTP 404); hidden

    def to_file(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind, "base_url": self.base_url, "key": self.key,
                "key_ref": self.key_ref, "no_key": self.no_key, "models": [m.to_dict() for m in self.models], "unusable": self.unusable}

    @classmethod
    def from_file(cls, d: dict[str, Any]) -> "ProviderEntry":
        return cls(id=str(d["id"]), name=str(d.get("name") or d["id"]), kind=str(d["kind"]), base_url=str(d.get("base_url") or ""),
                   key=d.get("key") or None, key_ref=d.get("key_ref") or None, no_key=d.get("no_key") is True,
                   models=[ModelInfo.from_dict(m) for m in d.get("models") or [] if isinstance(m, dict) and m.get("id")],
                   unusable=[m for m in d.get("unusable") or [] if isinstance(m, str)])

    def model(self, model_id: str) -> ModelInfo | None:
        return next((m for m in self.models if m.id == model_id), None)

    def public(self) -> dict[str, Any]:
        source = "stored" if self.key else (self.key_ref or None)
        return {"id": self.id, "name": self.name, "kind": self.kind, "base_url": self.base_url, "no_key": self.no_key,
                "key_set": self.key_set(), "key_source": source, "usable": self.usable(), "models": [self.model_out(m) for m in self.models],
                "unusable": self.unusable}

    def free(self) -> bool:
        """Its models cost nothing to run: a server on this machine or the local network, or NVIDIA's hosted NIM."""
        if self.kind == "openrouter":
            return False
        host = urlsplit(self.base_url).hostname or ""
        return host in FREE_HOSTS or _nearby(host)

    def model_out(self, m: ModelInfo) -> dict[str, Any]:
        """A model for the Settings pages; one on a free provider that reports no price costs `FREE`."""
        out = m.public()
        if out.get("cost") is None and self.free():
            out["cost"] = FREE
        return out

    def usable(self) -> bool:
        """It can serve requests: it has a key, or needs none."""
        return self.no_key or self.key_set()

    def key_set(self) -> bool:
        if self.key:
            return True
        if not self.key_ref:
            return False
        try:
            resolve_secret(self.key_ref)
        except (SecretNotFound, ValueError):
            return False
        return True


class ProviderStore:
    def __init__(self, vault_root: Path) -> None:
        self.path = settings_dir(vault_root) / FILE
        self.entries: dict[str, ProviderEntry] = {}
        self.loaded_from_file = False

    # -- file ---------------------------------------------------------------

    def load(self) -> "ProviderStore":
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self
        for d in raw.get("providers") or [] if isinstance(raw, dict) else []:
            try:
                e = ProviderEntry.from_file(d)
            except (KeyError, TypeError):
                continue
            if e.kind in KINDS:
                self.entries[e.id] = e
                self._export_key(e)
        self.loaded_from_file = True
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner only: the file may hold keys
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"providers": [e.to_file() for e in self.entries.values()]}, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def seed(self, key_ref: str | None, models: list[ModelInfo]) -> None:
        """First start with no file: one OpenRouter provider on the key the harness already uses."""
        if self.entries:
            return
        self.entries["openrouter"] = ProviderEntry("openrouter", "OpenRouter", "openrouter", OPENROUTER_BASE,
                                                   key_ref=key_ref if key_ref and REF.match(key_ref) else None, models=models)
        self.save()

    # -- entries ------------------------------------------------------------

    def get(self, provider_id: str) -> ProviderEntry:
        try:
            return self.entries[provider_id]
        except KeyError:
            raise ProviderStoreError(f"no provider {provider_id!r}") from None

    def add(self, name: str, kind: str, base_url: str | None = None, key: str | None = None, key_ref: str | None = None,
            no_key: bool = False) -> ProviderEntry:
        name = (name or "").strip()
        if not name:
            raise ProviderStoreError("name is empty")
        if kind not in KINDS:
            raise ProviderStoreError(f"unknown provider kind {kind!r}; known: {list(KINDS)}")
        pid, n = _slug(name), 2
        while pid in self.entries:
            pid, n = f"{_slug(name)}-{n}", n + 1
        if no_key and kind == "openrouter":
            raise ProviderStoreError("OpenRouter needs a key")
        e = ProviderEntry(pid, name[:80], kind, check_base_url(kind, base_url), no_key=no_key)
        self._set_key(e, key, key_ref)
        self.entries[pid] = e
        self.save()
        return e

    def update(self, provider_id: str, name: str | None = None, base_url: str | None = None, key: str | None = None,
               key_ref: str | None = None, no_key: bool | None = None) -> ProviderEntry:
        e = self.get(provider_id)
        if no_key is not None:
            if no_key and e.kind == "openrouter":
                raise ProviderStoreError("OpenRouter needs a key")
            e.no_key = no_key
        if name is not None:
            if not name.strip():
                raise ProviderStoreError("name is empty")
            e.name = name.strip()[:80]
        if base_url is not None:
            e.base_url = check_base_url(e.kind, base_url)
        if key is not None or key_ref is not None:
            self._set_key(e, key, key_ref)
        self.save()
        return e

    def remove(self, provider_id: str) -> None:
        self.get(provider_id)
        del self.entries[provider_id]
        os.environ.pop(_env_name(provider_id), None)
        self.save()

    def mark_unusable(self, provider_id: str, model_id: str) -> ProviderEntry:
        """The provider lists this model but won't run it for this key: hide it from the list."""
        e = self.get(provider_id)
        if model_id not in e.unusable:
            e.unusable.append(model_id)
            self.save()
        return e

    def set_models(self, provider_id: str, models: list[ModelInfo]) -> ProviderEntry:
        e = self.get(provider_id)
        e.models = list({m.id: m for m in models}.values())
        e.unusable = [u for u in e.unusable if e.model(u) is None]  # one that now works is no longer hidden
        self.save()
        return e

    def refresh_snapshots(self, provider_id: str, listed: list[ModelInfo]) -> None:
        """Bring enabled models' snapshots up to date with a fresh list (prices and names change). A model the
        harness probed keeps what it found: an id-only list knows less about it than the probe did."""
        e = self.entries.get(provider_id)
        if e is None:
            return
        by_id = {m.id: m for m in listed}
        fresh = [m if m.probed_at else by_id.get(m.id, m) for m in e.models]
        if [m.to_dict() for m in fresh] != [m.to_dict() for m in e.models]:
            e.models = fresh
            self.save()

    # -- keys ---------------------------------------------------------------

    def _set_key(self, e: ProviderEntry, key: str | None, key_ref: str | None) -> None:
        key, key_ref = (key or "").strip() or None, (key_ref or "").strip() or None
        if key and key_ref:
            raise ProviderStoreError("send a key or a key reference, not both")
        if key_ref and not REF.match(key_ref):
            raise ProviderStoreError("a key reference looks like env:NAME or vault:NAME")
        if key or key_ref:
            e.key, e.key_ref = key, key_ref
        self._export_key(e)

    def _export_key(self, e: ProviderEntry) -> None:
        name = _env_name(e.id)
        if e.key:
            exclusion.register_secret_value(e.key)  # before anything can log it
            os.environ[name] = e.key
        else:
            os.environ.pop(name, None)

    def key_ref(self, provider_id: str) -> str | None:
        """How the rest of the harness asks for this provider's key: an env:/vault: reference, or None."""
        e = self.get(provider_id)
        return f"env:{_env_name(e.id)}" if e.key else e.key_ref

    def secret(self, provider_id: str) -> Secret | None:
        ref = self.key_ref(provider_id)
        if not ref:
            return None
        try:
            return resolve_secret(ref)
        except (SecretNotFound, ValueError):
            return None

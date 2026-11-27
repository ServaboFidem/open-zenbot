"""Image generation: the `image.generate` tool.

It registers as its own tool group, `image`, and joins the catalog as an `mcp` entry, so the decider
scores it per task the way it scores an MCP server: a bot is offered the tool only when the task
needs a picture. The provider and model come from the environment (the repo's .env):
`ZEN_IMAGE_PROVIDER` (default `openrouter`, the only one today) and `ZEN_IMAGE_MODEL` (default
`tencent/hy-image-v3.5-preview`), and `ZEN_IMAGE_ENABLED` (default on). Settings > Models > Jobs
changes any of the three; its choice is kept in `vault-settings/tools.json` and wins over the
environment from then on. The key is the harness's own provider key, resolved per call, so
binding a key in Settings reaches this tool too. The HTTP call lives in `src/providers/images.py`.

An image lands in `<workspace>/generated-images/` and is attached to the reply through the
`attach_file` tool, so it shows in the conversation like any file a bot made.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.providers.images import PROVIDERS, ImageProvider, image_provider
from src.providers.secrets import SecretNotFound, resolve_secret
from src.skills.catalog import CatalogEntry
from src.vault_settings import settings_dir

GROUP = "image"
TOOL = "image.generate"
DEFAULT_PROVIDER = "openrouter"
DEFAULT_MODEL = "tencent/hy-image-v3.5-preview"
OUT_DIR = "generated-images"
ASPECTS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "4:5", "5:4", "21:9", "9:21")
RESOLUTIONS = ("512", "768", "1K", "1.5K", "2K", "4K")
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/svg+xml": "svg"}

USE = ("Generate a new image from a text description: an illustration, a picture, a logo, an icon, artwork, "
       "a poster, a concept sketch, anything the user wants drawn or made rather than found. "
       "pexels finds existing stock photos; this one makes a new image.")

DESCRIPTION = ("Generate an image from a text prompt and attach it to your reply. Describe the subject, style, "
               "composition, lighting and colours in the prompt; the more concrete, the better.")

SPEC: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "what the image shows, and its style"},
        "aspect_ratio": {"type": "string", "enum": list(ASPECTS), "description": "width:height; defaults to 1:1"},
        "resolution": {"type": "string", "enum": list(RESOLUTIONS), "description": "size tier; defaults to the model's own"},
        "title": {"type": "string", "description": "short label shown under the image"},
    },
    "required": ["prompt"],
}

NO_KEY = "Image generation needs a provider key: bind one in Settings."


NAME = "Image generation"
SUMMARY = "Lets a bot make a new image from a text prompt. The decider offers it only when a task wants a picture."
SETTINGS_FILE = "tools.json"


@dataclass
class ImageConfig:
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    enabled: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "ImageConfig":
        return cls(provider=(env.get("ZEN_IMAGE_PROVIDER") or DEFAULT_PROVIDER).strip().lower(),
                   model=(env.get("ZEN_IMAGE_MODEL") or DEFAULT_MODEL).strip(),
                   enabled=(env.get("ZEN_IMAGE_ENABLED") or "1").strip().lower() not in ("0", "false", "no", "off"))

    @classmethod
    def load(cls, vault_root: Path, env: Mapping[str, str] = os.environ) -> "ImageConfig":
        """The environment's config with the saved Settings choice on top; a broken file is ignored."""
        base = cls.from_env(env)
        try:
            saved = json.loads((settings_dir(vault_root) / SETTINGS_FILE).read_text(encoding="utf-8")).get(GROUP) or {}
        except (OSError, ValueError, AttributeError):
            return base
        if isinstance(saved.get("provider"), str) and saved["provider"].strip():
            base.provider = saved["provider"].strip().lower()
        if isinstance(saved.get("model"), str) and saved["model"].strip():
            base.model = saved["model"].strip()
        if isinstance(saved.get("enabled"), bool):
            base.enabled = saved["enabled"]
        return base

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        data[GROUP] = {"enabled": self.enabled, "provider": self.provider, "model": self.model}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)


def _slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return words[:40].rstrip("-") or "image"


class ImageGenerator:
    """Calls the provider and writes the image into the workspace. `key_ref` is read per call
    (Settings may rebind the key); `attach` is the attach_file tool's function, or None."""

    def __init__(self, config: ImageConfig, workspace: Path, key_ref: Callable[[], str], *,
                 bound: Callable[[], bool] = lambda: True, attach: Callable[..., Any] | None = None,
                 ledger: Any = None, provider: ImageProvider | None = None,
                 provider_kind: Callable[[str], str | None] = lambda provider_id: provider_id, **provider_kwargs: Any) -> None:
        self.config = config
        self.workspace = Path(workspace)
        self.key_ref = key_ref
        self.bound = bound
        self.attach = attach
        self.ledger = ledger
        self._provider_kwargs = provider_kwargs
        self.provider_kind = provider_kind  # a provider id (providers.json) to its kind; only an openrouter one makes images
        self.provider = provider or image_provider(provider_kind(config.provider) or "", **provider_kwargs)

    def reconfigure(self, config: ImageConfig) -> None:
        """Take a new provider, model, or on/off; call `register` after to apply it."""
        if config.provider != self.config.provider or self.provider is None:
            self.provider = image_provider(self.provider_kind(config.provider) or "", **self._provider_kwargs)
        self.config = config

    def status(self, providers: list[str] | None = None) -> dict[str, Any]:
        """`providers`: the provider ids that can make images (the harness passes its OpenRouter ones)."""
        return {"id": GROUP, "name": NAME, "summary": SUMMARY, "tool": TOOL, "enabled": self.config.enabled,
                "provider": self.config.provider, "model": self.config.model,
                "providers": providers if providers is not None else sorted(PROVIDERS),
                "default_provider": DEFAULT_PROVIDER, "default_model": DEFAULT_MODEL, "active": self.active}

    @property
    def active(self) -> bool:
        return self.config.enabled and self.provider is not None

    def register(self, tools: Any, catalog: Any) -> bool:
        """Offer the tool and its catalog entry when on and the provider is known; otherwise take both away."""
        if not self.active:
            tools.unregister(TOOL)
            catalog.remove_mcp(GROUP)
            return False
        tools.register(TOOL, DESCRIPTION, SPEC, self.generate, group=GROUP)
        if not any(e.ref == GROUP for e in catalog.mcps()):
            catalog.add_mcp(CatalogEntry(kind="mcp", name=GROUP, use=USE, inputs=[TOOL], ref=GROUP))
        return True

    async def generate(self, prompt: str, aspect_ratio: str = "1:1", resolution: str = "", title: str = "") -> str:
        prompt = (prompt or "").strip()
        if not prompt:
            return "Refused: the prompt is empty."
        if aspect_ratio and aspect_ratio not in ASPECTS:
            return f"Refused: aspect_ratio must be one of {', '.join(ASPECTS)}."
        if resolution and resolution not in RESOLUTIONS:
            return f"Refused: resolution must be one of {', '.join(RESOLUTIONS)}."
        if self.provider is None:
            return f"Image generation is off: {self.config.provider!r} is not a provider that makes images."
        if not self.bound():
            return NO_KEY
        try:
            key = resolve_secret(self.key_ref())
        except (SecretNotFound, ValueError):
            return NO_KEY
        image = await self.provider.generate(key, self.config.model, prompt, aspect_ratio=aspect_ratio, resolution=resolution)
        path = self._save(image.data, prompt, EXTENSIONS.get(image.media_type, "png"))
        self._log(prompt=prompt[:500], model=self.config.model, file=path.name, bytes=len(image.data), cost=image.cost)
        rel = path.relative_to(self.workspace.resolve()).as_posix()
        done = f"Generated {rel} with {self.config.model}."
        if self.attach is None:
            return f"{done} Call attach_file with path {rel!r} to show it."
        return f"{done} {self.attach(path=rel, title=title or prompt[:60])}"

    def _save(self, raw: bytes, prompt: str, ext: str) -> Path:
        folder = self.workspace.resolve() / OUT_DIR
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = folder / f"{stamp}-{_slug(prompt)}-{uuid4().hex[:6]}.{ext}"
        path.write_bytes(raw)
        return path

    def _log(self, **data: Any) -> None:
        if self.ledger is None:
            return
        from src.harness.tools import (
            current_call,  # local imports: generation stays free of a harness import cycle
        )
        from src.memory.ledger import Kind

        bot_id, session_id = current_call.get()
        self.ledger.append(Kind.image, bot_id, session_id, **data)

"""Image-generation providers. One today: OpenRouter's `POST /api/v1/images` (model, prompt, optional
aspect_ratio and resolution; images come back as base64 with their media type and the call's cost).
The tool that uses this lives in `src/generation/images.py`; HTTP stays behind `src/providers/`."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Protocol

from . import tracing
from .interface import ProviderError
from .openrouter import OpenRouterHttp
from .secrets import Secret

IMAGES_PATH = "/v1/images"  # OpenRouterHttp's base is https://openrouter.ai/api


@dataclass
class GeneratedImage:
    data: bytes
    media_type: str
    cost: float | None = None


class ImageProvider(Protocol):
    async def generate(self, key: Secret, model: str, prompt: str, **options: Any) -> GeneratedImage: ...


class OpenRouterImages:
    def __init__(self, *, timeout: float = 180.0, transport: Any = None) -> None:
        self.timeout = timeout
        self.transport = transport  # tests pass an httpx.MockTransport

    async def generate(self, key: Secret, model: str, prompt: str, **options: Any) -> GeneratedImage:
        payload = {"model": model, "prompt": prompt, **{k: v for k, v in options.items() if v}}
        params = {k: v for k, v in options.items() if v and isinstance(v, (str, int, float, bool))}
        with tracing.observe("generate-image", as_type="generation", model=model, input=prompt, model_parameters=params) as gen:
            http = OpenRouterHttp(key, timeout=self.timeout, transport=self.transport)
            try:
                data = await http.post_json(IMAGES_PATH, payload, what="image generation")
            finally:
                await http.aclose()
            images = [d for d in data.get("data") or [] if isinstance(d, dict) and d.get("b64_json")]
            if not images:
                raise ProviderError(f"{model} returned no image")
            try:
                raw = base64.b64decode(images[0]["b64_json"], validate=True)
            except (binascii.Error, ValueError):
                raise ProviderError(f"{model} returned an image that is not base64") from None
            cost = (data.get("usage") or {}).get("cost")
            image = GeneratedImage(raw, str(images[0].get("media_type") or "image/png").lower(), cost)
            gen.update(output=tracing.media(image.data, image.media_type),
                       cost_details={"total": float(cost)} if isinstance(cost, (int, float)) else None)
        return image


PROVIDERS: dict[str, type] = {"openrouter": OpenRouterImages}


def image_provider(kind: str, **kwargs: Any) -> ImageProvider | None:
    """The provider registered as `kind`, or None for an unknown one."""
    cls = PROVIDERS.get(kind)
    return cls(**kwargs) if cls else None

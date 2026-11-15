"""What models a provider offers: id, name, kinds (text, image, decision...), whether a text model
reasons, and what it costs, when the provider says.

OpenRouter's `GET /api/v1/models?output_modalities=all` reports all three: output modalities give the
kinds, `supported_parameters` holds `reasoning` for a model that reasons, and `pricing` is USD per
token (or per request). Input modalities, supported parameters, numeric prices and the output ceiling
are kept too, so a model can be matched to a job (image input, structured output, tools). A plain OpenAI-compatible `GET <base>/models` lists ids only, so kinds are
guessed from the id and cost and reasoning stay unknown (None)."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .interface import ProviderError
from .openrouter import OpenRouterHttp
from .secrets import Secret

KINDS = ("text", "image", "decision", "embedding", "tts", "transcription", "audio", "video", "rerank")  # tts: text to speech;
# transcription: speech to text; audio: audio generation (music, sound)
_MODALITY_KIND = {"text": "text", "image": "image", "decisions": "decision", "embeddings": "embedding", "speech": "tts",
                  "audio": "audio", "transcription": "transcription", "video": "video", "rerank": "rerank"}
_GUESSES = (  # id patterns for an API that lists ids only, first match wins
    (re.compile(r"embed", re.I), "embedding"),
    (re.compile(r"rerank", re.I), "rerank"),
    (re.compile(r"whisper|transcri|stt", re.I), "transcription"),
    (re.compile(r"tts|speech", re.I), "tts"),
    (re.compile(r"audio|music", re.I), "audio"),
    (re.compile(r"dall-e|gpt-image|imagen|flux|stable-diffusion|sdxl|image", re.I), "image"),
    (re.compile(r"sora|veo|video", re.I), "video"),
)
CACHE_SECONDS = 600


@dataclass
class ModelInfo:
    id: str
    name: str
    kinds: list[str]
    reasoning: bool | None = None  # None: the provider does not say (or it is not a text model)
    cost: str | None = None  # a short label, e.g. "$0.10 in · $0.20 out per 1M tokens"; None: unknown
    context_length: int | None = None
    guessed: bool = False  # kinds come from the id, not the provider
    # What the provider says it accepts and supports; None: it does not say (an id-only list)
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    supported_parameters: list[str] | None = None
    pricing: dict[str, float] | None = None  # USD per token (prompt, completion, image) or per request; -1: a router
    max_output_tokens: int | None = None
    description: str | None = None
    knowledge_cutoff: str | None = None
    probed_at: str | None = None  # ISO time the harness tested the model itself (an id-only provider)

    def _supports(self, *params: str) -> bool | None:
        return None if self.supported_parameters is None else any(p in self.supported_parameters for p in params)

    @property
    def image_input(self) -> bool | None:
        return None if self.input_modalities is None else "image" in self.input_modalities

    @property
    def structured_output(self) -> bool | None:
        return self._supports("structured_outputs", "response_format")

    @property
    def tools(self) -> bool | None:
        return self._supports("tools")

    def to_dict(self) -> dict[str, Any]:
        """What providers.json keeps: the provider's facts, not what is derived from them."""
        return asdict(self)

    def public(self) -> dict[str, Any]:
        """The facts plus the flags derived from them, for the Settings pages."""
        return {**self.to_dict(), "image_input": self.image_input, "structured_output": self.structured_output, "tools": self.tools}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ModelInfo":
        return cls(id=str(d["id"]), name=str(d.get("name") or d["id"]), kinds=[k for k in d.get("kinds") or [] if k in KINDS] or ["text"],
                   reasoning=d.get("reasoning"), cost=d.get("cost"), context_length=d.get("context_length"),
                   guessed=bool(d.get("guessed")), input_modalities=_strs(d.get("input_modalities")),
                   output_modalities=_strs(d.get("output_modalities")), supported_parameters=_strs(d.get("supported_parameters")),
                   pricing=_prices(d.get("pricing")), max_output_tokens=_positive(d.get("max_output_tokens")),
                   description=_text(d.get("description")), knowledge_cutoff=_text(d.get("knowledge_cutoff")),
                   probed_at=_text(d.get("probed_at")))


def _strs(v: Any) -> list[str] | None:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else None


def _positive(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def _text(v: Any) -> str | None:
    return v if isinstance(v, str) and v.strip() else None


def _prices(raw: Any) -> dict[str, float] | None:
    """OpenRouter's price strings as floats; a value that does not parse is left out."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, float] = {}
    for key in ("prompt", "completion", "image", "request"):
        try:
            if raw.get(key) is not None:
                out[key] = float(raw[key])
        except (TypeError, ValueError):
            continue
    return out or None


def _usd(per_token: float) -> str:
    per_m = per_token * 1_000_000
    return f"${per_m:,.2f}" if per_m >= 0.01 else f"${per_m:.2g}"


def cost_label(pricing: dict[str, Any] | None, kinds: list[str]) -> str | None:
    """OpenRouter's pricing (USD strings per token or per unit) as one short label; None when absent."""
    if not isinstance(pricing, dict) or not pricing:
        return None

    def num(key: str) -> float:
        try:
            return max(float(pricing.get(key) or 0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    try:
        if any(float(pricing.get(k) or 0) < 0 for k in ("prompt", "completion")):  # OpenRouter's -1: a router, priced per routed model
            return "price varies with the model it routes to"
    except (TypeError, ValueError):
        pass
    parts = []
    if "image" in kinds and (num("image_output") or num("image_token")):
        parts.append(f"{_usd(num('image_output') or num('image_token'))} per 1M image tokens")
    prompt, completion = num("prompt"), num("completion")
    if prompt or completion:
        if completion:
            parts.append(f"{_usd(prompt)} in · {_usd(completion)} out per 1M tokens")
        else:
            parts.append(f"{_usd(prompt)} per 1M input tokens")
    if num("request"):
        parts.append(f"${num('request'):.4g} per request")
    if parts:
        return " · ".join(parts)
    return "free" if any(k in pricing for k in ("prompt", "completion")) else None


def guess_kinds(model_id: str) -> list[str]:
    for pattern, kind in _GUESSES:
        if pattern.search(model_id):
            return [kind]
    return ["text"]


def from_openrouter(raw: dict[str, Any]) -> ModelInfo | None:
    mid = raw.get("id")
    if not isinstance(mid, str) or not mid:
        return None
    arch = raw.get("architecture") or {}
    kinds = list(dict.fromkeys(_MODALITY_KIND[m] for m in arch.get("output_modalities") or [] if m in _MODALITY_KIND)) or ["text"]
    params = raw.get("supported_parameters") or []
    reasoning = ("reasoning" in params or "include_reasoning" in params) if "text" in kinds else None
    ctx = raw.get("context_length")
    return ModelInfo(id=mid, name=str(raw.get("name") or mid), kinds=kinds, reasoning=reasoning,
                     cost=cost_label(raw.get("pricing"), kinds), context_length=_positive(ctx),
                     input_modalities=_strs(arch.get("input_modalities")), output_modalities=_strs(arch.get("output_modalities")),
                     supported_parameters=_strs(raw.get("supported_parameters")), pricing=_prices(raw.get("pricing")),
                     max_output_tokens=_positive((raw.get("top_provider") or {}).get("max_completion_tokens")),
                     description=_text(raw.get("description")), knowledge_cutoff=_text(raw.get("knowledge_cutoff")))


def from_openai(raw: dict[str, Any]) -> ModelInfo | None:
    mid = raw.get("id")
    if not isinstance(mid, str) or not mid:
        return None
    return ModelInfo(id=mid, name=mid, kinds=guess_kinds(mid), guessed=True)


@dataclass
class ModelLister:
    """Fetches and caches each provider's model list for `CACHE_SECONDS`."""

    transport: Any = None  # tests pass an httpx.MockTransport
    _cache: dict[str, tuple[float, list[ModelInfo]]] = field(default_factory=dict)

    def forget(self, provider_id: str) -> None:
        self._cache.pop(provider_id, None)

    async def list(self, provider_id: str, kind: str, base_url: str, key: Secret | None, refresh: bool = False) -> list[ModelInfo]:
        hit = self._cache.get(provider_id)
        if hit and not refresh and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        http = OpenRouterHttp(key, base_url=base_url, timeout=30.0, transport=self.transport)
        try:
            if kind == "openrouter":
                data = await http.get_json("/v1/models", {"output_modalities": "all"}, what="openrouter models")
                parse = from_openrouter
            else:
                data = await http.get_json("/models", what="model list")
                parse = from_openai
        finally:
            await http.aclose()
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ProviderError("the provider's model list has no data array")
        models = sorted((m for m in (parse(r) for r in rows if isinstance(r, dict)) if m), key=lambda m: m.name.lower())
        self._cache[provider_id] = (time.monotonic(), models)
        return models

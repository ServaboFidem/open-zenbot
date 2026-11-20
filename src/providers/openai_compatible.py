"""A chat provider for any OpenAI-compatible API: OpenAI, Groq, Together, DeepSeek, Mistral and the rest
that serve `POST <base>/chat/completions` and `GET <base>/models`. It is OpenRouter's adapter pointed
at another base URL, without the request fields only OpenRouter accepts (`usage`, `session_id`).
Registers as kind "openai-compatible"; the base URL comes from the provider entry in providers.json."""

from __future__ import annotations

from typing import Any

from . import registry
from .openrouter import OpenRouterHttp, OpenRouterProvider
from .secrets import resolve_secret

KIND = "openai-compatible"


class OpenAICompatibleProvider(OpenRouterProvider):
    kind = KIND
    chat_path = "/chat/completions"
    openrouter_fields = False

    def __init__(self, key: Any = None, *, model: str | None = None, base_url: str = "", timeout: float = 120.0,
                 transport: Any = None, http: OpenRouterHttp | None = None, extras: dict[str, Any] | None = None) -> None:
        if not base_url and http is None:
            raise ValueError("an OpenAI-compatible provider needs a base URL")
        if not model:
            raise ValueError("an OpenAI-compatible provider needs a model")
        # key None: a server that takes no key (LM Studio, Ollama, a local vLLM)
        http = http or OpenRouterHttp(key, base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)
        super().__init__(model=model, http=http, extras=extras)

    @classmethod
    def from_env(cls, *, key_ref: str | None = None, **kwargs: Any) -> "OpenAICompatibleProvider":
        return cls(resolve_secret(key_ref) if key_ref else None, **kwargs)

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(base_url={self.http.base_url!r}, model={self.model!r}, key=Secret(***))"


registry.register(KIND, OpenAICompatibleProvider.from_env)

__all__ = ["KIND", "OpenAICompatibleProvider", "OpenRouterHttp"]

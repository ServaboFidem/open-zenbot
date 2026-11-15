"""OpenRouter provider adapter.

Two things live here. `OpenRouterHttp` is the one authenticated HTTP
client for openrouter.ai, with error mapping; the chat provider below
and the decisions scorer in `src/decider/adapters/` both use it, so no
module outside `src/providers/` touches an HTTP library.

`OpenRouterProvider` is the chat-completions provider, OpenAI-compatible,
at /api/v1/chat/completions. The only provider on day one. Registers as
kind "openrouter".

The key comes from a secret ref (default ``env:OPENROUTER_API_KEY``) and
is held as a `Secret`. The resolved value is registered with the shared
exclusion filter so the ledger redacts it if it ever appears in text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

import httpx

from src.memory.exclusion import exclusion

from . import registry
from . import tracing
from .interface import (
    Completion,
    Message,
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    ToolCall,
    ToolSpec,
    Usage,
)
from .secrets import Secret, resolve_secret

KIND = "openrouter"
BASE_URL = "https://openrouter.ai/api"
CHAT_PATH = "/v1/chat/completions"
AUTH_PATH = "/v1/auth/key"
DEFAULT_MODEL = "meta/muse-spark-1.3-contributor"
DEFAULT_KEY_REF = "env:OPENROUTER_API_KEY"
APP_HEADERS = {"HTTP-Referer": "https://github.com/zen-agents", "X-Title": "zen-agents"}


class OpenRouterHttp:
    """Authenticated client for openrouter.ai. Maps HTTP failures onto the
    provider error hierarchy so every caller pauses the same way."""

    def __init__(
        self,
        key: Secret | None,
        *,
        base_url: str = BASE_URL,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = key  # None: no Authorization header, for public endpoints such as a model list
        self.base_url = base_url.rstrip("/")
        auth = {"Authorization": f"Bearer {key.reveal()}"} if key is not None else {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={**auth, **APP_HEADERS},
            transport=transport,
        )
        if key is not None:
            exclusion.register_secret_value(key.reveal())

    @classmethod
    def from_env(cls, *, key_ref: str = DEFAULT_KEY_REF, **kwargs: Any) -> "OpenRouterHttp":
        return cls(resolve_secret(key_ref), **kwargs)

    def __repr__(self) -> str:
        return f"OpenRouterHttp(base_url={self.base_url!r}, key=Secret(***))"

    async def post_json(self, path: str, payload: dict[str, Any], *, what: str = "openrouter") -> dict[str, Any]:
        return await self._send("POST", path, what, json=payload)

    async def get_json(self, path: str, params: dict[str, str] | None = None, *, what: str = "openrouter") -> dict[str, Any]:
        return await self._send("GET", path, what, params=params)

    async def _send(self, method: str, path: str, what: str, **kwargs: Any) -> dict[str, Any]:
        try:
            r = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise ProviderUnavailable(f"{what} unreachable: {type(e).__name__}", cause="timeout") from None
        except (httpx.ConnectError, httpx.NetworkError) as e:
            raise ProviderUnavailable(f"{what} unreachable: {type(e).__name__}", cause="network") from None
        except httpx.HTTPError as e:
            raise ProviderError(f"{what} request failed: {type(e).__name__}") from None
        if r.status_code in (401, 403):
            raise ProviderAuthError(f"{what} rejected the key (HTTP {r.status_code})")
        if r.status_code == 402:
            raise ProviderAuthError(f"{what} reports insufficient credits (HTTP 402)")
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderUnavailable(f"{what} HTTP {r.status_code}", cause="rate_limit" if r.status_code == 429 else "server",
                                      status=r.status_code)
        if r.status_code >= 400:
            raise ProviderError(f"{what} HTTP {r.status_code}: {_safe_error(r)}", status=r.status_code)
        try:
            data = r.json()
        except ValueError:
            raise ProviderError(f"{what} returned non-JSON") from None
        if not isinstance(data, dict):
            raise ProviderError(f"{what} returned a non-object body")
        return data

    async def probe(self, path: str = AUTH_PATH) -> bool:
        try:
            r = await self._client.get(path, timeout=10.0)
        except httpx.HTTPError:
            return False
        return r.status_code == 200

    async def aclose(self) -> None:
        await self._client.aclose()


def _safe_error(r: httpx.Response) -> str:
    """Error text from the body, bounded, never echoing request headers."""
    try:
        body = r.json()
        if not isinstance(body, dict):
            return ""
        err = body.get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        # NVIDIA NIM's gateway puts the reason in `detail` (problem+json); its model servers, like vLLM's,
        # in a top-level `message`
        return str(msg or body.get("detail") or body.get("message") or body.get("title") or "")[:300]
    except ValueError:
        return r.text[:300]


class OpenRouterProvider(Provider):
    kind = KIND
    chat_path = CHAT_PATH  # relative to the http client's base url
    openrouter_fields = True  # `usage` and `session_id` in the request: OpenRouter's own, refused by plain OpenAI-style APIs

    def __init__(
        self,
        key: Secret | None = None,
        *,
        model: str | None = None,
        http: OpenRouterHttp | None = None,
        base_url: str = BASE_URL,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if http is None:
            if key is None:
                raise ValueError("OpenRouterProvider needs a key or an http client")
            http = OpenRouterHttp(key, base_url=base_url, timeout=timeout, transport=transport)
        self.http = http
        self.model = model or DEFAULT_MODEL

    @classmethod
    def from_env(cls, *, key_ref: str = DEFAULT_KEY_REF, **kwargs: Any) -> "OpenRouterProvider":
        return cls(resolve_secret(key_ref), **kwargs)

    def __repr__(self) -> str:
        return f"OpenRouterProvider(model={self.model!r}, key=Secret(***))"

    # -- Provider ---------------------------------------------------------

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        session_id: str | None = None,
    ) -> Completion:
        names = ToolNames(t.name for t in tools or [])
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [_to_openai(m, names) for m in messages],
        }
        if self.openrouter_fields:
            payload["usage"] = {"include": True}
            if session_id:
                payload["session_id"] = session_id[:256]
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": names.wire(t.name), "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        meta = {"provider": self.kind, "tools": [t.name for t in tools or []]}
        with tracing.observe(tracing.step_name("generate-response"), as_type="generation", model=payload["model"],
                             input=payload["messages"], metadata=meta) as gen:
            data = await self.http.post_json(self.chat_path, payload, what=self.kind)
            try:
                choice = data["choices"][0]
                raw = choice["message"]
            except (KeyError, IndexError, TypeError) as e:
                raise ProviderError(f"malformed completion: {type(e).__name__}") from None
            usage = data.get("usage") or {}
            completion = Completion(
                message=_from_openai(raw, names),
                finish_reason=str(choice.get("finish_reason") or ""),
                model=str(data.get("model") or payload["model"]),
                usage=Usage(
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    cost=usage.get("cost"),
                    cached_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                ),
                kind=self.kind,
                reasoning=_reasoning(raw),
            )
            u = completion.usage
            gen.update(model=completion.model, output=_traced_output(raw, completion.reasoning, names),
                       usage_details=tracing.usage_details(u.prompt_tokens, u.completion_tokens, u.cached_tokens),
                       cost_details={"total": float(u.cost)} if isinstance(u.cost, (int, float)) else None,
                       metadata={**meta, "finish_reason": completion.finish_reason})
        return completion

    async def probe(self) -> bool:
        return await self.http.probe()

    async def aclose(self) -> None:
        await self.http.aclose()


_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
WIRE_MAX = 64


def wire_name(name: str) -> str:
    """A tool name strict OpenAI-style APIs accept (`^[A-Za-z0-9_-]{1,64}$`; NVIDIA NIM refuses anything else):
    a dot, the `<server>.<tool>` separator, becomes `__`, any other character outside that set becomes `_`."""
    return _UNSAFE.sub("_", name.replace(".", "__"))[:WIRE_MAX] or "_"


class ToolNames:
    """One request's tool names, ours to the wire and back. Names already valid keep themselves; a rewritten name
    that would clash with another gets a numeric suffix. A name the request does not list passes through."""

    def __init__(self, names: Iterable[str]) -> None:
        self._wire: dict[str, str] = {}
        self._local: dict[str, str] = {}
        unique = list(dict.fromkeys(names))
        for n in [n for n in unique if wire_name(n) == n] + [n for n in unique if wire_name(n) != n]:
            w = base = wire_name(n)
            i = 2
            while w in self._local:
                suffix = f"_{i}"
                w, i = base[:WIRE_MAX - len(suffix)] + suffix, i + 1
            self._wire[n], self._local[w] = w, n

    def wire(self, name: str) -> str:
        return self._wire.get(name) or wire_name(name)

    def local(self, name: str) -> str:
        return self._local.get(name, name)


def _to_openai(m: Message, names: ToolNames | None = None) -> dict[str, Any]:
    names = names or ToolNames([])
    d: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.role == "assistant" and m.tool_calls:
        d["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": names.wire(c.name), "arguments": json.dumps(c.arguments)}}
            for c in m.tool_calls
        ]
    if m.role == "tool":
        d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = names.wire(m.name)
    return d


def _reasoning(raw: dict[str, Any]) -> str:
    """The model's thinking: OpenRouter's `reasoning` (or its `reasoning_details`), vLLM/NIM's `reasoning_content`."""
    for key in ("reasoning", "reasoning_content"):
        if isinstance(raw.get(key), str) and raw[key].strip():
            return raw[key]
    parts = [d.get("text") or d.get("summary") or "" for d in raw.get("reasoning_details") or [] if isinstance(d, dict)]
    return "\n".join(p for p in parts if isinstance(p, str) and p)


def _traced_output(raw: dict[str, Any], reasoning: str, names: ToolNames | None = None) -> dict[str, Any]:
    """The reply as Langfuse renders it: an assistant message, tool calls with JSON-string arguments, thinking."""
    names = names or ToolNames([])
    out: dict[str, Any] = {"role": "assistant", "content": raw.get("content") or ""}
    calls = []
    for c in raw.get("tool_calls") or []:
        fn = c.get("function") or {}
        args = fn.get("arguments")
        calls.append({"id": str(c.get("id") or ""), "type": "function",
                      "function": {"name": names.local(str(fn.get("name") or "")),
                                   "arguments": args if isinstance(args, str) else json.dumps(args or {})}})
    if calls:
        out["tool_calls"] = calls
    if reasoning:
        out["thinking"] = [{"type": "thinking", "content": reasoning}]
    return out


def _from_openai(raw: dict[str, Any], names: ToolNames | None = None) -> Message:
    names = names or ToolNames([])
    calls: list[ToolCall] = []
    for c in raw.get("tool_calls") or []:
        fn = c.get("function") or {}
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
        except ValueError:
            args = {"_raw": args_raw}
        calls.append(ToolCall(id=str(c.get("id") or ""), name=names.local(str(fn.get("name") or "")), arguments=args))
    return Message(role="assistant", content=str(raw.get("content") or ""), tool_calls=calls)


registry.register(KIND, OpenRouterProvider.from_env)

"""Provider interface.

Every model call in the harness goes through a `Provider`. Nothing outside
`src/providers/` may import a provider HTTP client; a test enforces that.

Day one has one adapter (OpenRouter). The registry in `registry.py` is the
slot where a local inference adapter lands later, without touching callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx


@dataclass
class ToolSpec:
    """A tool the model may call. Parameters are a JSON schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments} for c in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None
    cached_tokens: int = 0


@dataclass
class Completion:
    message: Message
    finish_reason: str
    model: str
    usage: Usage = field(default_factory=Usage)
    kind: str = ""  # provider kind that produced this; flows into the ledger
    reasoning: str = ""  # the model's thinking before it answered, when the provider returns it (traced, not replayed)


class ProviderError(Exception):
    """Base class. The task loop pauses on any of these. `status` is the HTTP status, if there was one."""

    def __init__(self, message: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ProviderUnavailable(ProviderError):
    """Network down, timeout, rate limit, or 5xx. Retry may succeed.
    `cause` is one of "rate_limit", "server", "timeout", "network"; `status` is the HTTP status, if any."""

    def __init__(self, message: str = "", cause: str = "network", status: int | None = None) -> None:
        super().__init__(message, status)
        self.cause = cause


class ProviderAuthError(ProviderError):
    """Bad or missing key. The user must act."""


FAILURE_KINDS = ("timeout", "network", "auth", "rate_limit", "server", "other")


def failure_kind(exc: BaseException) -> str:
    """What kind of provider failure `exc` is, for callers outside `src/providers/` that must not import an HTTP
    client: `timeout`, `network` (could not connect), `auth` (401, 402 or 403), `rate_limit` (429), `server` (5xx),
    `other` (any other provider error: another HTTP status, a malformed answer from the API), or `""` when it is no
    provider or transport failure at all (a bad answer of the caller's own reading, a bug)."""
    if isinstance(exc, ProviderUnavailable):
        return exc.cause if exc.cause in FAILURE_KINDS else "other"
    if isinstance(exc, ProviderAuthError):
        return "auth"
    if isinstance(exc, ProviderError):
        return "other"
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout"
    if isinstance(exc, (httpx.TransportError, OSError)):
        return "network"
    return ""


@runtime_checkable
class Provider(Protocol):
    kind: str

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        session_id: str | None = None,
    ) -> Completion: ...

    async def probe(self) -> bool:
        """True when the provider answers. Used to resume from a pause."""
        ...


class UnboundProvider:
    """What the harness holds before Settings binds a provider. Every call
    fails with one clear message; the GUI shows it and points at Settings."""

    kind = "unbound"
    MESSAGE = "no provider bound; enter a provider and key in Settings"

    async def complete(self, messages: list[Message], *, tools: list[ToolSpec] | None = None, model: str | None = None, session_id: str | None = None) -> Completion:
        raise ProviderUnavailable(self.MESSAGE)

    async def probe(self) -> bool:
        return False

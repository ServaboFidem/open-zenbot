"""LLM tracing to Langfuse (https://langfuse.com), following its "What does a good trace look like?".

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (LANGFUSE_BASE_URL picks the server,
LANGFUSE_TRACING_ENVIRONMENT the environment). Off, every helper is a no-op, so call sites need no guard.
Langfuse runs on its own OpenTelemetry TracerProvider, so no other instrumentation reaches it, and every
string it exports passes through the harness's exclusion filter first: keys and excluded content stay home.

Names are an API (evaluators and dashboards target them): verb-first, stable, no ids or model names.
Generations get theirs from `llm_step(...)`, set by the caller around a provider call."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import Any

log = logging.getLogger(__name__)

_client: Any = None  # langfuse.Langfuse while tracing is on
_step: ContextVar[str | None] = ContextVar("llm_step", default=None)


class _NoOp:
    """Stands in for an observation while tracing is off."""

    id = trace_id = None

    def update(self, **_: Any) -> "_NoOp":
        return self


NOOP = _NoOp()


def enabled() -> bool:
    return _client is not None


def init_langfuse(env: Mapping[str, str] | None = None, *, span_exporter: Any = None) -> bool:
    """Start the client when the keys are set. Returns whether tracing is on. Never raises."""
    global _client
    env = os.environ if env is None else env
    public, secret = (env.get("LANGFUSE_PUBLIC_KEY") or "").strip(), (env.get("LANGFUSE_SECRET_KEY") or "").strip()
    if not (public and secret):
        return False
    try:
        from langfuse import Langfuse
        from opentelemetry.sdk.trace import TracerProvider

        from src.memory.exclusion import exclusion

        exclusion.register_secret_value(secret)
        _client = Langfuse(
            public_key=public, secret_key=secret,
            base_url=(env.get("LANGFUSE_BASE_URL") or env.get("LANGFUSE_HOST") or "").strip() or None,
            environment=(env.get("LANGFUSE_TRACING_ENVIRONMENT") or "").strip() or None,
            tracer_provider=TracerProvider(),  # isolated: never the global provider
            mask_otel_spans=redact_spans,
            span_exporter=span_exporter,
        )
    except Exception as e:  # noqa: BLE001 - tracing must never stop the harness
        log.warning("Langfuse tracing is off: %s", type(e).__name__)
        _client = None
    return _client is not None


def flush() -> None:
    if _client is not None:
        try:
            _client.flush()
        except Exception:  # noqa: BLE001
            pass


def shutdown() -> None:
    """Send what is buffered and stop. Safe to call when off."""
    global _client
    if _client is not None:
        try:
            _client.shutdown()
            _forget(_client)
        except Exception:  # noqa: BLE001
            pass
    _client = None


def _forget(client: Any) -> None:
    """The SDK keeps one client per public key and hands the same (now stopped) one back to a later
    `Langfuse(...)` with that key, whatever its secret or server: drop it so a restart builds a new one."""
    from langfuse._client.resource_manager import LangfuseResourceManager

    key = getattr(client, "_public_key", None) or getattr(getattr(client, "_resources", None), "public_key", None)
    with LangfuseResourceManager._lock:
        if key is not None:
            LangfuseResourceManager._instances.pop(key, None)


def restart(env: Mapping[str, str]) -> bool:
    """Settings > Tracing: send what is buffered, stop, and start again with these LANGFUSE_* values."""
    shutdown()
    return init_langfuse(env)


def check_keys(public_key: str, secret_key: str, base_url: str, *, transport: Any = None) -> str | None:
    """Ask the server whether the keys work (what the SDK's auth_check asks). None if they do, else why not."""
    import httpx

    url = (base_url or "https://cloud.langfuse.com").rstrip("/") + "/api/public/projects"
    try:
        with httpx.Client(timeout=10, transport=transport) as c:
            r = c.get(url, auth=(public_key, secret_key))
    except httpx.HTTPError as e:
        return f"could not reach {base_url or 'Langfuse Cloud'}: {type(e).__name__}"
    if r.status_code in (401, 403):
        return "the server refused these keys"
    if r.status_code >= 400:
        return f"the server answered HTTP {r.status_code}"
    return None


def redact_spans(*, params: Any) -> Any:
    """`mask_otel_spans` hook: every string attribute through the exclusion filter."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    from src.memory.exclusion import exclusion

    patches = {}
    for ident, span in params.spans.items():
        changed = {}
        for key, value in (span.attributes or {}).items():
            if isinstance(value, str):
                clean, reasons = exclusion.redact(value)
                if reasons:
                    changed[key] = clean
        if changed:
            patches[ident] = OtelSpanPatch(set_attributes=changed)
    return MaskOtelSpansResult(span_patches=patches)


# -- naming -------------------------------------------------------------------


@contextmanager
def llm_step(name: str) -> Iterator[None]:
    """Name the generations made inside this block (e.g. "summarize-document")."""
    token = _step.set(name)
    try:
        yield
    finally:
        _step.reset(token)


def step_name(default: str) -> str:
    return _step.get() or default


# -- observations -------------------------------------------------------------


@contextmanager
def observe(name: str, as_type: str = "span", **fields: Any) -> Iterator[Any]:
    """An observation that is current for the block; the block's exception marks it ERROR and re-raises.
    Off, it yields a no-op whose `update` does nothing."""
    if _client is None:
        yield NOOP
        return
    try:
        cm = _client.start_as_current_observation(name=name, as_type=as_type, **fields)
        obs = cm.__enter__()
    except Exception:  # noqa: BLE001 - a tracing failure never fails the work
        yield NOOP
        return
    try:
        yield obs
    except BaseException as e:
        try:
            obs.update(level="ERROR" if isinstance(e, Exception) else "WARNING", status_message=f"{type(e).__name__}: {e}"[:500])
        except Exception:  # noqa: BLE001
            pass
        cm.__exit__(type(e), e, e.__traceback__)
        raise
    else:
        cm.__exit__(None, None, None)


def attributes(**kw: Any):
    """Trace attributes (session_id, user_id, tags, metadata, trace_name) for everything started inside."""
    if _client is None:
        return nullcontext()
    try:
        from langfuse import propagate_attributes

        kw = {k: v for k, v in kw.items() if not _empty(v)}
        if isinstance(kw.get("metadata"), dict):
            kw["metadata"] = {k: v for k, v in kw["metadata"].items() if not _empty(v)}
        return propagate_attributes(**kw)
    except Exception:  # noqa: BLE001
        return nullcontext()


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def usage_details(prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> dict[str, int]:
    """Langfuse's usage buckets are exclusive: cached prompt tokens are not also counted as input."""
    cached = max(0, min(cached_tokens or 0, prompt_tokens or 0))
    out = {"input": (prompt_tokens or 0) - cached}
    if cached:
        out["input_cached_tokens"] = cached
    out["output"] = completion_tokens or 0
    return out


def media(data: bytes, content_type: str) -> Any:
    """Bytes (a generated image) as a Langfuse media reference, uploaded once and rendered in the UI."""
    if _client is None:
        return None
    try:
        from langfuse.media import LangfuseMedia

        return LangfuseMedia(content_bytes=data, content_type=content_type)
    except Exception:  # noqa: BLE001
        return {"content_type": content_type, "bytes": len(data)}

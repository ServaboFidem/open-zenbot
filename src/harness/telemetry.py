"""Error and trace reporting to GlitchTip (Sentry protocol). Off unless
ZEN_GLITCHTIP_DSN is set; the FastAPI and Starlette integrations enable themselves.
Without a client every helper here is a no-op, so call sites need no guard."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import sentry_sdk

DSN_ENV = "ZEN_GLITCHTIP_DSN"
# server-sent event streams stay open while what they watch runs: their duration is not latency
STREAM_SUFFIXES = ("/events",)
# never traced: the query string carries Google's authorization code and the sign-in's state
UNTRACED_PATHS = ("/calendar/google/callback",)


def traces_sampler(ctx: dict[str, Any]) -> float:
    path = (ctx.get("asgi_scope") or {}).get("path") or ""
    if path.endswith(STREAM_SUFFIXES) or path in UNTRACED_PATHS:
        return 0.0
    return 1.0


_active = False


def init_tracing(env: Mapping[str, str] | None = None, *, dsn: str | None = None) -> bool:
    """Start (or restart) the Sentry SDK. `dsn` (Settings > Tracing) wins over the environment; an empty
    one turns Sentry off. Returns whether it is on."""
    global _active
    dsn = ((os.environ if env is None else env).get(DSN_ENV, "") if dsn is None else dsn).strip()
    if not dsn:
        if _active:  # an explicit empty DSN: never None, which falls back to SENTRY_DSN
            sentry_sdk.get_client().close(timeout=2)
            sentry_sdk.init(dsn="")
        _active = False
        return False
    from src.memory.exclusion import exclusion

    exclusion.register_secret_value(dsn)  # an ingest credential: never in a trace, a prompt or the ledger
    if _active:
        sentry_sdk.get_client().close(timeout=2)
    _active = True
    sentry_sdk.init(
        dsn=dsn,
        traces_sampler=traces_sampler,
        send_default_pii=False,
        # POST /settings/provider carries the provider key in its body: never send request bodies
        max_request_body_size="never",
    )
    return True


def sentry_on() -> bool:
    return _active


def trace_link() -> dict[str, str]:
    """The running span's trace, to hand to work started now that runs later. Taken
    when the work is scheduled: by the time a task runs, the request's span has ended."""
    parent = sentry_sdk.get_current_span()
    return {"trace_id": parent.trace_id, "parent_span_id": parent.span_id} if parent is not None else {}


@contextmanager
def background_transaction(name: str, op: str, link: dict[str, str] | None = None, **tags: str) -> Iterator[Any]:
    """A root transaction for work that outlives the request that started it (a
    session's run). Given a trace_link() it joins the starting request's trace, so the
    GUI action, the request and the run read as one trace; its own spans land here,
    not on the request's transaction, which has already been sent."""
    link = link or {}
    with sentry_sdk.isolation_scope() as scope:
        for k, v in tags.items():
            scope.set_tag(k, v)
        with sentry_sdk.start_transaction(name=name, op=op, **link) as tx:
            yield tx


def span(op: str, name: str | None = None, **data: Any):
    """A child span of whatever transaction is running."""
    s = sentry_sdk.start_span(op=op, name=name or op)
    for k, v in data.items():
        s.set_data(k, v)
    return s

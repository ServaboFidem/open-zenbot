"""provider-interface: registry slot and the no-direct-client rule."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.providers import registry
from src.providers.interface import Provider

from .conftest import REPO, ScriptedProvider, completion


@pytest.fixture(autouse=True)
def _isolate_registry():
    saved = dict(registry._factories)
    registry.clear()
    yield
    registry.clear()
    registry._factories.update(saved)


def test_mock_adapter_registers_and_resolves():
    registry.register("mock", lambda **kw: ScriptedProvider([completion("hi")]))
    assert registry.kinds() == ["mock"]
    assert registry.is_registered("mock")
    p = registry.get("mock")
    assert isinstance(p, Provider)
    assert p.kind == "mock"


def test_unknown_kind_is_a_clear_error():
    with pytest.raises(KeyError, match="no provider registered"):
        registry.get("nope")


def test_factory_kind_mismatch_is_rejected():
    class Wrong:
        kind = "other"

        async def complete(self, messages, *, tools=None, model=None, session_id=None): ...
        async def probe(self): return True

    registry.register("mock", lambda **kw: Wrong())
    with pytest.raises(TypeError, match="kind"):
        registry.get("mock")


def test_empty_kind_rejected():
    with pytest.raises(ValueError):
        registry.register("  ", lambda **kw: ScriptedProvider())


CLIENT_IMPORTS = re.compile(r"^\s*(import|from)\s+(httpx|requests|aiohttp|openai|anthropic)\b", re.M)


def test_no_module_outside_providers_imports_a_provider_client():
    offenders = []
    for path in (REPO / "src").rglob("*.py"):
        if "providers" in path.relative_to(REPO / "src").parts:
            continue
        if path.name == "hindsight_http.py":  # the memory server's client, not an LLM provider
            continue
        if path.relative_to(REPO / "src").as_posix() == "harness/media.py":  # fetch files and media, not an LLM provider
            continue
        if "music" in path.relative_to(REPO / "src").parts[:1]:  # music web APIs (Discogs, MusicBrainz), not LLM providers
            continue
        if "events" in path.relative_to(REPO / "src").parts[:1]:  # the Google Calendar API, not an LLM provider
            continue
        if "mail" in path.relative_to(REPO / "src").parts[:1]:  # the AgentMail API, not an LLM provider
            continue
        if CLIENT_IMPORTS.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], f"provider clients must stay behind src/providers/: {offenders}"


def test_failure_kind_reads_provider_and_transport_errors():
    import httpx

    from src.providers.interface import ProviderAuthError, ProviderError, ProviderUnavailable, failure_kind
    assert failure_kind(ProviderUnavailable("x", cause="timeout")) == "timeout"
    assert failure_kind(ProviderUnavailable("x", cause="network")) == "network"
    assert failure_kind(ProviderUnavailable("x", cause="rate_limit", status=429)) == "rate_limit"
    assert failure_kind(ProviderUnavailable("x", cause="server", status=503)) == "server"
    assert failure_kind(ProviderUnavailable("x", cause="odd")) == "other"
    assert failure_kind(ProviderAuthError("401")) == "auth"
    for status in (400, 404, 413, 422, 409):  # about this request, not the service
        assert failure_kind(ProviderError(f"HTTP {status}", status=status)) == "request"
    assert failure_kind(ProviderError("HTTP 401", status=401)) == failure_kind(ProviderError("x", status=403)) == "auth"
    assert failure_kind(ProviderError("HTTP 429", status=429)) == "rate_limit"
    assert failure_kind(ProviderError("HTTP 502", status=502)) == "server"
    assert failure_kind(ProviderError("malformed completion: KeyError")) == "other"  # no status: cannot be told
    assert failure_kind(httpx.ReadTimeout("slow")) == failure_kind(TimeoutError()) == "timeout"
    assert failure_kind(httpx.ConnectError("refused")) == failure_kind(ConnectionRefusedError()) == "network"
    assert failure_kind(ValueError("no JSON")) == failure_kind(RuntimeError()) == ""

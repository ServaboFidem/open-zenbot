"""Scorer registry.

Maps a `kind` string to a factory that builds a `Scorer`. One entry on
day one: "openrouter-decision-1". A later scorer, local or remote,
registers here and the router, fit, supervisor, and promotion gates do
not change. Callers resolve by kind and never import an adapter module.

A scorer also says which provider kinds its decision model may sit on
(`provider_kinds`): "openrouter-decision-1" needs OpenRouter's Decisions
API, "letter-logprobs" an OpenAI-compatible server. Settings offers a
scorer only the models of those providers. A scorer may also name the
mark the capability test leaves on a model it can drive (`needs`, one of
a model's `supported_parameters`): "letter-logprobs" needs
"letter_decisions", "systemone" needs "typed_decisions".
"""

from __future__ import annotations

from typing import Any, Callable

from .interface import Scorer

Factory = Callable[..., Scorer]

DEFAULT_PROVIDER_KINDS = ("openrouter",)

_factories: dict[str, Factory] = {}
_provider_kinds: dict[str, tuple[str, ...]] = {}
_needs: dict[str, str] = {}


def register(kind: str, factory: Factory, *, provider_kinds: tuple[str, ...] = DEFAULT_PROVIDER_KINDS,
             needs: str | None = None) -> None:
    if not kind or not kind.strip():
        raise ValueError("scorer kind must be a non-empty string")
    _factories[kind] = factory
    _provider_kinds[kind] = tuple(provider_kinds)
    _needs.pop(kind, None)
    if needs:
        _needs[kind] = needs


def needs(kind: str) -> str | None:
    """The mark a model must carry for this scorer to drive it; None: any decision model of its providers."""
    return _needs.get(kind)


def provider_kinds(kind: str) -> tuple[str, ...]:
    """The provider kinds a scorer's decision model may be on."""
    return _provider_kinds.get(kind, DEFAULT_PROVIDER_KINDS)


def is_registered(kind: str) -> bool:
    return kind in _factories


def kinds() -> list[str]:
    return sorted(_factories)


def get(kind: str, **kwargs: Any) -> Scorer:
    try:
        factory = _factories[kind]
    except KeyError:
        raise KeyError(f"no scorer registered for kind {kind!r}; known: {kinds()}") from None
    scorer = factory(**kwargs)
    if getattr(scorer, "kind", None) != kind:
        raise TypeError(f"factory for {kind!r} returned a scorer of kind {getattr(scorer, 'kind', None)!r}")
    return scorer


def clear() -> None:
    """For tests."""
    _factories.clear()
    _provider_kinds.clear()
    _needs.clear()

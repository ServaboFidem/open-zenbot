"""Provider registry.

Maps a `kind` string to a factory that builds a `Provider`. One entry on
day one: "openrouter". A later local adapter registers here and nothing
else changes. Callers resolve by kind and never import an adapter module
directly.
"""

from __future__ import annotations

from typing import Any, Callable

from .interface import Provider

Factory = Callable[..., Provider]

_factories: dict[str, Factory] = {}


def register(kind: str, factory: Factory) -> None:
    if not kind or not kind.strip():
        raise ValueError("provider kind must be a non-empty string")
    _factories[kind] = factory


def is_registered(kind: str) -> bool:
    return kind in _factories


def kinds() -> list[str]:
    return sorted(_factories)


def get(kind: str, **kwargs: Any) -> Provider:
    try:
        factory = _factories[kind]
    except KeyError:
        raise KeyError(f"no provider registered for kind {kind!r}; known: {kinds()}") from None
    provider = factory(**kwargs)
    if getattr(provider, "kind", None) != kind:
        raise TypeError(f"factory for {kind!r} returned a provider of kind {getattr(provider, 'kind', None)!r}")
    return provider


def clear() -> None:
    """For tests."""
    _factories.clear()

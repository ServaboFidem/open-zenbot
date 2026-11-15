"""Scorer registry.

Maps a `kind` string to a factory that builds a `Scorer`. One entry on
day one: "openrouter-decision-1". A later scorer, local or remote,
registers here and the router, fit, supervisor, and promotion gates do
not change. Callers resolve by kind and never import an adapter module.
"""

from __future__ import annotations

from typing import Any, Callable

from .interface import Scorer

Factory = Callable[..., Scorer]

_factories: dict[str, Factory] = {}


def register(kind: str, factory: Factory) -> None:
    if not kind or not kind.strip():
        raise ValueError("scorer kind must be a non-empty string")
    _factories[kind] = factory


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

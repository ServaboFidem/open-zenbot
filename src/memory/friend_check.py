"""Friend check for browser-helper intake.

Personal means invited by a friend in memory or friends in memory going.
A friend is an existing :class:`PeopleRegistry` entry whose tier is
``close``. The registry already holds ``close`` and ``known`` with
``promote``; this module adds no flag, no person and no fact.

The owner (``is_owner``) is never a friend: the owner is self, not someone
the owner knows.
"""

from __future__ import annotations

from src.memory.registry import PeopleRegistry

__all__ = ["is_friend"]


def is_friend(people: PeopleRegistry, name: str) -> bool:
    """Answer whether ``name`` is a friend in memory.

    Read-only: resolves ``name`` (or one of its aliases,
    case-insensitively) with :meth:`PeopleRegistry.find` and returns True
    only when the entry exists, is not the owner, and its tier is
    ``close``. A blank or unknown name answers False. Nothing is added,
    promoted, or edited.
    """
    if not name or not name.strip():
        return False
    entry = people.find(name)
    if entry is None or entry.is_owner:
        return False
    return entry.tier == "close"

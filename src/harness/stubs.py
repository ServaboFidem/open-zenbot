"""Protocols the task loop talks to, plus day-one stubs.

`SkillSelector` returns the skills to inject for a task. p0 shipped
`InjectAllSelector`; p2 replaced it with `CatalogRouter` behind the
same protocol. `Recall` returns remembered facts; `NoRecall` is the
no-memory fallback; p5 replaced it with `MemoryRecall` over the graph. Neither swap touches the loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from src.decider.router import Selection
from src.skills.loader import Skill

if TYPE_CHECKING:
    from .bot import Bot

__all__ = ["Selection", "SkillSelector", "Recall", "InjectAllSelector", "NoRecall"]


class SkillSelector(Protocol):
    kind: str

    async def select(self, bot: "Bot", task: str, session_id: str = "") -> Selection: ...


class Recall(Protocol):
    kind: str

    async def recall(self, bot: "Bot", task: str, session_id: str = "") -> list[str]: ...


class InjectAllSelector:
    """Every attached skill goes into context. The p0 behaviour; also
    what the router falls back to when the scorer is down."""

    kind = "inject-all"

    async def select(self, bot: "Bot", task: str, session_id: str = "") -> Selection:
        skills: list[Skill] = list(bot.skills)
        return Selection(skills, {"skills": [s.slug for s in skills], "outcome": "act", "scorer": None})


class NoRecall:
    """No memory yet."""

    kind = "none"

    async def recall(self, bot: "Bot", task: str, session_id: str = "") -> list[str]:
        return []

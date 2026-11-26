"""The catalog.

One list of skills and one list of MCP tools, kept separate. Each entry
records name, when to use it, inputs, needs (access, keys, files), and
limits (what it must not do). The decider scores entries; day one the
stub injects them all.

Dedup key: slug, path, or title. A match on any one is a duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .loader import Reason, Skill, SkillState


@dataclass
class CatalogEntry:
    kind: str  # "skill" | "mcp"
    name: str
    use: str
    inputs: list[Any] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    ref: str = ""  # slug for skills, server:tool for mcp
    band_width: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "use": self.use,
            "inputs": self.inputs,
            "needs": self.needs,
            "limits": self.limits,
            "ref": self.ref,
            "band_width": self.band_width,
        }


@dataclass
class AddResult:
    state: SkillState
    reason: Reason | None = None


class Catalog:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._mcps: dict[str, CatalogEntry] = {}

    # -- skills -----------------------------------------------------------

    def add_skill(self, skill: Skill) -> AddResult:
        if self._duplicate(skill):
            return AddResult(SkillState.nothing, Reason.duplicate)
        self._skills[skill.slug] = skill
        return AddResult(SkillState.added)

    def replace_skill(self, skill: Skill) -> AddResult:
        """Swap the entry of the same slug; a name held by a different slug is still refused."""
        for s in self._skills.values():
            if s.slug != skill.slug and s.name == skill.name:
                return AddResult(SkillState.nothing, Reason.duplicate)
        self._skills[skill.slug] = skill
        return AddResult(SkillState.added)

    def _duplicate(self, skill: Skill) -> bool:
        for s in self._skills.values():
            if s.slug == skill.slug:
                return True
            if skill.path and s.path and Path(s.path) == Path(skill.path):
                return True
            if s.name == skill.name:
                return True
        return False

    def skill(self, slug: str) -> Skill | None:
        return self._skills.get(slug)

    def skills(self) -> list[Skill]:
        return list(self._skills.values())

    def remove_skill(self, slug: str) -> bool:
        return self._skills.pop(slug, None) is not None

    # -- mcps -------------------------------------------------------------

    def add_mcp(self, entry: CatalogEntry) -> AddResult:
        if entry.kind != "mcp":
            raise ValueError("add_mcp takes an entry of kind 'mcp'")
        if entry.ref in self._mcps:
            return AddResult(SkillState.nothing, Reason.duplicate)
        self._mcps[entry.ref] = entry
        return AddResult(SkillState.added)

    def mcps(self) -> list[CatalogEntry]:
        return list(self._mcps.values())

    def remove_mcp(self, ref: str) -> bool:
        return self._mcps.pop(ref, None) is not None

    # -- all --------------------------------------------------------------

    def entries(self) -> list[CatalogEntry]:
        return [skill_entry(s) for s in self._skills.values()] + list(self._mcps.values())


def skill_entry(skill: Skill) -> CatalogEntry:
    return CatalogEntry(
        kind="skill",
        name=skill.name,
        use=skill.use,
        inputs=skill.inputs,
        needs=list(skill.access),
        limits=list(skill.approvals),
        ref=skill.slug,
        band_width=skill.band_width,
    )

"""SKILL.md loader.

A skill is a directory holding a `SKILL.md`: YAML frontmatter between
`---` lines, then a Markdown body. The frontmatter is the contract a
provider-neutral harness needs; the body is the how-to the model reads.

Caps are guides from recon, held in `SkillConfig`, never constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

SKILL_FILENAME = "SKILL.md"


class SkillState(StrEnum):
    staged = "staged"
    added = "added"
    reading = "reading"
    idle = "idle"
    done = "done"
    failed = "failed"
    nothing = "nothing"


class Reason(StrEnum):
    empty = "empty"
    too_large = "too-large"
    unsupported = "unsupported"
    too_many_skills = "too-many-skills"
    duplicate = "duplicate"
    invalid = "invalid"


@dataclass
class SkillConfig:
    max_bytes: int = 256_000
    max_per_bot: int = 250


@dataclass
class Skill:
    name: str
    slug: str
    use: str
    inputs: list[Any] = field(default_factory=list)
    access: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    validation: list[str] = field(default_factory=list)
    returns: str = ""
    approvals: list[str] = field(default_factory=list)
    band_width: float | None = None
    body: str = ""
    path: Path | None = None
    size: int = 0
    source: str = "file"

    def to_prompt(self) -> str:
        """Render for injection into model context."""
        lines = [f"## Skill: {self.name}", f"Use when: {self.use}"]
        if self.inputs:
            lines.append("Inputs: " + "; ".join(_fmt(i) for i in self.inputs))
        if self.access:
            lines.append("Access: " + ", ".join(self.access))
        if self.steps:
            lines.append("Steps:")
            lines.extend(f"{n}. {s}" for n, s in enumerate(self.steps, 1))
        if self.validation:
            lines.append("Validate: " + "; ".join(self.validation))
        if self.returns:
            lines.append(f"Return: {self.returns}")
        if self.approvals:
            lines.append("Needs approval before: " + ", ".join(self.approvals))
        if self.body.strip():
            lines.append("")
            lines.append(self.body.strip())
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "use": self.use,
            "inputs": self.inputs,
            "access": self.access,
            "steps": self.steps,
            "validation": self.validation,
            "return": self.returns,
            "approvals": self.approvals,
            "band_width": self.band_width,
            "path": str(self.path) if self.path else None,
            "size": self.size,
            "source": self.source,
        }


def _fmt(item: Any) -> str:
    if isinstance(item, dict):
        return "; ".join(f"{k}: {v}" for k, v in item.items())
    return str(item)


@dataclass
class LoadResult:
    state: SkillState
    reason: Reason | None = None
    skill: Skill | None = None
    path: Path | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.skill is not None


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise ValueError("SKILL.md must start with a --- frontmatter block")
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise ValueError("frontmatter block is not closed")
    head = parts[0][3:]
    body = parts[1]
    if body.startswith("-"):
        raise ValueError("frontmatter block is not closed")
    meta = yaml.safe_load(head) or {}
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a mapping")
    return meta, body.lstrip("\n")


def parse_skill(text: str, path: Path | None = None, source: str = "file") -> Skill:
    meta, body = split_frontmatter(text)
    name = str(meta.get("name") or "").strip()
    use = str(meta.get("use") or "").strip()
    if not name:
        raise ValueError("frontmatter needs a name")
    if not use:
        raise ValueError("frontmatter needs a use line")
    slug = str(meta.get("slug") or _slugify(name))
    band = meta.get("band_width")
    return Skill(
        name=name,
        slug=slug,
        use=use,
        inputs=_as_list(meta.get("inputs")),
        access=[str(a) for a in _as_list(meta.get("access"))],
        steps=[str(s) for s in _as_list(meta.get("steps"))],
        validation=[str(v) for v in _as_list(meta.get("validation"))],
        returns=str(meta.get("return") or ""),
        approvals=[str(a) for a in _as_list(meta.get("approvals"))],
        band_width=float(band) if band is not None else None,
        body=body,
        path=path,
        size=len(text.encode("utf-8")),
        source=source,
    )


def load_skill(path: Path, config: SkillConfig | None = None) -> LoadResult:
    config = config or SkillConfig()
    path = Path(path)
    if path.is_dir() or not path.suffix:
        path = path / SKILL_FILENAME
    if path.suffix.lower() != ".md":
        return LoadResult(SkillState.nothing, Reason.unsupported, path=path, detail=path.suffix)
    if not path.exists():
        return LoadResult(SkillState.failed, Reason.empty, path=path, detail="missing")
    size = path.stat().st_size
    if size == 0:
        return LoadResult(SkillState.nothing, Reason.empty, path=path)
    if size > config.max_bytes:
        return LoadResult(SkillState.nothing, Reason.too_large, path=path, detail=f"{size} > {config.max_bytes}")
    try:
        skill = parse_skill(path.read_text(encoding="utf-8"), path=path)
    except (ValueError, yaml.YAMLError) as e:
        return LoadResult(SkillState.failed, Reason.invalid, path=path, detail=str(e))
    return LoadResult(SkillState.added, skill=skill, path=path)


def load_dir(root: Path, config: SkillConfig | None = None) -> list[LoadResult]:
    """Load every `<root>/*/SKILL.md`, sorted by directory name. A folder
    with no SKILL.md of its own groups skills (`skills/anthropic/docx`, with
    one license file for the group) and is searched one level down."""
    root = Path(root)
    if not root.exists():
        return []
    results = []
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        if (child / SKILL_FILENAME).exists():
            results.append(load_skill(child, config))
        else:
            results.extend(load_skill(sub, config) for sub in sorted(p for p in child.iterdir() if p.is_dir())
                           if (sub / SKILL_FILENAME).exists())
    return results


def _as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _slugify(name: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "skill"

"""bot-prompts/: a bot's identity as one markdown file.

Frontmatter carries `name`, `role`, an optional `skills` list of refs, and an
optional `mcps` list of MCP servers the bot always gets (the rest are routed),
and an optional `studio: true` that marks a design bot;
the body is the instructions. The file stem is the bot id, so ids are
stable across restarts and readable in room mentions (@research-partner).

Only the identity lives here. What the bot remembers, the skills injected
for a task, and the closing instructions about tools and approvals are
composed by the harness each turn (see `Bot.system_prompt`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.skills.loader import split_frontmatter

SUFFIX = ".md"
PLACES = ("text", "images")  # what a bot may place in another bot's design


@dataclass
class BotPrompt:
    id: str
    name: str
    role: str
    instructions: str = ""
    skills: list[str] | None = None  # None: not listed, the harness attaches every catalog skill
    mcps: list[str] | None = None  # MCP servers loaded on every task, whatever routing scores; None: routing only
    studio: bool = False  # a design bot: the GUI shows its design preview pane; set only in the file
    places: list[str] = field(default_factory=list)  # what this bot can place in another bot's design: "text", "images"; set only in the file
    closed: bool = False  # the bot gets only the tools of its own `mcps` (no built-ins, no routed servers); set only in the file


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "bot"


def parse(text: str, bot_id: str) -> BotPrompt:
    meta, body = split_frontmatter(text)
    name, role = meta.get("name"), meta.get("role")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("frontmatter needs a name")
    if not isinstance(role, str) or not role.strip():
        raise ValueError("frontmatter needs a role")
    skills = meta.get("skills")
    if skills is not None and (not isinstance(skills, list) or not all(isinstance(s, str) for s in skills)):
        raise ValueError("skills must be a list of refs")
    mcps = meta.get("mcps")
    if mcps is not None and (not isinstance(mcps, list) or not all(isinstance(s, str) for s in mcps)):
        raise ValueError("mcps must be a list of MCP server names")
    studio = meta.get("studio", False)
    if not isinstance(studio, bool):
        raise ValueError("studio must be true or false")
    places = meta.get("places", [])
    if not isinstance(places, list) or not all(isinstance(x, str) and x in PLACES for x in places):
        raise ValueError("places must be a list of: text, images")
    if places and studio:
        raise ValueError("a studio bot cannot have places")
    closed = meta.get("closed", False)
    if not isinstance(closed, bool):
        raise ValueError("closed must be true or false")
    if closed and not mcps:
        raise ValueError("a closed bot needs mcps: the servers it may use")
    if closed and (studio or places):
        raise ValueError("a closed bot cannot be a studio bot or have places")
    return BotPrompt(id=bot_id, name=name.strip(), role=role.strip(), instructions=body.strip(), skills=skills, mcps=mcps, studio=studio, places=list(places), closed=closed)


def render(p: BotPrompt) -> str:
    meta: dict = {"name": p.name, "role": p.role}
    if p.skills is not None:
        meta["skills"] = list(p.skills)
    if p.mcps:
        meta["mcps"] = list(p.mcps)
    if p.studio:
        meta["studio"] = True
    if p.places:
        meta["places"] = list(p.places)
    if p.closed:
        meta["closed"] = True
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=None).strip()
    return f"---\n{head}\n---\n{p.instructions.strip()}\n"


def load_dir(path: Path, *, report: bool = False):
    """Every `*.md` under `path`, sorted by name. With `report=True` returns
    (prompts, errors) where each error names the file; otherwise bad files
    are skipped."""
    prompts: list[BotPrompt] = []
    errors: list[str] = []
    if path.is_dir():
        for f in sorted(path.glob(f"*{SUFFIX}")):
            try:
                prompts.append(parse(f.read_text(encoding="utf-8"), f.stem))
            except (ValueError, yaml.YAMLError, OSError) as e:
                errors.append(f"{f.name}: {e}")
    return (prompts, errors) if report else prompts


def save(path: Path, p: BotPrompt, taken: frozenset[str] | set[str] = frozenset()) -> BotPrompt:
    """Write the prompt. An empty id gets a slug from the name, uniquified
    against existing files and the ids in `taken`; a known id rewrites that file in place."""
    path.mkdir(parents=True, exist_ok=True)
    if not p.id:
        base = slugify(p.name)
        p.id, n = base, 2
        while p.id in taken or (path / f"{p.id}{SUFFIX}").exists():
            p.id, n = f"{base}-{n}", n + 1
    (path / f"{p.id}{SUFFIX}").write_text(render(p), encoding="utf-8")
    return p


def delete(path: Path, bot_id: str) -> bool:
    f = path / f"{bot_id}{SUFFIX}"
    if f.exists():
        f.unlink()
        return True
    return False

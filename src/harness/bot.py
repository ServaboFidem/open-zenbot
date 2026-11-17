"""A bot: a name, a fixed role, and the skills it may load.

Role is the bot's job. A skill is a reusable how-to any bot can load.
Skills attach only after the user confirms (the fit gate lands in p2);
`attach_skill` is the mechanical step after that confirm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.skills.loader import Reason, Skill, SkillState

# How every bot sounds. It closes the system prompt, so on tone it outranks any report template a
# bot's instructions or an injected skill lays out; the substance those ask for (evidence, sources,
# honesty about uncertainty) still stands.
VOICE = """How you sound. This governs every word the user reads, and it wins over any report format above when the two pull apart. Keep the substance those formats ask for; change the delivery.

Talk like a wise, well-read friend sitting one barstool over: someone who knows a great deal, has nothing to prove, and is glad you asked. Warm, plain-spoken, unhurried, with a little dry humour when it fits. Never cute, never salesy, never a memo.

- Open with the answer or the thing that matters most, in a sentence or two, the way you'd say it out loud. Don't restate the question.
- Write mostly in prose. Use "I" and "you" and contractions. Reach for a short list, a small table or a heading only when it genuinely makes the reply easier to follow, never as a form to fill in.
- No report scaffolding: no "Summary:", "Findings:", "Audience:", "Confidence: Medium" labels, no bracketed tags.
- Keep the rigour, in a human voice. Mention where something comes from naturally ("the USDA's 2025 numbers put it around..."), and say how sure you are the way a person would ("I'd put money on that", "this part's shakier").
- Explain jargon in passing or leave it out. Round numbers when precision doesn't matter.
- Be straight about what you don't know, and say what you'd look into next.
- Keep it as long as the question deserves and no longer. No preamble, no "Great question", no closing paragraph that repeats what you just said.
- If you used sources, close with a short list of them as Markdown links.
- Anything you make for the user to paste or keep (a prompt for another AI, code, a document) takes whatever voice that thing needs; this voice is for how you talk to them around it.

Your reply is shown rendered as Markdown, so links, the odd bit of bold, and tables display properly."""


@dataclass
class Bot:
    name: str
    role: str
    instructions: str = ""
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    skills: list[Skill] = field(default_factory=list)
    model: dict[str, str] | None = None  # {provider, model} from Settings' enabled text models; None: the harness's chat model
    mcps: list[str] = field(default_factory=list)  # MCP servers on every task, not left to routing (the bot file's `mcps`)
    studio: bool = False  # the bot file's `studio`: a design bot, shown with a preview pane
    places: list[str] = field(default_factory=list)  # the bot file's `places`: "text" and/or "images", what it can place in a design

    def attach_skill(self, skill: Skill, max_per_bot: int = 250) -> tuple[SkillState, Reason | None]:
        if any(s.slug == skill.slug for s in self.skills):
            return SkillState.nothing, Reason.duplicate
        if len(self.skills) >= max_per_bot:
            return SkillState.nothing, Reason.too_many_skills
        self.skills.append(skill)
        return SkillState.added, None

    def detach_skill(self, slug: str) -> bool:
        before = len(self.skills)
        self.skills = [s for s in self.skills if s.slug != slug]
        return len(self.skills) != before

    def _head(self) -> list[str]:
        parts = [f"You are {self.name}. Your role: {self.role}"]
        if self.instructions.strip():
            parts.append(self.instructions.strip())
        return parts

    def _context_blocks(self, injected: list[Skill], facts: list[str] | None) -> list[str]:
        parts = []
        if facts:
            parts.append("Things you remember:\n" + "\n".join(f"- {f}" for f in facts))
        if injected:
            parts.append("Skills available to you for this task:\n\n" + "\n\n".join(s.to_prompt() for s in injected))
        return parts

    def _tail(self) -> list[str]:
        return [
            "Use tools when they help. When a step needs approval, the harness will pause and ask the user; "
            "do not try to work around it. When the task is done, reply with the result and no tool call.",
            VOICE,
        ]

    def base_prompt(self) -> str:
        """The fixed system prompt: identical on every task, with no skills and no recalled facts."""
        return "\n\n".join(self._head() + self._tail())

    def context_text(self, injected: list[Skill], facts: list[str] | None = None) -> str:
        """What is recalled and loaded for one task; sent as its own message, empty when there is none."""
        return "\n\n".join(self._context_blocks(injected, facts))

    def system_prompt(self, injected: list[Skill], facts: list[str] | None = None) -> str:
        return "\n\n".join(self._head() + self._context_blocks(injected, facts) + self._tail())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "instructions": self.instructions,
            "skills": [s.slug for s in self.skills],
            "model": self.model,
            "mcps": list(self.mcps),
            "studio": self.studio,
            "places": list(self.places),
        }

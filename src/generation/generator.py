"""The generator boundary. Prose lives here and only here.

The decider scores; the generator writes. Summaries for compaction,
the folded summary across rounds, and the short summary plus names,
dates, and facts for each vault document all come through this module.
It uses the provider registry; the model is config, not a constant.
The decider package never imports this module (a test asserts it).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.providers import tracing
from src.providers.interface import Message, Provider

SUMMARY_SYSTEM = (
    "You write compact, faithful summaries of agent transcripts for later reuse as context. "
    "Keep: the goal; decisions and why; files and commands touched; failed approaches and why they failed; "
    "confirmed facts; and verbatim identifiers such as paths, ticket ids, branch names, and table names. "
    "Drop pleasantries and repetition. Never invent. Plain text, no preamble."
)

FOLD_SYSTEM = (
    "You merge an earlier summary with a newer summary of the same agent session into one summary. "
    "Keep everything still true and relevant; drop what the newer part supersedes. "
    "Preserve verbatim identifiers. Plain text, no preamble."
)

TURN_NOTE_SYSTEM = (
    "You write an agent's private note to itself for its next turn in the same conversation. You get its "
    "previous note, how this turn ended, and this turn's tool calls with their results. Write plain text in up "
    "to five lines, leaving out any that would be empty: 'Found:' (inputs located: paths, counts, sizes), "
    "'Made:' (files written: full paths, and whether attached), 'Worked:' (commands, options or recipes that "
    "succeeded, verbatim), 'Failed:' (what failed and why), 'Left:' (what is unfinished; always present unless "
    "the turn ended done with nothing left). List a file under Made only when a tool result shows it was "
    "written. Never claim work the results do not show; say plainly when a result is unknown. Keep what is "
    "still true from the previous note and drop what this turn superseded. Preserve paths and identifiers "
    "verbatim. No keys, passwords or tokens. No preamble. At most {max_chars} characters."
)

VAULT_SYSTEM = (
    "You read one work document and return JSON only, with keys: "
    "summary (2 to 4 sentences), names (list of people, teams, products, systems), "
    "dates (list, verbatim as written), facts (list of short factual statements a bot should remember). "
    "Never include credentials, tokens, or passwords. No prose outside the JSON."
)

ROOM_TOPIC_SYSTEM = (
    "You name a conversation room from its first message. Return JSON only: "
    '{"name": a short human room name of at most 60 characters, "topic": one short sentence naming the subject the room is about, e.g. "Becoming a beekeeper in Ohio: registration, clubs and first hives" — never start with "The text", "The user" or "This room"}. '
    "No prose outside the JSON."
)

ENTITY_KINDS = ("organization", "product", "person", "place", "thing")

LEARN_SYSTEM = (
    "You read one web page and return JSON only, with keys: "
    "summary (one short paragraph of what the page says); "
    "entities (list of objects {name, kind, related_to, relation}: every organization, product, person, place, or thing the page is about; "
    "kind is one of organization, product, person, place, thing; "
    "related_to is the name of another entity on the page this one is tied to, or null; "
    "relation is a short verb phrase the page itself states for that tie, written so that '<name> <relation> <related_to>' "
    "reads as a true sentence with name as its subject, such as is sold by, is a feature of, is located in, is founder of, or null); "
    "facts (list of objects {text, about}: short factual statements worth remembering, each about one entity named in entities). "
    "Some names may be listed as already known: tie new things to them where the page supports it. "
    "Do not invent relations the page does not state. Never include credentials, tokens, or passwords. No prose outside the JSON."
)


@dataclass
class VaultSummary:
    summary: str
    names: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    parsed: bool = True  # False when the model did not return clean JSON

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "names": self.names, "dates": self.dates, "facts": self.facts, "parsed": self.parsed}


@dataclass
class PageEntity:
    name: str
    kind: str  # one of ENTITY_KINDS
    related_to: str | None = None
    relation: str | None = None


@dataclass
class PageFact:
    text: str
    about: str  # an entity name from the same PageLearning


@dataclass
class PageLearning:
    summary: str
    entities: list[PageEntity] = field(default_factory=list)
    facts: list[PageFact] = field(default_factory=list)
    parsed: bool = True  # False when the model did not return clean JSON


class Generator:
    kind = "generator"

    def __init__(self, provider: Provider, *, model: str | None = None, chunk_chars: int = 16_000) -> None:
        self.provider = provider
        self.model = model
        self.chunk_chars = chunk_chars

    async def _ask(self, system: str, user: str, step: str) -> str:
        """`step` names the Langfuse generation unless a caller already named the work (compaction does)."""
        with tracing.llm_step(tracing.step_name(step)):
            c = await self.provider.complete([Message("system", system), Message("user", user)], model=self.model)
        return c.message.content.strip()

    # -- compaction -------------------------------------------------------

    async def summarize(self, text: str, *, goal: str | None = None) -> str:
        prefix = f"The session goal: {goal}\n\n" if goal else ""
        return await self._ask(SUMMARY_SYSTEM, prefix + text, "summarize-text")

    def chunk(self, text: str) -> list[str]:
        """Split near `chunk_chars`, preferring line boundaries."""
        if len(text) <= self.chunk_chars:
            return [text] if text else []
        out, buf = [], ""
        for line in text.splitlines(keepends=True):
            if len(buf) + len(line) > self.chunk_chars and buf:
                out.append(buf)
                buf = ""
            while len(line) > self.chunk_chars:
                out.append(line[: self.chunk_chars])
                line = line[self.chunk_chars :]
            buf += line
        if buf:
            out.append(buf)
        return out

    async def compact(self, rendered: str, *, goal: str | None = None) -> str:
        """Summarize a rendered transcript. Chunks summarize in parallel
        and join as Part N of M; one chunk returns a single summary."""
        chunks = self.chunk(rendered)
        if not chunks:
            return ""
        if len(chunks) == 1:
            return await self.summarize(chunks[0], goal=goal)
        parts = await asyncio.gather(*(self.summarize(c, goal=goal) for c in chunks))
        n = len(parts)
        return "\n\n".join(f"Part {i} of {n}\n{p}" for i, p in enumerate(parts, 1))

    async def fold(self, previous: str, latest: str) -> str:
        """Carry a summary forward: merge the previous fold with the newest summary."""
        if not previous:
            return latest
        return await self._ask(FOLD_SYSTEM, f"EARLIER SUMMARY:\n{previous}\n\nNEWER SUMMARY:\n{latest}", "fold-summaries")

    async def turn_note(self, previous: str, rendered: str, outcome: str, max_chars: int) -> str:
        """A bot's note to its next turn: the previous note folded with this turn's tool calls and results."""
        user = (f"PREVIOUS NOTE:\n{previous or '(none)'}\n\nHOW THIS TURN ENDED: {outcome}\n\n"
                f"THIS TURN'S TOOL CALLS AND RESULTS:\n{rendered}")
        return await self._ask(TURN_NOTE_SYSTEM.format(max_chars=max_chars), user, "write-turn-note")

    async def room_topic(self, text: str) -> tuple[str, str]:
        """(name, topic) for a room: a short name and a one-sentence topic."""
        raw = await self._ask(ROOM_TOPIC_SYSTEM, text, "name-room-topic")
        parsed = _extract_json(raw)
        if isinstance(parsed, dict):
            name, topic = _prose(parsed.get("name"))[:60].strip(), _prose(parsed.get("topic"))
            if name and topic:
                return name, topic
        return text[:60].strip(), text[:200].strip()

    # -- vault ------------------------------------------------------------

    async def vault_summary(self, markdown: str, *, title: str = "") -> VaultSummary:
        head = f"Document title: {title}\n\n" if title else ""
        raw = await self._ask(VAULT_SYSTEM, head + markdown, "summarize-document")
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict) or not parsed.get("summary"):
            return VaultSummary(summary=raw[:1000], parsed=False)
        return VaultSummary(
            summary=_prose(parsed.get("summary")),
            names=_items(parsed.get("names")),
            dates=_items(parsed.get("dates")),
            facts=_items(parsed.get("facts")),
        )

    # -- the web --------------------------------------------------------------

    async def learn_page(self, markdown: str, *, focus: str = "", known: list[str] = ()) -> PageLearning:
        """One read of a fetched page: a summary, the entities it is about with
        the relations it states between them, and facts about them. `known`
        names what memory already holds so the model ties new things to it."""
        head = (f"Focus: {focus}\n" if focus else "") + (f"Already known: {', '.join(known)}\n" if known else "")
        raw = await self._ask(LEARN_SYSTEM, (head + "\n" if head else "") + markdown, "learn-from-page")
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict) or not parsed.get("summary"):
            return PageLearning(summary=raw[:1000], parsed=False)
        entities = [PageEntity(_prose(e.get("name")), _kind(e.get("kind")), _opt(e.get("related_to")), _opt(e.get("relation")))
                    for e in _dicts(parsed.get("entities"))]
        facts = [PageFact(_prose(f.get("text")), _prose(f.get("about"))) for f in _dicts(parsed.get("facts"))]
        return PageLearning(summary=_prose(parsed.get("summary")), entities=[e for e in entities if e.name],
                            facts=[f for f in facts if f.text and f.about])


def _dicts(v: Any) -> list[dict[str, Any]]:
    return [d for d in (v or []) if isinstance(d, dict)] if isinstance(v, list) else []


def _kind(v: Any) -> str:
    k = str(v or "").strip().lower()
    return k if k in ENTITY_KINDS else "thing"


def _opt(v: Any) -> str | None:
    return _prose(v) or None


def _prose(v: Any) -> str:
    """A summary the model sent as a list of sentences reads as one
    paragraph, not as a Python list literal."""
    if isinstance(v, (list, tuple)):
        return " ".join(_prose(x) for x in v if x).strip()
    return str(v or "").strip()


def _items(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [_prose(x) for x in v if _prose(x)]


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _extract_json(text: str) -> Any:
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for c in candidates:
        c = c.strip()
        start, end = c.find("{"), c.rfind("}")
        if start < 0 or end < 0:
            continue
        try:
            return json.loads(c[start : end + 1])
        except ValueError:
            continue
    return None

"""The onboarding interview. The harness fixes the topics and their
order; the Personal assistant writes every line it says. Each answer
goes through one extraction call and becomes shared facts (people as
entities), then one more call writes the next question. Nothing here
survives a restart except the facts, which is the point of it.
"""

from __future__ import annotations

import asyncio

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.providers import tracing
from src.generation.generator import _extract_json
from src.memory.graph import SHARED, MemoryGraph, Tier
from src.memory.stores import PERSONAL
from src.providers.interface import Message, Provider

from .bot import Bot

TOPICS = ("name", "family", "work", "hobbies", "likes", "people")
NEW_TOPICS = ("family", "likes", "people")  # what an install from before the personal graph is offered once
WORK_RELATIONS = ("owns", "runs", "works_at")
ASSISTANT_ID = "personal-assistant"

EXTRACT_SYSTEM = (
    "You turn one answer from a getting-to-know-you interview into facts for a shared memory. Return JSON only: "
    '{"facts": [{"text": "...", "entities": ["..."]}], "owner": {"name": "...", "aliases": ["..."]} or null, '
    '"people": [{"name": "...", "relation": "..."}], "orgs": [{"name": "...", "relation": "owns|runs|works_at"}]}. '
    "Write each fact as one plain sentence that names both ends in full, using the owner's full name when it is known "
    '("Alex Rivera\'s wife is Jamie Rivera."). For the name topic write exactly one fact, "The user\'s name is X.", '
    "with X in entities, and fill owner with the full name and any nicknames or short forms as aliases. "
    "For the family and people topics list each named person in people with their relation to the owner (wife, son, friend), "
    "and for the work topic list each organization the owner owns, runs or works at in orgs. "
    "Put every named person in entities. Use a known person's name exactly as given below; resolve my wife, my husband "
    "or my spouse to the known spouse, and for other relatives record a person only when a name is given. "
    "Leave owner null, and people and orgs empty, when the answer gives none. When the answer gives nothing "
    '(a skip, a joke, "rather not say"), return {"facts": [], "owner": null, "people": [], "orgs": []}. '
    "Never include credentials. No prose outside the JSON."
)
# keyed by the topic to ask about next; "done" closes the interview
NEXT_QUESTION = {
    "name": "Acknowledge briefly, then ask for their name, in one sentence.",
    "family": "Acknowledge briefly, then ask about their family: partner, children, parents, in one sentence.",
    "work": "Acknowledge briefly, then ask what they do for work or with their days, and which companies they own, run or work at, in one sentence.",
    "hobbies": "Acknowledge briefly, then ask about their hobbies and what they enjoy outside work, in one sentence.",
    "likes": "Acknowledge briefly, then ask what they like and dislike: food, music, tools, in one sentence.",
    "people": "Acknowledge briefly, then ask about the other important people in their life, friends and colleagues, in one sentence.",
    "done": "Thank them in one or two sentences and say that if they press the folder button you can learn more from a folder of their files.",
}
OPENER = "Introduce yourself as {name} in two sentences and ask for the user's name. Do not use any other name for yourself."
REOPENER = "Say hello again as {name} in one sentence and say you have a few new things to ask."


@dataclass
class Interview:
    id: str
    bot_id: str
    step: int = 0  # index into TOPICS; len(TOPICS) means done
    transcript: list[tuple[str, str]] = field(default_factory=list)  # ("assistant" | "user", text)
    fact_ids: list[str] = field(default_factory=list)
    fact_texts: list[str] = field(default_factory=list)
    name: str | None = None
    topics: tuple[str, ...] = TOPICS

    @property
    def done(self) -> bool:
        return self.step >= len(self.topics)

    @property
    def topic(self) -> str | None:
        return None if self.done else self.topics[self.step]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "bot_id": self.bot_id, "step": self.step, "topic": self.topic, "done": self.done,
                "transcript": [{"author": a, "text": t} for a, t in self.transcript], "fact_ids": list(self.fact_ids)}


class Interviewer:
    kind = "onboarding"

    def __init__(self, provider: Provider, memory: MemoryGraph, ledger: Any, *, model: str | None = None,
                 profile: Any = None, extract_queue: Any = None) -> None:
        self.provider = provider
        self.memory = memory
        self.ledger = ledger
        self.model = model
        self.profile = profile  # ProfileMemory, or None on the local backend (facts then go through `memory`)
        self.extract_queue = extract_queue
        self.interviews: dict[str, Interview] = {}
        self._bots: dict[str, Bot] = {}

    def get(self, interview_id: str) -> Interview | None:
        return self.interviews.get(interview_id)

    async def _say(self, bot: Bot, iv: Interview, instruction: str) -> str:
        messages = [Message("system", bot.system_prompt([], iv.fact_texts))]
        messages += [Message(author, text) for author, text in iv.transcript]
        messages.append(Message("user", instruction))
        with tracing.llm_step("ask-onboarding-question"):
            c = await self.provider.complete(messages, model=self.model)
        return c.message.content.strip()

    async def start(self, bot: Bot, topics: tuple[str, ...] = TOPICS) -> tuple[Interview, str]:
        iv = Interview(uuid4().hex[:12], bot.id, topics=tuple(topics))
        owner = self.profile.people.owner() if self.profile is not None else None
        iv.name = owner.name if owner else None
        if topics[0] == "name":
            instruction = OPENER.format(name=bot.name)
        else:
            instruction = REOPENER.format(name=bot.name) + " " + NEXT_QUESTION[topics[0]]
        opener = await self._say(bot, iv, instruction)
        iv.transcript.append(("assistant", opener))
        self.interviews[iv.id] = iv
        self._bots[iv.id] = bot
        self.ledger.append(self.kind, bot.id, "", interview_id=iv.id, event="start", topics=list(iv.topics))
        return iv, opener

    def _known(self, iv: Interview) -> str:
        """The owner and the known people, so the model writes names the way the registry has them."""
        owner = self.profile.people.owner() if self.profile is not None else None
        lines = [f"User's name: {owner.name if owner else iv.name or 'unknown'}"]
        if owner and owner.aliases:
            lines.append(f"Owner aliases: {', '.join(owner.aliases)}")
        if self.profile is not None:
            known = [f"{p.name} ({p.relation})" if p.relation else p.name for p in self.profile.people.all() if not p.is_owner]
            if known:
                lines.append(f"Known people: {'; '.join(known)}")
        return "\n".join(lines)

    async def _extract(self, iv: Interview, topic: str, text: str) -> dict[str, Any]:
        user = f"Topic: {topic}\n{self._known(iv)}\nAnswer: {text}"
        with tracing.llm_step("extract-profile-facts"):
            c = await self.provider.complete([Message("system", EXTRACT_SYSTEM), Message("user", user)], model=self.model)
        parsed = _extract_json(c.message.content)
        parsed = parsed if isinstance(parsed, dict) else {}
        facts = []
        for f in parsed.get("facts") or []:
            if isinstance(f, dict) and str(f.get("text", "")).strip():
                facts.append({"text": str(f["text"]).strip(), "entities": [str(e) for e in f.get("entities") or [] if str(e).strip()]})
        owner = parsed.get("owner")
        name = str(owner.get("name") or "").strip() if isinstance(owner, dict) else ""
        aliases = owner.get("aliases") if isinstance(owner, dict) else None
        aliases = [aliases] if isinstance(aliases, str) else aliases if isinstance(aliases, list) else []
        people = [{"name": str(p["name"]).strip(), "relation": str(p.get("relation") or "").strip()}
                  for p in parsed.get("people") or [] if isinstance(p, dict) and str(p.get("name") or "").strip()]
        orgs = [{"name": str(o["name"]).strip(), "relation": str(o.get("relation") or "").strip()}
                for o in parsed.get("orgs") or [] if isinstance(o, dict) and str(o.get("name") or "").strip()
                and str(o.get("relation") or "").strip() in WORK_RELATIONS]
        return {"facts": facts, "people": people, "orgs": orgs,
                "owner": {"name": name, "aliases": [str(a).strip() for a in aliases or [] if str(a).strip()]} if name else None}

    def _seed(self, topic: str, extracted: dict[str, Any]) -> None:
        """Registry writes from one answer. Only the profile has registries; the owner goes first."""
        if self.profile is None:
            return
        if topic == "name" and extracted["owner"]:
            self.profile.people.set_owner(extracted["owner"]["name"], extracted["owner"]["aliases"])
        if topic in ("family", "people"):
            for p in extracted["people"]:
                known = self.profile.people.find(p["name"])
                if known is not None and known.is_owner:
                    continue  # the owner's own entry keeps its relation
                self.profile.people.add(p["name"], relation=p["relation"], tier="close")
        if topic == "work":
            for o in extracted["orgs"]:
                self.profile.orgs.add(o["name"], o["relation"])

    async def _remember(self, bot: Bot, topic: str, f: dict[str, Any]) -> Any:
        source = f"onboarding:{topic}"
        if self.profile is not None:
            from src.memory.extract_queue import remember_shared

            return await remember_shared(self.profile, self.extract_queue, f["text"], bot_id=bot.id, source=source,
                                         entities=f["entities"], stores=[PERSONAL])
        return await asyncio.to_thread(self.memory.remember, f["text"], SHARED, Tier.project, bot_id=bot.id,
                                       entities=f["entities"], source=source)  # a store write: off the loop

    async def reply(self, interview_id: str, text: str) -> dict[str, Any]:
        iv = self.interviews[interview_id]
        bot = self._bots[interview_id]
        if iv.done:
            raise ValueError("the interview is already done")
        topic = iv.topic or ""
        extracted = await self._extract(iv, topic, text)
        facts = extracted["facts"]
        # the next line is asked before anything is stored, so a provider error leaves the interview untouched
        draft = Interview(iv.id, iv.bot_id, iv.step, list(iv.transcript) + [("user", text)], list(iv.fact_ids),
                          list(iv.fact_texts) + [f["text"] for f in facts], iv.name, iv.topics)
        upcoming = iv.topics[iv.step + 1] if iv.step + 1 < len(iv.topics) else "done"
        message = await self._say(bot, draft, NEXT_QUESTION[upcoming])
        self._seed(topic, extracted)
        added = []
        for f in facts:
            fact = await self._remember(bot, topic, f)
            added.append(fact.to_dict())
            iv.fact_ids.append(fact.id)
            iv.fact_texts.append(fact.text)
        if topic == "name":
            if extracted["owner"]:
                iv.name = extracted["owner"]["name"]
            elif facts and facts[0]["entities"]:
                iv.name = facts[0]["entities"][0]
        iv.transcript += [("user", text), ("assistant", message)]
        iv.step += 1
        self.ledger.append(self.kind, bot.id, "", interview_id=iv.id, topic=topic, fact_ids=[f["id"] for f in added], done=iv.done)
        return {"message": message, "step": iv.step, "done": iv.done, "facts_added": added}

"""Store routing: one cheap model call decides which shared stores a text
belongs in (`personal`, `org:<slug>`, `world`) and names any new organization
the owner owns, runs or works at.

The answer is cleaned, never trusted: an org slug the registry does not know
is dropped (unless it is a new org the owner holds, reported in the same
answer), an answer with no valid store goes to `world`, and a failed call
falls back to `personal` and `world` so nothing about the owner is lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, get_args

from src.providers import tracing
from src.generation.generator import _extract_json
from src.providers.interface import Message, Provider

from .ingest import _capped
from .stores import PERSONAL, WORLD, org_store, slugify
from .registry import OrgEntry, OrgRelation

OWNER_RELATIONS: tuple[str, ...] = get_args(OrgRelation)  # an org gets its own store only for these

SYSTEM = (
    "You sort a text into knowledge stores. Answer with JSON only, no prose:\n"
    '{"stores": [...], "new_orgs": [{"name": "...", "relation": "..."}]}\n'
    'Stores: "personal" for anything about the owner and their life: themselves, the people they know, '
    "their work history, skills, likes, dislikes, places, things, plans and commitments. "
    '"org:<slug>" for the internal work of one organization listed below (its people, teams, products, '
    'customers, vendors, projects, decisions). "world" for research and general knowledge that says '
    "nothing about the owner. A text may belong in several stores.\n"
    'new_orgs: an organization not listed below that the text says the owner owns, runs or works at, with '
    'relation "owns", "runs" or "works_at". To also file the text under it, add "org:<slug>" to stores, the '
    "slug being its name in lower case with underscores. Never list vendors, customers or companies that "
    "are merely mentioned."
)


@dataclass
class Route:
    stores: list[str]
    new_orgs: list[tuple[str, str]] = field(default_factory=list)  # (name, relation) as the model reported them


class StoreRouter:
    def __init__(self, provider: Provider, model: str | None = None, *, ledger: Any = None, chunk_chars: int = 16_000) -> None:
        self.provider = provider
        self.model = model
        self.ledger = ledger
        self.chunk_chars = chunk_chars  # the generator's cap for one summary input

    async def route(self, text: str, orgs: list[OrgEntry], owner: str) -> Route:
        known = "\n".join(f"- org:{o.slug} ({o.name}; the owner {o.relation.replace('_', ' ')} it)" for o in orgs)
        user = (
            f"The owner is {owner or 'not named yet'}.\n"
            f"Organizations with their own store:\n{known or '(none yet)'}\n\n"
            f"Text:\n{_capped(text, self.chunk_chars)}"
        )
        try:
            with tracing.llm_step("route-memory"):
                c = await self.provider.complete([Message("system", SYSTEM), Message("user", user)], model=self.model)
            data = _extract_json(c.message.content)
            if not isinstance(data, dict):
                raise ValueError(f"router answer is not a JSON object: {c.message.content[:200]!r}")
        except Exception as e:
            if self.ledger is not None:
                self.ledger.append("error", "", "", where="route", error=f"{type(e).__name__}: {e}"[:300],
                                   action="write to personal and world")
            return Route([PERSONAL, WORLD], [])
        return self._clean(data, orgs)

    @staticmethod
    def _clean(data: dict, orgs: list[OrgEntry]) -> Route:
        new_orgs = []
        for o in data.get("new_orgs") or []:
            if isinstance(o, dict) and str(o.get("name") or "").strip():
                new_orgs.append((str(o["name"]).strip(), str(o.get("relation") or "").strip()))
        allowed = {PERSONAL, WORLD, *(org_store(o.slug) for o in orgs)}
        allowed |= {org_store(slugify(n)) for n, r in new_orgs if r in OWNER_RELATIONS}
        raw = data.get("stores") or []
        if isinstance(raw, str):  # "world" for ["world"]
            raw = [raw]
        stores = list(dict.fromkeys(s for s in raw if isinstance(s, str) and s in allowed))
        return Route(stores or [WORLD], new_orgs)


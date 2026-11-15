"""Onboarding: the personal assistant interviews a new user over six
fixed topics; each answer becomes shared facts the other bots recall."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.bot_prompts import parse
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.memory.registry import OrgRegistry, PeopleRegistry
from src.providers.interface import ProviderError
from src.skills.catalog import Catalog

from .conftest import ScriptedProvider, completion

PROMPT = Path(__file__).resolve().parents[1] / "bot-prompts" / "personal-assistant.md"


def harness(vault: Path, tmp_path: Path, provider, *, with_assistant: bool = True) -> Harness:
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path)
    h = Harness(config=cfg, provider=provider, ledger=Ledger(vault), catalog=Catalog())
    if with_assistant:
        p = parse(PROMPT.read_text(encoding="utf-8"), PROMPT.stem)
        h.add_bot(Bot(p.name, p.role, p.instructions, id=p.id))
    return h


def client(h: Harness) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost")


def fact(text: str, *entities: str) -> str:
    import json
    return json.dumps({"facts": [{"text": text, "entities": list(entities)}]})


def payload(facts=(), owner=None, people=(), orgs=()) -> str:
    import json
    return json.dumps({"facts": [{"text": t, "entities": list(e)} for t, e in facts], "owner": owner,
                       "people": list(people), "orgs": list(orgs)})


class FakeProfile:
    """The slice of ProfileMemory the interview uses: registries and the shared write."""

    def __init__(self, root: Path) -> None:
        self.people = PeopleRegistry(root / "people.json")
        self.orgs = OrgRegistry(root / "orgs.json")
        self.writes: list[dict] = []

    async def remember_shared(self, text, *, bot_id, source, entities=None, stores=None, tier=None, session_id=""):
        from types import SimpleNamespace
        self.writes.append({"text": text, "source": source, "stores": stores, "entities": entities})
        fid = f"f{len(self.writes)}"
        return SimpleNamespace(id=fid, text=text, to_dict=lambda: {"id": fid, "text": text, "source": source, "entities": entities})


def with_profile(h: Harness, tmp_path: Path) -> FakeProfile:
    h.interviews.profile = fake = FakeProfile(tmp_path)
    return fake


async def test_interview_walks_the_six_topics_and_stores_facts(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Hi, I'm your assistant. What's your name?"),
        completion(fact("The user is called Alex.", "Alex")), completion("Nice to meet you, Alex. Tell me about your family."),
        completion('{"facts": []}'), completion("What do you do for work?"),
        completion(fact("Alex builds AI tooling.")), completion("What do you enjoy outside work?"),
        completion('{"facts": []}'), completion("What do you like?"),
        completion('{"facts": []}'), completion("Who are the important people in your life?"),
        completion('{"facts": [{"text": "Alex\'s partner is Sam.", "entities": ["Sam"]}, {"text": "Alex\'s daughter is Ava.", "entities": ["Ava"]}]}'),
        completion("Thanks! Press the folder button and I can learn from your files."),
    ])
    h = harness(vault, tmp_path, provider)
    async with client(h) as c:
        r = await c.post("/onboarding/start")
        assert r.status_code == 200, r.text
        iid = r.json()["interview"]["id"]
        assert r.json()["interview"]["topic"] == "name" and r.json()["message"].startswith("Hi")
        r = await c.post(f"/onboarding/{iid}/reply", json={"text": "Alex"})
        assert r.status_code == 200, r.text
        assert r.json()["step"] == 1 and r.json()["done"] is False
        assert r.json()["facts_added"][0]["text"] == "The user is called Alex."
        r = await c.post(f"/onboarding/{iid}/reply", json={"text": "rather not say"})
        assert r.json()["step"] == 2
        assert "The user is called Alex." in provider.calls[4][0].content  # the assistant remembers what it learned
        r = await c.post(f"/onboarding/{iid}/reply", json={"text": "I build AI tooling"})
        assert r.json()["step"] == 3
        r = await c.post(f"/onboarding/{iid}/reply", json={"text": "rather not say"})
        assert r.json()["step"] == 4 and r.json()["facts_added"] == []
        r = await c.post(f"/onboarding/{iid}/reply", json={"text": "rather not say"})
        assert r.json()["step"] == 5 and r.json()["facts_added"] == []
        r = await c.post(f"/onboarding/{iid}/reply", json={"text": "Sam and my daughter Ava"})
        assert r.json()["done"] is True and r.json()["message"].startswith("Thanks")
        added = r.json()["facts_added"]
        assert [f["entities"] for f in added] == [["sam"], ["ava"]] and {f["source"] for f in added} == {"onboarding:people"}
        facts = (await c.get("/memory")).json()
        assert len(facts) == 4 and {f["bot_id"] for f in facts} == {"personal-assistant"}
        assert len([e for e in h.ledger.tail(10, kind="onboarding") if "topic" in e.data]) == 6
        assert (await c.post(f"/onboarding/{iid}/reply", json={"text": "more"})).status_code == 409
        st = (await c.get(f"/onboarding/{iid}")).json()
        assert st["done"] is True and len(st["transcript"]) == 13 and st["transcript"][0]["author"] == "assistant"
        assert (await c.get("/onboarding/nope")).status_code == 404


async def test_a_provider_error_does_not_advance_the_step(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Hello! What's your name?"), ProviderError("boom"),
        completion(fact("The user is called Alex.", "Alex")), completion("What do you do?"),
    ])
    h = harness(vault, tmp_path, provider)
    async with client(h) as c:
        iid = (await c.post("/onboarding/start")).json()["interview"]["id"]
        r = await c.post(f"/onboarding/{iid}/reply", json={"text": "Alex"})
        assert r.status_code == 502
        st = (await c.get(f"/onboarding/{iid}")).json()
        assert st["step"] == 0 and len(st["transcript"]) == 1
        r = await c.post(f"/onboarding/{iid}/reply", json={"text": "Alex"})
        assert r.status_code == 200 and r.json()["step"] == 1


async def test_start_needs_a_provider_and_the_assistant(vault: Path, tmp_path: Path):
    async with client(harness(vault, tmp_path, None)) as c:
        assert (await c.post("/onboarding/start")).status_code == 503
    async with client(harness(vault, tmp_path, ScriptedProvider([completion("hi")]), with_assistant=False)) as c:
        assert (await c.post("/onboarding/start")).status_code == 404


async def test_name_topic_sets_owner(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Hi! Your name?"),
        completion(payload([("The user's name is Alex Rivera.", ["Alex Rivera"])], owner={"name": "Alex Rivera", "aliases": ["Alex", " "]})),
        completion("Tell me about your family."),
    ])
    h = harness(vault, tmp_path, provider)
    prof = with_profile(h, tmp_path)
    async with client(h) as c:
        iid = (await c.post("/onboarding/start")).json()["interview"]["id"]
        r = await c.post(f"/onboarding/{iid}/reply", json={"text": "Alex Rivera, call me Alex"})
        assert r.status_code == 200, r.text
    owner = prof.people.owner()
    assert owner is not None and owner.name == "Alex Rivera" and owner.aliases == ["Alex"]
    assert h.interviews.get(iid).name == "Alex Rivera"


async def test_family_topic_registers_close_people(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Hi! Your name?"),
        completion(payload([("The user's name is Alex Rivera.", ["Alex Rivera"])], owner={"name": "Alex Rivera", "aliases": []})),
        completion("Family?"),
        completion(payload([("Alex Rivera's wife is Jamie Rivera.", ["Alex Rivera", "Jamie Rivera"])],
                           people=[{"name": "Jamie Rivera", "relation": "wife"}, {"name": "  ", "relation": "son"}])),
        completion("Work?"),
    ])
    h = harness(vault, tmp_path, provider)
    prof = with_profile(h, tmp_path)
    async with client(h) as c:
        iid = (await c.post("/onboarding/start")).json()["interview"]["id"]
        await c.post(f"/onboarding/{iid}/reply", json={"text": "Alex Rivera"})
        r = await c.post(f"/onboarding/{iid}/reply", json={"text": "my wife is Jamie Rivera"})
        assert r.status_code == 200, r.text
    sarah = prof.people.find("Jamie Rivera")
    assert sarah is not None and sarah.tier == "close" and sarah.relation == "wife"
    assert [p.name for p in prof.people.all()] == ["Alex Rivera", "Jamie Rivera"]
    assert "User's name: Alex Rivera" in provider.calls[3][1].content  # the owner reaches the extractor


async def test_work_topic_registers_owned_org(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Hi! Your name?"),
        completion(payload(owner={"name": "Alex Rivera", "aliases": []})), completion("Family?"),
        completion(payload()), completion("Work?"),
        completion(payload([("Alex Rivera owns Acme Widgets.", ["Alex Rivera", "Acme Widgets"])],
                           orgs=[{"name": "Acme Widgets", "relation": "owns"}, {"name": "Somewhere", "relation": "dreams_of"},
                                 {"name": "", "relation": "runs"}])),
        completion("Hobbies?"),
    ])
    h = harness(vault, tmp_path, provider)
    prof = with_profile(h, tmp_path)
    async with client(h) as c:
        iid = (await c.post("/onboarding/start")).json()["interview"]["id"]
        for text in ("Alex Rivera", "skip", "I own Acme Widgets"):
            r = await c.post(f"/onboarding/{iid}/reply", json={"text": text})
            assert r.status_code == 200, r.text
    assert [(o.name, o.relation) for o in prof.orgs.all()] == [("Acme Widgets", "owns")]


async def test_new_topics_only_interview(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Welcome back! Tell me about your family."),
        completion(payload()), completion("What do you like?"),
        completion(payload()), completion("And other important people?"),
        completion(payload([("Alex Rivera's friend is Sam Lee.", ["Alex Rivera", "Sam Lee"])], people=[{"name": "Sam Lee", "relation": "friend"}])),
        completion("Thanks!"),
    ])
    h = harness(vault, tmp_path, provider)
    prof = with_profile(h, tmp_path)
    prof.people.set_owner("Alex Rivera", [])
    async with client(h) as c:
        r = await c.post("/onboarding/start", json={"topics": "new"})
        assert r.status_code == 200, r.text
        assert r.json()["interview"]["topic"] == "family"
        iid = r.json()["interview"]["id"]
        for expected in (1, 2, 3):
            r = await c.post(f"/onboarding/{iid}/reply", json={"text": "ok"})
            assert r.json()["step"] == expected
        assert r.json()["done"] is True
    assert prof.people.find("Sam Lee").relation == "friend"


async def test_facts_written_to_personal_store(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Hi! Your name?"),
        completion(payload([("The user's name is Alex Rivera.", ["Alex Rivera"])], owner={"name": "Alex Rivera", "aliases": []})),
        completion("Family?"),
    ])
    h = harness(vault, tmp_path, provider)
    prof = with_profile(h, tmp_path)
    async with client(h) as c:
        iid = (await c.post("/onboarding/start")).json()["interview"]["id"]
        await c.post(f"/onboarding/{iid}/reply", json={"text": "Alex Rivera"})
    assert [(w["stores"], w["source"]) for w in prof.writes] == [(["personal"], "onboarding:name")]


async def test_aliases_given_as_a_string_are_one_alias(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Hi! Your name?"),
        completion(payload(owner={"name": "Alex Rivera", "aliases": "Lexi"})), completion("Family?"),
    ])
    h = harness(vault, tmp_path, provider)
    prof = with_profile(h, tmp_path)
    async with client(h) as c:
        iid = (await c.post("/onboarding/start")).json()["interview"]["id"]
        await c.post(f"/onboarding/{iid}/reply", json={"text": "Alex Rivera"})
    assert prof.people.owner().aliases == ["Lexi"]


async def test_aliases_of_another_type_are_ignored(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Hi! Your name?"),
        completion(payload(owner={"name": "Alex Rivera", "aliases": 7})), completion("Family?"),
    ])
    h = harness(vault, tmp_path, provider)
    prof = with_profile(h, tmp_path)
    async with client(h) as c:
        iid = (await c.post("/onboarding/start")).json()["interview"]["id"]
        await c.post(f"/onboarding/{iid}/reply", json={"text": "Alex Rivera"})
    assert prof.people.owner().name == "Alex Rivera" and prof.people.owner().aliases == []


async def test_a_people_entry_for_the_owner_leaves_the_owner_alone(vault: Path, tmp_path: Path):
    provider = ScriptedProvider([
        completion("Family?"),
        completion(payload(people=[{"name": "Alex", "relation": "husband"}, {"name": "Jamie Rivera", "relation": "wife"}])),
        completion("Work?"),
    ])
    h = harness(vault, tmp_path, provider)
    prof = with_profile(h, tmp_path)
    prof.people.set_owner("Alex Rivera", ["Alex"])
    async with client(h) as c:
        iid = (await c.post("/onboarding/start", json={"topics": "new"})).json()["interview"]["id"]
        await c.post(f"/onboarding/{iid}/reply", json={"text": "my wife Jamie"})
    assert prof.people.owner().relation == "" and prof.people.find("Jamie Rivera").relation == "wife"
    assert len(prof.people.all()) == 2


def test_extraction_prompt_names_both_ends_in_full():
    from src.harness.onboarding import EXTRACT_SYSTEM

    assert "names both ends in full" in EXTRACT_SYSTEM

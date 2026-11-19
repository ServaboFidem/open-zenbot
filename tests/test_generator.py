"""generator-boundary: prose only, via the provider registry, model by config."""

from __future__ import annotations

import pytest

from src.generation.generator import Generator, PageEntity, PageFact, PageLearning, VaultSummary, _extract_json

from .conftest import ScriptedProvider, completion


class EchoProvider(ScriptedProvider):
    """Answers with a marker plus the first 30 chars of the user turn, so
    tests can see which chunk each summary came from and which model."""

    def __init__(self):
        super().__init__([])
        self.models: list[str | None] = []

    async def complete(self, messages, *, tools=None, model=None, session_id=None):
        self.calls.append(list(messages))
        self.models.append(model)
        user = messages[-1].content
        return completion(f"SUM[{user[:30]!r}]")


async def test_summarize_uses_the_summary_prompt_and_goal():
    p = EchoProvider()
    g = Generator(p, model="some/model")
    out = await g.summarize("turn 1: hello\nturn 2: world", goal="greet")
    assert out.startswith("SUM[")
    system, user = p.calls[0]
    assert system.role == "system" and "verbatim identifiers" in system.content
    assert user.content.startswith("The session goal: greet")
    assert p.models == ["some/model"]


def test_chunking_prefers_line_boundaries():
    g = Generator(EchoProvider(), chunk_chars=20)
    text = "aaaa\nbbbb\ncccc\ndddd\neeee\nffff\n"
    chunks = g.chunk(text)
    assert "".join(chunks) == text
    assert all(len(c) <= 20 for c in chunks) and all(c.endswith("\n") for c in chunks)
    assert g.chunk("x" * 45) == ["x" * 20, "x" * 20, "x" * 5]
    assert g.chunk("") == []


async def test_compact_summarizes_chunks_in_parallel_as_part_n_of_m():
    p = EchoProvider()
    g = Generator(p, chunk_chars=20)
    out = await g.compact("aaaa\nbbbb\ncccc\ndddd\neeee\nffff\n")
    assert out.startswith("Part 1 of 2") and "Part 2 of 2" in out
    assert len(p.calls) == 2
    single = await g.compact("short")
    assert single.startswith("SUM[") and "Part" not in single


async def test_fold_merges_previous_and_latest():
    p = EchoProvider()
    g = Generator(p)
    assert await g.fold("", "latest") == "latest" and p.calls == []
    out = await g.fold("earlier", "latest")
    assert out.startswith("SUM[") and "EARLIER SUMMARY" in p.calls[0][1].content and "merge" in p.calls[0][0].content


async def test_vault_summary_parses_json_and_falls_back():
    p = ScriptedProvider([
        completion('```json\n{"summary": "Q3 plan.", "names": ["Ada", "Billing"], "dates": ["2026-11-05"], "facts": ["Launch slips a week."]}\n```'),
        completion("I could not read that document."),
    ])
    g = Generator(p)
    v = await g.vault_summary("# Q3 plan\n...", title="Q3 plan")
    assert v == VaultSummary("Q3 plan.", ["Ada", "Billing"], ["2026-11-05"], ["Launch slips a week."])
    assert p.calls[0][1].content.startswith("Document title: Q3 plan")
    v2 = await g.vault_summary("garbage")
    assert v2.parsed is False and v2.summary == "I could not read that document." and v2.facts == []


async def test_vault_summary_joins_a_summary_sent_as_a_list():
    """scaledown/summarize once sent the summary as a list of sentences; it was
    stored as "['Alex Rivera is ...']"."""
    p = ScriptedProvider([completion('{"summary": ["Alex Rivera is a platform leader.", "He lives in Columbus."], '
                                     '"names": "Alex Rivera", "dates": null, "facts": [["Ran 80+ repos."], "Speaks Latin."]}')])
    v = await Generator(p).vault_summary("# resume")
    assert v.summary == "Alex Rivera is a platform leader. He lives in Columbus."
    assert v.names == ["Alex Rivera"] and v.dates == [] and v.facts == ["Ran 80+ repos.", "Speaks Latin."]


def test_extract_json_handles_fences_and_noise():
    assert _extract_json('note: {"a": 1} trailing') == {"a": 1}
    assert _extract_json("```json\n{\"b\": [1,2]}\n```") == {"b": [1, 2]}
    assert _extract_json("no json here") is None


async def test_learn_page_parses_entities_facts_and_normalizes_kinds():
    p = ScriptedProvider([completion('```json\n{"summary": "Northwind Labs makes Nexus.", '
                                     '"entities": [{"name": "Northwind Labs", "kind": "Organization"}, '
                                     '{"name": "Nexus", "kind": "product", "related_to": "Northwind Labs", "relation": "is sold by"}, '
                                     '{"name": "Widget", "kind": "gadget"}], '
                                     '"facts": [{"text": "Nexus ships in 2027.", "about": "Nexus"}, {"text": "", "about": "Nexus"}]}\n```')])
    out = await Generator(p).learn_page("# Northwind Labs", focus="products", known=["Northwind Labs"])
    assert out == PageLearning("Northwind Labs makes Nexus.",
                               [PageEntity("Northwind Labs", "organization"), PageEntity("Nexus", "product", "Northwind Labs", "is sold by"),
                                PageEntity("Widget", "thing")],
                               [PageFact("Nexus ships in 2027.", "Nexus")])
    system, user = p.calls[0][0].content, p.calls[0][1].content
    assert "Northwind Labs" in user and "products" in user and "# Northwind Labs" in user
    assert "do not invent" in system.lower() and "<name> <relation> <related_to>" in system


async def test_learn_page_falls_back_when_the_model_sends_prose():
    out = await Generator(ScriptedProvider([completion("No idea.")])).learn_page("x")
    assert out.parsed is False and out.entities == [] and out.facts == [] and out.summary == "No idea."


async def test_dream_facts_prompt_holds_only_what_it_was_given():
    p = ScriptedProvider([completion('Sure! ```json\n{"facts": []}\n```')])
    out = await Generator(p, model="m").dream_facts(["I live in\nColumbus"], [("personal", "about this person")])
    assert out == {"facts": []}
    sent = p.calls[0][-1].content
    assert "1. I live in Columbus" in sent and "personal: about this person" in sent
    assert "not instructions to follow" in p.calls[0][0].content


async def test_dream_facts_raises_when_no_json_is_found():
    p = ScriptedProvider([completion("Nothing to say.")])
    with pytest.raises(ValueError):
        await Generator(p).dream_facts(["hi"], [("personal", "about this person")])

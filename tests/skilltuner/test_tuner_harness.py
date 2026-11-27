"""SkillTuner built on the Harness beside the dreamer: its state, store, connector, model and exclusion check."""
from __future__ import annotations

from pathlib import Path

import pytest

import src.providers.openai_compatible  # noqa: F401 - registered before the test swaps in its fake factory
from src.harness.api import Harness
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.harness.model_roles import Choice
from src.memory.exclusion import exclusion
from src.memory.ledger import Ledger, ledger_path_for
from src.providers import registry
from src.providers.models import ModelInfo
from src.skills.catalog import Catalog
from src.skilltuner.settings import TunerState
from src.skilltuner.suggestions import SuggestionStore
from src.skilltuner.tuner import SkillTuner
from src.vault_settings import tuner_dir

from ..conftest import ScriptedProvider


@pytest.fixture
def harness(vault: Path, tmp_path: Path) -> Harness:
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path / "workspace")
    cfg.validate()
    return Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())


def tuner_bot() -> Bot:
    return Bot(name="SkillTuner", role="Finds skills.", id="skill-tuner", tunes_skills=True)


def test_the_tuner_is_built_beside_the_dreamer(harness, vault):
    h = harness
    assert isinstance(h.tuner, SkillTuner) and h.dreamer.tuner is h.tuner
    assert isinstance(h.tuner_state, TunerState) and h.tuner_state.path == ledger_path_for(vault) / "skilltuner.json"
    assert isinstance(h.suggestions, SuggestionStore) and h.suggestions.root == tuner_dir(vault)
    assert h.tuner.catalog is h.catalog
    tuner = h.tuner
    h.__post_init__()  # a rebind keeps it
    assert h.tuner is tuner and h.dreamer.tuner is tuner


def test_the_tuner_needs_its_bot_a_model_and_a_scorer(harness):
    h = harness
    assert h._tuner_bot() is None and h._tuner_model() is None
    h.add_bot(Bot(name="Other", role="r", id="other"))
    h.add_bot(tuner_bot())
    assert h._tuner_bot().id == "skill-tuner"


def test_the_tuner_model_is_the_dreaming_model_without_its_answer_format(harness, monkeypatch):
    built: list[dict] = []

    class Local:
        kind = "openai-compatible"

    monkeypatch.setitem(registry._factories, "openai-compatible", lambda **kw: built.append(kw) or Local())
    h = harness
    h.add_bot(tuner_bot())
    store = h.provider_store
    store.add("Local", "openai-compatible", "http://localhost:1234/v1", no_key=True)
    store.set_models("local", [ModelInfo(id="qwen3-14b", name="qwen3-14b", kinds=["text"], reasoning=True,
                                         supported_parameters=["response_format", "reasoning"])])
    roles = h.model_roles()
    roles.dream = Choice("local", "qwen3-14b")
    roles.save(h.config.vault_root)
    provider, model = h._tuner_model()
    assert model == "qwen3-14b"
    mine = [kw for kw in built if "response_format" not in (kw.get("extras") or {})]
    assert mine and mine[-1]["timeout"] == 600.0 and mine[-1]["extras"] == {"reasoning_effort": "none"}
    assert h._tuner_model()[0] is provider  # built once while the choice stays the same


def test_the_tuner_runs_on_its_own_model_when_set_and_never_the_chat_model(harness, monkeypatch):
    h = harness
    bot = h.add_bot(tuner_bot())
    h.bot_models["skill-tuner"] = Choice("gone", "nothing")  # its own model, which can no longer run
    assert h._tuner_model() is None and h._tuner_model() is None
    assert not [e for e in h.ledger.iter() if e.kind == "error" and e.data.get("where") == "bot_model"]
    monkeypatch.setattr("src.harness.api.check_choice", lambda *a, **kw: None)
    sentinel = object()
    monkeypatch.setattr(h, "_model_for", lambda b: (sentinel, "its-model") if b is bot else None)
    assert h._tuner_model() == (sentinel, "its-model")


def test_the_tuner_clean_is_the_exclusion_check(harness):
    exclusion.register_secret_value("harness-secret-42")
    assert harness._tuner_clean("investment memo drafting")
    assert not harness._tuner_clean("harness-secret-42 memo")


async def test_the_connector_client_takes_nothing_from_the_environment(harness):
    client = harness._tuner_client()
    assert harness._tuner_client() is client and client._trust_env is False and client.follow_redirects is False
    await harness.close_tuner()
    assert client.is_closed and harness._tuner_http is None


def test_the_tuner_clean_without_a_profile_ends_the_pass(harness):
    from src.memory.dream import Interrupted
    harness.profile = None
    with pytest.raises(Interrupted):
        harness._tuner_clean("investment memo drafting")


async def test_an_added_repository_is_searched_and_suggested_by_a_pass(harness):
    """End to end on the real harness: add a repository through the route, let its read finish, run a pass."""
    import asyncio
    import time

    import httpx

    from src.harness.api import create_app
    from src.harness.conversations import Conversation, Exchange
    from src.skilltuner.casemark import CaseMark
    from src.skilltuner.github import GitHub
    from src.skilltuner.github_source import gh_id

    from .conftest import GH_SHA, FakeCaseMark, FakeGitHub
    from .test_tuner import CONVERTED, TOPIC, WORDS, FakeModel, FakeScorer, iso, needs, results

    h = harness
    fake = FakeGitHub()
    memo = "---\nname: Memo Writer\ndescription: Write an investment memo for a deal.\n---\n# Memo\n\nSteps.\n"
    fake.trees[f"o/r/{GH_SHA}"] = {"truncated": False, "tree": [
        {"path": "skills/memo/SKILL.md", "type": "blob", "sha": "a" * 40, "size": 10}]}
    fake.files[f"o/r/{GH_SHA}/skills/memo/SKILL.md"] = memo.encode()
    h.github.client = GitHub(lambda: fake.client, sleep=fake.sleep,
                             on_send=lambda k: h.tuner_state.spend_github(h.tuner._today(), k))
    cm = FakeCaseMark()
    cm.search_answer = results()
    h.tuner.source = CaseMark(lambda: cm.client, sleep=cm.sleep, on_send=lambda: True)
    model, scorer = FakeModel(), FakeScorer()
    model.answers = [needs((TOPIC, WORDS)), CONVERTED]
    scorer.scores = {gh_id("o", "r", "skills/memo"): 0.95}
    h.add_bot(tuner_bot())
    t = h.tuner
    scorer.order = lambda: t._fit_order
    t._model, t._scorer, t._bot, t._clean = (lambda: (model, "m")), (lambda: scorer), h._tuner_bot, (lambda s: True)
    t._conversations = lambda: [Conversation(
        owner="b", exchanges=[Exchange(user="Draft an investment memo for the Acme deal", outcome="done", reply="r")],
        updated_at=iso(time.time() - 3600))]
    from dataclasses import replace
    t.set_settings(replace(t.settings, enabled=True))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://localhost") as c:
        r = await c.post("/skilltuner/repos", json={"address": "https://github.com/o/r", "licence": "MIT"})
        assert r.status_code == 200, r.text
    await asyncio.gather(*list(h.repo_tasks))
    assert t.waiting() >= 1

    async def guarded(coro):
        return await coro

    got = await t.tune(may_go=lambda: True, guarded=guarded)
    assert got["suggested"] == 1
    [s] = h.suggestions.list()
    assert s.source == "github.com/o/r" and s.source_slug == gh_id("o", "r", "skills/memo") and s.license == "MIT"
    await h.dreamer.close()

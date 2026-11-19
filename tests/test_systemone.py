"""The typed local decision scorer (`systemone`): one question a request, answers in the Decisions API's shape,
and the mark a model needs before Settings lets a scorer drive it. Offline: the server is an httpx.MockTransport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.decider import registry
from src.decider.adapters.letter_model import KIND as LETTERS
from src.decider.adapters.systemone import KIND, PATH, SystemOneScorer
from src.decider.interface import ChoiceQuestion, DecisionRefused, DecisionRequest, NoulQuestion, ScoreQuestion
from src.harness.model_roles import Choice, ModelRoles
from src.harness.models_setup import build
from src.providers.interface import ProviderError, ProviderUnavailable
from src.providers.models import ModelInfo
from src.providers.openrouter import OpenRouterHttp
from src.providers.store import ProviderStore, ProviderStoreError

BASE = "http://localhost:1234/v1"
MODEL = "pplx-decider-flash-v1.3"
ANSWERS = {"help": {"type": "noul", "noul": 0.91},
           "tool": {"type": "choice", "choice": "search", "probabilities": {"editor": 0.1, "search": 0.9}, "confidence": 0.8},
           "done": {"type": "score", "score": 1.2, "probabilities": {"0": 0.1, "1": 0.6, "2": 0.3}, "legend": {"0": "no", "1": "partly", "2": "yes"}}}
QUESTIONS = {"help": NoulQuestion("Does it help?"), "tool": ChoiceQuestion("Which tool?", {"editor": "edits", "search": "searches"}),
             "done": ScoreQuestion("How far along?", ["no", "partly", "yes"])}


def scorer(handler, seen: list | None = None) -> SystemOneScorer:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(json.loads(request.content))
    return SystemOneScorer(OpenRouterHttp(None, base_url=BASE, transport=httpx.MockTransport(wrapped)), model=MODEL)


def good(body: dict) -> httpx.Response:
    [key] = body["questions"]
    return httpx.Response(200, json={"model": MODEL, "answers": {key: ANSWERS[key]}, "usage": {"input_tokens": 100, "output_tokens": 0}})


async def test_one_question_a_request_one_after_another_and_typed_answers():
    seen: list[httpx.Request] = []
    busy = 0

    def handler(body: dict) -> httpx.Response:
        nonlocal busy
        busy += 1
        assert busy == 1  # never two at once
        try:
            return good(body)
        finally:
            busy -= 1

    s = scorer(handler, seen)
    result = await s.decide(DecisionRequest(state={"task": "resize photos"}, questions=QUESTIONS, purpose="routing"))
    assert result.kind == KIND == "systemone" and result.model == MODEL
    assert result.noul("help").noul == 0.91
    assert result.choice("tool").choice == "search" and result.choice("tool").probabilities == {"editor": 0.1, "search": 0.9}
    assert result.score("done").score == 1.2 and result.score("done").legend["1"] == "partly"
    assert result.usage.input_tokens == 300 and result.usage.output_tokens == 0
    bodies = [json.loads(r.content) for r in seen]
    assert [list(b["questions"]) for b in bodies] == [["help"], ["tool"], ["done"]]
    assert all(r.url.path == f"/v1{PATH}" and "authorization" not in r.headers for r in seen)
    assert bodies[0] == {"model": MODEL, "state": {"task": "resize photos"}, "questions": {"help": {"type": "noul", "instructions": "Does it help?"}}}
    assert bodies[1]["questions"]["tool"]["criteria"] == {"editor": "edits", "search": "searches"}
    await s.aclose()


async def test_a_refusal_a_missing_answer_and_a_server_that_is_down():
    refused = await scorer(lambda body: httpx.Response(200, json={"answers": {"q": {"type": "refusal"}}})).decide(
        DecisionRequest("state", {"q": NoulQuestion("Is it?")}))
    with pytest.raises(DecisionRefused):
        refused.noul("q")
    with pytest.raises(ProviderError, match="no answer to the question"):
        await scorer(lambda body: httpx.Response(200, json={"answers": {}})).decide(DecisionRequest("state", {"q": NoulQuestion("Is it?")}))
    with pytest.raises(ProviderError, match="type 'choice' for a 'noul' question"):
        await scorer(lambda body: httpx.Response(200, json={"answers": {"q": {"type": "choice", "choice": "a"}}})).decide(
            DecisionRequest("state", {"q": NoulQuestion("Is it?")}))
    with pytest.raises(ProviderUnavailable):
        await scorer(lambda body: httpx.Response(502, json={"error": {"message": "Engine protocol systemOne request failed"}})).decide(
            DecisionRequest("state", {"q": NoulQuestion("Is it?")}))


async def test_probe_and_what_it_needs():
    assert await scorer(lambda body: httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.9}}})).probe() is True
    assert await scorer(lambda body: httpx.Response(400, json={"error": {"message": "Invalid model identifier"}})).probe() is False
    assert SystemOneScorer.checks_commands is False
    with pytest.raises(ValueError):
        SystemOneScorer.from_env(model=MODEL)
    with pytest.raises(ValueError):
        SystemOneScorer.from_env(base_url=BASE)
    assert registry.get(KIND, model=MODEL, base_url=BASE).model == MODEL
    assert registry.provider_kinds(KIND) == ("openai-compatible",)
    assert registry.needs(KIND) == "typed_decisions" and registry.needs(LETTERS) == "letter_decisions"
    assert registry.needs("openrouter-decision-1") is None


def _store(vault: Path, monkeypatch) -> ProviderStore:
    monkeypatch.setenv("ZEN_TEST_OR_KEY", "sk-or-v1-systemone0123456789abcdef")
    store = ProviderStore(vault).load()
    store.seed("env:ZEN_TEST_OR_KEY", [ModelInfo("or/chat", "Chat", ["text"])])
    store.add("LM Studio", "openai-compatible", BASE, no_key=True)
    store.set_models("lm-studio", [ModelInfo(MODEL, MODEL, ["decision"], supported_parameters=["typed_decisions"]),
                                   ModelInfo("letters", "letters", ["text", "decision"], supported_parameters=["letter_decisions"]),
                                   ModelInfo("older", "older", ["text", "decision"], supported_parameters=[])])
    return store


def _roles(scorer_kind: str, model: str) -> ModelRoles:
    return ModelRoles(chat=Choice("openrouter", "or/chat"), summary=Choice("openrouter", "or/chat"),
                      decision=Choice("lm-studio", model), scorer=scorer_kind)


def test_a_scorer_binds_only_to_a_model_that_passed_its_own_test(vault: Path, monkeypatch):
    store = _store(vault, monkeypatch)
    bound = build(store, _roles(KIND, MODEL))
    assert isinstance(bound.scorer, SystemOneScorer) and bound.scorer.http.base_url == BASE
    assert build(store, _roles(LETTERS, "letters")).scorer.kind == LETTERS
    with pytest.raises(ProviderStoreError, match="letters did not pass the test the systemone scorer needs \\(Typed decisions\\)"):
        build(store, _roles(KIND, "letters"))
    with pytest.raises(ProviderStoreError, match="did not pass the test the letter-logprobs scorer needs \\(Letter decisions\\)"):
        build(store, _roles(LETTERS, MODEL))
    with pytest.raises(ProviderStoreError, match="disable the model and enable it again"):
        build(store, _roles(LETTERS, "older"))

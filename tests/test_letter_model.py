"""The local decision scorer: the letter format, the answers built from token probabilities, and where
Settings lets it run. Offline: the server is an httpx.MockTransport."""

from __future__ import annotations

import json
import math
from pathlib import Path

import httpx
import pytest

from src.decider import registry
from src.decider.adapters.decision_model import KIND as OPENROUTER_KIND
from src.decider.adapters.letter_model import CHAT_PATH, KIND, LetterModelScorer
from src.decider.interface import ChoiceQuestion, DecisionRefused, DecisionRequest, NoulQuestion, ScoreQuestion
from src.harness.model_roles import Choice, ModelRoles
from src.harness.models_setup import build
from src.providers import letters
from src.providers.interface import ProviderError
from src.providers.models import ModelInfo
from src.providers.openrouter import OpenRouterHttp
from src.providers.store import ProviderStore, ProviderStoreError

BASE = "http://localhost:1234/v1"
MODEL = "jev-style-qwen3.5-2b-decision"


def tokens(**probs: float) -> httpx.Response:
    """A one-token answer whose candidates are the given tokens (a letter is sent with its leading space)."""
    top = [{"token": (f" {t}" if len(t) == 1 else t), "logprob": math.log(p)} for t, p in probs.items()]
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": top[0]["token"]},
                                                  "logprobs": {"content": [{"token": top[0]["token"], "top_logprobs": top}]}}],
                                     "usage": {"prompt_tokens": 40, "completion_tokens": 1}})


def scorer(handler, seen: list | None = None) -> LetterModelScorer:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(json.loads(request.content))
    return LetterModelScorer(OpenRouterHttp(None, base_url=BASE, transport=httpx.MockTransport(wrapped)), model=MODEL)


# -- the format ---------------------------------------------------------------------------------------------


def test_the_prompt_is_the_model_cards_format():
    assert letters.prompt("It rained.", "Was it dry?", ["yes", "no"]) == (
        "You are a decision function. Read the state, then answer the question by choosing exactly one option.\n\n"
        "[State]\nIt rained.\n\n[Question]\nWas it dry?\n\n[Options]\nA. yes\nB. no\n\nAnswer:")
    assert "A. one line" in letters.prompt("s", "q", ["one\nline", "b"])  # an option never breaks the list
    with pytest.raises(ValueError):
        letters.prompt("s", "q", [str(i) for i in range(21)])
    assert letters.payload(MODEL, "p") == {"model": MODEL, "messages": [{"role": "user", "content": "p"}], "max_tokens": 1,
                                            "temperature": 0, "logprobs": True, "top_logprobs": 20}


def test_read_renormalises_over_the_letters_and_reports_their_mass():
    probs, mass = letters.read(tokens(A=0.6, B=0.2, The=0.1).json(), 2)
    assert probs == pytest.approx([0.75, 0.25]) and mass == pytest.approx(0.8)
    probs, mass = letters.read(tokens(A=0.5, C=0.4).json(), 2)  # C is not an option here
    assert probs == pytest.approx([1.0, 0.0]) and mass == pytest.approx(0.5)
    assert letters.read(tokens(The=0.9).json(), 3) == ([pytest.approx(1 / 3)] * 3, 0.0)
    assert letters.read({"choices": [{"message": {"content": "A"}}]}, 2) is None  # a server without logprobs
    assert letters.read({"choices": [{"logprobs": {"content": [{"top_logprobs": [{"token": " A", "logprob": "x"}]}]}}]}, 2)[1] == 0.0


# -- the scorer ---------------------------------------------------------------------------------------------


async def test_one_request_a_question_and_typed_answers():
    seen: list[httpx.Request] = []

    def handler(body: dict) -> httpx.Response:
        prompt = body["messages"][0]["content"]
        if "Does it help?" in prompt:
            return tokens(A=0.72, B=0.18)
        if "Which tool?" in prompt:
            return tokens(B=0.63, A=0.07, C=0.2)
        return tokens(A=0.1, B=0.3, C=0.6)

    s = scorer(handler, seen)
    result = await s.decide(DecisionRequest(state={"task": "resize photos"}, questions={
        "help": NoulQuestion("Does it help?", {"true": "it does", "false": "it does not"}),
        "tool": ChoiceQuestion("Which tool?", {"editor": "edits pictures", "search": "searches the web", "calendar": "events"}),
        "done": ScoreQuestion("How far along?", ["not started", "partway", "done"])}, purpose="routing"))
    assert result.kind == KIND == "letter-logprobs" and result.model == MODEL
    assert result.noul("help").noul == pytest.approx(0.8)
    tool = result.choice("tool")
    assert tool.choice == "search" and tool.probabilities == pytest.approx({"editor": 0.07 / 0.9, "search": 0.7, "calendar": 0.2 / 0.9})
    assert tool.confidence == pytest.approx((3 * 0.7 - 1) / 2)
    done = result.score("done")
    assert done.score == pytest.approx(1.5) and done.legend == {"0": "not started", "1": "partway", "2": "done"}
    assert result.usage.input_tokens == 120 and result.usage.output_tokens == 3
    assert len(seen) == 3 and all(q.url.path == f"/v1{CHAT_PATH}" and "authorization" not in q.headers for q in seen)
    first = json.loads(seen[0].content)
    assert first["max_tokens"] == 1 and first["logprobs"] is True and first["model"] == MODEL
    text = first["messages"][0]["content"]
    assert '"task": "resize photos"' in text and "A. yes: it does\nB. no: it does not" in text and text.endswith("Answer:")
    await s.aclose()


async def test_a_model_that_does_not_answer_with_a_letter_refuses():
    s = scorer(lambda body: tokens(The=0.7, A=0.1, B=0.1))
    result = await s.decide(DecisionRequest("state", {"q": NoulQuestion("Is it?")}))
    with pytest.raises(DecisionRefused):
        result.noul("q")


async def test_no_token_probabilities_or_too_many_options_is_a_provider_error():
    s = scorer(lambda body: httpx.Response(200, json={"choices": [{"message": {"content": "A"}}]}))
    with pytest.raises(ProviderError, match="no token probabilities"):
        await s.decide(DecisionRequest("state", {"q": NoulQuestion("Is it?")}))
    with pytest.raises(ProviderError, match="21 options; this scorer reads 2 to 20"):
        await s.decide(DecisionRequest("state", {"q": ChoiceQuestion("Which?", {f"o{i}": "x" for i in range(21)})}))


async def test_probe_says_whether_the_server_answers_in_letters():
    assert await scorer(lambda body: tokens(A=0.9, B=0.05)).probe() is True
    assert await scorer(lambda body: httpx.Response(200, json={"choices": [{"message": {"content": "A"}}]})).probe() is False
    assert await scorer(lambda body: httpx.Response(500)).probe() is False


def test_it_needs_a_model_and_a_base_url_and_is_not_relied_on_for_commands():
    assert LetterModelScorer.checks_commands is False
    with pytest.raises(ValueError):
        LetterModelScorer.from_env(model=MODEL)
    with pytest.raises(ValueError):
        LetterModelScorer.from_env(base_url=BASE)
    assert registry.get(KIND, model=MODEL, base_url=BASE).model == MODEL


# -- where Settings lets it run -----------------------------------------------------------------------------


def test_each_scorer_names_the_providers_its_model_may_be_on():
    assert registry.provider_kinds(KIND) == ("openai-compatible",)
    assert registry.provider_kinds(OPENROUTER_KIND) == ("openrouter",)
    assert registry.provider_kinds("unknown") == ("openrouter",)


def _store(vault: Path, monkeypatch) -> ProviderStore:
    monkeypatch.setenv("ZEN_TEST_OR_KEY", "sk-or-v1-letters0123456789abcdef")
    store = ProviderStore(vault).load()
    store.seed("env:ZEN_TEST_OR_KEY", [ModelInfo("or/chat", "Chat", ["text"]), ModelInfo("or/decide", "Decide", ["decision"])])
    store.add("LM Studio", "openai-compatible", BASE, no_key=True)
    store.set_models("lm-studio", [ModelInfo(MODEL, MODEL, ["text", "decision"], supported_parameters=["letter_decisions"]), ModelInfo("plain", "plain", ["text"])])
    return store


def _roles(scorer_kind: str, provider: str, model: str) -> ModelRoles:
    return ModelRoles(chat=Choice("openrouter", "or/chat"), summary=Choice("openrouter", "or/chat"),
                      decision=Choice(provider, model), scorer=scorer_kind)


def test_the_local_scorer_binds_on_a_local_provider(vault: Path, monkeypatch):
    bound = build(_store(vault, monkeypatch), _roles(KIND, "lm-studio", MODEL))
    assert isinstance(bound.scorer, LetterModelScorer) and bound.scorer.model == MODEL
    assert bound.scorer.http.base_url == BASE and bound.provider.kind == "openrouter"


def test_a_scorer_is_refused_on_a_provider_it_cannot_run_on(vault: Path, monkeypatch):
    store = _store(vault, monkeypatch)
    with pytest.raises(ProviderStoreError, match="letter-logprobs scorer runs on an OpenAI-compatible provider"):
        build(store, _roles(KIND, "openrouter", "or/decide"))
    with pytest.raises(ProviderStoreError, match="openrouter-decision-1 scorer runs on an OpenRouter provider"):
        build(store, _roles(OPENROUTER_KIND, "lm-studio", MODEL))
    with pytest.raises(ProviderStoreError, match="plain is not a decision model"):  # it did not pass the Decisions test
        build(store, _roles(KIND, "lm-studio", "plain"))
    assert build(store, _roles(OPENROUTER_KIND, "openrouter", "or/decide")).scorer.kind == OPENROUTER_KIND


def test_a_scorer_from_the_environment_that_needs_settings_starts_without_one(vault: Path, tmp_path: Path, monkeypatch, capsys):
    from src.harness.config import HarnessConfig
    from src.harness.main import build_harness

    monkeypatch.setenv("ZEN_TEST_OR_KEY", "sk-or-v1-letters0123456789abcdef")
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path, key_ref="env:ZEN_TEST_OR_KEY", scorer_kind=KIND,
                        skills_dir=tmp_path / "none", bot_prompts_dir=tmp_path / "none")
    h = build_harness(cfg)
    assert h.bound and h.scorer is None and "choose it on Settings" in capsys.readouterr().err


async def test_a_question_with_one_option_is_a_provider_error_and_nothing_is_asked():
    seen: list[httpx.Request] = []
    with pytest.raises(ProviderError, match="1 options; this scorer reads 2 to 20"):
        await scorer(lambda body: tokens(A=0.9), seen).decide(DecisionRequest("s", {"q": ChoiceQuestion("Which?", {"only": "the one"})}))
    assert seen == []


async def test_a_failed_question_stops_the_ones_still_waiting():
    import asyncio

    started = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal started
        started += 1
        if "Question 0?" in json.loads(request.content)["messages"][0]["content"]:
            return httpx.Response(400, json={"error": {"message": "bad request"}})
        await asyncio.sleep(0.05)
        return tokens(A=0.9, B=0.1)

    s = LetterModelScorer(OpenRouterHttp(None, base_url=BASE, transport=httpx.MockTransport(handler)), model=MODEL)
    with pytest.raises(ProviderError):
        await s.decide(DecisionRequest("s", {f"q{i}": NoulQuestion(f"Question {i}?") for i in range(8)}))
    at_failure = started
    await asyncio.sleep(0.3)  # long enough for every question still waiting on the gate to have been sent
    assert started == at_failure < 8
    await s.aclose()


def test_a_saved_key_binds_chat_when_the_environment_names_a_local_scorer(vault: Path, tmp_path: Path, monkeypatch, capsys):
    from src.harness.config import HarnessConfig
    from src.harness.main import build_harness

    monkeypatch.delenv("ZEN_TEST_OR_KEY", raising=False)
    monkeypatch.setenv("ZEN_PROVIDER_OPENROUTER_API_KEY", "")  # so the stored key's export is undone after the test
    monkeypatch.delenv("ZEN_PROVIDER_OPENROUTER_API_KEY")
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path, key_ref="env:ZEN_TEST_OR_KEY", scorer_kind=KIND,
                        skills_dir=tmp_path / "none", bot_prompts_dir=tmp_path / "none")
    first = build_harness(cfg)  # seeds the OpenRouter provider, with no key anywhere
    assert not first.bound
    first.provider_store.update("openrouter", key="sk-or-stored0123456789")
    capsys.readouterr()
    h = build_harness(cfg)
    assert h.bound and h.provider.kind == "openrouter" and h.scorer is None
    assert "choose it on Settings" in capsys.readouterr().err

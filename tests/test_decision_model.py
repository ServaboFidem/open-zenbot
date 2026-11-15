"""decision-scorer-adapter: offline against the response captured live on
2026-11-05; live only with a key. Closes the open verification that
microsoft-decision-1 supports choice, noul, and score."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from src.decider import registry
from src.decider.adapters.decision_model import DECISIONS_PATH, KIND, DecisionModelScorer
from src.decider.interface import (
    SUPERVISOR_QUESTIONS,
    ChoiceQuestion,
    DecisionRefused,
    DecisionRequest,
    NoulQuestion,
    RefusalAnswer,
    ScoreQuestion,
    SupervisorAction,
    SupervisorCheck,
    decide_action,
    decide_and_log,
)
from src.memory.ledger import Ledger
from src.providers.interface import ProviderAuthError, ProviderError, ProviderUnavailable
from src.providers.openrouter import OpenRouterHttp
from src.providers.secrets import Secret

KEY = "sk-or-v1-decisiontestkey0123456789"

# Captured from a live call to microsoft/microsoft-decision-1 on 2026-11-05.
LIVE_RESPONSE = {
    "model": "microsoft/microsoft-decision-1-20261009",
    "answers": {
        "completion": {"type": "score", "score": 0.07662382036894036,
                       "legend": {"0": "Has not started or is far from done", "1": "Partway there", "2": "Essentially done"},
                       "probabilities": {"0": 0.9237740884703505, "1": 0.07582800269035869, "2": 0.00039790883929083284},
                       "confidence": 0.8856611327055257},
        "is_repeating": {"type": "noul", "noul": 0.9959298661562725},
        "run_state": {"type": "choice", "choice": "looping",
                      "probabilities": {"looping": 0.9998557544889732, "progressing": 3.535241296429431e-05, "stuck": 0.00010889309806252757},
                      "confidence": 0.9997836317334599},
    },
    "usage": {"input_tokens": 228, "output_tokens": 3, "cost": 9.576e-06, "is_byok": False},
    "id": "gen-dec-1793916637-d3hUCGaNGEnJJdg0VktQ",
    "provider": "Azure",
}


def scorer(handler) -> DecisionModelScorer:
    return DecisionModelScorer(OpenRouterHttp(Secret(KEY), transport=httpx.MockTransport(handler)))


def test_registered_under_its_kind():
    assert registry.is_registered(KIND)


async def test_request_shape_and_answer_mapping():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=LIVE_RESPONSE)

    s = scorer(handler)
    req = DecisionRequest(state="the agent ran ls four times", questions=SUPERVISOR_QUESTIONS, purpose="supervise")
    r = await s.decide(req)

    assert seen["path"] == "/api" + DECISIONS_PATH == "/api/alpha/decisions"
    body = seen["body"]
    assert body["model"] == "microsoft/microsoft-decision-1"
    assert body["state"] == {"text": "the agent ran ls four times"}
    assert body["questions"]["run_state"]["type"] == "choice" and set(body["questions"]["run_state"]["criteria"]) == {"progressing", "stuck", "looping"}
    assert body["questions"]["is_repeating"] == {"type": "noul", "instructions": SUPERVISOR_QUESTIONS["is_repeating"].instructions}
    assert body["questions"]["completion"]["criteria"][2] == "Essentially done, only wrap-up left"

    assert r.kind == KIND and r.model.startswith("microsoft/microsoft-decision-1") and r.provider == "Azure"
    assert r.choice("run_state").choice == "looping" and r.choice("run_state").probabilities["looping"] > 0.99
    assert r.noul("is_repeating").noul == pytest.approx(0.9959, abs=1e-4)
    assert r.score("completion").score == pytest.approx(0.0766, abs=1e-4) and r.score("completion").max_score == 2
    assert r.score("completion").legend["2"] == "Essentially done"
    assert r.usage.input_tokens == 228 and r.usage.cost == 9.576e-06 and r.id.startswith("gen-dec-")

    assert decide_action(SupervisorCheck.from_result(r)) == SupervisorAction.halt


async def test_dict_state_passes_through():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.2}}, "model": "m", "usage": {}})

    r = await scorer(handler).decide(DecisionRequest(state={"task": "t", "catalog": ["a"]}, questions={"q": NoulQuestion("?")}))
    assert seen["body"]["state"] == {"task": "t", "catalog": ["a"]}
    assert r.noul("q").noul == 0.2 and r.usage.cost is None


async def test_refusal_answer():
    h = lambda r: httpx.Response(200, json={"answers": {"q": {"type": "refusal"}}, "model": "m"})
    r = await scorer(h).decide(DecisionRequest(state="x", questions={"q": ChoiceQuestion("?", {"a": "A"})}))
    assert isinstance(r.answers["q"], RefusalAnswer)
    with pytest.raises(DecisionRefused):
        r.choice("q")


@pytest.mark.parametrize("body,match", [
    ({"model": "m"}, "no answers"),
    ({"answers": {}, "model": "m"}, "lacks an answer"),
    ({"answers": {"q": {"type": "score", "score": 1}}, "model": "m"}, "answered type"),
    ({"answers": {"q": {"type": "choice"}}, "model": "m"}, "malformed"),
])
async def test_malformed_responses(body, match):
    with pytest.raises(ProviderError, match=match):
        await scorer(lambda r: httpx.Response(200, json=body)).decide(DecisionRequest(state="x", questions={"q": ChoiceQuestion("?", {"a": "A"})}))


@pytest.mark.parametrize("status,exc", [(401, ProviderAuthError), (402, ProviderAuthError), (429, ProviderUnavailable), (502, ProviderUnavailable), (400, ProviderError)])
async def test_http_errors_map_like_the_provider(status, exc):
    with pytest.raises(exc) as ei:
        await scorer(lambda r: httpx.Response(status, json={"error": {"message": "bad"}})).decide(DecisionRequest(state="x", questions={"q": NoulQuestion("?")}))
    assert KEY not in str(ei.value)


async def test_empty_questions_rejected():
    with pytest.raises(ValueError):
        await scorer(lambda r: httpx.Response(200, json={})).decide(DecisionRequest(state="x", questions={}))


async def test_logs_probabilities_and_cost_through_the_ledger(ledger: Ledger):
    s = scorer(lambda r: httpx.Response(200, json=LIVE_RESPONSE))
    await decide_and_log(s, DecisionRequest(state="transcript", questions=SUPERVISOR_QUESTIONS, purpose="supervise"), ledger, "bot1", "s1")
    e = ledger.tail(kind="decider_score")[0]
    assert e.data["scorer"] == KIND and e.data["purpose"] == "supervise" and e.data["cost"] == 9.576e-06
    assert e.data["answers"]["run_state"]["probabilities"]["looping"] > 0.99
    assert e.data["answers"]["is_repeating"]["noul"] > 0.99
    assert "transcript" not in json.dumps(e.data)


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="live check needs OPENROUTER_API_KEY")
async def test_live_decision_1_supports_choice_noul_and_score():
    """The open verification from plan 04, closed."""
    s = DecisionModelScorer.from_env()
    try:
        transcript = (
            "Task: write a brief. The agent ran `ls notes/` and saw 3 files. It ran `ls notes/` again, same 3 files. "
            "Again, same. Again, same. It has not read any file."
        )
        r = await s.decide(DecisionRequest(state=transcript, questions=SUPERVISOR_QUESTIONS, purpose="supervise"))
        assert r.kind == KIND and r.model.startswith("microsoft/microsoft-decision-1")
        c = SupervisorCheck.from_result(r)
        assert set(c.run_state.probabilities) == {"progressing", "stuck", "looping"}
        assert sum(c.run_state.probabilities.values()) == pytest.approx(1.0, abs=0.01)
        assert 0.0 <= c.is_repeating.noul <= 1.0
        assert 0.0 <= c.completion.score <= 2.0 and c.completion.max_score == 2
        assert c.run_state.choice == "looping" and c.is_repeating.noul > 0.5
        assert r.usage.cost is not None and r.usage.cost < 0.001
        assert await s.probe() is True
    finally:
        await s.aclose()

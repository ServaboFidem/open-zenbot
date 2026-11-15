"""microsoft-decision-1 on the OpenRouter Decisions API.

POST /api/alpha/decisions with `state` and typed `questions`; one typed
answer per question with probabilities. Verified live on 2026-11-05:
choice, noul, and score all return the shapes `interface.py` expects.

Registers as kind "openrouter-decision-1". Uses `OpenRouterHttp` from
the providers package, so this module imports no HTTP library.
"""

from __future__ import annotations

from typing import Any

from src.providers.interface import ProviderError
from src.providers.openrouter import DEFAULT_KEY_REF, OpenRouterHttp

from .. import registry
from ..interface import (
    Answer,
    ChoiceAnswer,
    DecisionRequest,
    DecisionResult,
    DecisionUsage,
    NoulAnswer,
    RefusalAnswer,
    Scorer,
    ScoreAnswer,
    question_to_dict,
)

KIND = "openrouter-decision-1"
DECISIONS_PATH = "/alpha/decisions"
DEFAULT_MODEL = "microsoft/microsoft-decision-1"


class DecisionModelScorer(Scorer):
    kind = KIND

    def __init__(self, http: OpenRouterHttp, *, model: str | None = None) -> None:
        self.http = http
        self.model = model or DEFAULT_MODEL

    @classmethod
    def from_env(cls, *, key_ref: str = DEFAULT_KEY_REF, model: str | None = None, **kwargs: Any) -> "DecisionModelScorer":
        return cls(OpenRouterHttp.from_env(key_ref=key_ref, **kwargs), model=model)

    def __repr__(self) -> str:
        return f"DecisionModelScorer(model={self.model!r})"

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        if not request.questions:
            raise ValueError("a decision needs at least one question")
        payload = {
            "model": self.model,
            "state": request.state if not isinstance(request.state, str) else {"text": request.state},
            "questions": {k: question_to_dict(q) for k, q in request.questions.items()},
        }
        data = await self.http.post_json(DECISIONS_PATH, payload, what="decisions")
        raw_answers = data.get("answers")
        if not isinstance(raw_answers, dict):
            raise ProviderError("decisions response has no answers")
        answers: dict[str, Answer] = {}
        for key in request.questions:
            raw = raw_answers.get(key)
            if raw is None:
                raise ProviderError(f"decisions response lacks an answer for {key!r}")
            answers[key] = _parse_answer(raw, request.questions[key].type)
        usage = data.get("usage") or {}
        return DecisionResult(
            answers=answers,
            model=str(data.get("model") or self.model),
            kind=self.kind,
            usage=DecisionUsage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cost=usage.get("cost"),
            ),
            id=str(data.get("id") or ""),
            provider=str(data.get("provider") or ""),
        )

    async def probe(self) -> bool:
        return await self.http.probe()

    async def aclose(self) -> None:
        await self.http.aclose()


def _parse_answer(raw: dict[str, Any], expected: str) -> Answer:
    t = raw.get("type")
    if t == "refusal":
        return RefusalAnswer()
    if t != expected:
        raise ProviderError(f"decisions answered type {t!r} for a {expected!r} question")
    try:
        if t == "choice":
            return ChoiceAnswer(
                choice=str(raw["choice"]),
                probabilities={str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()},
                confidence=_opt_float(raw.get("confidence")),
            )
        if t == "noul":
            return NoulAnswer(noul=float(raw["noul"]))
        if t == "score":
            return ScoreAnswer(
                score=float(raw["score"]),
                probabilities={str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()},
                legend=dict(raw.get("legend") or {}),
                confidence=_opt_float(raw.get("confidence")),
            )
    except (KeyError, TypeError, ValueError) as e:
        raise ProviderError(f"malformed {t} answer: {type(e).__name__}") from None
    raise ProviderError(f"unsupported answer type {t!r}")


def _opt_float(v: Any) -> float | None:
    return None if v is None else float(v)


registry.register(KIND, DecisionModelScorer.from_env)

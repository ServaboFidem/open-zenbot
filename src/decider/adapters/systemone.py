"""A local server that answers typed decisions itself: kind "systemone".

`POST <base>/systemone` with `{"model", "state", "questions": {key: question}}` answers
`{"answers": {key: answer}, "usage": {...}}` in the shape of OpenRouter's Decisions API, so each answer is read
with `decision_model.parse_answer`. LM Studio serves it for the models it lists with type `decision` (and refuses
those models on `/chat/completions`); `jev-style serve` has the same route.

One question a request, one request at a time (`AT_ONCE`). Checked by hand on 2026-11-19 against LM Studio with
`pplx-decider-flash-v1.3`: three skill questions in one request (about 750 tokens), and four single-question
requests sent together, each ended in HTTP 502 "Engine protocol systemOne request failed" and the model
unloaded; 123 requests one after another all answered. The cause was not found.

`checks_commands` is False: that model labelled all twelve shell commands tried correctly, which is too few to
let a command run on this scorer's word (`write_guard.ShellWriteCheck`)."""

from __future__ import annotations

from typing import Any

from src.providers.interface import ProviderError
from src.providers.openrouter import OpenRouterHttp
from src.providers.secrets import resolve_secret

from .. import registry
from ..interface import Answer, DecisionRequest, DecisionResult, DecisionUsage, Question, Scorer, question_to_dict
from .decision_model import parse_answer

KIND = "systemone"
PATH = "/systemone"
PROVIDER_KINDS = ("openai-compatible",)
NEEDS = "typed_decisions"  # the capability test's mark on a model that answered this route
AT_ONCE = 1


def payload(model: str, state: Any, key: str, q: Question) -> dict[str, Any]:
    return {"model": model, "state": state, "questions": {key: question_to_dict(q)}}


def read(data: Any, key: str, q: Question) -> Answer:
    """The answer to `key` in a reply; ProviderError when it is not there or not of the question's type."""
    answers = data.get("answers") if isinstance(data, dict) else None
    raw = answers.get(key) if isinstance(answers, dict) else None
    if not isinstance(raw, dict):
        raise ProviderError("the decision server's reply has no answer to the question")
    return parse_answer(raw, q.type)


class SystemOneScorer(Scorer):
    kind = KIND
    checks_commands = False

    def __init__(self, http: OpenRouterHttp, *, model: str) -> None:
        if not model:
            raise ValueError("the systemone scorer needs a model")
        self.http = http
        self.model = model

    @classmethod
    def from_env(cls, *, key_ref: str | None = None, model: str | None = None, base_url: str = "",
                 **kwargs: Any) -> "SystemOneScorer":
        if not base_url:
            raise ValueError("the systemone scorer needs its provider's base URL; choose it in Settings")
        key = resolve_secret(key_ref) if key_ref else None  # None: a server that takes no key
        return cls(OpenRouterHttp(key, base_url=base_url.rstrip("/"), **kwargs), model=model or "")

    def __repr__(self) -> str:
        return f"SystemOneScorer(model={self.model!r})"

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        if not request.questions:
            raise ValueError("a decision needs at least one question")
        usage = DecisionUsage()
        answers: dict[str, Answer] = {}
        for key, q in request.questions.items():  # one after another: see AT_ONCE
            data = await self.http.post_json(PATH, payload(self.model, request.state, key, q), what="decisions")
            answers[key] = read(data, key, q)
            used = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            usage.input_tokens += int(used.get("input_tokens") or 0)
            usage.output_tokens += int(used.get("output_tokens") or 0)
        return DecisionResult(answers=answers, model=self.model, kind=self.kind, usage=usage)

    async def probe(self) -> bool:
        from ..interface import NoulQuestion

        q = NoulQuestion("Is it raining?")
        try:
            read(await self.http.post_json(PATH, payload(self.model, "It is raining.", "q", q), what="decisions"), "q", q)
        except ProviderError:
            return False
        return True

    async def aclose(self) -> None:
        await self.http.aclose()


registry.register(KIND, SystemOneScorer.from_env, provider_kinds=PROVIDER_KINDS, needs=NEEDS)

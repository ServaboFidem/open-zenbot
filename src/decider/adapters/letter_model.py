"""A local decision model on an OpenAI-compatible server (LM Studio, llama.cpp): kind "letter-logprobs".

Each question is one request to `/chat/completions` in the letter format (`src/providers/letters.py`): one
token, its `top_logprobs`, renormalised over the option letters. Checked by hand on 2026-11-19 against LM Studio
with `jev-style-qwen3.5-2b-decision` (the Jev-Style v1 GGUF, whose model card gives this format): about 200 ms a
question, and the same numbers for the same input.

`checks_commands` is False: on that model the shell questions came back near 0.7 "inside" for `ls` and for
`rm -rf ~/Documents` alike, so a shell command never runs on this scorer's word (`write_guard.ShellWriteCheck`).

A server that answers in the typed shape itself (state and questions in, `noul` / `choice` / `score` out) needs
none of this: that is the `systemone` scorer (`systemone.py`)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from src.providers import letters
from src.providers.interface import ProviderError
from src.providers.openrouter import OpenRouterHttp
from src.providers.secrets import resolve_secret

from .. import registry
from ..answers import from_probabilities, options, text
from ..interface import Answer, DecisionRequest, DecisionResult, DecisionUsage, Question, RefusalAnswer, Scorer

KIND = "letter-logprobs"
CHAT_PATH = "/chat/completions"
PROVIDER_KINDS = ("openai-compatible",)
NEEDS = "letter_decisions"  # the capability test's mark on a model that answered in letters
AT_ONCE = 4  # questions of one request sent together; a local server queues what it cannot run side by side


class LetterModelScorer(Scorer):
    kind = KIND
    checks_commands = False

    def __init__(self, http: OpenRouterHttp, *, model: str) -> None:
        if not model:
            raise ValueError("the letter-logprobs scorer needs a model")
        self.http = http
        self.model = model

    @classmethod
    def from_env(cls, *, key_ref: str | None = None, model: str | None = None, base_url: str = "",
                 **kwargs: Any) -> "LetterModelScorer":
        if not base_url:
            raise ValueError("the letter-logprobs scorer needs its provider's base URL; choose it in Settings")
        key = resolve_secret(key_ref) if key_ref else None  # None: a server that takes no key
        return cls(OpenRouterHttp(key, base_url=base_url.rstrip("/"), **kwargs), model=model or "")

    def __repr__(self) -> str:
        return f"LetterModelScorer(model={self.model!r})"

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        if not request.questions:
            raise ValueError("a decision needs at least one question")
        state = request.state if isinstance(request.state, str) else json.dumps(request.state, ensure_ascii=False, indent=2)
        gate = asyncio.Semaphore(AT_ONCE)
        usage = DecisionUsage()

        async def one(q: Question) -> Answer:
            shown = options(q)
            if len(shown) > letters.MAX_OPTIONS:
                raise ProviderError(f"a question has {len(shown)} options; this scorer reads at most {letters.MAX_OPTIONS}")
            async with gate:
                data = await self.http.post_json(CHAT_PATH, letters.payload(self.model, letters.prompt(state, text(q.instructions), shown)),
                                                 what="decisions")
            got = letters.read(data, len(shown))
            if got is None:
                raise ProviderError("the server returned no token probabilities for the decision model")
            used = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            usage.input_tokens += int(used.get("prompt_tokens") or 0)
            usage.output_tokens += int(used.get("completion_tokens") or 0)
            probs, mass = got
            # most of the token's probability off the letters: the model is not answering this question
            return RefusalAnswer() if mass < letters.MIN_MASS else from_probabilities(q, probs)

        keys = list(request.questions)
        answers = await asyncio.gather(*(one(request.questions[k]) for k in keys))
        return DecisionResult(answers=dict(zip(keys, answers)), model=self.model, kind=self.kind, usage=usage)

    async def probe(self) -> bool:
        try:
            got = letters.read(await self.http.post_json(CHAT_PATH, letters.payload(self.model, letters.prompt("It is raining.", "Is it raining?", ["yes", "no"])),
                                                          what="decisions"), 2)
        except ProviderError:
            return False
        return got is not None and got[1] >= letters.MIN_MASS

    async def aclose(self) -> None:
        await self.http.aclose()


registry.register(KIND, LetterModelScorer.from_env, provider_kinds=PROVIDER_KINDS, needs=NEEDS)

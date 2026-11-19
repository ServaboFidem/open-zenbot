"""One decision from an ordinary chat endpoint, read from the first token's probabilities.

The prompt lists the options as letters and ends with `Answer:`; one token is asked for with its
`top_logprobs`, and the mass on the option letters, renormalised, is the distribution over the options. This
is the format the Jev-Style v1 decision models were trained on (their model card gives it), and it is what
LM Studio and llama.cpp serve on `/chat/completions`. No text is generated or read: an answer is numbers.

Pure functions, used by the capability test (`probe.py`) and by the `letter-logprobs` scorer
(`src/decider/adapters/letter_model.py`), so both ask in exactly the same way."""

from __future__ import annotations

import math
from typing import Any

LETTERS = "ABCDEFGHIJKLMNOPQRST"
MAX_OPTIONS = len(LETTERS)  # a server returns at most 20 candidate tokens for a position
TOP = 20
MIN_MASS = 0.5  # under this much probability on the letters, the model is not answering with one
HEADER = "You are a decision function. Read the state, then answer the question by choosing exactly one option.\n\n"


def prompt(state: str, question: str, options: list[str]) -> str:
    if not 2 <= len(options) <= MAX_OPTIONS:
        raise ValueError(f"a decision takes 2 to {MAX_OPTIONS} options, not {len(options)}")
    lines = "\n".join(f"{LETTERS[i]}. {_line(o)}" for i, o in enumerate(options))
    return f"{HEADER}[State]\n{state}\n\n[Question]\n{question}\n\n[Options]\n{lines}\n\nAnswer:"


def _line(text: str) -> str:
    return " ".join(str(text).split())  # one option, one line


def payload(model: str, text: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": text}], "max_tokens": 1, "temperature": 0,
            "logprobs": True, "top_logprobs": TOP}


def read(data: Any, count: int) -> tuple[list[float], float] | None:
    """(the probability of each of the first `count` options, summing to 1; how much of the token's probability
    was on those letters), or None when the answer carries no token probabilities. With nothing on the letters
    the options come back uniform and the mass 0.0."""
    try:
        top = data["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(top, list) or not top:
        return None
    raw = [0.0] * count
    for t in top:
        if not isinstance(t, dict):
            continue
        tok, lp = t.get("token"), t.get("logprob")
        if not isinstance(tok, str) or isinstance(lp, bool) or not isinstance(lp, (int, float)) or not math.isfinite(lp):
            continue
        i = LETTERS.find(tok.strip()) if len(tok.strip()) == 1 else -1
        if 0 <= i < count:
            raw[i] += math.exp(min(lp, 0.0))
    mass = sum(raw)
    if mass <= 0:
        return [1.0 / count] * count, 0.0
    return [r / mass for r in raw], min(mass, 1.0)

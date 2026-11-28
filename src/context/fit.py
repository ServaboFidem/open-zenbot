"""Fitting rules: how big a summary piece may be so it fits a model's context.

A local summary model has a context window the harness may not know (or
that the server cuts short). These rules size each piece from the window
it has, and read a length refusal from a provider's error so the harness
can learn the real window and try again.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol

MAX_PIECE = 16_000  # characters, the most one piece may carry
CHARS_PER_TOKEN = 3  # a conservative estimate for text
MIN_RESERVE = 512  # tokens kept free for the answer, at least
RECOMMENDED_CONTEXT = 16_384  # tokens, the window the settings suggest
MIN_PIECE = 500  # characters, below this a piece is not worth making


class ContextTooSmall(Exception):
    """The model's window leaves no room for a useful piece."""


@dataclass(frozen=True)
class Overrun:
    """A length refusal: the window the server has and the prompt it was sent."""

    n_ctx: int | None
    n_prompt_tokens: int | None


class ContextOverrun(Exception):
    """The provider refused a request as too long for its context."""

    def __init__(self, overrun: Overrun) -> None:
        super().__init__(f"prompt too long for the context ({overrun.n_prompt_tokens} tokens, window {overrun.n_ctx})")
        self.overrun = overrun


class ContextSource(Protocol):
    """Where the usable window comes from, and what a refusal teaches it."""

    async def limit(self) -> int | None: ...

    def learn(self, o: Overrun) -> None: ...


_REFUSAL = re.compile(r"exceed_context_size_error|exceeds the available context size")
_N_CTX = re.compile(r'"n_ctx"\s*:\s*(\d+)')
_N_PROMPT = re.compile(r'"n_prompt_tokens"\s*:\s*(\d+)')
_CTX_SENTENCE = re.compile(r"available context size \((\d+) tokens\)")
_PROMPT_SENTENCE = re.compile(r"request \((\d+) tokens\)")


def parse_overrun(text: str) -> Overrun | None:
    """The length refusal in a provider's error text, or None when the text
    shows none. Either number may be missing."""
    if not _REFUSAL.search(text):
        return None
    n_ctx = _N_CTX.search(text) or _CTX_SENTENCE.search(text)
    n_prompt = _N_PROMPT.search(text) or _PROMPT_SENTENCE.search(text)
    return Overrun(
        int(n_ctx.group(1)) if n_ctx else None,
        int(n_prompt.group(1)) if n_prompt else None,
    )


def piece_chars(context: int | None, overhead_chars: int, rate: float = CHARS_PER_TOKEN) -> int:
    """How many characters one piece may carry for a window of `context`
    tokens, after the prompt's own `overhead_chars` and a reserve for the
    answer, at `rate` characters a token. Raises ContextTooSmall when that
    leaves under MIN_PIECE."""
    if context is None:
        return MAX_PIECE
    overhead_tokens = math.ceil(overhead_chars / rate)
    reserve = max(MIN_RESERVE, context // 4)
    chars = int((context - overhead_tokens - reserve) * rate)
    if chars < MIN_PIECE:
        raise ContextTooSmall(f"a window of {context} tokens leaves no room for a summary piece")
    return min(MAX_PIECE, chars)

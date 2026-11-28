"""fit: rules for sizing summary pieces to a model's context, and reading a
length refusal from a provider's error text."""

from __future__ import annotations

import pytest

from src.context.fit import (
    ContextOverrun,
    ContextTooSmall,
    Overrun,
    parse_overrun,
    piece_chars,
)

LM_STUDIO_TEXT = (
    'openai-compatible HTTP 400: Engine protocol predict request returned 400: '
    '{"error":{"code":400,"message":"request (5726 tokens) exceeds the available context size '
    '(4096 tokens), try increasing it","type":"exceed_context_size_error","n_prompt_tokens":5726,"n_ctx":4096}}'
)
SENTENCE = "request (5726 tokens) exceeds the available context size (4096 tokens), try increasing it"


def test_parse_overrun_reads_lm_studios_refusal():
    assert parse_overrun(LM_STUDIO_TEXT) == Overrun(4096, 5726)


def test_parse_overrun_reads_the_sentence_alone():
    assert parse_overrun(SENTENCE) == Overrun(4096, 5726)


def test_parse_overrun_is_none_for_other_errors():
    assert parse_overrun("HTTP 500") is None
    assert parse_overrun("rate limited") is None


def test_parse_overrun_keeps_a_missing_number_as_none():
    text = '{"error":{"type":"exceed_context_size_error","n_ctx":4096}}'
    assert parse_overrun(text) == Overrun(4096, None)


def test_context_overrun_carries_the_overrun():
    overrun = Overrun(4096, 5726)
    err = ContextOverrun(overrun)
    assert err.overrun == overrun


def test_piece_chars():
    assert piece_chars(None, 900) == 16_000
    assert piece_chars(4096, 900) == 8316
    assert piece_chars(131_072, 900) == 16_000


def test_piece_chars_raises_when_the_context_is_too_small():
    with pytest.raises(ContextTooSmall):
        piece_chars(900, 900)

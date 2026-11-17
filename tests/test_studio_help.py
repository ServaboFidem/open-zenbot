"""`studio_help.pick`: a helper takes a revision only as the single clear winner."""

from __future__ import annotations

import pytest

from src.decider.interface import NoulAnswer
from src.harness.bot import Bot
from src.harness.studio_help import StudioHelpConfig, pick
from src.memory.ledger import Ledger
from src.providers.interface import ProviderError

from .conftest import MockScorer

OWNER = Bot(name="Designer", role="Designs pages", id="designer", studio=True)
COPY = Bot(name="Copywriter", role="Writes copy", id="copywriter", places=["text"])
IMG = Bot(name="ImageEditor", role="Edits pictures", id="darkroom", places=["images"])
COMMENTS = [{"tag": "p", "note": "Punchier"}]


def scorer(**by_id: float) -> MockScorer:
    return MockScorer(lambda req: {k: NoulAnswer(by_id.get(k.removeprefix("bot:"), 0.0)) for k in req.questions})


async def test_one_helper_above_the_floor_takes_it(ledger: Ledger):
    s = scorer(copywriter=0.9)
    assert await pick(s, ledger, OWNER, [COPY], "punch it up", COMMENTS) == "copywriter"
    req = s.requests[0]
    assert req.purpose == "studio_help" and req.state == {"message": "punch it up", "comments": ["<p> Punchier"]}
    assert req.questions["bot:copywriter"].instructions == (
        "Is Copywriter (Writes copy) clearly a better fit for this revision than the designer Designer, given that "
        "Copywriter can only change the text inside elements that already exist and cannot add, remove, move or restyle anything?")
    route = [e.data for e in ledger.iter() if e.kind == "design_part"]
    assert route == [{"event": "route", "scores": {"copywriter": 0.9}, "outcome": "copywriter"}]


async def test_a_low_score_goes_to_the_owner(ledger: Ledger):
    assert await pick(scorer(copywriter=0.6), ledger, OWNER, [COPY], "x", []) is None
    assert [e.data["outcome"] for e in ledger.iter() if e.kind == "design_part"] == ["owner"]


async def test_two_helpers_within_the_band_go_to_the_owner(ledger: Ledger):
    assert await pick(scorer(copywriter=0.9, darkroom=0.88), ledger, OWNER, [COPY, IMG], "x", []) is None
    assert await pick(scorer(copywriter=0.9, darkroom=0.2), ledger, OWNER, [COPY, IMG], "x", []) == "copywriter"


async def test_a_scorer_error_goes_to_the_owner_and_is_logged(ledger: Ledger):
    s = MockScorer(script=[ProviderError("down")])
    assert await pick(s, ledger, OWNER, [COPY], "x", []) is None
    errors = [e.data for e in ledger.iter() if e.kind == "error"]
    assert len(errors) == 1 and errors[0]["where"] == "studio_help"


async def test_no_scorer_or_no_helpers_means_no_call(ledger: Ledger):
    s = scorer(copywriter=0.9)
    assert await pick(None, ledger, OWNER, [COPY], "x", []) is None
    assert await pick(s, ledger, OWNER, [], "x", []) is None
    assert s.requests == []


async def test_the_thresholds_can_be_set(ledger: Ledger):
    cfg = StudioHelpConfig(band_width=0.1, k=1, low_confidence=0.5)
    assert await pick(scorer(copywriter=0.6), ledger, OWNER, [COPY], "x", [], cfg) == "copywriter"


def test_the_defaults_read_the_environment(monkeypatch: pytest.MonkeyPatch):
    assert (StudioHelpConfig().low_confidence, StudioHelpConfig().band_width) == (0.7, 0.1)
    monkeypatch.setenv("ZEN_STUDIO_HELP_MIN", "0.8")
    monkeypatch.setenv("ZEN_STUDIO_HELP_BAND", "0.2")
    assert (StudioHelpConfig().low_confidence, StudioHelpConfig().band_width) == (0.8, 0.2)

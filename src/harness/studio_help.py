"""Routing a design revision: does a helper bot fit it better than the Designer? One decider request with
a question per helper; a helper takes the revision only as the single clear winner, anything doubtful
leaves it with the Designer (the owner)."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.decider.interface import BandConfig, DecisionRefused, DecisionRequest, NoulQuestion, Outcome, decide_and_log, select_band
from src.memory.ledger import Kind
from src.providers.interface import ProviderError

if TYPE_CHECKING:
    from .bot import Bot


def _flat(s: str) -> str:
    return " ".join(s.split())


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class StudioHelpConfig:
    band_width: float = field(default_factory=lambda: _env_float("ZEN_STUDIO_HELP_BAND", 0.1))
    k: int = 1
    low_confidence: float = field(default_factory=lambda: _env_float("ZEN_STUDIO_HELP_MIN", 0.7))


async def pick(scorer: Any, ledger: Any, owner: Bot, helpers: list[Bot], text: str, comments: list[dict[str, str]],
               config: StudioHelpConfig | None = None) -> str | None:
    """A helper's bot id when it is the single clear winner, else None (the owner takes the revision)."""
    if scorer is None or not helpers:
        return None
    cfg = config or StudioHelpConfig()
    questions = {
        f"bot:{b.id}": NoulQuestion(
            f"Is {b.name} ({b.role}) clearly a better fit for this revision than the designer {owner.name}, given that "
            f"{b.name} can only change the {' and '.join(b.places)} inside elements that already exist and cannot add, "
            f"remove, move or restyle anything?")
        for b in helpers}
    request = DecisionRequest(state={"message": text, "comments": [f"<{_flat(c['tag'])}> {_flat(c['note'])}" for c in comments]},
                              questions=questions, purpose="studio_help")
    try:
        decision = await decide_and_log(scorer, request, ledger, owner.id, "")
        scores = {b.id: decision.noul(f"bot:{b.id}").noul for b in helpers}
        if not all(math.isfinite(v) for v in scores.values()):
            raise ValueError("a score is not a finite number")
    except (ProviderError, DecisionRefused, KeyError, TypeError, ValueError) as e:
        ledger.append(Kind.error, owner.id, "", where="studio_help", error=f"{type(e).__name__}: {e}", action="designer takes it")
        return None
    band = select_band(scores, BandConfig(cfg.band_width, cfg.k, cfg.low_confidence))
    chosen = band.winners[0] if band.outcome == Outcome.act and len(band.winners) == 1 and not band.capped else None
    ledger.append(Kind.design_part, owner.id, "", event="route", scores=scores, outcome=chosen or "owner")
    return chosen

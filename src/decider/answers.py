"""Typed answers from a probability per option. For a scorer whose model gives a distribution over the options
it was shown (the `letter-logprobs` scorer); a scorer whose API already answers in the typed shape parses that
instead (`adapters/decision_model.py` `parse_answer`)."""

from __future__ import annotations

from .interface import Answer, ChoiceAnswer, ChoiceQuestion, NoulAnswer, NoulQuestion, Question, ScoreAnswer

YES, NO = "yes", "no"


def options(q: Question) -> list[str]:
    """What the model is shown for a question, in the order `from_probabilities` reads the answer back."""
    if isinstance(q, NoulQuestion):
        c = q.criteria or {}
        return [_with(YES, c.get("true")), _with(NO, c.get("false"))]
    if isinstance(q, ChoiceQuestion):
        return [_with(label, what) for label, what in q.criteria.items()]
    return [text(level) for level in q.criteria]


def from_probabilities(q: Question, probs: list[float]) -> Answer:
    if isinstance(q, NoulQuestion):
        return NoulAnswer(noul=probs[0])
    if isinstance(q, ChoiceQuestion):
        labels = list(q.criteria)
        best = max(range(len(probs)), key=probs.__getitem__)
        return ChoiceAnswer(choice=labels[best], probabilities=dict(zip(labels, probs)), confidence=_confidence(probs))
    return ScoreAnswer(score=sum(i * p for i, p in enumerate(probs)), probabilities={str(i): p for i, p in enumerate(probs)},
                       legend={str(i): text(level) for i, level in enumerate(q.criteria)})


def text(guidance: object) -> str:
    """Guidance (a string, or a dict or list of them) as one piece of text."""
    if guidance is None:
        return ""
    if isinstance(guidance, str):
        return guidance
    if isinstance(guidance, dict):
        return "; ".join(f"{k}: {text(v)}" for k, v in guidance.items())
    if isinstance(guidance, (list, tuple)):
        return "; ".join(text(v) for v in guidance)
    return str(guidance)


def _with(label: str, what: object) -> str:
    what = text(what)
    return f"{label}: {what}" if what else label


def _confidence(probs: list[float]) -> float:
    """0 when the options are equally likely, 1 when one holds everything."""
    k = len(probs)
    return 1.0 if k <= 1 else min(1.0, max(0.0, (k * max(probs) - 1.0) / (k - 1.0)))

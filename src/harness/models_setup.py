"""From Settings' choices (which provider and model for chat, summaries, decisions) to the providers and
scorer the harness runs on. Used at start (main.py, from `vault-settings/harness.json`) and when the
Settings > Models > Jobs form saves. Every refusal is a `ProviderStoreError` whose message is safe to show."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.decider import registry as scorers
from src.decider.interface import Scorer
from src.generation.images import DEFAULT_MODEL as DEFAULT_IMAGE_MODEL
from src.providers import registry as providers
from src.providers.interface import Provider
from src.providers.models import ModelInfo
from src.providers.store import ProviderEntry, ProviderStore, ProviderStoreError

from .config import HarnessConfig
from .model_roles import ROLE_LABELS, ROLES, Choice, ModelRoles

DEFAULT_CHAT = "meta/muse-spark-1.3-contributor"  # the OpenRouter adapter's default
DEFAULT_DECISION = "microsoft/microsoft-decision-1"  # the decision scorer's default
# the dreaming model's request timeout: a local model on a modest computer may take minutes over one 12,000-character
# batch, and nobody waits on a dream; every other role keeps the adapters' own default
DREAM_TIMEOUT_S = 600.0
# the shape `Generator.dream_facts` asks for, as a schema: LM Studio refuses the plain `json_object` form
DREAM_FORMAT: dict[str, Any] = {"type": "json_schema", "json_schema": {"name": "dream_facts", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["facts"],
    "properties": {"facts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "store"],
        "properties": {"text": {"type": "string"}, "store": {"type": "string"}}}}}}}}


def dream_extras(kind: str, m: ModelInfo | None) -> dict[str, Any]:
    """The optional request fields of a dream call, from what is known of the model: a JSON answer for a model
    that takes `response_format`, and thinking off for one that reasons (OpenRouter's `reasoning` object there,
    OpenAI's `reasoning_effort` on any other server). Nothing is sent for a model whose abilities are unknown."""
    out: dict[str, Any] = {}
    if m is None:
        return out
    if m.structured_output:
        out["response_format"] = DREAM_FORMAT
    if m.reasoning:
        out.update({"reasoning": {"enabled": False}} if kind == "openrouter" else {"reasoning_effort": "none"})
    return out


def default_models(c: HarnessConfig) -> list[ModelInfo]:
    """What the seeded OpenRouter provider starts with enabled: the models the harness already uses."""
    picks = [(c.model or DEFAULT_CHAT, "text"), (c.summary_model, "text"), (c.decision_model or DEFAULT_DECISION, "decision"),
             (DEFAULT_IMAGE_MODEL, "image")]
    seen: dict[str, ModelInfo] = {}
    for mid, kind in picks:
        seen.setdefault(mid, ModelInfo(id=mid, name=mid, kinds=[kind]))
    return list(seen.values())


def roles_from_config(c: HarnessConfig, provider_id: str = "openrouter") -> ModelRoles:
    """The choices the harness is running on when no harness.json exists yet."""
    kind = c.scorer_kind or ""
    if "openrouter" not in scorers.provider_kinds(kind):  # a local scorer needs its provider: it is chosen in Settings
        kind = ""
    return ModelRoles(chat=Choice(provider_id, c.model or DEFAULT_CHAT), summary=Choice(provider_id, c.summary_model),
                      decision=Choice(provider_id, c.decision_model or DEFAULT_DECISION), scorer=kind)


def dream_provider(store: ProviderStore, roles: ModelRoles) -> tuple[Provider, str] | None:
    """The dreamer's provider and model, or None when no Dreaming model is chosen (it is never filled from
    another role). Raises ProviderStoreError when the choice cannot run."""
    if roles.dream is None:
        return None
    e = check_choice(store, roles.dream, "text", ROLE_LABELS["dream"])
    extras = dream_extras(e.kind, e.model(roles.dream.model))
    return make_provider(store, e, roles.dream.model, timeout=DREAM_TIMEOUT_S, extras=extras), roles.dream.model


@dataclass
class Bound:
    provider: Provider
    summary_provider: Provider | None  # None: summaries run on the chat provider
    scorer: Scorer | None
    chat: Choice
    summary: Choice
    decision: Choice | None
    key_ref: str
    openrouter_ref: str | None


def _entry(store: ProviderStore, role: str, choice: Choice | None) -> ProviderEntry:
    return check_choice(store, choice, ROLES[role], ROLE_LABELS[role])


def check_choice(store: ProviderStore, choice: Choice | None, kind: str, label: str) -> ProviderEntry:
    """The provider entry for a choice that can run: the provider exists and is usable, the model is enabled
    on it and is of `kind`. Raises ProviderStoreError naming `label`."""
    if choice is None:
        raise ProviderStoreError(f"{label}: pick a model")
    if choice.provider not in store.entries:
        raise ProviderStoreError(f"{label}: no provider {choice.provider!r}")
    e = store.entries[choice.provider]
    m = e.model(choice.model)
    if m is None:
        raise ProviderStoreError(f"{label}: {choice.model} is not enabled on {e.name}; enable it under Settings > Models > Providers")
    if kind not in m.kinds:
        raise ProviderStoreError(f"{label}: {choice.model} is not a {kind} model")
    if not e.usable():
        raise ProviderStoreError(f"{label}: {e.name} has no key; add one under Settings > Models > Providers")
    return e


def make_provider(store: ProviderStore, e: ProviderEntry, model: str, *, timeout: float | None = None,
                  extras: dict[str, Any] | None = None) -> Provider:
    """`timeout` (seconds a request may take) is passed only when given; otherwise the adapter keeps its default.
    `extras` are optional request fields for every call of this client (see `OpenRouterProvider.extras`)."""
    import src.providers.openai_compatible  # noqa: F401 - registers the provider
    import src.providers.openrouter  # noqa: F401 - registers the provider

    kwargs: dict[str, Any] = {"key_ref": store.key_ref(e.id), "model": model}
    if e.kind != "openrouter":
        kwargs["base_url"] = e.base_url
    if timeout is not None:
        kwargs["timeout"] = timeout
    if extras:
        kwargs["extras"] = extras
    try:
        return providers.get(e.kind, **kwargs)
    except Exception as ex:  # noqa: BLE001 - a factory refused; the store keeps keys out of messages
        raise ProviderStoreError(f"{e.name}: {type(ex).__name__}: {ex}") from None


MARKS = {"letter_decisions": "Letter decisions", "typed_decisions": "Typed decisions"}  # the test's step names


def _kinds(kinds: tuple[str, ...]) -> str:
    names = {"openrouter": "an OpenRouter provider", "openai-compatible": "an OpenAI-compatible provider (LM Studio, llama.cpp)"}
    return " or ".join(names.get(k, k) for k in kinds)


def build(store: ProviderStore, roles: ModelRoles) -> Bound:
    import src.decider.adapters.decision_model  # noqa: F401 - registers the scorer
    import src.decider.adapters.letter_model  # noqa: F401 - registers the local scorer
    import src.decider.adapters.systemone  # noqa: F401 - registers the typed local scorer

    chat_e = _entry(store, "chat", roles.chat)
    summary_e = _entry(store, "summary", roles.summary)
    provider = make_provider(store, chat_e, roles.chat.model)
    summary_provider = None if summary_e.id == chat_e.id else make_provider(store, summary_e, roles.summary.model)
    scorer, decision_e = None, None
    if roles.scorer:
        if not scorers.is_registered(roles.scorer):
            raise ProviderStoreError(f"unknown scorer {roles.scorer!r}; known: {scorers.kinds()}")
        decision_e = _entry(store, "decision", roles.decision)
        needs = scorers.provider_kinds(roles.scorer)
        if decision_e.kind not in needs:
            raise ProviderStoreError(f"Decision model: the {roles.scorer} scorer runs on {_kinds(needs)}; "
                                     f"{decision_e.name} is not one. Pick a model on one, or another scorer")
        mark = scorers.needs(roles.scorer)
        chosen = decision_e.model(roles.decision.model)
        if mark and chosen is not None and mark not in (chosen.supported_parameters or []):
            raise ProviderStoreError(f"Decision model: {roles.decision.model} did not pass the test the {roles.scorer} scorer "
                                     f"needs ({MARKS.get(mark, mark)}). Pick another model or scorer, or disable the model "
                                     "and enable it again to test it")
        kwargs: dict[str, Any] = {"key_ref": store.key_ref(decision_e.id), "model": roles.decision.model}
        if decision_e.kind != "openrouter":
            kwargs["base_url"] = decision_e.base_url
        try:
            scorer = scorers.get(roles.scorer, **kwargs)
        except Exception as ex:  # noqa: BLE001
            raise ProviderStoreError(f"Decision model: {type(ex).__name__}: {ex}") from None
    openrouter = next((e for e in (decision_e, chat_e, summary_e) if e is not None and e.kind == "openrouter"), None)
    return Bound(provider, summary_provider, scorer, roles.chat, roles.summary, roles.decision if roles.scorer else None,
                 store.key_ref(chat_e.id) or "", store.key_ref(openrouter.id) if openrouter else None)

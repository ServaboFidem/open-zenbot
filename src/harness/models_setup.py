"""From Settings' choices (which provider and model for chat, summaries, decisions) to the providers and
scorer the harness runs on. Used at start (main.py, from `vault-settings/harness.json`) and when the
Harness settings page saves. Every refusal is a `ProviderStoreError` whose message is safe to show."""

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
    return ModelRoles(chat=Choice(provider_id, c.model or DEFAULT_CHAT), summary=Choice(provider_id, c.summary_model),
                      decision=Choice(provider_id, c.decision_model or DEFAULT_DECISION), scorer=c.scorer_kind or "")


def dream_provider(store: ProviderStore, roles: ModelRoles) -> tuple[Provider, str] | None:
    """The dreamer's provider and model, or None when no Dreaming model is chosen (it is never filled from
    another role). Raises ProviderStoreError when the choice cannot run."""
    if roles.dream is None:
        return None
    e = check_choice(store, roles.dream, "text", ROLE_LABELS["dream"])
    return make_provider(store, e, roles.dream.model), roles.dream.model


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
        raise ProviderStoreError(f"{label}: {choice.model} is not enabled on {e.name}; enable it on the Model providers page")
    if kind not in m.kinds:
        raise ProviderStoreError(f"{label}: {choice.model} is not a {kind} model")
    if not e.usable():
        raise ProviderStoreError(f"{label}: {e.name} has no key; add one on the Model providers page")
    return e


def make_provider(store: ProviderStore, e: ProviderEntry, model: str) -> Provider:
    import src.providers.openai_compatible  # noqa: F401 - registers the provider
    import src.providers.openrouter  # noqa: F401 - registers the provider

    kwargs: dict[str, Any] = {"key_ref": store.key_ref(e.id), "model": model}
    if e.kind != "openrouter":
        kwargs["base_url"] = e.base_url
    try:
        return providers.get(e.kind, **kwargs)
    except Exception as ex:  # noqa: BLE001 - a factory refused; the store keeps keys out of messages
        raise ProviderStoreError(f"{e.name}: {type(ex).__name__}: {ex}") from None


def build(store: ProviderStore, roles: ModelRoles) -> Bound:
    import src.decider.adapters.decision_model  # noqa: F401 - registers the scorer

    chat_e = _entry(store, "chat", roles.chat)
    summary_e = _entry(store, "summary", roles.summary)
    provider = make_provider(store, chat_e, roles.chat.model)
    summary_provider = None if summary_e.id == chat_e.id else make_provider(store, summary_e, roles.summary.model)
    scorer, decision_e = None, None
    if roles.scorer:
        if not scorers.is_registered(roles.scorer):
            raise ProviderStoreError(f"unknown scorer {roles.scorer!r}; known: {scorers.kinds()}")
        decision_e = _entry(store, "decision", roles.decision)
        if decision_e.kind != "openrouter":
            raise ProviderStoreError("Decision model: the decisions API is OpenRouter's; pick a model on an OpenRouter provider")
        try:
            scorer = scorers.get(roles.scorer, key_ref=store.key_ref(decision_e.id), model=roles.decision.model)
        except Exception as ex:  # noqa: BLE001
            raise ProviderStoreError(f"Decision model: {type(ex).__name__}: {ex}") from None
    openrouter = next((e for e in (decision_e, chat_e, summary_e) if e is not None and e.kind == "openrouter"), None)
    return Bound(provider, summary_provider, scorer, roles.chat, roles.summary, roles.decision if roles.scorer else None,
                 store.key_ref(chat_e.id) or "", store.key_ref(openrouter.id) if openrouter else None)

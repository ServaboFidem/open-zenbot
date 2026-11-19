"""Which provider and model the harness uses for each job, chosen in Settings > Harness settings from the
models enabled on the Model providers page. Kept in `vault-settings/harness.json`.

- chat: bots' tasks (a text model)
- summary: compaction, conversation folds, vault-doc summaries (a text model)
- decision: the decider's scorer (a decision model on an OpenRouter provider; the decisions API is OpenRouter's)
- dream: the background dreamer (a text model); optional, never filled from another role

Image generation has its own entry in `vault-settings/tools.json` (Harness Tools)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.vault_settings import settings_dir

FILE = "harness.json"
BOT_FILE = "bot-models.json"  # bot id -> {provider, model}; the bot-prompts files are in git, provider ids are this vault's
ROLES = {"chat": "text", "summary": "text", "decision": "decision", "dream": "text"}
ROLE_LABELS = {"chat": "Chat model", "summary": "Summary model", "decision": "Decision model", "dream": "Dreaming model"}
OPTIONAL_ROLES = {"dream"}  # may stay unset; the harness runs without them


@dataclass
class Choice:
    provider: str
    model: str

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model}


@dataclass
class ModelRoles:
    chat: Choice | None = None
    summary: Choice | None = None
    decision: Choice | None = None
    dream: Choice | None = None
    scorer: str = ""  # a scorer kind, or "" for none
    extra: dict[str, Any] = field(default_factory=dict)

    def get(self, role: str) -> Choice | None:
        return getattr(self, role)

    def to_dict(self) -> dict[str, Any]:
        return {**{r: (c.to_dict() if (c := self.get(r)) else None) for r in ROLES}, "scorer": self.scorer}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ModelRoles":
        def choice(v: Any) -> Choice | None:
            if isinstance(v, dict) and isinstance(v.get("provider"), str) and isinstance(v.get("model"), str) and v["provider"] and v["model"]:
                return Choice(v["provider"], v["model"])
            return None

        return cls(chat=choice(d.get("chat")), summary=choice(d.get("summary")), decision=choice(d.get("decision")),
                   dream=choice(d.get("dream")),
                   scorer=d.get("scorer") if isinstance(d.get("scorer"), str) else "")

    @classmethod
    def load(cls, vault_root: Path) -> "ModelRoles | None":
        try:
            raw = json.loads((settings_dir(vault_root) / FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return cls.from_dict(raw) if isinstance(raw, dict) else None

    def save(self, vault_root: Path) -> None:
        path = settings_dir(vault_root) / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)


def load_bot_models(vault_root: Path) -> dict[str, Choice]:
    try:
        raw = json.loads((settings_dir(vault_root) / BOT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for bot_id, v in (raw.items() if isinstance(raw, dict) else []):
        if isinstance(v, dict) and isinstance(v.get("provider"), str) and isinstance(v.get("model"), str) and v["provider"] and v["model"]:
            out[str(bot_id)] = Choice(v["provider"], v["model"])
    return out


def save_bot_models(vault_root: Path, models: dict[str, Choice]) -> None:
    path = settings_dir(vault_root) / BOT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: c.to_dict() for k, c in sorted(models.items())}, indent=2), encoding="utf-8")
    tmp.replace(path)

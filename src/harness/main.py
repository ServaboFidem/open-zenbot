"""Entry point. `python -m src.harness.main` starts the harness with every
bot under bot-prompts/ and every skill under the skills directory."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

from src.memory.ledger import Ledger
from src.providers import registry
from src.providers.secrets import SecretNotFound
from src.providers.store import ProviderStoreError
from src.skills.catalog import Catalog
from src.skills.loader import load_dir

from .api import Harness, create_app
from .bot import Bot
from .bot_prompts import load_dir as load_bot_prompts
from .config import HarnessConfig
from .model_roles import ModelRoles

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"  # the repo's .env, wherever the harness is started from


def env_file(env: Mapping[str, str] = os.environ) -> Path:
    """ZEN_ENV_FILE when set and non-empty (the installed app keeps its .env outside the repo), else the repo's."""
    moved = env.get("ZEN_ENV_FILE")
    return Path(moved) if moved else ENV_FILE


def load_env_file(path: Path) -> list[str]:
    """Copy the file's settings into the environment, never over one already set (the shell wins).
    Returns the names it set; never the values, which are keys."""
    loaded = []
    for name, value in dotenv_values(path).items():
        if value is not None and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def build_harness(config: HarnessConfig | None = None) -> Harness:
    config = config or HarnessConfig()
    config.validate()
    import src.decider.adapters.decision_model  # noqa: F401 - registers the scorer
    import src.decider.adapters.letter_model  # noqa: F401 - registers the local scorer
    import src.providers.openrouter  # noqa: F401 - registers the provider
    from src.decider import registry as scorers

    try:
        provider = registry.get(config.provider_kind, key_ref=config.key_ref, model=config.model)
        if "openrouter" in scorers.provider_kinds(config.scorer_kind):
            scorer = scorers.get(config.scorer_kind, key_ref=config.key_ref, model=config.decision_model)
        else:  # a scorer on another provider needs that provider's address: it is chosen in Settings (harness.json)
            scorer = None
            print(f"ZEN_SCORER={config.scorer_kind} is not set up from the environment: choose it on Settings > Harness settings",
                  file=sys.stderr)
    except SecretNotFound:  # no key in the environment: start unbound, the GUI's Settings page binds one
        provider, scorer = None, None
    ledger = Ledger(config.vault_root)
    catalog = Catalog()
    # the catalog keeps the first skill of a slug, so the user's folder goes first and wins
    folders = [config.skills_dir]
    if config.user_content:
        from src.vault_settings import user_skills_dir

        folders.insert(0, user_skills_dir(config.vault_root))
    for folder in folders:
        for r in load_dir(folder, skill_config(config)):
            if r.ok:
                catalog.add_skill(r.skill)
    h = Harness(config=config, provider=provider, ledger=ledger, catalog=catalog, scorer=scorer)
    saved = ModelRoles.load(config.vault_root)  # Settings > Harness settings, once saved, wins over the environment
    if saved is not None:
        try:
            h.apply_models(saved)
        except ProviderStoreError as e:
            print(f"vault-settings/harness.json not applied: {e}", file=sys.stderr)
    else:  # nothing saved and no key in the environment: a key saved on a provider (providers.json) still binds
        h.bind_saved_key()
    folders = [config.bot_prompts_dir]
    if config.user_content:
        from src.vault_settings import user_bots_dir

        folders.append(user_bots_dir(config.vault_root))
    by_id = {}
    for folder in folders:  # the user's folder loads last and wins on the same id
        found, errors = load_bot_prompts(folder, report=True)
        for err in errors:
            print(f"{folder.name}: skipped {err}", file=sys.stderr)
        for p in found:
            by_id[p.id] = p
    prompts = list(by_id.values())
    for p in prompts:
        bot = Bot(p.name, p.role, p.instructions, id=p.id, mcps=list(p.mcps or []), studio=p.studio, places=list(p.places), closed=p.closed)
        wanted = catalog.skills() if p.skills is None else [s for s in (catalog.skill(ref) for ref in p.skills) if s is not None]
        for s in wanted:
            bot.attach_skill(s, config.max_skills_per_bot)
        h.add_bot(bot)
    return h


def skill_config(config: HarnessConfig):
    from src.skills.loader import SkillConfig

    return SkillConfig(config.max_skill_bytes, config.max_skills_per_bot)


def main() -> int:
    import uvicorn

    from .telemetry import DSN_ENV, init_tracing

    # first: the settings below, the provider key, and the MCP servers' ${VAR}s all read the environment
    env_path = env_file()
    loaded = load_env_file(env_path)
    if loaded:
        print(f"{env_path.name}: {len(loaded)} {'setting' if len(loaded) == 1 else 'settings'} ({', '.join(sorted(loaded))})", file=sys.stderr)
    from src.providers import tracing as llm_tracing
    from src.vault_settings import migrate, settings_dir

    from . import tracing_settings

    # settings live beside the vault, out of the files MCP server's reach: move an older <vault>/settings/ out first
    vault_root = HarnessConfig().vault_root
    for note in migrate(vault_root):
        print(f"{settings_dir(vault_root).name}: {note}", file=sys.stderr)
    # Settings > Tracing, once saved, wins over .env
    eff = tracing_settings.effective(vault_root)
    if init_tracing(dsn=eff["sentry"]["dsn"]):
        print(f"sentry tracing on ({DSN_ENV if eff['sentry']['source'] == '.env' else settings_dir(vault_root).name + '/tracing.json'})", file=sys.stderr)
    if llm_tracing.init_langfuse(tracing_settings.langfuse_env(eff["langfuse"])):
        print(f"langfuse tracing on ({eff['langfuse']['base_url'] or 'cloud'}, from {eff['langfuse']['source']})", file=sys.stderr)
    h = build_harness()
    if not h.bots:
        print(f"no bots: add a file under {h.config.bot_prompts_dir}/ (name, role in frontmatter; instructions in the body) or create one in the app", file=sys.stderr)
    if not h.bound:
        print("no usable key in the environment or on a saved provider: the harness is unbound until Settings binds a provider (or set OPENROUTER_API_KEY / ZEN_KEY_REF)", file=sys.stderr)
    print(f"zen-agents harness on http://{h.config.host}:{h.config.port}  provider={h.provider.kind}  scorer={h.scorer.kind if h.scorer else None}  "
          f"bots={len(h.bots)}  skills={len(h.catalog.skills())}  ledger={h.ledger.file}")
    try:
        uvicorn.run(create_app(h), host=h.config.host, port=h.config.port, log_level="info")
    finally:
        llm_tracing.shutdown()  # send what is buffered
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

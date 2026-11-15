# Wiki publish check

Result: 3 passed, 0 failed. Zero fixes.

Sources reviewed:

- `feat/wiki-init-03/wiki-home:docs/wiki/Home.md` (12 lines)
- `feat/wiki-init-03/wiki-getting-started:docs/wiki/Getting-Started.md` (41 lines)
- `feat/wiki-init-03/wiki-capabilities:docs/wiki/Capabilities.md` (111 lines)
- `README.md` at `006e7ef` in this worktree
- `mcp.json`, `scripts/dev.ps1`, `scripts/dev.sh`, `scripts/install-deps.ps1`, `scripts/install-deps.sh`, `bot-prompts/`

Checks per page: links resolve to existing files, commands match `README.md` and the scripts, no secret values appear in text.

## Home — pass

- Links: `[Getting started](Getting-Started)` resolves to `docs/wiki/Getting-Started.md`. `[Capabilities](Capabilities)` resolves to `docs/wiki/Capabilities.md`. Both target files exist on their sibling branches. No other links. No broken links.
- Commands: page gives no run steps by design. No commands to verify.
- Secrets: none found. No key values, tokens, passwords, or private paths. Stale hits: 0.
- Fixes: none.

## Getting-Started — pass

- Links: page holds no markdown links. No broken links.
- Commands: all commands match `README.md` and the scripts in this worktree.
  - `.\scripts\install-deps.ps1` and `./scripts/install-deps.sh` match `README.md` and exist under `scripts/`.
  - `--dry-run` matches `scripts/install-deps.ps1` and `scripts/install-deps.sh` (`--server NAME`, `--dry-run`).
  - `.\scripts\dev.ps1` and `./scripts/dev.sh` match `README.md` and exist under `scripts/`.
  - `-Port 9000` matches `scripts/dev.ps1` (`[int]$Port = 8765`). `--port 9000` matches `scripts/dev.sh` (`-p|--port`).
  - `http://127.0.0.1:8765` matches `README.md` and the default port `8765` in both start scripts.
  - `npm` install when `node_modules` is missing, GUI build, first-start model download of two to four minutes with loading overlay, and later start of about 35 seconds match `README.md` (`about 35 s`).
  - Copy `.env.example` to `.env`, shell wins over file, restart after edit match `README.md`. The caution against committing `.env` is present.
- Secrets: none found. The page names no key value. It points at the Settings page and at `.env.example` by name only. Stale hits: 0.
- Fixes: none.

## Capabilities — pass

- Links: page holds no markdown links. All `code-font` references resolve to existing files: `src/harness/main.py`, `src/harness/api.py`, `src/harness/mcp_client.py`, `src/harness/mcp_keys.py`, `src/harness/allowed_folders.py`, `src/harness/magick_guard.py`, `src/decider/router.py`, `src/decider/supervisor.py`, `src/decider/`, `src/decider/adapters/`, `src/memory/ledger.py`, `src/memory/recall.py`, `src/memory/ingest.py`, `src/memory/learn.py`, `src/memory/exclusion.py`, `src/memory/hindsight_store.py`, `src/memory/hindsight_process.py`, `src/generation/`, `src/rooms/room.py`, `src/rooms/turns.py`, `src/rooms/handoff.py`, `src/rooms/orchestrator.py`, `src/rooms/`, `src/skills/loader.py`, `src/skills/catalog.py`, `src/skills/`, `src/providers/registry.py`, `src/providers/openai_compatible.py`, `src/providers/openrouter.py`, `src/providers/probe.py`, `src/music/tools.py`, `src/music/check.py`, `src/music/digest.py`, `src/music/collection.py`, `src/music/routes.py`, `src/music/settings.py`, `src/music/atlas.py`, `src/music/`, plus `bot-prompts/`, `skills/`, `mcp.json`, `README.md`, `AGENTS.md`, `.env`, `.env.example`. No missing paths. No broken links.
- Commands: page gives no run steps by design. The one command reference, `python -m src.harness.main`, matches the harness entry point in `README.md`. `127.0.0.1:8765` matches `README.md`.
- Content spot checks: bot list (`personal-assistant`, `music-librarian`, `research-partner`, `darkroom-bot`, `picasso-bot`, `explainer`) matches `bot-prompts/` (6 files). MCP list (`exa`, `time`, `files`, `browser`, `toolsmonk`, `hound`, `pexels`, `magick`) matches `mcp.json` server names. Memory note (Hindsight 1.2.0, `127.0.0.1`, two to four minutes, about 35 seconds) matches `README.md`.
- Secrets: none found. The page names `${VAR}`, `TOOLSMONK_API_KEY`, and `PEXELS_API_KEY` by reference only. No key values, tokens, passwords, or private paths. Stale hits: 0.
- Fixes: none.

## Fixes

Zero fixes. No page needs changes before publication.

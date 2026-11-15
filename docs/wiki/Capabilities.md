# Capabilities

This page is for a new reader who wants to understand what open-zenbot does. After you read it, you can name each system and know where to look next. This page does not explain how to run it. For the start path, see `Home` and `Getting-Started`.

> Status note: open-zenbot is experimental. Expect rough edges and half-built features (`README.md`).

## Harness

The harness runs bots on your machine and keeps a readable record of each step (`README.md`, `AGENTS.md`, `src/harness/main.py`).

- It starts locally and serves an HTTP API on `127.0.0.1:8765` (`README.md`, `src/harness/main.py`).
- It loads every bot under `bot-prompts/` and every skill under `skills/` at start (`src/harness/main.py`).
- It runs the task loop with approvals, pause, and tools (`AGENTS.md`, `src/harness/`).
- The entry point is `python -m src.harness.main` (`AGENTS.md`, `src/harness/main.py`).
- The app calls the same HTTP API you can call directly with `curl` (`README.md`):
  - List bots, start a task, read a session, list approvals, read the ledger.
- The ledger is JSONL in a directory beside the vault (`README.md`, `src/memory/ledger.py`).

Sources: `README.md`, `AGENTS.md`, `src/harness/main.py`, `src/harness/api.py`, `src/memory/ledger.py`.

## Providers and models

You bring your own keys and pick models per job (`README.md`, `AGENTS.md`).

- Supported hosts include OpenRouter, NVIDIA NIM, vLLM, LM Studio, and Ollama (`README.md`).
- Any other OpenAI-compatible server works through the same adapter (`README.md`, `src/providers/openai_compatible.py`).
- Local models and hosted NIM models show as Free (`README.md`).
- You add providers on Settings > Model providers (`README.md`).
- You choose a model for chat, summary, and decision work (`README.md`).
- Each bot can use its own model (`README.md`).
- The adapter normalizes server differences such as reasoning fields and tool names (`README.md`).
- OpenRouter reports model skills. For plain OpenAI-compatible servers, the harness tests each model directly for chat, embeddings, tool calls, structured output, and image input, then offers only models of the right kind (`README.md`, `src/providers/probe.py`).

Sources: `README.md`, `AGENTS.md`, `src/providers/registry.py`, `src/providers/openai_compatible.py`, `src/providers/openrouter.py`, `src/providers/probe.py`.

## Decider and routing

Routing is a scoring step, not a writing step (`README.md`, `src/decider/`).

- Before each task, the router asks the decider one yes-or-no question per catalog entry (`README.md`, `src/decider/router.py`).
- The decider is a calibrated scoring model on the OpenRouter Decisions API (`README.md`, `src/decider/adapters/`).
- Skills compete in a band near the top score. The harness keeps skills within 0.1 of the best, at most three. When the best score is below 0.2, no skill loads (`README.md`, `src/decider/`).
- MCP servers cooperate. Each server above 0.5 loads, up to the same cap (`README.md`).
- Servers listed in a bot file always load (`README.md`).
- Routes are cached and re-scored when the task or scope changes (`README.md`).
- Scoring and writing stay separate. The decider scores fixed options. The generator writes summaries and folds (`README.md`, `src/decider/`, `src/generation/`).
- A supervisor checks progress every few steps and continues, nudges, or pauses the run (`README.md`, `src/decider/supervisor.py`).
- Without a decider key, the harness fails open with safe defaults and records the reason in the ledger (`README.md`).

Sources: `README.md`, `AGENTS.md`, `src/decider/router.py`, `src/decider/supervisor.py`, `src/decider/adapters/`, `src/generation/`.

## Memory

Memory is a knowledge graph that learns only what you confirm (`README.md`, `src/memory/`).

- The harness runs a Hindsight 1.2.0 server as a child process with an embedded Postgres store (`README.md`, `src/memory/hindsight_process.py`, `src/memory/hindsight_store.py`).
- It binds to 127.0.0.1 and stops with the harness (`README.md`).
- It learns from the first-run interview, imported files, fetched pages, and facts from work sessions (`README.md`, `src/memory/ingest.py`, `src/memory/learn.py`).
- A bot recalls only facts scored as relevant to the current task (`README.md`, `src/memory/recall.py`).
- Nothing moves into shared memory until you confirm it (`README.md`).
- The `learn_page` tool and the `learn-company` skill keep only what a page says about a known organization, person, or product. New people and loose findings wait in Approvals (`README.md`, `src/memory/learn.py`).
- Secrets and approvals pass through an exclusion filter (`AGENTS.md`, `src/memory/exclusion.py`).

Sources: `README.md`, `AGENTS.md`, `src/memory/hindsight_process.py`, `src/memory/hindsight_store.py`, `src/memory/recall.py`, `src/memory/ingest.py`, `src/memory/learn.py`, `src/memory/exclusion.py`.

## Rooms and bots

Bots do the work. Rooms let them work together (`README.md`, `src/rooms/`).

- Each bot identity is one markdown file under `bot-prompts/` (`AGENTS.md`, `src/harness/main.py`).
- The file stem is the bot id. Front matter holds name and role. The body holds instructions (`AGENTS.md`).
- The repo ships `personal-assistant` plus specialist bots such as `music-librarian`, `research-partner`, `darkroom-bot`, `picasso-bot`, and `explainer` (`bot-prompts/`, `README.md`).
- You can ask ZenBot for work. It answers directly or opens a room with the specialists that fit the job (`README.md`, `src/rooms/orchestrator.py`).
- Rooms track turns and handoff between bots (`AGENTS.md`, `src/rooms/room.py`, `src/rooms/turns.py`, `src/rooms/handoff.py`).
- Long sessions compact old turns into a summary. The task block stays pinned word for word (`README.md`, `src/generation/`).
- Scope changes wait for your confirmation before the task block changes (`README.md`).

Sources: `README.md`, `AGENTS.md`, `bot-prompts/`, `src/rooms/room.py`, `src/rooms/turns.py`, `src/rooms/handoff.py`, `src/rooms/orchestrator.py`, `src/generation/`.

## App and GUI

The desktop app is a client of the harness API (`README.md`, `src/gui/`, `src/electron/`).

- The GUI is React with Fluent 2. It builds into `dist/` (`README.md`, `AGENTS.md`).
- Electron serves `dist/` from the `app://openzenbot` origin (`README.md`, `src/electron/`).
- Bot replies show preview cards for images, video, PDFs, diagrams, and files (`README.md`).
- Each card offers Save through the system dialog. Images and video also offer Open and Copy link (`README.md`).
- Remote media loads through the harness with a trusted-host list (`README.md`).
- The first launch offers an interview and guided file import (`README.md`).
- Settings pages cover Model providers, Harness settings, MCP servers, Allowed Folders, Harness Tools, Tracing, and Music (`README.md`, `AGENTS.md`).

Sources: `README.md`, `AGENTS.md`, `src/gui/`, `src/electron/`.

## MCP servers

MCP servers add tools. Each tool registers as `<server>.<tool>` (`AGENTS.md`, `mcp.json`, `src/harness/mcp_client.py`).

Configured servers in `mcp.json`:

- `exa`: search the live web and fetch page contents (`mcp.json`).
- `time`: current date and time in any timezone, plus conversions (`mcp.json`).
- `files`: read, write, search, and list files under the vault and allowed folders (`mcp.json`).
- `browser`: drive a real Chrome window for pages that need interaction (`mcp.json`).
- `toolsmonk`: ready-made utilities by slug, such as calculators, text helpers, and light PDF or image jobs (`mcp.json`).
- `hound`: fetch pages as markdown, crawl sites, read PDFs, and search public engines without a key (`mcp.json`).
- `pexels`: free stock photos and videos by subject, color, orientation, and size (`mcp.json`).
- `magick`: local image edits with ImageMagick through a fixed-rule guard (`mcp.json`, `src/harness/magick_guard.py`).

Rules that apply to all servers:

- The harness connects them at start. `mcp.json` uses Claude Desktop shape (`AGENTS.md`, `src/harness/mcp_client.py`).
- Each server carries a `use` line. The decider scores that line to route tasks (`AGENTS.md`, `mcp.json`).
- Keys use `${VAR}` refs only. The file never holds key values (`AGENTS.md`, `mcp.json`, `.env.example`).
- You supply keys on the MCP servers page or in `.env`. A saved key wins for that server only (`AGENTS.md`).
- A missing key leaves the server connected but its tools answer unauthorized (`AGENTS.md`).
- A server that needs a host binary declares it in `requires` with install help (`AGENTS.md`, `mcp.json`).
- A server with an unmet requirement reports `needs_install` with a fix and a Recheck button (`AGENTS.md`).
- The `files` server reaches the vault and your allowed folders, each Full Access or Read-Only. Keys, settings, ledger, and music folders stay out of reach (`AGENTS.md`, `src/harness/allowed_folders.py`).
- The `magick` server runs through `src/harness/magick_guard.py` with path, option, and format checks (`AGENTS.md`).

Sources: `mcp.json`, `AGENTS.md`, `.env.example`, `src/harness/mcp_client.py`, `src/harness/mcp_keys.py`, `src/harness/allowed_folders.py`, `src/harness/magick_guard.py`.

## Skills

Skills are task packages the router loads only when they help (`README.md`, `src/skills/`).

- The base prompt stays small. Each skill is a one-line catalog entry until picked (`README.md`, `src/skills/catalog.py`).
- The full `SKILL.md` body loads only when the router selects it (`README.md`, `src/skills/loader.py`).
- A bot with many skills carries only two or three into a given task (`README.md`).
- You confirm new skill attachments before they apply (`README.md`).
- Shipped skills live under `skills/`, including vendored Anthropic skills with their own licenses (`AGENTS.md`, `README.md`).
- The GUI and `bot-prompts/` files reference skills by name (`src/harness/main.py`, `bot-prompts/`).

Sources: `README.md`, `AGENTS.md`, `src/skills/catalog.py`, `src/skills/loader.py`, `skills/`, `bot-prompts/`.

## Music discovery

Music discovery is off until you turn it on (`README.md`, `src/music/`).

- You switch it on in Music > Settings and enter a Discogs username. A scan reads the public collection and shows the Taste map (`README.md`, `src/music/collection.py`, `src/music/atlas.py`).
- The weekly digest needs a Discogs token. It is stored owner-only in settings, never in the repo (`README.md`, `src/music/settings.py`).
- The `music-librarian` bot proposes albums with the `music_*` tools (`AGENTS.md`, `src/music/tools.py`).
- The gate in `src/music/check.py` is the single authority on what reaches a digest. A card must exist on Discogs, stay unowned, stay new, stay off the wantlist, and stay obscure enough (`AGENTS.md`, `README.md`, `src/music/check.py`).
- Each card links to YouTube Music and can join the Discogs wantlist (`README.md`).
- The digest is an ordinary routine. It runs Fridays at 09:00 for 4 albums by default. You can change the day, time, and count, switch it off, edit it, or run it now (`README.md`, `src/music/digest.py`).
- Music data lives beside the vault, out of bots' reach (`README.md`, `AGENTS.md`, `src/music/settings.py`).

Sources: `README.md`, `AGENTS.md`, `src/music/tools.py`, `src/music/check.py`, `src/music/digest.py`, `src/music/collection.py`, `src/music/routes.py`, `src/music/settings.py`.

## Limits

- This page is a concept overview. It is not a full API reference.
- For behavior details, read the source file named after each claim.
- For setup and daily use, see `Home` and `Getting-Started`.

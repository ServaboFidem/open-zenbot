## What lives here

You are a contributor or an agent working in this repo. After this page you can
name each top-level system and run the harness and the tests.

| Path | What |
| --- | --- |
| `src/harness/` | Bot, task loop, approvals, pause, tools, HTTP API (`api.py`), entry point (`main.py`), MCP client (`mcp_client.py`), MCP keys (`mcp_keys.py`) and host dependencies (`host_deps.py`, `tool_paths.py`); conversations, routines (`scheduler.py`), the onboarding interview, model roles; the guards (`allowed_folders.py`, `write_guard.py`, `magick_guard.py`); the design studio (`design_studio.py`: numbered copies, comment block, preview bridge `design_bridge.js`; `design_systems.py`: loading and checking design systems; `design_html.py`: the HTML scan; `design_images.py`: pictures as files; `design_parts.py`: slots, worksheet, apply; `studio_helpers.py`: a helper's turn, apply and restore; `studio_help.py`: routing a revision; `studio_edits.py`: the user's own changes, restore, catch-up and the ZIP; `design_tweaks.py`: the tweak knobs a design declares); coding agents: `agent_clis.py` (find, sign-in, switches), `delegation.py` (the `delegate` tool, runner, folder rules), `dream_routes.py` (the `/dream` routes) |
| `src/providers/` | Provider interface, registry, provider store (`providers.json`) and secrets, OpenRouter and OpenAI-compatible adapters, model listing and probing, the image provider, the speech provider (`speech.py`), Langfuse tracing |
| `src/decider/` | Decider interface, scorer registry (each scorer names the provider kinds its model may be on, `registry.provider_kinds`), skill and MCP routing (band and floor), skill-to-bot fit, supervisor, `answers.py` (typed answers from a probability per option), `adapters/`: `decision_model.py` (`openrouter-decision-1`, OpenRouter's Decisions API), `letter_model.py` (`letter-logprobs`, a local decision model asked through chat) and `systemone.py` (`systemone`, a local server's own decision route); see `### Local decision models` |
| `src/context/` | Compaction, the protected (pinned) task, transcript rendering |
| `src/generation/` | Generator boundary: compaction summaries and folds, vault summaries; the `image.generate` tool (`images.py`); reading replies aloud (`speech.py`: the Narrator, then the speech model) |
| `src/memory/` | Exclusion filter (secrets and approvals), ledger (`ledger.py`), recall, ingest, directory import, learning from a crawl, profile memory and store routing, Hindsight store and processes, dreaming (`dream.py`: learning facts from the user's own messages while idle) |
| `src/rooms/` | Rooms, turns, handoff, orchestrator |
| `src/music/` | Music discovery: Discogs scan, taste profile, the Soundlore atlas, MusicBrainz and YouTube Music lookups, candidates, the album gate, digests, `music_*` tools, `/music` routes |
| `src/events/` | The built-in calendar: settings and Google keys (`settings.py`), the SQLite store (`store.py`), the Google client (`google.py`), conversion to and from Google's shape (`convert.py`), sign-in (`connect.py`), pull and push (`sync.py`), the service that runs them (`service.py`) and the `/calendar` routes (`routes.py`); discovery of local events: settings and the Eventbrite address check (`discover_settings.py`), the Eventbrite reader (`eventbrite.py`), the FindLocal reader (`findlocal.py`), the pool, digests, cards and events added from the pool (`discover_store.py`), the service that refreshes, checks, saves and fetches pool pictures (`discovery.py`), the scout's tools (`discover_tools.py`) and the `/discover` routes (`discover_routes.py`) |
| `src/skills/` | `SKILL.md` loader and catalog; recorder |
| `src/mail/` | AgentMail inbox settings, client, service, `/agentmail` routes and scheduled digest delivery (`morning_delivery.py`); see `### AgentMail` |
| `src/sonos/` | Sonos control over SoCo: settings (`settings.py`), the device boundary (`devices.py`), the service that finds speakers (`service.py`), the `sonos_*` tools and the volume ceiling (`tools.py`) and the `/sonos` routes (`routes.py`); see `### Sonos` |
| `src/speakers/` | Wi-Fi speakers (finding only, nothing played): settings and the speaker record (`settings.py`), the three finders behind one `Finder` protocol (`finders.py`, the only importer of pyatv, pychromecast, async_upnp_client and zeroconf), the service with a look per speaker type and the one default (`service.py`) and the `/speakers` routes (`routes.py`); see `### Wi-Fi speakers` |
| `src/kokoro/` | Speech made on this computer by Kokoro on onnxruntime, for Read aloud: the pinned model files, their download and removal (`model.py`: `KokoroModel`), and the engine with the 28 English voices (`engine.py`: `KokoroEngine`, the only importer of `kokoro_onnx` and `onnxruntime`); see `### Reading replies aloud`. The splitting into pieces and the reading itself are in `src/generation/speech.py`, the routes in `src/harness/speech_routes.py` |
| `src/vault_settings.py` | `settings_dir` (the settings folder beside the vault) and `migrate` |
| `src/gui/` + `src/electron/` | OpenZenBot workspace GUI (built into `dist/`, assets in `src/assets/`; the design pane is `DesignPane.jsx` + `design.js`, with the editor `DesignEditor.jsx`, `TweaksPanel.jsx`, `DesignPendingBar.jsx`, `DesignCompare.jsx` and `ExportMenu.jsx`, and its unsaved changes decided in `design-edits.js`, the question forms `QuestionForm.jsx` + `question-form.js`, the design system picker `DesignSystemPicker.jsx`, the Design page (in `app.jsx`: a card per design from `GET /designs`, and New design, which opens the studio bot in a fresh conversation), the Settings home screen `SettingsHome.jsx` + `settings-index.js` (icon tiles and a search over a hand-kept index: a new settings section adds an entry to `SECTIONS` and puts `id={anchorId(panel, title)}` on its element; provider and tool cards carry `anchorId(panel, id)` and reach the search through `liveSections`; checked by `tests/gui/settings-index.test.mjs`), the coding agents page `AgentSettings.jsx` + `agents.js`, the approval dialog `ApprovalDialog.jsx` + `approval-dialog.js` (any pending approval opens it over whatever page is open, oldest first; `app.jsx` reads `GET /approvals` every 5 s while online and on a live `approval_request` event; closing it or Decide later decides nothing and puts that request off until a reload; it does not open on the Approvals page; never seen on screen with a real approval), program paths `program-paths.js`, the Events page `events/` (`EventsArea.jsx`, `CalendarView.jsx`, `CalendarGrid.jsx`, `EventDialog.jsx`, the date rules `dates.js`, the status line `page-state.js`) and the Calendar settings panel `CalendarSettings.jsx` + `calendar-settings.js`, both through `calendar-api.js`) and Electron shell (`frame-nav.cjs` limits where a framed page may go; `export-design.cjs` renders a design as PDF or PNG in an offscreen window (`makeExportWindow` and `exportSession` in `main.cjs`); `open-vault.cjs` opens the vault's root in the OS file manager for the home page's Browse your vault button, shown in the desktop app only: the page sends no path, the folder is `ZEN_VAULT` as the main process reads it, checked by `tests/gui/open-vault.test.mjs`; the installed app's side is in `## Installed app`: `main.cjs`, `managed*.cjs`, `setup.cjs`, `runtime-paths.cjs`, `harness-process.cjs`, `update-check.cjs`, `download.cjs`, `pins.json`, and the setup page `setup-view.js`) (the Your Recommendations tab (once Discover) `events/DiscoverView.jsx`, `EventCard.jsx`, `SignupDialog.jsx` with its decisions in `events/discover.js`, and the All Events tab `events/AllEventsView.jsx` with its event panel `EventPanel.jsx` and its decisions in `events/all-events.js`, both through `discover-api.js`; the Event discovery section of Settings > Calendar `DiscoverySettings.jsx` + `discovery-settings.js`) (AgentMail: the section at the top of Settings > Connected apps `MailSettings.jsx`, the AgentMail page `MailPage.jsx` (shown in the navigation below Conversations once a key is saved and an inbox chosen), their decisions in `mail.js` and styles in `mail.css`, through `mail-api.js`) (Sonos: the section of Settings > Connected apps `SonosSettings.jsx`, its decisions in `sonos.js`, and the Music > Sonos page `SonosPage.jsx` with its decisions in `sonos-panel.js` and styles in `sonos.css`, both through `sonos-api.js`) (Wi-Fi speakers: the Settings > Wi-Fi speakers panel `WifiSpeakers.jsx`, its decisions in `wifi-speakers.js`, through `speakers-api.js`) (Read aloud: the card on Settings > Harness Tools, its voice on this computer in `ReadAloudLocal.jsx`, its decisions in `speech-settings.js`, and the player and reader in `speech.js`) (the Dreaming section of Settings > Harness settings `DreamSection.jsx` + `dream.js`; the Electron main process reports idle time through `idle-report.cjs`) |
| `bot-prompts/` | One markdown file per bot identity; the file stem is the bot id (`designer.md` is the studio bot: `studio: true`); a prompt can also set `closed: true` (only the tool groups in its `mcps`, no built-in tool, never in a room: `event-scout.md`), or `narrates: true` (the Narrator, `narrator.md`: asked directly to rewrite replies for speech, never given a task) |
| `design-systems/` | Shipped design systems, one folder each (`DESIGN.md`, optional `tokens.css`); `ZEN_DESIGN_SYSTEMS` moves it. The user's own live in `<vault>/design-systems/` |
| `scripts/` | Start scripts (`dev.sh`, `dev.ps1`), the GUI build, the host-dependency installers `install-deps.sh` / `install-deps.ps1`, `ubuntu-apparmor-profile.sh` (the AppArmor profile Electron needs on Ubuntu 23.10+), and `fetch-uv.mjs` (downloads the pinned uv into `build/bin/` for the installers). Packaging itself is `electron-builder.yml`, `build/` (icon, the .deb's AppArmor profile `apparmor-profile`, the NSIS include `installer.nsh`) and `packaging/harness/` (the CPU-only `pyproject.toml` and `uv.lock` the installed app uses) |
| `skills/` | Shipped skills, including vendored Anthropic skills under `skills/anthropic/`, `skills/design-studio/` and `skills/delegation/` (when a bot should use `delegate`); `skills/events-digest/` (the scout's weekly run); `skills/sonos-morning/` (ZenBot's morning music routine) |
| `mcp.json` | MCP servers to connect at start; each tool registers as `<server>.<tool>` |
| `tests/` | Offline suite (`uv run pytest`, music tests under `tests/music/`, design studio in `tests/test_design_studio.py` and `tests/test_design_systems.py`; studio helpers in `tests/test_design_html.py`, `tests/test_design_images.py`, `tests/test_design_parts.py`, `tests/test_studio_helpers.py` and `tests/test_studio_help.py`; direct changes in `tests/test_studio_edits.py`, `tests/test_studio_edit_routes.py` and `tests/test_design_tweaks.py`); GUI node tests under `tests/gui/` (`npm test`; the studio's are `design.test.mjs`, `design-edits.test.mjs`, `design-bridge.test.mjs`, `export-design.test.mjs`, `save-media.test.mjs`, `question-form.test.mjs`, `frame-nav.test.mjs`, `api.test.mjs`, `actions.test.mjs`); MCP echo fixture at `tests/fixtures/mcp_echo_server.py`; fake agent CLI at `tests/fixtures/fake_agent_cli.py`; agents in `tests/test_agent_clis.py`, `tests/test_agent_probe_guard.py`, `tests/test_delegation.py`, GUI `tests/gui/agents.test.mjs`; the calendar in `tests/events/` (its Google stand-in `FakeGoogle` is in `tests/events/conftest.py`), GUI `tests/gui/calendar-api.test.mjs`, `events-dates.test.mjs`, `events-page-state.test.mjs` and `calendar-settings.test.mjs`; event discovery in `tests/events/test_eventbrite.py`, `test_discover_settings.py`, `test_discover_store.py`, `test_discovery.py`, `test_discover_tools.py`, `test_findlocal.py` and `test_discover_routes.py` (the page fixture is `tests/events/fixtures/eventbrite_page.html`, the Eventbrite stand-in `fake_site` and the FindLocal stand-in `FakeFindLocal` are in `tests/events/conftest.py`), closed bots in `tests/test_bot_prompts.py`, `test_task_loop.py`, `test_rooms.py` and `test_orchestrator.py`, GUI `tests/gui/events-discover.test.mjs`, `all-events.test.mjs`, `discover-api.test.mjs` and `discovery-settings.test.mjs`; dreaming in `tests/test_dream.py` and `tests/test_dream_routes.py`, GUI `tests/gui/dream.test.mjs` and `idle-report.test.mjs`; the approval dialog's rules in `tests/gui/approval-dialog.test.mjs`; AgentMail in `tests/mail/` (its stand-in `FakeAgentMail` is in `tests/mail/conftest.py`), GUI `tests/gui/mail.test.mjs` and `mail-api.test.mjs`; Sonos in `tests/sonos/` (its stand-in `FakeSpeakers` is in `tests/sonos/conftest.py`), GUI `tests/gui/sonos.test.mjs`, `sonos-panel.test.mjs` and `sonos-api.test.mjs`; Wi-Fi speakers in `tests/speakers/` (finders get stand-ins and an `httpx.MockTransport`, the service a `FakeFinder`), GUI `tests/gui/wifi-speakers.test.mjs` and `speakers-api.test.mjs`; local speech in `tests/kokoro/` (`test_model.py`: the download through an `httpx.MockTransport`; `test_engine.py`: a fake library object, never `kokoro_onnx`) and `tests/test_speech.py`, GUI `tests/gui/speech.test.mjs` and `speech-settings.test.mjs` |
| `docs/` | Screenshots, OpenGraph images, and the GitHub wiki as a git submodule (`docs/open-zenbot.wiki`) |
| `.github/` | Workflows: `stale.yml`, and `release.yml` (a `v*` tag builds the .deb and the Windows installer and opens a draft release; a manual run only builds them; see `## Installed app`) |
| `.env.example` | Every environment variable with its default; copy to `.env` (git-ignored) |

Run the harness with `uv sync --group dev`, then `uv run python -m src.harness.main`
(`http://127.0.0.1:8765`). Keep secrets in `.env` or the shell environment, never
in a committed file. Run `uv run pytest` for the offline suite and `npm test`
for the GUI tests. See `README.md` for the full start sequence.

## Adding an MCP server that needs a key

MCP servers live in `mcp.json` at the repo root (Claude Desktop shape). The
harness connects them at start (`src/harness/mcp_client.py`); the GUI's MCP
servers page (Settings > MCP servers) lists them and asks the user for any keys. **`mcp.json` is
committed: never write a real key, token or password into it**, not even
temporarily. A key that lands there is public in git history and must be
rotated.

1. Pick one variable name per credential: `SERVICE_API_KEY` (or `_TOKEN`,
   `_SECRET`). The suffix matters: only the suffixes listed below are scrubbed
   from stdio children, so a key named `SERVICE_KEY` would reach every stdio
   server from `.env`. Not a name the harness supplies itself (`ZEN_VAULT_ROOT`,
   `ZEN_ALLOWED_FOLDERS`, `ZEN_READ_ONLY_FOLDER_ARGS`): those win over saved keys
   and the environment, and are never asked for.
2. Reference it as `${VAR}` where the server reads it. It may sit inside a
   longer string:
   - stdio server, key via environment: `"env": {"PEXELS_API_KEY": "${PEXELS_API_KEY}"}`.
     Always map it in `env`, even when the name is the same: stdio children
     get a scrubbed environment that drops every `*_API_KEY`, `*_TOKEN`,
     `*_SECRET`, `*_PASSWORD` suffix and every `VAULT_*` prefix plus
     `ZEN_GUI_KEY`, so a key in `.env` alone never reaches the server.
   - HTTP server, key via header: `"headers": {"Authorization": "Bearer ${TOOLSMONK_API_KEY}"}`
     or `{"x-api-key": "${EXA_API_KEY}"}`.
   - HTTP server, key in the URL: `"url": "https://host/mcp?key=${SERVICE_API_KEY}"`.
     Prefer a header: with the variable unset the whole URL drops and the
     server fails to connect, rather than connecting keyless. HTTP servers get
     no `env`, so their keys go in `headers` or `url`.
   - Not in `args`. `${VAR}` in args expands, but args are treated as paths,
     so the MCP servers page will not ask for it. If a server only takes its
     key as a CLI flag, say so to the user rather than committing the key.
3. Give the entry a `use` line: the one sentence the decider scores to route
   tasks to it. Add `timeout` (seconds) for slow starters. Example:

   ```json
   "pexels": {
     "command": "uvx",
     "args": ["--with", "mcp<2", "pexels-mcp-server"],
     "env": {"PEXELS_API_KEY": "${PEXELS_API_KEY}"},
     "use": "Free stock photos and videos from Pexels: search, curated, by id."
   }
   ```

4. Document the variable, without a value, in `.env.example` under
   "MCP servers (read by mcp.json)": what it is, where to get one, and what
   happens without it.
5. Restart the harness. `mcp.json` is read at start and again on every
   reconnect of one server (a key save, Recheck, a program path save, an
   Allowed Folders change), but `.env` is read only at start. One broken
   entry (no `command` or `url`, a malformed `requires`) makes the whole file
   fail to load: at start every MCP server is dropped and the error goes to
   the ledger; a later reconnect answers 422.

How the key gets supplied. This is the user's job; don't ask them to paste
keys into chat:

- **MCP servers page** (preferred): every `${VAR}` in a server's `env`,
  `headers` or `url` becomes a password field on its card. A save goes to
  `vault-settings/mcp_keys.json` (owner-only) and reconnects that server at
  once. A saved key wins over `.env` for that server only. API:
  `GET /mcps` (each key's name, its `source` (`saved`, `.env` meaning the
  process environment, or empty) and its last four characters when it has
  eight or more, never the value), `PUT /mcps/{name}/keys` with
  `{"keys": {VAR: value}}` (a blank value keeps the saved key), and
  `DELETE /mcps/{name}/keys/{VAR}`.
- **`.env`** (git-ignored): `VAR=value`, read at harness start. A variable
  already set in the shell wins over the file.

An unset variable drops the header, env entry or arg that names it (in `url`
it drops the whole URL; see above). The
server still connects keyless, so a missing key shows as tools answering
"unauthorized" or "key empty", not as a failed connection. Check
`GET /mcps`: a key with an empty `source` is unset. Tests for this path:
`tests/test_mcp_keys.py`, using the stdio echo fixture in `tests/fixtures/`.

## Folders the files server may reach

The `files` server (`@modelcontextprotocol/server-filesystem@2.3.0`, pinned: `--read-only-dir` first
shipped in 2.3.0, which needs Node 24+, declared in its `requires`) gets the vault, `ZEN_FS_ROOT` when
set, and the folders the user adds on Settings > Allowed Folders (`src/harness/allowed_folders.py`,
stored in `vault-settings/allowed_folders.json`), each **Full Access** or **Read-Only**. The harness hands
them to `mcp.json` as two list variables, each a whole arg on its own (inside a longer arg a list counts
as unset): `${ZEN_ALLOWED_FOLDERS}` (Full Access, one arg per folder) and `${ZEN_READ_ONLY_FOLDER_ARGS}`
(`--read-only-dir <path>` pairs). Read-only flags must come before the `--` that ends the server's
options; the server refuses every write inside a read-only folder, whatever else is allowed. Any server
whose args name either variable is reconnected when the folders change. Only folders that still exist
are handed over (the server will not start on a missing one). Nesting rules are `AllowedFolders.place`:
a Read-Only folder may sit inside a Full Access one (or the vault); a Full Access folder inside a
Read-Only one, a Read-Only folder holding a Full Access one, or a folder inside one of the same access
(Full Access inside the vault or `ZEN_FS_ROOT` too) is refused with the reason. Going the other way, a
new parent with the same access as folders already inside it replaces them, since it covers them. Don't
write user folders into `mcp.json`. Broad folders (a drive root, the home folder or above it, system
folders) are refused by `allowed_folders.check`, and so are the harness's settings, ledger, music, calendar and
models folders, anything inside them, and anything holding them (`private_folders`); keep both rules when adding
other ways in (`allowed_folders.reachable` gives the same readable, writable and private lists).

### What bots can and cannot reach

The `files` server is rooted at the vault, so a bot can read **and write** anything inside it. Keep
nothing there a bot must not see or change. The harness's own folders sit beside the vault instead. The
vault is `ZEN_VAULT` (default `~/.zen-agents/vault`), and the others take its name: `ZEN_VAULT=~/x/notes`
gives `notes-ledger/`, `notes-music/`, `notes-calendar/`, `notes-models/` and `notes-settings/`.

| Folder (default under `~/.zen-agents/`) | Holds | Owned by |
| --- | --- | --- |
| `vault/` | Work docs (`docs/`), `delegated/` (job folders for `delegate`), uploads, browser output (`browser/`), anything bots save; `designs/` (studio designs; each folder also holds `images/` and `.versions/`, whose `before-<n>.<name>` files are the working file as it was before a helper's change) and `design-systems/` (the user's own); `workspace/` (the bots' working folder, unless `ZEN_SHELL_CWD` moves it); `memory/`, `sync.json`, learn logs (`learn_log.json`, `learn_holds.json`) | the vault; bots reach it |
| `vault-ledger/` | Ledger (`ledger.jsonl`), rooms, conversations, `dreams.json` (dreaming progress) | `src/memory/ledger.py` `ledger_path_for` |
| `vault-models/` | `kokoro/`: the Kokoro model (`kokoro-v1.0.onnx`) and voices (`voices-v1.0.bin`), fetched by `POST /speech/local/download`; a `.part` file only while a download runs | `src/vault_settings.py` `models_dir`; a private folder (`allowed_folders.private_folders`), so no bot tool reaches it |
| `vault-music/` | Music database (`music.db`), the Soundlore atlas, cover images | `src/music/settings.py` `music_dir`; bots never reach it (`allowed_folders.private_folders`) |
| `vault-calendar/` | Calendar database (`calendar.db`: events, calendars, sync state) | `src/events/settings.py` `calendar_dir`; a private folder (`allowed_folders.private_folders`), so no bot tool reaches the file. The `/calendar` routes are another matter: see "Bots and the calendar" under `### The calendar and Google`; also holds `discover.db` (the discovered events, digests, cards and the events added from the pool) and `discover-images/` (card pictures, and pool pictures in `discover-images/pool/`), private like `calendar.db` |
| `vault-settings/` | `providers.json` (providers and their keys), `mcp_keys.json`, `tracing.json`, `harness.json` (model roles), `bot-models.json`, `tools.json` (program paths, image generation and Read aloud), `agents.json` (coding agent switches), `allowed_folders.json`, `routines.json`, `music.json`, `music_keys.json`, `music_state.json`, `calendar.json`, `calendar_keys.json`, `events.json` (event discovery: place, ticked categories, interests, on or off, the FindLocal city and its time zone), `events_keys.json` (the FindLocal key, owner-only), `agentmail.json` (ZenBot's chosen AgentMail inbox: its id and address; no secret), `agentmail_keys.json` (the AgentMail API key, owner-only), `sonos.json` (Sonos: on or off, the volume ceiling, the speaker addresses last found; no secret), `wifi_speakers.json` (Wi-Fi speakers: the three switches, the default speaker's key, the speakers each type last found with their addresses; no secret), `dream.json` (dreaming: on or off and the idle minutes), `magick/policy.xml` | `src/vault_settings.py` `settings_dir` |

A new setting goes in `settings_dir(vault_root) / "<name>.json"`, never under the vault. Secrets and
`allowed_folders.json` (which decides what `files` may reach) must stay out of the vault: a bot that could
write that file could grant itself any folder. The harness moves an older `<vault>/settings/` to
`vault-settings/` at start (`vault_settings.migrate`; a copy already in `vault-settings/` wins, and a
different old copy is left in place and reported). The desktop app reads `tracing.json` from
`vault-settings/` first, then the old place.

### Music tools and the gate

The `music-librarian` bot finds albums with the `music_*` tools (`src/music/tools.py`). The gate
(`src/music/check.py`) is the single authority on what reaches a digest: the bot proposes, the harness
verifies, and a card that fails is refused. A suggested album must exist on Discogs, not be owned, not have
been suggested before, not be on the wantlist, and be obscure enough (by its Discogs `have` count; the
obscurity setting picks the cutoff). `music_save_digest` runs the gate again on every card and also
refuses duplicates in the batch and cards without exactly two standout tracks from the verified
tracklist; a digest saves whole or not at all (`src/music/digest.py`). The `music_*` tools exist only
when music is on and the Discogs token is set. Don't add a path that saves a card without the gate. The Discogs token lives in `vault-settings/music_keys.json`; `vault-music/` is as private
as the settings and ledger folders, so keep it out of anything a bot can read or write.

### Dreaming

While the computer is idle, the harness reads the user's own messages from one-to-one conversations and writes a few
short facts into shared memory, where every bot's recall finds them (`src/memory/dream.py` `Dreamer`; the routes are
`src/harness/dream_routes.py`; the loop runs in the `lifespan` in `api.py` beside the extract queue's). It is off by
default. Design: `docs/superpowers/specs/2026-11-19-dreaming-design.md` (git-ignored, on the computer the work was
done on).

- **Where things live.** The switch and the idle minutes in `vault-settings/dream.json` (`DreamSettings`: `enabled`
  default false, `idle_minutes` default 5, a whole number from 1 to 120; no secret). The model is the `dream` role
  ("Dreaming model") in `vault-settings/harness.json`, chosen like the other roles. It is never filled from another
  role: with none chosen dreaming does not run, and switching on without one is refused. Progress is in
  `<vault>-ledger/dreams.json` (`DreamMarks`, tmp-then-replace; per conversation id the exchanges read, failed attempts
  and the time of the next try, plus the count learned and the time of the last dream), in the ledger folder and never
  the vault, where a bot could rewrite it. A damaged file is moved to `dreams.json.bad` and dreaming starts empty.
- **What is read** (`unread_marked`). One-to-one conversations (`kind == "bot"`; rooms are not read), newest
  `updated_at` first, whose `updated_at` is at least 30 minutes old (`QUIET_S`) and that have exchanges past their mark.
  The 30 minutes are checked again as each conversation's turn comes (`Dreamer._is_quiet`), so one touched since the
  pass listed it waits for a later pass.
  From each exchange only `Exchange.user` goes anywhere: never `addendum`, `comments`, `reply`, attachments, the
  conversation's `summary` or `note`. An exchange still `pending` stops the read there. Skipped, but counted as read:
  outcome `routed` (a message sent through ZenBot is recorded again where it was handled), `set_aside`, a question
  form's answers (`answers_to` set, or an older record whose text starts `Form answers (`), a message equal to an
  earlier one in the conversation (a Redo repeats it), an empty one, and one the exclusion filter refuses for shared
  memory (a secret never reaches the model). One message is cut to 4,000 characters; messages are batched to at most
  12,000 characters a model call (`batches`).
- **When it runs** (`Dreamer._may_go`). All three must hold, and are checked before each conversation, each model call
  and each write, and every 2 s while a model call is in flight:
  1. Ready (`ready`): `enabled`, a `dream` role that builds (`Harness._dream_generator`), and the owner's name known
     (a shared write needs it; since `ready()` is checked before every write, no write is made without it).
  2. The machine is idle: Electron's main process posts `powerMonitor.getSystemIdleTime()` to `POST /dream/idle` every
     60 s, and also reads it every 5 s and posts at once when it dropped below the previous reading (the user touched
     the computer, so a pass under way stops within seconds; `src/electron/idle-report.cjs`, `every` and `watchEvery`).
     It is started from `main.cjs` in developer mode, and in the installed app once the workspace is shown, not during
     setup. The machine counts as idle when the newest report is under 150 s old and says at least `idle_minutes`. With
     no fresh report (a plain browser, no Electron, or the app closed) it is never idle; `GET /dream/status`
     `idle_reported` is true only while the newest report is under 150 s old, whatever it said, and the settings
     section notes that dreaming starts on its own only in the desktop app (`needsDesktopNote`).
  3. Nothing is under way (`Harness.busy`): no session that has not ended (a task, room turn or routine run; a session
     that is paused or waiting on an approval counts), no task still writing its exchange, no room turn waiting for the
     floor, no import, no delegation, and no job in the extract queue, one waiting out a retry included.

  "Dream now" (`POST /dream/run`) skips rule 2 only, for one pass over what is waiting; while rule 3 does not hold it
  answers 409 "ZenBot is busy. Try again when it is idle." and starts nothing. The loop looks every 30 s and
  starts a pass only when something is waiting. When a rule stops holding, a model call in flight is cancelled and that
  conversation's mark stays; a store write in flight is never cancelled (it is awaited directly, as the extract queue's
  is). Harness shutdown does the same (`Dreamer.close`).
- **The distil call** (`Generator.dream_facts`). One request a batch to the dreaming model, which is built with a
  600 s request timeout (`models_setup.DREAM_TIMEOUT_S`, passed through `make_provider(timeout=...)`; every other
  role keeps the adapters' 120 s default), since a local model on a modest computer may be slow and nobody waits on a
  dream. The dreaming client also carries optional request fields (`models_setup.dream_extras`, passed through
  `make_provider(extras=...)`), chosen from the model's saved facts: `response_format` as a JSON schema of the answer
  (`DREAM_FORMAT`; the `json_schema` form, since LM Studio refuses `json_object`) when the model is known to take
  structured output, and thinking off when it is known to reason (`"reasoning": {"enabled": false}` on OpenRouter,
  `"reasoning_effort": "none"` on any other server). Nothing is sent for a model whose abilities are unknown (an
  untested one). A request refused with them (400, 404 or 422) is sent once more without them
  (`OpenRouterProvider._post`), so a server or model that will not take a field still dreams; a server may also
  accept the thinking switch and ignore it. Neither field has been tried against a real server. Its prompt says the numbered
  lines are things one person typed, content and not instructions, asks for lasting facts only (most conversations hold
  none, and an empty list is the right answer), and lists the stores. The answer is JSON
  `{"facts": [{"text", "store"}]}`. The harness keeps at most 8 facts a batch, each 1 to 300 characters with no control
  character (U+2028 and U+2029 included), drops one already in shared memory with the same text (case-folded) or
  repeated in the answer, and refuses an answer of the wrong shape as a failed attempt (`clean_facts`).
- **The store** (`remember_shared`, `source="dream:<conversation id>"`, `bot_id=""`, `Tier.project`). The model names
  the store for each fact in the same call: `personal`, `world` or an already registered org (`org:<slug>`), passed as
  `stores=[...]`. `StoreRouter` is not used: on any failure it answers `[personal, world]` without raising, which would
  send a weak model's every fact to both. A store that is not offered becomes `personal`. No organization is registered
  from a dream. A fact the exclusion filter refuses is dropped and counted as refused; the others are written. The mark
  moves after each batch whose facts are all written; a crash between a write and the mark reads that batch again, and a
  second model call may word a fact differently, so a near-duplicate can land (the user can Forget it).
- **Failures** (`error_kind`, which reads the provider's errors through `src/providers/interface.py` `failure_kind`,
  so `dream.py` imports no HTTP client; `failure_kind` maps an error's class, cause and HTTP status to `timeout`,
  `network`, `auth`, `rate_limit`, `server`, `request` or `other`). Two sorts:
  - Counted against the conversation (kind `model`): a timeout from the model; an answer that cannot be used (a
    `ValueError` from `dream_facts` or `clean_facts`: no JSON, the wrong shape; or an API answer with no `choices`); a
    client error about the request itself (`failure_kind` `request`: 400, 404, 413, 422 and any other 4xx but 401, 402,
    403 and 429), so one conversation the model can never take (a context too small for the batch) is skipped rather
    than holding every older one back; and any provider failure whose status or cause cannot be told, since this rule
    is bounded and the systemic one is not. The conversation then waits 10 minutes
    after the first failure and 1 hour after the second, and after 3 failed attempts is skipped until it grows past the
    exchange count it last saw (`RETRY_WAITS`, `MAX_ATTEMPTS`). The pass goes on to the next conversation.
  - Systemic (`SYSTEMIC`), counted against nothing: the pass ends and automatic dreaming pauses 10 minutes
    (`UNREACHABLE_WAIT_S`). Kind `unreachable` (the provider could not be reached at all: a refused connection, as from
    a local model that is not running), `provider` (a rejected key, 401 or 403, and OpenRouter's 402 for credits run
    out; a rate limit, 429; a server error, 5xx) and `store` (a store write that fails with anything
    but `ExclusionError`). So an outage, a lapsed key or a failing memory store never burns out the backlog.

  `GET /dream/status` `error` and the `pass_end` entry's `error` carry the kind (`model`, `unreachable`, `provider`,
  `store`); a systemic kind stays shown through its pause. When the `dream` choice itself cannot run (`generator()`
  raises `ProviderStoreError`), the status says `setup` instead. "Dream now" clears the pause and resets every mark's
  attempts and retry wait before it tries (`DreamMarks.reset_attempts`, which keeps `read`, `seen`, `learned` and
  `last_dream_at`), so a skipped conversation is tried again. A change or clearing of the `dream` role through
  `PUT /settings/models` calls `Dreamer.reset_attempts()` (the marks' attempts, the pause and the status error): a new
  model gets a fresh try. It first cancels a call to the old model still in flight, which ends that pass as
  interrupted, so a late failure of the old model cannot bring the pause back; a memory write in flight finishes.
  `Harness.rebind` drops the cached dreaming client, so a key replaced in the environment is picked up, and hands the
  rooms' running-turn tracker to the new room service, so `busy()` still sees a turn under way. `remember_shared` handles `OwnerUnknown` itself, so the dreamer never sees it from a write;
  the owner is required by `ready()`, checked before every write.
- **Routes** (`dream_routes.py`, under `/dream`; none returns message text, and only `GET /facts` returns fact text;
  its answer goes through `exclusion.redact_obj`, since a conversation's title is the start of the user's first message):
  `GET /status` (`enabled`, `idle_minutes`, `model` (the `dream` role or null), `ready`, `idle`, `dreaming`, `waiting`,
  `last_dream_at`, `learned`, `error` (`model`, `unreachable`, `provider`, `store`, `setup` or empty), `idle_reported`
  (a report under 150 s old)); `PUT /settings` (`{"enabled", "idle_minutes"}`, 422 outside the range, 409 "Choose a
  dreaming model first." when switching on with no `dream` role); `POST /idle` (`{"idle_s": n}`, 0 to 10,000,000);
  `POST /run` (409 "Dreaming is not set up." when not ready, 409 "ZenBot is busy. Try again when it is idle." while
  `Harness.busy`, 409 when a pass is under way); `GET /facts` answers a bare list of the dreamt facts (source starting `dream:`), newest first, at most 200,
  each with `id`, `text`, `at`, `conversation` and `title`. A fact is removed with the existing
  `DELETE /memory/{fact_id}`. Every POST takes a required JSON body (`NothingIn` where it needs nothing), so a
  cross-site page cannot send one without a preflight; `tests/test_dream_routes.py` walks every POST.
- **The settings section** (`DreamSection.jsx`, decisions in `dream.js`, an entry in `settings-index.js` `SECTIONS`):
  the switch, the model, minutes idle, a status line, Dream now, and the list of what was learned with Forget. While
  the harness is online it reads the status every 5 s, between passes too, and reads the facts again when a pass ends
  or the totals move (`factsChanged`). Saving the Dreaming model keeps the picks not yet saved in the form above it
  (`models.js` `keepEdits`). Under
  the switch (`dream.js` `PRIVACY`, also in the search index): "Only the messages you typed are read, never a bot's
  reply. They are sent to the dreaming model you choose: a hosted model's provider receives them, a local model reads
  them on this computer. The facts it writes are then stored by the memory engine, which uses its own model." Each
  error kind has one sentence (`errorLine`): `unreachable` "could not be reached", `model` "an answer that could not be
  used", `provider` "The dreaming model's provider refused the request. Check its key and limits. Trying again in 10
  minutes.", `store` "A fact could not be saved to memory. Trying again in 10 minutes.", `setup` "The dreaming model
  cannot be used. Pick it again under Dreaming model."
- **Ledger.** Kind `dream`, events `pass_start`, `conversation` (conversation id, exchanges read, messages, facts
  written, dropped, refused, an error kind and the exception's class name) and `pass_end` (counts, error kind); and a
  `settings` entry (event `dream`) when the switch changes. They carry ids and counts only, never a message or a fact. A
  failed background pass is a kind `error` entry with the exception's class name. The `memory` entry a write makes
  carries the fact's id and size, not its text, so in the harness's own files the store and `GET /dream/facts` are the
  only record of what was dreamt. Two copies leave it, as for other memory work: with tracing on, a trace of the
  dreaming model's call holds the user's messages and the facts it answered (as every model call's trace does), and
  Hindsight's retain sends each fact to Hindsight's own model.
- **Known limits.**
  - The harness API has no authentication: a bot with `shell` or `browser` can post an idle report or call
    `POST /dream/run`, which starts dreaming early.
  - For the same reason "only the user's words" is told and checked, not walled: such a bot can call
    `POST /bots/{id}/tasks` or `POST /orchestrate` with any text, which is stored as `Exchange.user` and would be dreamt
    into shared facts with no bot id on them. It can already write shared memory directly (`POST /memory`), so dreaming
    adds no new reach, but do not describe dreamt facts as certainly the user's. Closing it needs authentication on the
    harness API.
  - A bot cannot write a conversation record on disk (the ledger folder is private). Text the user pasted from
    elsewhere is the user's message and is read.
  - Dreamt facts go into memory with no review step; the list with Forget is the control. Cost is nearly zero, not
    zero: each fact is one Hindsight retain on OpenRouter, until Hindsight's own model can be pointed at a local one
    (not built).
- **Unverified.** Dreaming has never run with a real model, local or hosted: the tests use a fake provider. The settings
  section has never been seen on screen, and the Electron idle report has only run in tests, not in a real window.

Rules for contributors: only `Exchange.user` goes in a prompt, never `addendum`, `comments`, `reply`, attachments,
`summary` or `note`. No dream tool for bots, no catalog entry for one, and no organization registered from a dream. Every
`/dream` POST takes a required JSON body. No message text or fact text in a `dream` ledger entry, a log line or an error
message. Never cancel a store write in flight (a model call may be). The `dream` role is never filled from another role,
and dreaming never runs without it. Keep `dreams.json` and `dream.json` out of the vault. Tests never touch the network:
a fake provider stands in for the model. Do not count a systemic failure (`SYSTEMIC`) against a conversation.
Tests: `tests/test_dream.py`, `tests/test_dream_routes.py`, `tests/test_dream_harness.py` (end to end through the
`Harness`), the dream-role reset and timeout in `tests/test_model_providers.py`, `failure_kind` in
`tests/test_provider_registry.py`; GUI
`tests/gui/dream.test.mjs`, `idle-report.test.mjs` and `settings-index.test.mjs`.

### The calendar and Google

The Events page shows a built-in calendar, kept by the harness in `<vault>-calendar/calendar.db`
(`src/events/store.py`). That calendar is the master: the page reads and writes only the `/calendar` routes
(`src/gui/calendar-api.js`) and never talks to Google. Google is one account at a time (`Settings.account`,
the primary calendar's id), brought in by pull and, when the user switches it on, sent back by push.

**Partly verified (2026-11-17): sign-in and a first pull have run against Google itself, once, by hand; everything
else has only run against the test fake (`tests/events/conftest.py` `FakeGoogle`).** Push has never reached Google,
and neither has a pull by sync token that was watched for its result. Assumptions about Google to check by hand
before a release:

- Seen working against Google (one account, a "Desktop app" client):
  - The client accepts the loopback redirect with a path (`/calendar/google/callback`) on the harness's port.
  - The read sign-in returns a refresh token and a `scope`, and the calendar list has a primary calendar.
  - `calendar.calendarlist.readonly` and `calendar.events.readonly` are enough for `calendarList.list` and
    `events.list`, on shared and holiday calendars too: their events arrived.
- Believed right, confirm once:
  - An event id of 32 hex characters plus `g` plus digits is accepted (base32hex, 5 to 1,024 characters).
  - Inserting a reused id answers 409, including when the earlier event was deleted.
  - GET of a deleted event answers 200 with `status: "cancelled"`; DELETE of an already-deleted one answers 410.
  - A PATCH merges `start` and `end` key by key, and a null removes a key (why `to_google(patch=True)` nulls the
    other form; the fake now behaves this way).
  - On the write upgrade, with `include_granted_scopes=true`, the token answer's `scope` lists the combined grant.
  - `prompt=consent` with `access_type=offline` returns a refresh token every time, not only the first.
  - The `primary` flag is on exactly one calendar-list entry (a list without one is now refused as `provider`).
  - `htmlLink` is on `*.google.com` (otherwise `isGoogleLink` hides the link).
- Not sure:
  - PATCH of a cancelled event: 404, 410 and a 200 that is still cancelled are handled; a 403 or 400 would
    retry on every run.
  - Sync tokens with `singleEvents=true`: whether a first read with `timeMin`/`timeMax`, `singleEvents` and
    `showDeleted` returns `nextSyncToken`, and whether the next read takes `singleEvents` and `showDeleted`
    beside `syncToken`.
  - What a sync-token read delivers for repeating events as time passes (the weekly full read does not depend
    on it).
  - Whether repeating `syncToken` beside `pageToken` on later pages is right (the code does).
  - Whether a rate limit arrives as 403 with `rateLimitExceeded` / `userRateLimitExceeded`, or as 429 (both
    are read as `rate`).
  - 404 on PATCH after losing access to a shared calendar: read as "the copy is gone", so a made-here event
    is made again at the destination, which would be a second copy.
  - An unticked write box on the upgrade consent: that `scope` then simply lacks `calendar.events` rather than
    the exchange failing.
  - How long a deleted event stays visible to `showDeleted` and GET; once purged, a reused id may insert
    afresh (fine) or still answer 409 with GET 404 (handled by the next generation).
  - Whether revoking one refresh token ends the account's whole grant. The code assumes it may: a sign-in
    refused while an account is linked revokes nothing unless the new token is known to be another account's.

- **Where things live.** Settings in `vault-settings/calendar.json` (`push`, `destination`, `account`, the granted
  `scopes`; no secret), the Google client id, client secret and refresh token in
  `vault-settings/calendar_keys.json` (`CalendarKeys.save`, mode `0o600`), the data in `vault-calendar/`
  (`settings.calendar_dir`, a private folder). `CalendarKeys` registers the secret and the refresh token with the
  exclusion filter. No new package: Google is reached with `httpx` through the one client the service holds
  (`CalendarService.http`), which tests replace with a `MockTransport`.
- **Each user brings their own Google app.** Nothing is shipped: the user makes a project, enables the Calendar API
  and an OAuth client of type "Desktop app" in their own Google account, and pastes its id and secret on Settings >
  Calendar (`PUT /calendar/google/credentials`; the steps are `APP_STEPS` in `calendar-settings.js`). They cannot be
  changed or cleared while connected.
- **Sign-in** (`Connection.begin`, `finish`; `google.auth_url`, `pkce`). Authorization code with PKCE (S256) and
  `access_type=offline`. The redirect is `http://127.0.0.1:<harness port>/calendar/google/callback`. The page asks
  for the read scopes first (`calendar.calendarlist.readonly`, `calendar.events.readonly`); write access
  (`calendar.events`) is a separate, later request (`POST /calendar/google/connect` with `{"write": true}`), and
  what Google granted is read from the token answer, never assumed (an answer with no `scope` is a failed sign-in).
  Google lets the user untick each permission: a grant without both read permissions (`connect.missing_read`;
  `calendar.events` counts as reading events) is refused with a sentence naming the missing one, since every pull
  would otherwise answer 403.
  `state` is random, kept in memory only (at most 5 pending), valid `STATE_LIFETIME` (600 s) and spent by the first
  lookup. It is the callback's only credential. The callback answers a fixed sentence in a page with
  `Content-Security-Policy: default-src 'none'`, `nosniff` and `no-store`, whatever fails (the route catches every
  exception); it reflects nothing it was sent, and no code, token, secret or `state` value goes to the ledger, an
  exception message or that page. A sign-in that gets no refresh token (and has none saved), no primary calendar,
  a missing read permission, or lists a different account than the connected one is refused. The new token is
  revoked when no account is linked, and when it is another account's; a refusal while an account is linked that
  comes before the account is known (a missing permission, a failed calendar list) revokes nothing, since a
  revoke may end the linked account's whole grant, and the new token is dropped unsaved. (A different account when
  not connected clears the made-here events' links to the old one, `CalendarStore.clear_links`.) A newly connected account starts with its primary calendar selected; a later
  sign-in (a reconnect, the write upgrade) leaves the user's choice alone. The token and client secret never
  leave the harness: no route returns one (a key is shown as `mcp_keys.hint` of the client id only), and
  `GET /calendar/status` says only whether they are set.
- **Secrets, logs and traces.** A traceback through the Google client holds the client secret and the tokens in
  its frames, and GlitchTip would be sent them. So `src/events/` logs an exception's class name only (never
  `log.exception` or `exc_info`), a bad `expires_in` counts as already expired instead of raising, the callback
  logs a warning (an error record is sent on with the request's query string), and the trace sampler gives 0.0 for
  `/calendar/google/callback` (`telemetry.UNTRACED_PATHS`), whose query carries the code and `state`.
- **Pull** (`Sync.pull`). Reads each selected Google calendar (`singleEvents`, so a repeating event arrives as its
  occurrences) from 31 days back to 366 days ahead, first in full and then by sync token; the token is kept per
  calendar. A token only reports changes inside the window its first read fixed, so each calendar is read in full
  again once its last full read (`full_read_at`) is 7 days old (`Sync.FULL_READ_EVERY`), which moves the window
  on: the rows stay until that read has answered, then the clean pulled events Google no longer lists there are
  dropped (`CalendarStore.keep_pulled`). The built-in copy wins: a pulled event with an unsent edit (`dirty`) or a
  local delete (a tombstone) is never overwritten or removed by pull, a full read included. A `gone` answer (the
  sync token expired) drops only the clean pulled events of that calendar (`clear_clean_pulled`) and reads it in
  full once; unselecting a calendar drops all its pulled events (`clear_pulled`). A pulled event outside the
  window is not kept, and one with a malformed date is skipped. Pulled text is kept whole, however long
  (`convert.to_local` never shortens it, so nothing can be sent back to Google cut off); an edit here that would
  save a title, location or description over the limits is refused by `store.validate` with its sentence. A
  Google event with no summary shows as "(No title)" here and goes back with an empty summary. A calendar list
  with no primary calendar is a `provider` error and changes nothing (`Sync.refresh_calendars`): stored, it would
  drop every calendar and pulled event it leaves out. A copy on Google whose id has the shape of
  `store.google_event_id` and matches a made-here event with no link yet (an insert that landed while its answer
  was lost) is tied to that event (`Sync._link_made_here`), never added as a second one; `mark_pushed` also drops
  a pulled row holding the same Google event. Pull writes nothing to Google.
- **Lost edits.** An event deleted on Google that was edited here is made again by push when it was made here,
  and is lost when it was pulled: counted in the meta counter `lost_edits`, which only grows, in whichever run it
  happens (a background one too). `GET /calendar/status` carries the count in `sync.lost_edits`; the Events page
  shows a notice for any status it loads with a count above zero and then acknowledges that count
  (`POST /calendar/lost-edits/ack` with `{"count": n}`, which subtracts with a floor of 0 and answers the status;
  `page-state.js` `lostEditStep`), so a loss counted between the read and the acknowledgement is shown next time.
  Don't reset the counter anywhere else.
- **Push** (`Sync.push`) is off by default and runs only when `Sync.can_push` says so: connected, the write scope
  granted, push on and a destination chosen. The destination is a writable Google calendar (`PUT /calendar/push`);
  `GET /calendar/push/preview` counts what a switch-on would send, with no network. An event made here goes to the
  destination under an id the harness makes (`store.google_event_id`: the event's own id plus a generation), so an
  insert whose answer was lost and is retried meets a 409 instead of making a second copy; on a 409 a live copy at
  that id is adopted and patched, a cancelled one takes the next generation, and after 3 tries the event shows the
  error. The calendar an insert is sent to is saved on the row first (`set_target`), so a retry goes to the same
  calendar even after the destination changed (unless that calendar is no longer writable). Once pushed an event
  stays on the calendar it went to, and one pulled from Google goes back to its own calendar, if that calendar is
  writable. While push is on, the destination and every calendar holding an event pushed from here are `locked`
  (`CalendarService.locked`): switching push on selects all of them that Google still lists, and unselecting one
  answers 409, checked again under the run lock (`CalendarService.unselect`), because an unselected calendar's
  deletes on Google would never be seen. Deletes made here are sent to the calendar the event is on. The body is
  `convert.to_google` only (no guests, no reminders, no `If-Match`: the local copy wins); a PATCH body nulls the
  other date form (`patch=True`), since Google merges `start` and `end` and refuses an event holding both `date`
  and `dateTime`, and an insert carries no nulls. `can_push` is read again before each event, so switching push
  off stops a run that is under way after the event in flight. While push is off, edits and deletes of made-here events wait in the store
  (`dirty`, tombstones) and go out when it is switched on; a pulled event cannot be edited then (`editable` in
  `routes.py`), and a repeating event's occurrence never can. An edit that lands while its push is in flight keeps
  the row pending (`rev`); an event deleted here while it was being sent has its Google copy deleted
  (`orphaned` in the ledger entry when that fails). A per-event failure is kept on the event (`push_error`, shown as
  "Not yet on Google") and does not stop the run; an `auth` failure does and marks the connection `needs_reconnect`
  (a single 401 is first retried once with a new access token, `Google._call`). The status line words a failed run
  by its kind (`page-state.js` `SYNC_ERRORS`): only `network` says Google could not be reached.
- **One run at a time** (`CalendarService`). Every run (`Sync.run`: refresh the calendar list, pull, push) holds one
  lock, as do a sign-in, a disconnect and unselecting a calendar, so none overlaps a run. A change made here asks
  for a run (`kick`, only while push can run); a second request while one runs queues one more run after it, never
  more. Runs also happen when Google is connected at start and every 15 minutes after (`SYNC_EVERY_S`), when a
  sign-in finishes, when a calendar is selected or unselected, when push is switched on, and on
  `POST /calendar/sync`, which joins the run in flight. The periodic loop outlives any exception. Nothing reaches
  Google before it is connected.
- **Disconnect** (`Connection.disconnect`): revokes the refresh token (best effort), forgets the keys' token and the
  granted scopes, switches push off, removes every Google calendar and pulled event from the store and clears the
  last sync time and error. It keeps the user's Google app (client id and secret), the events made here and their
  pending changes, and the `lost_edits` count. A pushed event stays on Google.
- **Times** (`store.validate`, `convert.py`). A timed event stores `start` and `end` as UTC instants
  `YYYY-MM-DDTHH:MM:SSZ` plus `tz`, an IANA name; an all-day event stores dates with the end exclusive. The harness
  never turns a wall-clock time into an instant: the page does, with `Intl` (`dates.js` `wallToInstant`,
  `instantToParts`), which is why the harness needs no zone database. An edit keeps the event's own `tz`. Title 300
  characters, location 500, description 8,000; the end must be after the start. `GET /calendar/events` takes day
  keys (`start`, `end` as `YYYY-MM-DD`, at most 400 days apart); the page asks a day wider than its grid on each
  side so no zone loses an event at the edge.
- **Routes and the ledger.** Every route is under `/calendar` (`src/events/routes.py`) and none returns a secret
  or token. Every POST takes a required JSON body, an empty one (`NothingIn`) where it needs nothing: a request
  another site's page can send without a preflight has no body, a form or `text/plain`, and FastAPI answers 422
  to each for a JSON body, so no page can disconnect Google, start a sync or add an event cross-site. A new POST
  route must take a body too; `tests/events/test_routes.py` walks every POST on the router. The callback is a GET
  guarded by its `state`. An event id or calendar id in a URL is quoted by the Google client (`google._quote`) and
  by `calendar-api.js`. Ledger entries of kind `calendar` (`connect`, `connect_refused`, `disconnect`, `sync`)
  carry ids, counts and error kinds only, never a title, description or location, and a failed background task is
  a kind `error` entry with the exception's class name.
- **Bots and the calendar (a known residual risk).** There is no calendar tool for bots: nothing in the calendar
  is registered with the tool runner, and `vault-calendar/` is a private folder: the `files` server and `magick`
  cannot reach it, and a `shell` command that names it opens an approval. But the harness API has no authentication, so a bot with `shell` (curl)
  or the `browser` server (a real Chrome) can call the `/calendar` routes on `127.0.0.1`: read events
  (`GET /calendar/events`), or create one (`POST /calendar/events` with a JSON body), which push then sends to
  Google with no review. The same holds for every other route of the harness; the calendar is the first feature
  whose safety argument ("only the user makes events here") leans on it. Closing it needs authentication across
  the whole harness API (for example a per-launch token the GUI holds), which is not built. Until then, do not
  describe the calendar as out of bots' reach.
- **Not built.** Anything beyond this and event discovery (finding local events, under `### Event discovery`, which
  makes a calendar event only through its two add routes). Don't count other pieces of the design as there.

Don't add a calendar tool for bots without revisiting automatic push: it sends whatever is in the calendar to
Google with no review. Don't write an event write to Google outside `Sync.push` (every insert, patch and delete of
an event goes through it; the only other request that changes anything at Google is the token revoke in
`connect.py`), add a POST route without a required JSON body, serve the callback without the closed headers or
reflect its query, log a traceback from `src/events/`, put a secret, a code, a token or a `state` value in a
response, a log line or a ledger entry, or put a title, description or location in a ledger entry. Keep the
calendar folder in `private_folders`. Tests: `tests/events/` (`test_settings.py`, `test_store.py`,
`test_convert.py`, `test_google.py`, `test_connect.py`, `test_pull.py`, `test_push.py`, `test_routes.py`);
GUI `tests/gui/calendar-api.test.mjs`, `events-dates.test.mjs`, `events-page-state.test.mjs`,
`calendar-settings.test.mjs` and `settings-index.test.mjs`.

### Event discovery

The Events page has three tabs (`EventsArea.jsx`): My Calendar, Your Recommendations and All Events. Your Recommendations
(`DiscoverView.jsx`; called Discover until 2026-11-18, and still `discover` in code, routes and file names) shows a short
digest of local events picked for the user. The harness reads one
city's events from Eventbrite's public pages and, when the user sets it up, from FindLocal's keyed API, and keeps them as one pool; a bot (the scout, `bot-prompts/event-scout.md`)
picks six to eight from it and says why; the user follows a card's link and, on saying they signed up, the event goes on
the built-in calendar. All Events (`AllEventsView.jsx`) shows the whole pool as a grid to filter, sort, open and add to the
calendar. Design: `docs/superpowers/specs/2026-11-17-event-discovery-design.md` and
`docs/superpowers/specs/2026-11-18-all-events-design.md` (git-ignored, on the computer the work was done on).

**Eventbrite: checked against it on 2026-11-17 and 2026-11-18**, by plain GET with a browser User-Agent, no cookies: each
page `https://www.eventbrite.com/d/<place>/events--this-month/?page=N` (and `events--next-month`) answers 200 and holds
`window.__SERVER_DATA__ = {...}`, whose `search_data.events.results` is a list of about 20 events with
`pagination.page_count`. On 2026-11-18 one real refresh of `oh--columbus` (40 pages, one a second, about 100 s) read 727
rows and kept 650 events (72 repeated across the two months, 5 rows refused by `_event`, none of them cancelled or
online), with no sign-in or challenge page and no refusal. Rows in the real pages carry keys the test fixture does not
(`eid`, `debug_info`, `urgency_signals`, `full_description` and others); nothing reads them. Not every event has an image
(19 of 727 had none), a summary (10) or a category tag (22 of those kept), and some listings run all day (`00:00` to
`23:30`).

- **Where things live.** Settings in `vault-settings/events.json` (`DiscoverSettings`: `city`, `state`, `country` `US`,
  `source_url`, `place`, `categories`, `interests`, `enabled`; no secret), the pool, digests, cards and the `added` table
  (events added to the calendar from the pool) in `vault-calendar/discover.db` (`DiscoverStore`), card thumbnails in
  `vault-calendar/discover-images/<card id>` and pool pictures in `vault-calendar/discover-images/pool/<event id>`. The
  last three are in a private folder, so no bot tool reaches them.
- **The address** (`discover_settings.parse_place`) is a security boundary. Only `https`, host exactly `www.eventbrite.com`
  or `eventbrite.com` (no user info, no port), path `/d/<place>/...` with `<place>` matching `[a-z]{2}--[a-z0-9-]{1,80}`,
  no backslash, whitespace, control character, `.` or `..`. Anything else is a `ValueError` with the expected form, which
  `PUT /discover/settings` answers as 422. Only the `place` it returns is used afterwards: every fetch URL is built from it.
- **Reading Eventbrite** (`eventbrite.Eventbrite`). Fetches go only to `https://www.eventbrite.com/d/<place>/events--this-month/` and
  `events--next-month/` (`?page=N`) and, for a card thumbnail or a pool picture, `https://img.evbuc.com/`
  (`Eventbrite.image`); nothing else read from a page is ever fetched. One request at a time, `GAP_S` (1 s) apart, a 30 s timeout, 5 MB a page, 1,500,000 bytes a picture (PNG, JPEG,
  WebP or GIF by first bytes), at most `MAX_PAGES` (40) pages: up to 20 of this month, then up to 20 of next. The pages are
  Eventbrite's own ranking, so the cap keeps its top results, not every event (a big city has more than 80 pages across the
  two months). Any 3xx is `source_changed` and is never followed (the built URLs answered 200 with no redirect). The JSON
  object is decoded from its opening brace with `raw_decode`. Error kinds: `network`, `refused` (403 or 429),
  `source_changed` (a page without the expected shape, or one that has rows of which none has the shape `_event` needs;
  a page whose rows are all cancelled or online-only is not an error), `provider` (any other status). A failure on the first page fails the
  refresh and leaves the pool as it was; a later failure ends the reading and keeps what was read, with its kind in `error`.
- **What is kept from Eventbrite** (`eventbrite._event`): digits `id`, non-empty `name`, a real `start_date`, a `url` on
  `https://www.eventbrite.com/e/`, not cancelled, not online-only. Times are kept when `HH:MM`; `timezone` as given. No start
  time means all-day; a timed event whose end is missing or not after its start is kept with no end. Text is capped on
  storage: name 300, venue and address together 500, summary 1,000 characters, control characters removed.
- **The pool** (`DiscoverStore`). A refresh inserts or updates the rows it reads and deletes none of Eventbrite's (the page
  cap reads only part of a ranked list, so an unseen row is not a gone row); rows whose last day (`end_date`, else
  `start_date`) has passed are dropped (`drop_past`), so a row still running today stays for the morning digest. Each row has a `source`, `eventbrite` or `findlocal` (a row from before FindLocal counts as `eventbrite`).
  `Discovery.refresh` reads Eventbrite and then FindLocal under one lock. Eventbrite is skipped while its last good refresh
  is under 12 hours old (`REFRESH_AFTER_S`) unless forced, and a refresh that failed is not tried again for 10 minutes
  (`RETRY_AFTER_S`) unless forced (no caller passes `force` today). After both sources, once the refresh lock is released,
  a refresh sweeps the pool pictures (`Discovery._sweep`, under the picture lock; see *All Events* below).
  The candidates are the pool rows starting today or later, in a ticked category (or with no category tag, or any when none
  is ticked), never on a saved card and never in the `added` table (an event the user added from All Events is no
  candidate from then on, whether or not its calendar event still exists). The pool has no place column: when the settings are saved with a different `place`,
  `Discovery.apply` deletes every Eventbrite pool row (`DiscoverStore.clear_pool("eventbrite")`) and clears Eventbrite's last
  refresh time, error and failure time, so the next refresh reads the new city at once; saved cards are snapshots and stay.
  A different `findlocal_city` does the same for the FindLocal rows and FindLocal's own three meta values.
- **FindLocal** (`findlocal.FindLocal`, a second source, optional: nothing is read without discovery on with an Eventbrite `place` set, and a key, a city and a time zone,
  `Discovery.findlocal_ready`). It reads `https://findlocal.community/api/events` (`API_URL`), a keyed REST API.
  - *Where things live.* The city (`findlocal_city`, one of `FINDLOCAL_CITIES`, FindLocal's own slugs) and `timezone` in
    `vault-settings/events.json`; the key in `vault-settings/events_keys.json` (`DiscoverKeys.save`, mode `0o600`, never in
    the vault; registered with the exclusion filter), set on Settings > Calendar > Event discovery. The key is sent only as
    an `Authorization: Bearer` header. Settings answers carry `key_set` and `key_hint` (the last four characters when it
    has eight or more, `mcp_keys.hint`), never the key.
  - *The time zone.* FindLocal rows carry no zone, so the settings hold one, `timezone` (`^[A-Za-z0-9_+\-/]{1,64}$`), and
    every FindLocal event gets it as its `timezone`. The settings section shows it as "Time zone of that city": picking a
    city starts it on the computer's own zone (`Intl`, `discovery-settings.js` `pickCity`), which is wrong for a city
    elsewhere, so the user can correct it; the page saves only a zone `Intl` accepts (`validZone`, `canSave`). The harness
    takes the zone in the request; with none sent it keeps the saved one for the same city, and a new city needs one (else
    422). It has no zone database and does not check the name beyond its shape. When the zone changes and the city does
    not, the FindLocal rows already read and their cards not yet added take the new zone (`DiscoverStore.set_zone`), with
    no new read; an added card keeps the zone its calendar event was made with.
  - *Reading* (`FindLocal.read`). One request at a time, `GAP_S` (1 s) apart, each with a `TIMEOUT_S` (30 s) timeout and an answer limit of `PAGE_LIMIT` (5,000,000 bytes), at most `MAX_CALLS` (10) calls of `LIMIT` (100)
    rows from today for `DAYS_AHEAD` (60) days, sorted by date, stopping at an empty page or when `meta.total` is reached. The
    free plan allows 100 calls a month, so a run costs up to a tenth of it. Requests go only to `API_URL` with the city
    checked against the list; no 3xx is followed. A key that is empty or not printable ASCII without whitespace is refused
    with kind `key` before any request. Error kinds (`SourceError.kind`): `network`, `key` (401), `refused` (429),
    `source_changed` (a redirect, or an answer that is not JSON with a `data` list, or rows none of which has a string
    `id`), `provider` (any other status or an answer over the 5,000,000-byte limit). A failure on the first call fails the read; a later
    failure ends it and keeps what was read, with its kind in `error`. `pages` in the read's ledger entry counts the calls
    that answered. The result's `complete` is true only when the read reached the end (an empty page, or `meta.total`):
    a read stopped at `MAX_CALLS` with more left has no error and is not complete.
  - *What is kept* (`findlocal._event`): an `id` that is a UUID (stored as `fl-<uuid>`), a non-empty `title`, a real
    `event_date`, and a link (`detail_page_url`, else `ticket_page_url`) that is `https` with a host, at most 500 characters
    and made only of ASCII with no space, control character, backslash or user info; a link that fails these checks (a
    non-ASCII character included) is skipped, and the row is dropped when neither link passes. Not kept: `status` cancelled or postponed, `attendance` online, `is_deleted`, `duplicate_of`,
    `is_real_event` 0. Times are kept when `HH:MM`; a timed event ends the same day or has no end; an all-day one takes
    `end_date` when it is not before the start. Text caps are Eventbrite's. A row has no picture. `category` maps to an
    Eventbrite tag for some slugs (`CATEGORY_TAGS`) so the user's ticked categories apply; a slug with none has no tag and so
    passes the filter.
  - *Duplicates* (`DiscoverStore.known_duplicate`, checked as FindLocal rows are read, before they are stored). A row is
    skipped when the Eventbrite id in its link is a pool row or a card already, or an Eventbrite pool row, or a card of
    another event, has the same day and the same name (casefolded, letters and digits only).
  - *A row's life.* A read stores the rows it kept with the time they were stored (`seen_at`). A FindLocal row stays in
    the pool (no age rule makes it less of a candidate) until a later read with no error says it is gone
    (`DiscoverStore.keep_pool`, by id, never by `seen_at`, so a clock set back cannot keep a gone row or drop one just
    read). A read that reached the end (`complete`) deletes every FindLocal row it did not store, which includes a stored
    row that has since become a duplicate of an Eventbrite row (it is skipped, so not stored again). A read stopped at
    `MAX_CALLS` saw, in date order, everything before the day of its last row: it deletes only the rows it did not store
    that start before that day, and a row past the cap stays (not seen is not gone); if its rows were not in date order
    it deletes nothing. A failed or partial read deletes nothing. A row that starts before today is
    never listed or a candidate, and is deleted by the next good Eventbrite refresh once its last day has passed
    (`drop_past` takes every source's).
    So a mid-week on-demand run and the All Events grid see the weekly read's FindLocal rows all week.
  - *When it is read* (`Discovery._refresh_findlocal`). The first time it is set up (no last read), and after that only on the
    weekly routine's run, once the last read is over 20 hours old (`FINDLOCAL_AFTER_S`): `events_candidates` passes
    `weekly=Discovery.is_weekly_run(session id)`. `force` does not apply to it, so an on-demand run ("Find events now") and
    `POST /discover/refresh` (Refresh on All Events) read it only when it has no last read. Inside 10 minutes of a failed read nothing is requested and the refresh answer's `findlocal` block
    carries the recorded error kind; a failed first read is tried again after that. Any other exception in a FindLocal read
    or in storing its rows (one that is not a `SourceError`) is recorded as kind `provider`, logged by its class name only,
    waits the same 10 minutes and never fails Eventbrite's part of the refresh. `set_key` writes `events_keys.json` before it
    swaps the key in memory, so a save that fails leaves the old key in use. `set_key` clears FindLocal's last error
    and failure, and, when the last read had failed (a refused key, say), the last read time too, so a new key after a
    failure is read at the next refresh of any kind. After a good read the last read time stays, and the new key is first
    used by the weekly run once the last read is over 20 hours old (the quota). Saving a new key over an old one keeps the
    FindLocal rows. A key that cannot be written (`PUT` or `DELETE /discover/findlocal/key`) is answered 500 "The key could
    not be saved." and never raised: the key is in that call's frames, and an exception leaving the route would carry
    them to the error tracker. Forgetting the key
    (`DELETE /discover/findlocal/key`, `set_key(None)`) also deletes every FindLocal pool row (`clear_pool("findlocal")`)
    and clears its last read time, so with its error and failure time cleared too, a key entered later is read at the next
    refresh.
  - *Routes.* `PUT /discover/findlocal/key` (body `{"key": ...}`, checked by `discover_settings.check_key`: trimmed, plain
    ASCII, no whitespace, at most 200 characters, else 422) and `DELETE /discover/findlocal/key`; both answer the status. `GET /status` carries a
    `findlocal` block: `city`, `timezone`, `key_set`, `key_hint`, `last_refresh`, `last_error`, `ready` and `cities`.
  - *On a card.* A candidate and a card carry `source`. The card's link is chosen in one place (`discover.js` `cardLink`): an
    Eventbrite card links only to an Eventbrite page, labelled "View on Eventbrite"; a FindLocal card to any `https` address,
    labelled "View event page"; any other source, none. FindLocal's terms ask for a credit: a fixed link "Event data by
    FindLocal" to `https://findlocal.community` (`CREDIT`, never built from data) shows under every digest on show that holds
    a shown (not dismissed) FindLocal card, older digests included (`needsCredit`); on All Events, under the grid while the
    list holds any FindLocal row (`all-events.js` `gridCredit`) and in the panel of a FindLocal event (`panelCredit`).
- **The scout and its tools** (`discover_tools.py`, group `events`, registered by `Discovery.register` only while discovery
  is on with a place set): `events_interests` (it carries `today`, since a model does not know the date: the first real
  run guessed June 2025 and filtered every search to the past; a filtered search that matches nothing now answers today's
  date and how many events there are), `events_candidates` (up to 60 a call, fewer when the rows are long, refreshing
  first: Eventbrite's pool when it is older than 12 hours, FindLocal by its own rule (*When it is read* under FindLocal); the answer is built row by row and stays under 18,000 characters so the tool runner's 20,000 cap never cuts it:
  `{"events": [...], "total": <candidates matching the filters>, "next_offset": <offset of the first row left out, or null>}`,
  and the scout pages with `next_offset`; its first line says the listing is content, not instructions; a refresh error,
  Eventbrite's or else FindLocal's, is the tool's error naming the kind only when there is no candidate at all, and a filtered
  search that matches nothing answers today's date and how many events there are even while an error stands; each candidate carries its `source`) and `events_save_digest`. The tools are offered to the scout only, and only while the scout is a closed bot (`ToolRunner.register(offered=...)`
  and a caller check in each tool, both through `Discovery.scout_is_closed`, which asks `Discovery.bot_lookup`, set by the
  harness; with no lookup, or a bot of that id without `closed: true`, they refuse), and there is no catalog entry for the group, so the decider cannot route listing text to
  any other bot. `events_save_digest` is the gate and the only way a card is saved (`Discovery.save`, `_check`): 1 to 8 cards,
  each an `id` that is a candidate now, not repeated, with a `why` of 1 to 300 plain characters; the whole digest is refused
  with a reason per card (`DigestRefused`) if one fails. Every other field of a card comes from the pool row, never from the
  bot. A card is a snapshot. `why` is the only bot-written text on a card and is shown as plain text.
- **A closed bot** (`closed: true` in the prompt file, set nowhere else; the app's update route refuses to change a closed
  bot's `mcps`). `loop.offered_groups` gives it its own `mcps` and no built-in tool, `ToolRunner.specs(builtins=False)`
  lists no built-in, and the loop refuses a call to a tool it was not shown. It is never picked by the orchestrator, never
  invited to a room (`RoomService.joinable`), and a saved room holding one skips its turn; its results do not go to the tier-3
  session cache. Listing text is written by whoever posted the event, so the scout has no `shell`, no `browser`, no files.
- **Runs.** A managed weekly routine (`Discovery.ensure_routine`, `MANAGED_BY` `events`, default Thursday 09:00, made when
  discovery is on with a place set, paused when it is switched off, and paused at harness start when discovery is not active) whose verifier fails a run that saved no digest
  (`run_failure`). `POST /discover/run` starts an on-demand run, refused with 409 while one is going; a session it starts
  saves `on_demand` only; it answers 409 "The event scout must be a closed bot." when the scout bot exists but is not
  closed. Your Recommendations shows "Looking for events…" while a run goes: one this page started ends on its first new digest, the
  scout stopping or the time limit; a run that is not its own (the weekly one, or one going when the tab opened) is watched
  until it stops or the time limit, with the digests on hand as the ones already seen, and a digest that arrives meanwhile
  shows at once (`discover.js` `lookStep`, `viewState`). One 15-minute limit per run. A digest's `note` is held to the same
  plain-text check and length cap as a card's `why`.
- **All Events** (`AllEventsView.jsx`, `EventPanel.jsx`, decisions in `all-events.js`, through `discover-api.js`). The grid
  shows the pool as the last refresh left it; its routes never read Eventbrite's pages or FindLocal, and the picture route
  fetches only the picture, from `img.evbuc.com`.
  - *The list* (`GET /discover/events`): while discovery is active, every pool row starting today or later in date order,
    at most `MAX_LISTING` (5,000; `DiscoverStore.listing`), each with the slim fields (`SLIM`: id, name, dates and times,
    venue, city, category names, source) and its `state`; plus `total` and `read_at` (Eventbrite's last refresh). Not
    active, it answers an empty list. The page filters (text over name, venue and city; category; source, shown only when
    both sources are present; from and to, inclusive, on the start date), sorts by any column (an empty venue or category
    last either way; ties by date, time with all day first, name), draws 200 rows at a time as the user scrolls, and
    marks a row ★ Recommended or ✓ On your calendar.
  - *An event's `state`* (`state` in `discover_router`, from `DiscoverStore.listing` and `marks`): `added` while a calendar event made from it (the `added` row's or any
    card's, a dismissed card included) still exists, not deleted; else `recommended` while it has a card that is not dismissed; else empty.
  - *The panel* (`EventPanel.jsx`, opened from a row): `GET /discover/events/{id}` answers the row's `DETAIL` fields, its
    `state` and `image` (`/discover/events/{id}/image` for an Eventbrite row with a picture address, else null), or 404
    "No such event." It shows the picture, the details, the link (`cardLink`'s rule), and Add to calendar when
    `addTimes` gives times; following the link and coming back asks "Did you sign up?" (`SignupDialog.jsx`, without "Not
    for me": a pool event cannot be dismissed).
  - *Pool pictures* (`GET /discover/events/{id}/image`, `Discovery.pool_image`). Only for an id of 1 to 30 digits
    (`POOL_ID`) that is an Eventbrite pool row with an `image_url`; fetched the first time it is asked for, only through
    `Eventbrite.image` (so only `https://img.evbuc.com/`, at most 1,500,000 bytes, PNG, JPEG, WebP or GIF by first bytes),
    one fetch at a time and `GAP_S` apart with one another (card thumbnails fetched at a digest save are not paced with
    them), and kept as `discover-images/pool/<id>`: written new to `<id>.part` (`O_EXCL`, `O_NOFOLLOW` where there is one)
    and renamed into place; a failed fetch keeps nothing, so a later ask tries again. When `discover-images` or `pool` is a
    link nothing is read or written. Served with its type by first bytes and `nosniff`; 404 "No picture." otherwise. Both
    picture routes read the file once and answer those bytes (`discover_routes.picture`), so a file removed after its path
    was found is a 404, never a failed response. A refresh that reads a new `image_url` for a row marks its kept picture
    stale (`Discovery._stale_images`): it is no longer answered, the next ask fetches the new one, and the sweep removes
    the old file; a fetch whose row's `image_url` changed while it waited keeps nothing. After
    each refresh, once the refresh lock is released, the sweep (`sweep_pool_images`, under the picture lock the fetches
    take, so it never holds up a settings or key save while fetches queued ahead of it run; the call that started the refresh, the scout's `events_candidates` included, still waits for its own sweep)
    deletes kept pictures whose event is no longer an Eventbrite pool row, stray part files and links (never followed). A
    sweep that fails is logged by its class name and never fails the refresh.
  - *Refresh* (`POST /discover/refresh`, `NothingIn`; 409 when discovery is not active). Starts an unforced `refresh()` in
    the background (`Discovery.start_refresh`; none while one is under way) and answers the status, whose `refreshing`
    says one is going. Unforced, so Eventbrite's 12-hour and 10-minute waits hold, and FindLocal is read only when it has
    no last read. A background refresh that ends in an unexpected exception is logged by class name and shows as error
    kind `provider` in the status (`Discovery.refresh_failed`, in memory) until a refresh ends. The 10-minute limit is
    checked at the start of every look (`pollExpired`), so a status request that never answers cannot hold it off; a list
    load that was out while an add finished keeps that add's mark (`withAdded`); and a list read that fails with the
    status on hand shows its error with "Try again". The page offers Refresh only when set up, online, nothing refreshing, and Eventbrite's last read is
    none or 12 hours old (`canRefresh`); while one goes it shows "Reading events…", looks at the status every 3 s and
    loads the list again when it ends, for at most 10 minutes. After that the reading line goes (`isBusy`), the page says
    "The refresh has not finished. Check back later.", and Refresh follows `canRefresh` (still not offered while the
    last status it read says `refreshing`).
  - *Active or not.* `GET /discover/events/{id}`, the pool add and the picture route do not check that discovery is active:
    with discovery switched off the pool rows stay, a picture can still be fetched on request, and no sweep runs until a
    refresh happens again (a refresh does nothing while discovery is off).
- **Routes** (`discover_routes.py`, under `/discover`, none returns a disk path or a key): `GET /status`, `PUT /settings`, `PUT` and `DELETE /findlocal/key`, `GET /digests`,
  `GET /digests/{id}`, `GET /images/{card_id}` (served `nosniff`), `GET /events`, `GET /events/{id}`,
  `GET /events/{id}/image` (served `nosniff`), and the POSTs `/run`, `/cards/{id}/dismiss`, `/cards/{id}/add`,
  `/events/{id}/add` and `/refresh`. Every POST takes a required JSON body (`NothingIn` where it needs nothing, so a cross-site page cannot
  send one without a preflight); `tests/events/test_discover_routes.py` checks it.
- **Adding to the calendar** (`POST /discover/cards/{id}/add`, body `{start, end}`). The harness cannot turn a wall-clock time
  into an instant, so the page does, with `Intl` (`dates.js` `wallToInstant`, through `discover.js` `addTimes`; a start inside a
  daylight-saving gap is stepped back to the jump minute). The harness checks each instant lies within 14 hours of the card's
  wall-clock value (no zone is further from UTC), else 422; it makes the event from the card's snapshot through `store.add` and
  its validation (title the name, location venue and address, description the summary then the link, `tz` the event's zone),
  marks the card `added` with the event's id, and asks for a sync. Before making one it answers a live calendar event
  (existing, not deleted) made from the same pool event by either add route: the card's own, else the `added` row's or
  another card's, a dismissed card included (`marks`, `pool_live`); the card is then marked `added` with that event's id
  and nothing is made. So for one pool event, while a calendar event made from it by either route still exists and is not
  deleted, neither route makes another; once it is deleted, an add makes a new one. If the calendar event is made and the
  mark then cannot be written, the event is deleted again and the route answers 500 "The event could not be added. Try
  again.", so a retry makes one event and not a second (best effort: if the delete fails too, the event stays). A listing
  whose last day is before today is 409 "This event has already happened.", and so is a timed listing whose end instant
  (the page's, checked against the listing) is not after now; an all-day listing keeps the date rule. A timed event with no end ends one hour after it starts. A timed card whose listing had no usable zone (stored as
  empty) cannot be added: `addTimes` gives null, so Add is not offered, and the route answers 422 "This listing has no
  usable time zone." With push on, the event goes to Google like any made here.
  Adding is the user's yes: "Yes, add it" in the dialog after following a link and coming back, or Add on the card. No
  dismiss is offered on an added card.
  From All Events, `POST /discover/events/{id}/add` (body `{start, end}`, `addTimes` of the pool row; 404 "No such event."
  for an id that is no pool row) does the same checks (`make_event`, shared with the card add: 409 for a past event, 422 for times that are not the listing's or fields the
  calendar refuses) and makes the event from the pool row, never a card's older snapshot. When the event already has a live
  calendar event made by either route (the `added` row's or any card's, a dismissed card included) it answers that one
  and makes none. Otherwise, when the event has a card that is not dismissed
  (`card_for_event`, the newest), that card is marked `added` with the new event's id instead of writing an `added` row;
  else an `added` row is kept (`mark_event_added`). It answers `{"event": <the detail with its new state>,
  "calendar_event": ...}` and asks for a sync. Both add routes run under one lock (`add_lock`), and they are the only way
  discovery makes a calendar event.
- **Ledger.** The entries the discovery code writes are kind `calendar`, events `discover_refresh` (the source, pages, events,
  error kind; one entry for each source read, never the key) and `discover_digest` (digest kind, card and picture counts), and carry no listing text: never a listing's name,
  summary, venue or link. They are not the only place listing text lands: the loop's generic `tool_call` entry for the
  scout's session stores the tool's answer (so each `events_candidates` answer, up to 4,000 characters), and its `turn`
  entries store the scout's messages, as for every bot; so listing text is in `ledger.jsonl`, in the session's event stream
  and, when tracing is on, in traces. The ledger folder is a private folder and no bot or memory path reads those entries
  back. `src/events/` logs an exception's class name only.

Unverified, and open:

- **FindLocal has only run against the stand-in (`FakeFindLocal`); no request has reached it** (2026-11-18). Its docs give no
  example answer, so the row keys `_event` reads (`id`, `title`, `event_date`, `start_time`, `end_time`, `end_date`,
  `detail_page_url`, `ticket_page_url`, `venue_name`, `venue_address`, `venue_tba`, `city`, `region`, `category`, `status`,
  `attendance`, `is_deleted`, `duplicate_of`, `is_real_event`, `description`) and `meta.total` are as the docs name them, not
  as an answer was seen. The settings section and the Your Recommendations tab (then Discover) with a FindLocal card and
  the credit have not been looked at in a browser either. The first real read (a city and the owner's key, up to 10 of the month's 100 calls) should record
  what Eventbrite's check records: calls made, rows read, kept and refused and why, and what real rows hold that `_event`
  does not read.
- FindLocal's terms cap caching at 24 hours, but saved FindLocal cards are shown for a week, and FindLocal rows now stay in
  the pool until the next complete read (about a week), offered to a mid-week run and shown on All Events: the term is
  stretched further (the owner's accepted call).
- What a collapsed series row's `event_date` means is unknown.
- A stored FindLocal row that later becomes a duplicate of a new Eventbrite row stays in the pool, a candidate and on All
  Events until the next complete FindLocal read deletes it (duplicates are checked only as FindLocal rows are read).
- **Eventbrite's terms on automated access were not read** (the owner chose scraping knowing this); `robots.txt` did not
  disallow `/d/` for `User-agent: *` on 2026-11-17. How the site answers a longer run, or many users, is unknown.
- **The scout has never run with a model.** Its prompt, the skill and the recall query are untested against a real model;
  pressing "Find events now" in the owner's own app is the first real test. Whether anything of the scout's turns reaches
  the user's memory by the usual paths was not checked.
- The page cap reads Eventbrite's top-ranked 40 pages, not every event. Per-category page addresses (for example
  `/d/oh--columbus/music--events--this-month/`) were not tried.
- The Discover tab (now Your Recommendations) and the settings section were looked at in a browser on 2026-11-18, against a
  throwaway vault and a digest saved by hand, not by the scout; the "window got focus again" event was sent by script, not by a real window switch
  (the Electron shell was not run).
- **The All Events tab, the event panel and the renamed tab have not been seen on screen by anyone**: only `npm test` and
  the build have run them. The grid has not been tried with a real pool's size.
- The harness API still has no authentication, so a bot with `shell` (curl) or the `browser` server can call the `/discover`
  routes as it can call `/calendar`: read the digests and the whole pool (`GET /discover/events`, `/events/{id}`), dismiss
  a card, add any card or pool event to the calendar (`POST /discover/cards/{id}/add`, `/events/{id}/add`), which push
  then sends to Google, start a refresh (`POST /discover/refresh`), and make the harness fetch any Eventbrite pool row's
  picture from `img.evbuc.com` (`GET /discover/events/{id}/image`). A caller walking every id makes the harness keep up
  to 1,500,000 bytes for each Eventbrite pool row with a picture: bounded by the pool, and deleted by the sweep after the
  next refresh once the row has left it. Do not describe discovery as out of bots' reach.

Rules for contributors. No card is saved except through `events_save_digest` (`Discovery.save`). No fetch to a host other than
`www.eventbrite.com` (the pages), `img.evbuc.com` (pictures) and `findlocal.community/api/events` (FindLocal), none to a
URL read from a page or an answer except a picture address that `Eventbrite.image` accepts (`https://img.evbuc.com/`),
and no 3xx followed. The FindLocal key never goes in a route answer, a log line, an exception message, a ledger
entry or a URL, only in the request's `Authorization` header; no bot gets the key or a FindLocal tool of its own. The
scout stays `closed: true` and out of rooms; do not give any bot a calendar or discovery tool, and do not add one to the
decider's catalog. Every `/discover` POST takes a required JSON body. No listing text, disk path or picture bytes in a
ledger entry the discovery code writes, a log line or a route answer beyond what a card, the grid or the event panel shows (the loop's own `tool_call`
and `turn` entries for the scout's session hold its tool answers and messages: see Ledger above). The pool picture route
answers only for an id of digits (`POOL_ID`) that is an Eventbrite pool row, names its file by that id alone, and fetches
only through `Eventbrite.image` (`img.evbuc.com`); never build a picture's path or address from anything else. The grid's
routes (`GET /discover/events`, `/events/{id}`, `/events/{id}/image`) never read Eventbrite's pages or FindLocal; only a
refresh does, and the picture route fetches only the picture, from `img.evbuc.com`. The two add
routes (`POST /discover/cards/{id}/add`, `/events/{id}/add`) are the only way discovery makes a calendar event. Tests never touch the network: the connector takes an
`httpx.MockTransport` (`fake_site`, `FakeFindLocal`). Tests: `tests/events/test_eventbrite.py`, `test_findlocal.py`, `test_discover_settings.py`,
`test_discover_store.py`, `test_discovery.py`, `test_discover_tools.py`, `test_discover_routes.py`; GUI
`tests/gui/events-discover.test.mjs`, `all-events.test.mjs`, `discover-api.test.mjs`, `discovery-settings.test.mjs` and
`settings-index.test.mjs`.

### AgentMail

ZenBot has an email address of its own through [AgentMail](https://agentmail.to). The owner connects the key
and inbox in Settings > Connected apps and reads incoming messages on the AgentMail page. The morning digest
sender uses this inbox when a recipient is set in desktop Settings > Calendar > Morning digest.

- **Morning digest.** `src/events/morning_agenda.py` reads today’s commitments and birthday observations.
  Email picks come from public discovery listings and the configured event scout. `morning_weather.py`
  owns Open-Meteo search and forecasts, with the location and unit in `vault-settings/morning_weather.json`.
  `morning_digest.py` builds the two editions and serves `GET /morning/agenda`, `/forecast`, `/locations`
  and `GET/PUT /morning/settings` under the `/morning` prefix. Opening chat only reads.
  Electron owns the recipient and time in `vault-settings/morning_email.json`. Keep recipient changes out
  of the harness HTTP routes. `src/mail/morning_delivery.py` tracks attempts by local day and is started and
  cancelled in the harness lifespan. The weather and mail clients are exercised offline using HTTP stand-ins.

- **Where things live.** `vault-settings/agentmail.json` (`MailSettings`: `inbox_id`, `email`; no secret) and
  `vault-settings/agentmail_keys.json` (`MailKeys`: `api_key`, mode `0o600`, tmp-then-replace, registered with the
  exclusion filter, kept out of reprs). Never in the vault. Answers carry `key_set` and `key_hint` (`mcp_keys.hint`),
  never the key. `MailService` is built by `Harness` (`Harness.mail`) with its own `httpx.AsyncClient`, closed at
  shutdown; there is no network until a key is saved.
- **The client** (`agentmail.AgentMail`). Requests go only to `https://api.agentmail.to/v0/...`, each path part
  percent-quoted as one part; the key only in the `Authorization: Bearer` header. 30 s timeout; an answer over
  5,000,000 bytes is refused; no 3xx is followed. Errors are `MailError(kind)`: `network`, `key` (401, or a 403
  without `missing_permission`), `permission` (403 `missing_permission`), `not_found`, `taken` and `invalid` (a
  create refused: 409, or 400/422 whose code or message says the name is in use, is `taken`), `refused` (429),
  `provider` (anything else, a 3xx, or an answer not in the expected JSON shape). Every answer is cut to the fields
  the GUI shows, with control characters removed (U+2028 and U+2029 too) and caps: addresses and names 320
  characters, subject 300, preview 500, body 200,000. A message's text is `extracted_text` (the reply without the
  quoted history), else `text`; its `html` and `extracted_html` are never read. A create sends `client_id`
  `openzenbot-<name>`, so a retried create makes no second inbox. The `am_` prefix is not required of a key.
- **Routes** (`routes.py`, under `/agentmail`): `GET /status` (`key_set`, `key_hint`, `inbox` or null, `enabled`,
  which is key and inbox both set); `PUT /key` (`{key}`; checked by `check_key`, then by listing one inbox with it
  before it is saved; 422 "AgentMail did not accept this key." or "This key cannot list inboxes. Use an organization
  API key.", 502 when unreachable, 500 "The key could not be saved." when the file cannot be written; a chosen inbox
  the new key cannot see is cleared); `DELETE /key` (forgets the key and the choice; nothing changes at AgentMail);
  `GET /inboxes` (one page of up to 100; 409 without a key); `POST /inboxes` (`{username}`, `check_username`:
  `[a-z0-9][a-z0-9._-]{0,63}` after trim and lower-case; creates and chooses it; 409 "That address is taken. Try
  another name."); `PUT /inbox` (`{inbox_id}`; 404 "No such inbox for this key."); `GET /messages?page_token=` (50
  a page; 409 "AgentMail is not set up." unless enabled; a token over 1,000 characters is 422; an inbox AgentMail no
  longer has is 409 "ZenBot's inbox is no longer in this AgentMail account. Choose another in Settings.", a key that
  may not read it 403 "The saved key cannot read ZenBot's inbox. Save an organization API key in Settings.");
  `GET /messages/{id}` (404 "No such message."). Elsewhere: `refused` is 429, a saved key AgentMail now refuses is
  401, other kinds 502. `PUT /key` catches every exception (the key is in its frames) and answers 500 with the class
  name logged. Half a UTF-16 pair in an answer (an emoji cut short) is dropped like a control character, since no
  JSON answer could carry it on. The only POST takes a JSON body, so a cross-site page cannot send it without a
  preflight.
- **Ledger.** Kind `mail`, events `key_saved` (with `inbox_cleared`), `key_forgotten`, `inbox_created` and
  `inbox_chosen` (with ZenBot's own address). Never a subject, sender, preview, body or key. Reading the inbox writes
  no entry. A route that may hold the key logs an exception's class name only.
- **The GUI.** The page shows a message as a plain text child in a `white-space: pre-wrap` block: never its HTML,
  never through Markdown, so links are not clickable and nothing remote loads. The inbox is read when the page opens
  and on Refresh, never on a timer. The status is read when the harness comes online and after each change in the
  settings section; when AgentMail stops being enabled while its page is open, the app goes to Overview. In the
  settings section the inbox radios only mark a pick (arrow keys move it) and "Use this inbox" makes the change
  (`mail.js` `canUseInbox`), so a keyboard user does not choose every inbox on the way to one.
- **Packaging.** `src/mail` is listed in `electron-builder.yml` `extraResources` like every other harness package.
- **Bots and AgentMail (a known residual risk, the owner's accepted call on 2026-11-21).** There is no mail tool,
  no catalog entry and no memory ingest. But the harness API has no authentication, so a bot with `shell` (curl) or
  the `browser` server can call the `/agentmail` routes on `127.0.0.1`: list and read ZenBot's mail, list the
  account's inboxes, create an inbox in the owner's AgentMail account (up to the plan's limit), switch ZenBot's inbox
  or forget the key. It cannot read the key: no route returns it, and `agentmail_keys.json` is in the private
  settings folder, where a `shell` command that names it opens an approval. Mail read this way is stranger-written
  text reaching the bot as tool output. Closing it needs authentication across the whole harness API, which is not
  built. Until then, do not describe the mail as out of bots' reach.
- **Unverified.** Nothing here has reached AgentMail: the tests use `FakeAgentMail`, and the first real key save is
  the first live check. How AgentMail answers a create whose name is taken (409, or 400/422 with which code) is
  assumed. Whether an inbox-scoped key can list inboxes is unknown (the settings ask for an organization key when it
  cannot). The settings section and the AgentMail page have not been seen on screen by anyone: only `npm test` and
  the build have run them.

Rules for contributors: no mail tool for bots, no catalog entry, and no memory ingest of mail without revisiting the
residual risk above. Never render a message's HTML or load anything from it. The key never goes in a route answer,
a log line, an exception message, a ledger entry or a URL, only in the request's `Authorization` header. No request
to a host other than `api.agentmail.to`, and no 3xx followed. Every `/agentmail` POST takes a required JSON body. No
mail content in a ledger entry. Keep both files in `vault-settings/`. Tests never touch the network: the client takes
an `httpx.MockTransport` (`FakeAgentMail`). Tests: `tests/mail/test_settings.py`, `test_agentmail.py`,
`test_service.py`, `test_routes.py`; GUI `tests/gui/mail.test.mjs`, `mail-api.test.mjs` and `settings-index.test.mjs`.

### Sonos

ZenBot can play music on the Sonos speakers on the user's home network, through [SoCo](https://github.com/SoCo/SoCo)
(`soco==0.32.2`, MIT). It is a built-in feature, not an MCP server: the `sonos` tool group (`src/sonos/tools.py`) exists
only while a speaker answers. The music it plays is the user's Sonos Favorites and Sonos playlists, nothing else.
Settings > Connected apps has a Sonos section (`SonosSettings.jsx`). Design:
`docs/superpowers/specs/2026-11-21-sonos-control-design.md` (git-ignored, on the computer the work was done on).

- **Where things live.** `vault-settings/sonos.json` (`SonosSettings`: `enabled`, default true; `max_volume`, a whole
  number from 0 to 100, default 60; `known`, the speaker addresses last found; no secret). Never in the vault. The
  service (`src/sonos/service.py` `SonosService`) is built by `Harness` (`Harness.sonos`) and started in the `lifespan`;
  there is no network before that. SoCo is reached only through `src/sonos/devices.py` (`SocoSpeakers`, behind the
  `Speakers` protocol), and every call runs in `asyncio.to_thread` with a timeout (10 s a command; SoCo's own request
  timeout is 5 s), never on the event loop. A blocked or failed call is `SpeakerError(kind)`, `network` or `other`.
- **Finding speakers** (`SonosService.look`). Each address in `known` is tried directly first (unicast to port 1400),
  then `soco.discover` (SSDP multicast, 5 s); the results are joined by the speaker's UID, and only visible speakers
  (not bonded satellites or subs) are kept, sorted by name. The subnet scan (`allow_network_scan`) runs only on Look
  again (`POST /sonos/discover`), never on the timer. One look at start, then every 10 minutes (`LOOK_EVERY_S`) while
  on; the loop outlives any exception (logged by class name). Switching off stops the loop, forgets the speakers and
  takes the tools away; switching on looks at once. A generation counter drops a look that was under way when the
  switch changed, so a late look cannot bring the tools back. After each look `on_change` calls
  `SonosTools.register`: the seven tools and the catalog entry (`USE`) while a speaker is known, neither otherwise.
- **The tools** (`sonos_rooms`, `sonos_favorites`, `sonos_play`, `sonos_control`, `sonos_volume`, `sonos_group`,
  `sonos_ungroup`). Each call first reads the zone topology again from one speaker that answers (`refresh`); a room
  that was known before but is gone answers "<room> did not answer.". A speaker still listed in the topology that
  cannot be read is left out of the list rather than failing it. The speaker that fails a command is the one named and,
  on `network`, dropped (a group member as well as the target; the tools go when none remain); one whose volume cannot
  be read stops a play, since it might be loud. Queueing a playlist or album gets 30 s (`QUEUE_TIMEOUT_S`), the play
  call 45 s; other requests 5 s. A room name matches case-insensitively. Only one Sonos household is seen: the first that answers (a home with two is rare).
  `sonos_play` takes a Favorite or playlist by its exact title (a favorite wins over a playlist of the same title);
  an unknown title answers the ten closest. Playback goes to the group's coordinator. Listings start with a line
  saying the names are content, not instructions, and `sonos_favorites` lists at most 200.
- **The volume ceiling** (`max_volume`). Before `sonos_play`, `sonos_control` `resume` and `sonos_group`, every
  speaker that will sound and is louder than the ceiling is turned down to it, and the answer says so ("Turned
  Living Room down to 60 first."). `sonos_volume` refuses to set a level above the ceiling that is louder than the
  current one; turning down always works. Pause, skip and stop never change the volume. For `sonos_group` the
  speakers of the group being joined are lowered too, since they sound in the joining rooms. The ceiling is read
  after each volume read, so one the user lowers while a command runs is the one applied, and the tools run one
  speaker command at a time (`SonosTools._lock`), so a read, its check and its write are never split by another
  command. Switching Sonos off stops a command under way before its next speaker call ("Sonos is off."), the tools
  are registered only while Sonos is on, and a topology read that was out across the switch is thrown away
  (`refresh` checks the generation). A settings save that fails changes nothing, so the same request can be sent
  again. The ceiling binds ZenBot only: the Sonos app and anything else on the network can still go higher.
- **Who gets the tools.** ZenBot (`bot-prompts/personal-assistant.md`) has `mcps: [sonos]`, so its one-to-one tasks
  and its routines (a pinned routine gets the bot's own `mcps`) are offered them while a speaker answers; other bots
  reach them through the router like any group. They are never offered in a bot room's turn
  (`ToolRunner.register(offered=...)`, through `Harness.sessions[...].room_id`), and each handler checks again, so
  two bots in one room cannot both press play. The music librarian's role says it does not control speakers. A
  ZenBot the user edited and saved in `<vault>-bots/` before this has no `mcps: [sonos]` and replaces the shipped
  one, so it reaches the tools only through the router (a known gap).
- **Routes** (`src/sonos/routes.py`, under `/sonos`): `GET /status` (`enabled`, `max_volume`, `rooms` (each `name`,
  `group`, `model`, `state`, `title`), `looking`, `last_look_at`, `error`); `PUT /settings` (`{enabled, max_volume}`,
  422 with the sentence of `check_volume` outside 0 to 100); `POST /discover` (`NothingIn`; a look with the scan, in
  the background; 409 "Sonos is off." or "Already looking for speakers."). The Sonos page's routes, each 409 "Sonos
  is off." while off: `GET /rooms` (`{rooms, max_volume}`; each room `name`, `group` (its lead's name), `lead`,
  `model`, `state`, `title`, `artist`, `volume` or null when it cannot be read); `GET /favorites`
  (`{favorites: [{title, kind}]}`, sorted, at most 1,000); `POST /play` (`{rooms: [1 to 32 names], name}`), `POST
  /control` (`{room, action}`, an action of `ACTIONS`) and `POST /volume` (`{room, level}`, 0 to 100), each answering
  `{message}`. A command the speakers or the rules refuse (an unknown Sonos room or favorite, a speaker that did not
  answer, the ceiling) is 409 with the sentence as `detail`; a malformed body is 422. Every POST takes a required
  JSON body; `tests/sonos/test_routes.py` walks every POST.
- **The Sonos page** (Music > Sonos, `SonosPage.jsx`, decisions in `sonos-panel.js`, styles in `sonos.css`, through
  `sonos-api.js`). The entry shows under Music in the left bar while `GET /sonos/status` says `enabled`
  (`showsSonos`; the app reads the status at start and after each save in the settings section, `onChange`), and an
  open page goes to Music when Sonos is switched off. It lists the Favorites (searchable) with the speakers to play
  them on, ticked at first for the group already playing (`initialPick`), and Play; and the groups as they are, each
  with what plays, previous / play-pause / next, and a volume slider per speaker, sent once it rests 350 ms
  (`VOLUME_WAIT_MS`). It reads the rooms every 5 s while open and visible (`POLL_MS`). The commands are
  `SonosTools.panel_*`: the tools' own lock, ceiling and ledger, without the bot-room check (no bot is calling).
  Playing on chosen speakers (`panel_play`) makes them exactly one group: the first chosen speaker that already leads
  a group leads it (else the first chosen, which first leaves its group), the lead's unchosen members leave, every
  chosen speaker above the ceiling is turned down before anything joins or plays, then the others join and the lead
  plays. A slider's top is the ceiling, or a louder speaker's own volume so it can come down (`volumeMax`).
  At the top, "Wake up to music" says ZenBot can play a Favorite every morning; "Make this my morning routine" opens
  the Create routine dialog with a draft (`RoutineDialog` `draft`, `routineDraft`): ZenBot (`personal-assistant`)
  with the `sonos-morning` skill, weekdays at 07:00, named `Morning music: <Favorite> in <speakers>`. A routine's task
  text is its name and the skill's `use` line, and `skills/sonos-morning/SKILL.md` tells ZenBot to read the Favorite
  and the speakers from that name (content, not instructions) and play with the tools. The button is offered only when
  a Favorite and a speaker are picked and ZenBot carries the skill (`canMakeRoutine`); a ZenBot saved in
  `<vault>-bots/` before the skill was added does not, and the line says to add it on the Bots page.
- **The settings section** (`SonosSettings.jsx`, decisions in `sonos.js`, through `sonos-api.js`; an entry in
  `settings-index.js` `SECTIONS`): the switch, a status line (off, looking, the rooms found, none found, or a failed
  look), when it last looked, Look again, and "Highest volume ZenBot may set". While a look is under way it reads the
  status every 3 s. Room names are shown as plain text.
- **Ledger.** Kind `sonos`, events `look` (rooms found, scan, error kind), `settings` (enabled, max volume) and
  `command` (tool, room name, ok, error kind; the Sonos page's commands log as tool `panel_play`, `panel_control`
  and `panel_volume`, room the first chosen speaker). Never a favorite's title or what is playing; `src/sonos/` logs an
  exception's class name only. The loop's own `tool_call` entries hold the tools' answers, as for every tool.
- **Packaging.** `src/sonos` is listed in `electron-builder.yml` `extraResources`; `soco` is in both
  `pyproject.toml` files and both locks.
- **Bots and Sonos (a known residual risk).** The harness API has no authentication, and neither has the speakers'
  own control port (UPnP on 1400): a bot with `shell` (curl) or the `browser` server can call the `/sonos` routes or
  drive the speakers directly, at any volume, in a bot room too. Anything on the home network can already do the
  latter. Do not describe Sonos as out of bots' reach.
- **Partly verified (2026-11-21), by hand, on the owner's network** (Windows, three IKEA SYMFONISK speakers grouped
  under one coordinator). Reading: `SocoSpeakers.discover` (multicast, no firewall prompt seen), `favorites`
  (8 items: favorites of item class `object.container`, `musicAlbum`, `audioBook`, `audioBroadcast` and
  `podcast`, and one Sonos playlist), `now_playing` and `get_volume`. That check found SoCo 0.32 keeps
  `get_sonos_playlists` on the speaker, not the music library, which the stand-ins had hidden. Then, through the Sonos
  page (the owner, the same day): a Favorite played on all three speakers (`panel_play`), pause, skip to the next
  track and a volume change from a slider all worked on the real speakers. Which favorite kind was played was not
  recorded, so `play` for each kind (a service `object.container` may not take `add_to_queue`) is still unchecked;
  so are resume, previous and stop. The three were already one group, so `panel_play` may not have had to regroup:
  how real speakers take its unjoin-then-join from a different grouping (a coordinator leaving a group it leads
  hands the group to another member) is unchecked. The bot tools (`sonos_*`) have not been run against real speakers,
  only the page's commands, which share their code; nor have grouping with bonded sets (stereo pairs, home-theatre
  subs), the known-address path on a later look, or the settings section on screen. The morning-music skill has
  never run with a model.

Rules for contributors: SoCo only behind `devices.py`, every call through `SonosService.call` (a thread and a
timeout). No favorite title, track or artist in a ledger entry the Sonos code writes or a log line (the loop's own
`tool_start` and `tool_call` entries hold every tool's arguments and answer, these included: see Ledger above). Keep `sonos.json` in `vault-settings/`.
Never offer the tools in a bot room's turn, and keep the handler's own check. Never play, resume or group without
first lowering every speaker above the ceiling. Every `/sonos` POST takes a required JSON body. Say "Sonos room" or
"speaker" where a bare "room" could be read as a bot room. Tests never touch the network: `tests/conftest.py`
`_sonos_inert` swaps the real speakers for ones that find nothing, and `tests/sonos/` passes `FakeSpeakers`. Tests:
`tests/sonos/test_settings.py`, `test_devices.py`, `test_service.py`, `test_tools.py`, `test_panel.py` (the page's
commands), `test_routes.py`, the prompt checks in `tests/test_bot_prompts.py` (ZenBot carries `sonos-morning`), the
routine in `tests/test_task_loop.py`; GUI `tests/gui/sonos.test.mjs`, `sonos-panel.test.mjs`,
`sonos-api.test.mjs` and `settings-index.test.mjs`.

### Wi-Fi speakers

Settings > Wi-Fi speakers finds the AirPlay, Chromecast and DLNA speakers on the home network and keeps one default
speaker overall. This slice only finds them: nothing is played, nothing is paired, and no bot has a tool for them.
Reading announcements and digests through the default speaker comes later. Design:
`docs/superpowers/specs/2026-11-21-wifi-speakers-design.md` (git-ignored, on the computer the work was done on).

- **Where things live.** `vault-settings/wifi_speakers.json` (`SpeakerSettings`: `airplay`, `chromecast`, `dlna`, each
  default false; `default`, a speaker key or null; `known`, per type the speakers its last good look found, at most 64,
  each with its address; no secret; tmp-then-replace). Never in the vault. A broken or hostile file reads as the
  defaults, entry by entry. The service (`src/speakers/service.py` `SpeakerService`) is built by `Harness`
  (`Harness.speakers`) and started in the `lifespan` after Sonos; nothing listens on the network while every switch
  is off.
- **A speaker** (`settings.Speaker`): key `<protocol>:<id>`, the id its library would play to (pyatv's `identifier`,
  the Cast UUID, the DLNA UDN); name and model with control characters (U+2028 and U+2029 too) removed, trimmed and
  cut to 120 characters (`clean`); the address, which never leaves the harness.
- **Finding** (`src/speakers/finders.py`; each finder answers a list or raises `FindError(kind)`, `network` for an
  `OSError` or a timeout, else `other`):
  - AirPlay: `pyatv.scan` for AirPlay and RAOP with pyatv's own scanner, which sends its queries and listens for the
    timeout (given an `aiozc`, pyatv only reads that zeroconf's cache, which nothing fills); a device is kept only
    when it offers RAOP, the audio service (a video-only or remote-only one is not a speaker).
  - Chromecast: PyChromecast's `discover_chromecasts`, which blocks, in a thread, on a zeroconf it makes for that
    look; every cast type is kept (a speaker group is a place to play); the browser is always stopped, which closes
    that zeroconf (pychromecast's `stop_discovery` closes whatever zeroconf it was given, so never hand it one to
    share).
  - DLNA: an SSDP search for `urn:schemas-upnp-org:device:MediaRenderer:1` (async-upnp-client), then each
    responder's description, read once: only `http`, only on the address that answered the search (`_host`), never
    through a proxy (`trust_env=False`), uncompressed only (`Accept-Encoding: identity`, any other `content-encoding`
    refused), no 3xx followed, at most 262,144 bytes and 4 s (`DESCRIPTION_TIMEOUT_S`, for the whole read), parsed
    with defusedxml (no entities). A responder that fails any of these, or raises anything, is skipped; the others
    still list. Name `friendlyName`, model `modelName`, id the UDN.
  - Nothing is kept listening between looks: each look makes and closes what it needs.
- **Looks** (`SpeakerService`). Each type is looked for on its own task with its own generation: at start for the
  types that are on, every 10 minutes while on (`LOOK_EVERY_S`), on Look again, and at once when its switch goes on. A
  finder is cut off at 15 s (`FIND_TIMEOUT_S` 5 plus `FIND_SLACK_S` 10) as `network`; one type failing or hanging
  never holds up another (a Chromecast look cut off this way keeps its thread until pychromecast's own timeout ends
  it, then cleans up). A good look replaces that type's list (at most 64, the default first) and its `known`; a failed one keeps the list it had
  and sets `error`. Switching a type off bumps its generation (a look under way is thrown away), cancels its look and
  drops its speakers from `known`. Before a type's first look its remembered speakers stand in, so the lists and the
  default's name show at once.
- **The default.** One speaker overall, picked from the lists (a key in no list is 422 "Pick a speaker from the
  lists."); null clears it. Its `known` entry is always kept: a look that misses it, or its type switched off, leaves
  it the default, shown by its last name with `found: false` or `protocol_on: false`. It is kept first in every list,
  so the cap of 64 never drops it.
- **Also in Sonos.** A speaker whose address is one of the Sonos rooms' (`Harness._sonos_addresses`, empty while Sonos
  is off) is marked `also_sonos`, worked out when the status is asked for. A SYMFONISK shows up both ways.
- **Routes** (`src/speakers/routes.py`, under `/speakers`; no answer carries an address): `GET /status`
  (`{default: {key, name, protocol, found, protocol_on} | null, protocols: {airplay|chromecast|dlna: {on, looking,
  last_look_at, error, speakers: [{key, name, model, also_sonos}]}}}`); `PUT /settings` (`{airplay?, chromecast?,
  dlna?}`; a failed save changes nothing and answers 500); `PUT /default` (`{key}`, required, at most 300 characters, or an explicit null to clear; `{}` is 422);
  `POST /look` (`NothingIn`; 409 "No speaker type is switched on.", or "Already looking for speakers." when every type
  that is on is already looking). The only POST takes a required JSON body; `tests/speakers/test_routes.py` walks it.
- **The panel** (`WifiSpeakers.jsx`, decisions in `wifi-speakers.js`, through `speakers-api.js`; a `speakers` entry in
  `settings-index.js` `PANELS` and `SECTIONS`, its icon in `SettingsHome.jsx`): the default line, Look again, No
  default, when it last looked, and per type a switch, a status line and a radio per speaker. A radio saves the
  default at once; the radios stay usable while it saves (a disabled radio loses keyboard focus). Picks are written
  one at a time, a pick made meanwhile waiting and only the newest waiting one sent (`createPickQueue`), so the harness
  saves them in the order made; when the last write fails the panel reads the status again. Under the heading: "Turning a speaker type on makes ZenBot listen on
  your home network. Windows may ask whether to allow it." While any type is looking it reads the status every 3 s, and every 30 s while any type is on (the harness looks again
  on its own every 10 minutes); only the newest status read is used.
  Names and models are plain text.
- **Ledger.** Kind `speakers`, events `look` (protocol, speakers found, error kind), `settings` (the three switches)
  and `default` (the protocol, or empty when cleared). Never a name, model or address; `src/speakers/` logs an
  exception's class name only.
- **Packaging.** `src/speakers` is in `electron-builder.yml` `extraResources`; `pyatv==0.18.0`, `pychromecast==14.1.1`
  and `async-upnp-client==0.49.0` (bringing zeroconf) are in both `pyproject.toml` files and both locks.
- **Bots and Wi-Fi speakers (a known residual risk).** There is no speaker tool. But the harness API has no
  authentication, so a bot with `shell` (curl) or the `browser` server can call the `/speakers` routes: read the
  names, switch a type on or off, change the default and start a look. It cannot read an address from them. The
  speakers' own control ports are open to anything on the home network already. Do not describe the speakers as out
  of bots' reach.
- **Unverified.** No real device has been found: the tests use stand-ins and fakes. Whether Windows asks about the
  firewall when a type is switched on, how Bonjour on the machine shares the mDNS port, and a Linux install of the
  three libraries are unchecked. The panel has not been seen on screen: only `npm test` and the build have run it.

Rules for contributors: only `finders.py` imports pyatv, pychromecast, async_upnp_client or zeroconf. No address in a
route answer, a ledger entry or a log line, and no name or model in a ledger entry or a log line. The DLNA description
is fetched only from the responder's own address, over `http`, with no proxy, no compression, no redirect followed and the size
and time limits. Never hand pychromecast a zeroconf that something else uses. Never
listen on the network while every switch is off. Keep `wifi_speakers.json` in `vault-settings/`. Every `/speakers`
POST takes a required JSON body. Tests never touch the network: `tests/conftest.py` `_speakers_inert` swaps the real
finders for ones that find nothing, and `tests/speakers/` passes its own. Tests: `tests/speakers/test_settings.py`,
`test_finders.py`, `test_service.py`, `test_routes.py`; GUI `tests/gui/wifi-speakers.test.mjs`,
`speakers-api.test.mjs` and `settings-index.test.mjs`.

### Reading replies aloud

A Read aloud button in the chat header reads each new reply aloud on the computer's own speakers. The Narrator bot
rewrites the reply for the ear, then a voice speaks it: a speech model on a provider (OpenRouter's text-to-speech), or
Kokoro, a voice made on this computer. Wi-Fi speakers (AirPlay, Chromecast, DLNA) are found (`### Wi-Fi speakers`) but
not yet played to. Designs: `docs/superpowers/specs/2026-11-21-narrator-speech-design.md` and
`docs/superpowers/specs/2026-11-22-local-speech-kokoro-design.md` (both git-ignored, on the computer the work was done
on). `docs/superpowers/specs/2026-11-22-airplay-read-aloud-design.md` (playing on a Wi-Fi speaker) is not built; it will
sit on `Reading.wav()` and needs the two edits the Kokoro design lists. A Credits panel in Settings linking Kokoro,
kokoro-onnx and phonemizer with their licences is planned and not built.

- **The Narrator** (`bot-prompts/narrator.md`, `narrates: true`, set only in the file; `bot_prompts.parse` refuses it
  beside `studio`, `places`, `closed`, skills or mcps). It is asked directly with one completion
  (`generation/speech.py` `narrator_messages`: its prompt as the system message, the reply fenced in the user message
  as content, the fence longer than any backtick run in the reply), on its own model (Edit bot) or the chat model
  (`Harness._model_for`). No task, session, tools or conversation, so dreaming never reads a reply as the user's words.
  `POST /bots/{id}/tasks` answers 409 for it, a routine on it fails, the orchestrator never asks about it and it never
  joins a room (`RoomService.joinable`). Its chat page shows a note instead of the composer. With Kokoro the Narrator
  still runs on its model, so the reply's text still goes to that model's provider.
- **Speech models** (`SpeechConfig.provider`). `openrouter` (any provider-store entry of kind `openrouter`):
  `src/providers/speech.py` `OpenRouterSpeech`, `POST /api/v1/audio/speech` with `response_format: "pcm"` (Gemini TTS
  answers only PCM), wrapped in a WAV header (`pcm_to_wav`: 24 kHz, 16-bit, mono, assumed); a 200 answer with a JSON
  content type is refused as no audio (`OpenRouterHttp.post_bytes`). `kokoro`: a reserved provider id
  (`generation/speech.py` `LOCAL`), never looked up in `providers.json` (a provider entry of that id changes nothing),
  with the fixed model `kokoro-v1.0` and no key. Both speak 24 kHz mono 16-bit audio.
- **The Kokoro model files** (`src/kokoro/model.py`, `KokoroModel`), two files in `<vault>-models/kokoro/`, pinned in
  code by URL, size and SHA-256, from `https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/`:
  `kokoro-v1.0.onnx` (325,505,369 bytes, `beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a`) and
  `voices-v1.0.bin` (28,214,398 bytes, `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d`). Download:
  one at a time, in the background, `httpx` streaming through a client the download builds for itself
  (`KokoroModel._fetch_all`: `trust_env=False`, so no proxy or certificate setting from the environment applies, and
  `follow_redirects=False`) with 30 s timeouts, not a client shared with the rest of the harness; redirects are
  followed by hand, at most 5, a relative `Location` resolved against the address it came from, and only to `https`
  on `github.com`, `objects.githubusercontent.com` or `release-assets.githubusercontent.com`, with no user info and on
  the default port (`_allowed`), anything else fails as `source_changed`; written to `<name>.part` (new, not
  through a link), stopped the moment it passes the pinned size, hashed as written, renamed into place only when size
  and hash both match, the part file removed on any failure; a file already in place with the right size and hash is
  kept. Error kinds: `network`, `refused` (403, 429), `source_changed`, `disk` (an `OSError`, or under 1 GB free on the
  drive) and `provider` (any other status). "Ready" means both files in place with the pinned sizes; the hash is checked
  again the first time the engine loads them in a harness run (a mismatch deletes the file and the state goes back to
  absent). A file whose hash matched is remembered with its size and mtime (`_verified`, the stat taken before hashing
  and kept only when it is the same after), and `verify()` and a download's "already in place" check hash it again
  whenever either has changed since. `voices-v1.0.bin` is a numpy archive that `kokoro-onnx` loads with pickle off (numpy's default): do not
  change that.
- **The engine** (`src/kokoro/engine.py`, `KokoroEngine`). The only module that imports `kokoro_onnx` and `onnxruntime`,
  and only inside its load function (`real_load`), so a harness that never speaks locally never imports them; numpy too
  is imported only inside `speak`. `real_load` holds the library's logger (`kokoro_onnx`) at WARNING, inside a
  `suppress(Exception)` so it cannot fail the load: the library sets that logger from the generic `LOG_LEVEL`
  variable, and at DEBUG it writes the phonemes of the spoken text to stderr. `speak(text, voice)` makes one piece of
  text as 16-bit mono 24 kHz PCM. The model loads on first use (CPU provider only) and is dropped after `IDLE_S` (10
  minutes) without a call.
  - *One at a time.* Each load and each synthesis runs in a job (`_run`): a task of its own that takes the one lock,
    runs the work in a thread, waits for the thread to end whatever happens to it (`_thread`, which waits through any
    cancel of the job itself, as at loop shutdown; the first load's hash check runs in `KokoroModel.verify`'s own
    thread, which a cancel of the job at shutdown would not wait for) and only then releases the lock. Nothing cancels a
    job that holds the lock except the loop's shutdown. The caller awaits the job through
    `asyncio.shield`. So one load or synthesis runs at a time, harness-wide, and `unload()`, the idle unload, `close()`
    and `DELETE /speech/local/model` (which calls `unload()` first) wait, through the lock, for the thread in flight.
  - *A caller that goes away.* uvicorn announces ASGI spec 2.3, so Starlette runs `POST /speech`'s body in an anyio
    task group, and when the app closes the connection its cancel scope cancels the body again on every loop turn: a
    cancelled caller can await nothing. It returns at once and the job is left alone. A job that already holds the lock
    runs to its end: the piece in flight is finished and its audio thrown away; the next reading (or sample, unload or
    removal) waits for it. A job still waiting for the lock is cancelled and starts nothing. A synthesis whose caller
    went away while the model was loading is not started; a model loaded for a caller that went away is kept, so the
    next call does not load it again. Every call, even a failed or abandoned one, starts the idle count again; an idle
    unload that fires while a job runs waits for it and is then replaced by the job's new count. `close()` (harness
    shutdown) cancels the jobs still waiting, waits for the one in flight, and arms no new idle count.
  - *Errors.* A failure of the library or onnxruntime (to import, to load, to make a piece) is `EngineFailed`, a
    `ProviderError` whose message is the exception's class name; it is raised outside the handler, so it holds no
    reference to the library's exception, whose message can embed the text (`kokoro_onnx` puts the text in its
    `ValueError`s). Each job's outcome is read in a done callback, and the thread's exception is read inside the job, so
    a failure whose caller went away never reaches the event loop's exception handler; the only log line is the job's
    warning with the class name.
  - The voices are a fixed table of Kokoro's 28 English voices (American `a*`, British `b*`, female `*f_`, male `*m_`);
    nothing reads names from the voices file, and a voice not in the table is refused before the engine is touched.
    Text is cleaned first (control characters, U+2028 and U+2029 removed, whitespace collapsed); a piece empty after
    that is skipped.
- **Splitting** (`generation/speech.py` `split`). The Narrator's answer (at most 4,000 characters) is split at sentence
  ends and line breaks, in order: the first piece is the first sentence alone so speech starts fast, later pieces join
  sentences up to about `PIECE_AIM` (300) characters, no piece is over `PIECE_MAX` (400) (a longer sentence is cut at a
  comma, else a space), and nothing is dropped. A first sentence under `PIECE_MIN` (12) characters ("Dr.", "1.") takes
  the sentences after it until it reaches 12 (within 400), and then any piece still under 12 is joined to the next
  piece, else the one before, when the two fit in 400 (`_join_short`). OpenRouter's one WAV is its reading's only
  piece.
- **The pause between pieces** (`Speech._local_pieces`). `kokoro_onnx` trims the silence at both ends of what it makes
  (it puts its own pause only between sentences inside a piece), and the app starts the next piece when one ends, so
  every Kokoro piece the reading yields but the first begins with `PIECE_PAUSE_S` (0.25 s) of silence: 6,000 zero
  samples (12,000 bytes) put before the PCM before it is wrapped as a WAV, so `Reading.wav()` carries them too. The
  pause goes before a piece, not after one, so the reading never ends on a pause, even when the pieces after the last
  one yielded come back empty, and no piece waits for the next to be made. A piece the engine answers empty for is
  skipped with its pause, so the pause is never a piece of its own. OpenRouter's one piece is untouched. The 0.25 s is
  a controller's choice, made without listening, for the owner to tune.
- **A reading** (`Speech.read`, awaited, then `Reading.pieces()`). `read` does every check and the Narrator call before
  the answer starts, so a failure with a known cause is a plain refusal with its sentence: off, no Narrator, no key
  (not asked for `kokoro`), the Narrator failing or answering nothing (502); for `kokoro` a model not ready is 409 "The
  voice for this computer is not downloaded. Download it in Settings > Harness Tools."; the engine is made ready inside
  `read` (library imported, hashes checked the first time, model loaded), a hash mismatch being that 409 and any other
  failure to start 502 "The voice on this computer could not be started. Remove it and download it again in Settings >
  Harness Tools."; a narration that is empty after cleaning is 502; for OpenRouter the speech call itself runs inside
  `read`, so a lapsed key or a rate limit still answers 502 `SPEAK_FAILED`. Any other exception inside `read` (in the
  Narrator's call, the engine's start or OpenRouter's call) writes the reading's ledger entry with its `stage` and the
  exception's class name as `error`, then goes on to the route, which answers 502 "Reading aloud failed. Try again in
  a moment." Only the synthesis of Kokoro's pieces happens in the stream. `Reading.pieces()` yields one complete WAV
  per piece for both providers; a failure after the first piece ends it by raising. `Reading.wav()` awaits every piece
  and answers one WAV (the PCM under one header), which `Speech.speak` returns and the AirPlay design will hand to a
  speaker. `active` is `enabled`, a provider that can speak, and for `kokoro` the model ready.
- **Settings** in `vault-settings/tools.json` under `speech` (`SpeechConfig`: `provider` (default `openrouter`),
  `model` (`google/gemini-3.8-flash-lite-tts`, or `kokoro-v1.0` for `kokoro`), `voice` (default `Zephyr`; `VOICE_RE`;
  for `kokoro` it must be in the voice table), `enabled`), the Read aloud card on Settings > Harness Tools; the key is
  the speech provider's, resolved per call (`Harness._speech_key_ref`). `PUT /settings/tools/speech` takes
  `provider: "kokoro"` without a provider-store entry, fixes the model, and may save it before the model is downloaded
  (Read aloud is then not active); a voice in the body is checked and never replaced. With no voice in the body the
  saved voice is swapped in two cases (`set_speech_tool`): whenever the provider the save ends with is `kokoro` (sent
  or already saved) and the saved voice is not a Kokoro voice, it becomes `af_heart`; when the saved provider is
  `kokoro` and the save names another, a Kokoro voice becomes `Zephyr`, and with no model in the body the model goes
  back to the default. The voice of the other side is not remembered. `GET /settings/tools` lists the tool with
  `kind: "tts"`, `providers` (the ids that can speak, `kokoro` first) and a `local` block: `state` (`absent`, `downloading`, `ready` or `failed`), `done`, `total`
  (353,719,767), `error` (a kind), `voices` (`name`, `label`, `accent`, `gender`) and `default_voice`.
- **Routes** (`src/harness/speech_routes.py`). `POST /speech` (`{text}`, 1 to 20,000 characters, else 422; the Narrator
  gets at most 12,000 and its answer is cut to 4,000) answers `200 application/x-zenbot-speech` with `nosniff` and
  `no-store`, whichever provider: a body of frames, each a 4-byte big-endian length then that many bytes of one WAV,
  and a frame of length 0 as the end mark, always the last thing on success; a body that ends without it means the
  reading failed part-way. No frame is over `FRAME_MAX` (64,000,000 bytes) and the app refuses a larger length as a
  broken stream. Refusals come before the response starts as a plain 409, 422, 502 or 503 with their sentences. When
  the app closes the connection the body is cancelled and no further piece is started; the piece already in the
  engine's thread is finished with the engine's lock held and its audio thrown away (see the engine above), and
  `SpeechResponse` finishes the reading's ledger entry whichever way the response ends. A piece whose WAV would pass
  `FRAME_MAX` ends the body before any of its bytes are sent, with no end mark and an entry with an error.
  `POST /speech/local/download` (`NothingIn`): starts the download in the background and answers the status; 409
  "Already downloading."; at once when ready. `DELETE /speech/local/model`: unloads the engine (waiting for a load or
  synthesis in flight), removes both files and any part file, answers the status; 409 "Wait for the download to
  finish." while one runs, and 409 "The voice could not be removed. Close anything using it and try again." when a
  file remains. `POST /speech/local/sample` (`{voice}`): speaks one fixed sentence
  held in code ("Good morning. Here is how this voice sounds when it reads a reply aloud.") with no Narrator and answers
  `audio/wav` with `nosniff` and `no-store`; 422 "Pick a voice from the list." for a voice not in the table, and for a
  voice that is missing, not a string, empty or over 40 characters (checked in the route, not by the body's model, so
  the 422 never echoes the body, an extra `text` field included); 409 with
  the not-downloaded sentence; it works whether Read aloud is on or off and takes no text.
- **The GUI** (`src/gui/speech.js`, `speech-settings.js`, `ReadAloudLocal.jsx`, wired in `app.jsx`). The toggle is
  remembered in `localStorage` (`openzenbot.speakReplies`). Only a session started from that chat while the toggle is on
  is read (`toSpeak`), never an old reply, a routine run or a room. `api.js` `speak(text, signal)` reads the framed body
  with the fetch reader (a linear parser, so chunk boundaries do not matter), yields one `Blob` per frame and throws when
  the body ends without the end mark; the player is a queue that plays a piece when the one before it ends. A newer
  reply, a new message, turning the toggle off or changing bot or conversation stops playback, frees every object URL
  and aborts the request, which ends the making of the rest. The page plays blob URLs, so `index.html`'s CSP allows
  `blob:` in `media-src`. A failure before any audio shows "Could not read this reply aloud." under the reply; one after
  some audio played shows "The rest of this reply could not be read aloud." ("played" is the player's `started()`: a
  piece of this reading began playing, not merely arrived; a late `play()` of a stopped piece counts for nothing); a
  reading reports one failure at most, a piece the browser cannot play and a stream that then ends early included. The card: "Speech model" is one select with a first group "On this computer" holding "Kokoro (free, no key)",
  then the provider groups. With Kokoro picked, a status line and button by state (not downloaded: Download; "Downloading…
  120 of 354 MB", read from the tools settings every 2 s, of which only the speech tool's `local` block is taken, and
  its `active` when the poll saw the same switch, provider and model (`withLocal`; a poll answer is dropped when a save,
  download, removal or newer poll came after it was sent, `toolSeq` in `app.jsx`); failed: a sentence per error kind and Try again; ready:
  "Downloaded." and Remove, which asks "Remove the downloaded voice? Read aloud will need it again."), a voice select of
  the 28 voices (American then British, female then male), **Play a sample** (the picked voice, saved or not; off until
  the model is ready) and a line saying the Narrator still rewrites the reply on its own model. The Kokoro voice and a
  provider's free-text voice are kept apart in the form (`speechForm`, `speechPatch`, `speechDirty`; leaving a saved
  Kokoro for a provider model, any voice typed is sent, even one spelled like the Kokoro voice, and a blank one is left
  to the route's default), and a sample that arrives after the card is gone or another voice is picked is dropped, and
  one playing then stops (`createSampler`, stopped by `ReadAloudLocal.jsx`). The status line is not a live region: a visually hidden
  one (`.sr-only`, `role="status"`) says `liveLine`, which is the status line except "Downloading…" while downloading,
  so a screen reader hears the start, the end and a failure but not each 2 s progress tick. The voice select takes its
  accessible name from its Field only. The card's error line is cleared when the model's state or the picked voice
  changes (`errorKey`), and Download sends one request however fast it is clicked (`oneAtATime`). The alert "<provider>
  is not a provider that speaks…" is decided on the saved tool, never on the pick in the select, and never shows when
  the saved provider is `kokoro` (`showsNoSpeakerAlert`). The Settings search finds the card by "Kokoro" and "on this
  computer" through the tool's `summary` (`liveSections`), and the Harness Tools panel's description names reading
  aloud.
- **Ledger.** Kind `speech`: `chars_in`, `chars_out`, `bytes`, `model`, `voice`, `provider`, `pieces` (made), `narrator_model`,
  `stopped: true` when the app closed the connection first, and on failure `stage` (`narrate` or `speak`) and `error`
  (a `failure_kind`, `empty`, or for an unexpected exception inside `read` its class name). Event `model` for a download or removal: `action` (`download`, `remove`), `bytes`,
  `ok`, `error`. A sample writes `sample: true`, the voice and `bytes`. Never the text, a path or a URL. With tracing on,
  the Narrator's call and the speech call are traced like every model call, the reply and the spoken text with them: a
  Kokoro piece's text goes in the trace's input (`Speech._synthesise`) with the voice.
- **Packaging.** `kokoro-onnx==0.6.1` and `onnxruntime==1.32.0` in both `pyproject.toml` files and both locks
  (`tests/test_packaged_deps.py` keeps them equal); `kokoro-onnx` brings `phonemizer` (GPLv3, which the owner accepted
  shipping), `espeakng-loader` (espeak-ng bundled in its wheel, nothing installed on the host) and `dlinfo`. `src/kokoro`
  is listed in `electron-builder.yml` `extraResources`. The model is fetched by the harness at the owner's request, not
  by setup, so `pins.json` is not involved.
- **Bots and local speech (a known residual risk).** The harness API has no authentication, so a bot with `shell` (curl)
  or the `browser` server can start the 354 MB download, remove the model, change the voice or the provider, and call
  `POST /speech` or the sample route, which uses the CPU for as long as the text lasts (bounded by the 4,000-character
  narration, one synthesis at a time). It cannot make the harness fetch anything but the two pinned files, and the
  sample route speaks only its fixed sentence. Do not describe local speech as out of bots' reach.
- **Known limits.**
  - A synthesis in a thread is not cancelled: after a stop, one piece (at most 400 characters) is still finished and
    thrown away, and the engine is busy until then, so a new reading or sample started at once waits for it.
  - `split` still ends a sentence at an abbreviation, and only text under 12 characters is joined to a neighbour:
    "Please call Dr. Smith about the results." gives the pieces "Please call Dr." and "Smith about the results.", so a
    pause falls after "Dr.".
  - Stopping while `Speech.read` is still awaiting the Narrator, or while the engine's start waits its turn, does not
    cancel that work: the response that notices the app closing the connection does not exist until `read` returns. The
    Narrator's call completes (one chat-model call) and the engine may load; nothing is spoken and no audio is sent.
    This was so for the Narrator before local speech too. Watching for the disconnect during `read` is not built.
  - The 354 MB hash is computed, and the file written, on the event loop in 1 MiB steps while downloading.
  - In a screen reader's browse mode the status is read twice: the visible line and the hidden live region.
  - Remove unloads the engine first, but a new reading or sample between the unload and the delete can load it again
    (the result is a 409 or a failed piece).
  - A client that goes away just after the end mark is sent can be logged as `stopped`.
  - Synthesis is not byte-for-byte repeatable: two samples of one voice had the same length and different bytes (seen
    on 2026-11-22).
  - With an older harness or an empty voice list the voice select has no options.
- **Measured on 2026-11-22**, one run by hand against the real harness on a Windows 11 machine with 80 logical CPUs
  (the times flatter a laptop), a throwaway vault, Python 3.13.9 from `uv run` and the owner's OpenRouter chat model for
  the Narrator: the download of both files took 33 s (31 s the second time; GitHub answered the onnx file's release URL
  with a redirect to `release-assets.githubusercontent.com`) and both files had the pinned sizes and hashes with no part
  file left; a sample of 4.3 to 4.9 s of audio took 5.5 s the first time (model load and hash check) and 1.3 to 1.8 s
  after; a 1,254-character message read aloud came as 7 pieces and 71.6 s of audio in 27.8 s, with the response headers
  after 11.7 s (the Narrator's call and the engine's start; 8.3 s in a second reading, with the engine already loaded),
  the first frame 0.8 s later, and 3.6 s of audio in the first piece; stopping after the first frame left the harness
  healthy, a sample answered in 1.6 s straight after, and the ledger entry said `pieces: 1, stopped: true`; Remove
  deleted the files, a sample then answered 409 with the not-downloaded sentence, and a new download worked. Nothing of
  the feature was written inside the vault folder.
- **Unverified.** Nothing was heard: the audio was checked by its WAV header and duration, not by a person (the voices'
  quality, whether pieces join without a gap or a click, whether the 0.25 s pause between pieces sounds right, and
  whether the narration reads at a natural pace are not known). The measured run above was made before the engine's
  jobs, the pause between pieces, the short-sentence rule, the port rule and the card's later changes: those have run
  only in the tests (a fake voice library, `httpx.MockTransport`, node tests of the card's decisions) and the GUI
  build, not against the real model, GitHub or a screen reader. `real_load`'s logger change is not covered by any test
  (no test runs `real_load`) and has not run. Not tried: a laptop-class CPU (speed, and the memory use: about 420 MB
  loaded, measured in the spike before this was built), Linux, the installed app's environment (`kokoro-onnx` and
  `espeakng-loader` inside it, a user folder with non-ASCII characters, the .deb and Windows installer with
  `src/kokoro`), any language but English, and the Electron shell. In a plain
  browser against the real harness the card was seen in its not-downloaded, downloading and downloaded states, Play a
  sample made its request (200) with no error line, and Remove asked its confirmation (answered No). With no speech
  model enabled on a provider in that vault the select held only Kokoro, so the switch to a provider model and back (the
  voice field) was not seen on screen, and neither were the in-chat Read aloud button, the player and its queue, the
  stop on a new message, the two error lines or the Settings search. The app's player has never run against the real
  stream. No request reached OpenRouter's speech endpoint, so `google/gemini-3.8-flash-lite-tts` answering 24 kHz mono
  PCM and taking `Zephyr` is still from OpenRouter's docs. The Narrator prompt ran with a model for two readings, and
  its rewritten text was not read by anyone. Which Kokoro voices the owner likes is not known. The redirect host for
  `voices-v1.0.bin` was not recorded.

Rules for contributors: never give the Narrator a task, tools or a conversation, and never put the reply in its
system message. No text, spoken text, path or URL in a `speech` ledger entry, a log line or an error message; where the
text may be in a call's frames, log the exception's class name only. Keep `speech` in `vault-settings/tools.json` and the
model in `<vault>-models/`, in `private_folders`, never in the vault. Only `src/kokoro/engine.py` imports `kokoro_onnx`
or `onnxruntime`, only inside its load function, and the harness must start without them. Never use a model file whose
size and SHA-256 do not match the pin; keep the redirect rule (https, the three hosts, no user info, the default
port) and the part-file-then-rename download; never add a second source for the files or a way to fetch anything
else. Keep the download's own client (`trust_env=False`, redirects off): do not swap in a client shared with the rest
of the harness, which would bring the environment's proxy settings and its own redirect rule. `kokoro` stays a
reserved provider id, and the voice sent in a `PUT` is checked and never replaced. One load or synthesis at a time:
run each only inside an engine job (`KokoroEngine._run`) that holds the lock until its thread has ended, never await a
thread from a caller the route can cancel, and never let the library's exception or its message out of the job
(class name only). Keep `kokoro_onnx` and numpy imported only where they are now. The framed stream (media type, 4-byte length, end
mark, `FRAME_MAX`) is one format for the app and the route: change both sides and their tests together. Every POST takes a
required JSON body (`NothingIn` where it needs nothing). Tests never touch the network, never import `kokoro_onnx`,
never load a model and never download: `tests/conftest.py` `_kokoro_inert` makes the engine a harness builds load and
speak nothing, and `tests/kokoro/` passes a fake library. A test of a stop cancels the way production does (inside
`anyio.create_task_group()`, or `SpeechResponse` driven at ASGI spec 2.3), not with one `task.cancel()`. Tests: `tests/test_speech.py`, `tests/kokoro/test_model.py`,
`tests/kokoro/test_engine.py`, the `narrates` checks in `tests/test_bot_prompts.py`, `tests/test_rooms.py` and
`tests/test_orchestrator.py`; GUI `tests/gui/speech.test.mjs`, `speech-settings.test.mjs`, `api.test.mjs` and the CSP
check in `tests/gui/shell.test.mjs`.

### Design studio

A bot whose prompt sets `studio: true` (`bot-prompts/designer.md`) designs one self-contained HTML file per
design, shown live in the docked pane beside the chat (`src/gui/DesignPane.jsx`). The harness side is
`src/harness/design_studio.py` and `src/harness/design_systems.py`.

- **Where things live.** A design is `<vault>/designs/<slug>/<name>.html`, written by the bot through the
  `files` server (so inside the vault, where bots may write). The harness keeps its own numbered copies
  beside it: `<vault>/designs/<slug>/.versions/<n>.<name>` (`design_studio.snapshot`). Each attached design is
  one fixed copy, so version 1 stays version 1 after the bot revises the file. Only a file exactly two
  levels under `designs/` counts (compared resolved, so `..` and links cannot pass a file from elsewhere off
  as a design), and a `.versions` that is a symlink is refused. A studio bot's relative `designs/...` path in
  `attach_file` resolves against the vault root, then goes through the usual `attachable` check. No new
  write path outside `allowed_folders.reachable`: the harness writes only under `<vault>/designs/`.
- **The folder is the harness's pick.** In a studio bot's own conversation the harness chooses the design folder,
  not the bot (`Harness._design_folder`, `design_studio.fresh_folder`): at the conversation's first task it makes a
  new, empty `<vault>/designs/<slug>/` from the brief's words, taking `<slug>-2`, `<slug>-3`... when the name is
  already there, so a new design never writes over an old one. The slug is kept on the conversation
  (`design_folder`), and every task there is told it in the context (`design_studio.folder_block`). A conversation
  from before this keeps the folder of its newest design. `attach_file` there refuses a file in another design's
  folder. This is told and checked at attach, not walled: the `files` server would still let the bot write into
  another design's folder. In a room there is no such folder and the bot picks its own slug.
- **The preview.** The frame gets exactly `PREVIEW_CSP` (`api.py`; sandboxed, `default-src 'none'`, inline
  script and style, `data:` and `blob:` images, `data:` fonts, nothing loads from anywhere) plus one script,
  the pick bridge (`design_bridge.js`, added by `design_studio.with_bridge`). A file over
  `design_studio.BRIDGE_MAX_BYTES` (5 MB) or not HTML goes as it is, without the bridge. The pane shows a new
  revision when the task that attached it ends, not while it runs.
- **Bot-made pages are closed by default.** Every attachment a browser will run as a page (`api.CLOSED_SUFFIXES`:
  `.html`, `.htm`, `.xhtml`, `.xht`, `.shtml`, `.xml`, `.svg`, `.svgz`, `.mht`, `.mhtml`, any case), on the
  conversation and room routes alike, is served under `PREVIEW_CSP` with `nosniff`, whatever the query, so a page that
  navigates its own frame to the bare URL is still closed. Only the viewer (`MediaViewer`), when the user
  opens a page, asks for a token (`POST .../attachments/{id}/open`) and frames `...?open=<token>`, served
  `sandbox allow-scripts`. A token is in memory, bound to one attachment, valid 60 seconds and spent by the
  first GET that shows it (`api.OpenTokens`). A closed page cannot ask for one: its fetch is blocked by
  `default-src 'none'` and its forms by the sandbox. Thumbnails and the design pane never ask. Don't add a
  route that serves a bot-made page open without a token.
- **The GUI never trusts the frame.** A message from the framed page can only offer a pick (which element,
  its selector and a short text, each capped) or say it is ready; the GUI only ever adds a chip the user sees,
  or opens the editor on a slot the harness listed (see Direct changes). Nothing
  a framed page posts is sent to a bot, run or saved on its own. At most 20 comments go with a task; the
  note is 1000 characters, the text 160, the selector 300. The comment block (`design_studio.comment_block`)
  names the file relative to the vault, never a disk path. Its lines are also pinned as the task's
  constraints (the protected block), so a mid-task compaction cannot summarise them away; the pinned task
  stays the user's words. A bot reply can carry a question form (at most 5 questions); the answers go back
  as an ordinary message, without the scope-change check.
- **Electron.** `src/electron/main.cjs` cancels a subframe navigation (`will-frame-navigate`; a framed design
  can navigate its own frame, which `will-navigate` never sees) unless `frame-nav.cjs` allows it: the
  harness's own origin (`ZEN_HARNESS_URL`, default `http://127.0.0.1:8765`) or http(s) on `127.0.0.1`,
  `localhost` or `[::1]` (the memory dashboard). This guards the desktop app only; the harness's closed
  default above is what keeps a page off the network.
- **Pictures.** A design keeps its pictures as files in `<vault>/designs/<slug>/images/` (PNG, JPEG, WebP or
  GIF by first bytes, at most 1,500,000 bytes each, names `[a-z0-9][a-z0-9._-]{0,63}`), referenced as
  `<img src="images/<name>">`. The live file is never rewritten: `design_studio.snapshot` embeds them as `data:`
  URIs, through `design_images.embed`, only into the numbered copy, so the preview (which loads nothing) still
  shows them. Only an `<img>` whose `src` is written in quotes and starts `images/` is embedded; a CSS
  `url()`, a `srcset`, an unquoted `src`, a missing, oversized, non-picture or linked file stays as written, and
  so does a picture whose `data:` URI would take the copy past `BRIDGE_MAX_BYTES` (`embed`'s byte budget; each
  file is read once per page, the reason is "the design would be too large"), and `attach_file` names each such picture (`design_studio.embed_notes`) plus a copy over
  `BRIDGE_MAX_BYTES`. `images/` must be a real folder, a picture a regular file with one hard link, and a copy
  is never written through a link. `design_add_image(path, name?)` (`Harness.design_add_image`) copies a
  picture the bot may read (`attachable`) into the conversation's design folder and answers `images/<name>`,
  never a disk path; a taken name gets `-2`, `-3`.... It is listed only to a studio bot's one-to-one task in a
  conversation it owns that has a design folder (`ToolRunner.register(offered=...)`, `specs(caller=...)`;
  `Harness._studio_session`) and to an `images` helper turn (below; its answer there says to place the picture
  with `design_set_image`), and its handler refuses any other caller. A picture is written to a temporary name in
  `images/` and linked into place (where the drive has no hard links, written to its name directly and removed if
  the write fails), so a failed write leaves no part of a file; `design_images.load` opens without
  following a link and checks the open file. `design_studio.is_current` says whether
  a numbered copy still equals what `snapshot` would write now, pictures included.
- **Design systems.** A folder with `DESIGN.md` and optionally `tokens.css`: the shipped ones in
  `design-systems/` (`ZEN_DESIGN_SYSTEMS`), the user's in `<vault>/design-systems/`, which bots can write, so
  the files are untrusted text for a prompt. They grant nothing, so they may live in the vault. A conversation
  picks one (`PUT /conversations/{id}/design-system`, `{"slug": ...}` or `null`; `GET /design-systems` lists
  them with `source` `shipped` or `yours`), and the harness adds it to the context of every task there.
  Every file must resolve inside its own folder: a system is refused when a file resolves outside its own
  folder, is hard-linked, is over the size bound (40,000 characters for `DESIGN.md` and `tokens.css`
  together), or when `<vault>/design-systems` itself is a link. A pick that fails answers 422 with the
  reason. The pick also keeps the system's `source` (`design_system_source`): a folder of yours written later
  with the same slug replaces a shipped one, so a system whose source has changed since the pick is not
  used until it is picked again. A picked system that stops loading or has changed source is recorded in the
  ledger (an `error` entry, `where: design_system`) while the task still runs without it. The context block
  says the system governs looks only and ends with `End of design system: <name>.`.
- **From ZenBot.** When the orchestrator's best fit is a studio bot (`src/rooms/orchestrator.py`), no room opens:
  `POST /orchestrate` answers `{"outcome": "studio", "bot_id", "conversation_id"}` (a new conversation, or the bot's
  current one when it is empty) and the GUI opens that bot and starts the task there, where the pane, the forms and
  the comments are. A room that already matches the message still takes it, and a studio bot that is not the top
  fit joins a room as before. In a room a studio bot's `attach_file` follows the same rules as one to one
  (`Harness.attach_target`: `designs/...` against the vault, attached as a numbered copy), but a room has no
  pane, live forms or comments, and no `design_add_image`: there the Designer saves a picture into the design's
  `images/` folder with the files tools (same naming rule) and references it as `images/<name>`.
- **The Design page.** `GET /designs` lists every design made in a studio bot's own conversation, one entry per
  conversation and design with its newest numbered copy (names and ids, never a path), newest conversation first.
  A card's thumbnail is the attachment served closed (`?preview=1`), like any other thumbnail. A file attached
  in a room before designs there became numbered copies is not listed.
- **Studio helpers.** In a studio bot's own conversation that already has a design, one revision can go to a
  helper bot that places text or pictures while the harness rewrites the file. Told and checked, not walled: the
  `files` server would still let a helper write the design file.
  - *Who is a helper.* A bot whose prompt frontmatter has `places: [text]` and/or `[images]` (`bot_prompts.parse`
    refuses `places` on a `studio: true` bot; set only in the file; `GET /bots` returns it). Shipped:
    `bot-prompts/copywriter.md` (`text`) and `bot-prompts/darkroom-bot.md` (`images`).
  - *Routing* (`studio_help.pick`, called from `POST /bots/{id}/tasks`). Only for a studio bot's task in a
    conversation with a design revision, at least one helper loaded, a scorer, and `route` not `"owner"`. One scorer
    request (`purpose="studio_help"`) asks per helper whether it is clearly a better fit than the Designer. A helper
    takes the revision only as the single winner at or above `ZEN_STUDIO_HELP_MIN` (0.7) inside `ZEN_STUDIO_HELP_BAND`
    (0.1). A scorer error, a missing or malformed answer, two winners or a low score all give the task to the
    Designer; so does a worksheet with nothing in it, a working file that is not the newest numbered copy
    (`design_studio.is_current`), or a newest copy already over `BRIDGE_MAX_BYTES`. When the scorer answers, the scores are logged (`design_part`, event `route`); a scorer failure is an `error` entry (`where: studio_help`) instead. A form's answers skip
    routing: `answers_to`, the session id of the reply that carried the form, sends them to that bot. The GUI sends
    a form's answers alone, without the pending picks, and leaves the composer as it is (`design.js`
    `clearsComposer`, `picksSent`).
  - *One task at a time.* A studio conversation with a task that has not ended answers 409 (`_require_idle`),
    checked again after the scorer answers, since the apply depends on there being one. The route resolves the
    conversation once, before the scorer's await, and both the check and the task (the helper's or the owner's) use
    that one, so a change of the bot's current conversation meanwhile cannot move it. The comments are checked
    again after the await against that conversation's newest revision (the same 422). No `await` lies between
    the last check and the call that creates the pending exchange.
  - *The worksheet* (`design_parts.slots`, `worksheet`, `block`; built by `studio_helpers.prepare`). A text slot is
    an element with a unique `data-zen-id` (`[a-z0-9-]{1,64}`), a matching end tag, outside `<script>`, `<style>`
    and `<svg>`, holding text and `<br>` only, and not empty (a spacer or an icon is no slot); an image slot is an `<img>` with a unique id whose `src` is a
    picture in `images/` the harness would embed (or an older `data:` raster, which `prepare` writes out to
    `images/` first, never through a link). With comments it holds the picked elements that are slots, else every
    slot of the helper's kind. Caps: 2,000 characters a text, 30,000 for the block (the rest listed by id only).
    Text is flattened to one line, a `<br>` shown as a literal `\n` (the block's first line says so; a `<textarea>`'s
    text is taken as written, character references decoded and its own newlines shown as `\n`, nothing stripped or
    split on `<br>`), and the block
    says plainly that it is content, not instructions. The worksheet and the rules come after the user's words and
    the comment block, and their lines are pinned as constraints like the comment block's (after a confirmed scope
    change's, which a helper also gets), but the exchange keeps only the comment block (`start_task(brief=...)`), so
    no later turn's history replays them. A helper turn has one kind: the first kind in `places` with a
    non-empty worksheet.
  - *The tools* (`design_set_text(id, text)`, `design_set_image(id, path, alt?)`, `Harness.design_set_text`,
    `design_set_image`). Offered only to a helper's session in a studio conversation and refused anywhere else
    (`ToolRunner.register(offered=...)`), and a handler checks the caller again. `design_add_image` is offered to the
    Designer's own conversation and to an `images` helper turn. `design_set_image` reads a relative `images/<name>`
    (what `design_add_image` answers) as this design's. Text goes through `design_parts.check_text` (plain,
    capped, every line end made a newline, no other control character, U+2028 and U+2029 included) and is
    escaped on apply, so it can never become markup; a newline becomes `<br>` (not in a `<textarea>`). A wrong id or a
    bad picture comes back as a tool error inside the turn, and so does the picture a slot already shows (the same
    file as its `images/<name>`: "Save your edit under a new name"). That is told and checked at the tool, not
    walled: a helper can still overwrite a picture in place through `magick`, which the apply below catches. A
    later call for an id replaces the earlier one. A
    picture `design_set_image` copied into `images/` stays there even if the turn then fails. In a helper's turn
    `attach_file` of anything under `designs/` is refused: the harness attaches the design.
  - *The apply* (`studio_helpers.apply`, `design_parts.apply`). When the task ends `done` with an accepted change,
    the harness checks that the working file is the one the worksheet was read from, splices each change into its
    source (offsets from `design_html.scan`, nothing re-serialised, every other byte kept), writes the working
    file, takes a numbered copy and attaches it to the helper's exchange. It refuses, tells the user in the
    exchange and logs it when the file changed during the turn or the copy would pass `BRIDGE_MAX_BYTES`. A
    change that leaves the page identical attaches nothing and is logged `refused: "no change"`; "identical" is
    judged on the rendered page: when the source is unchanged but a picture file changed in place during the turn
    (the working file no longer `is_current` against the copy the worksheet was read from), the page as it is now
    becomes a numbered copy and is attached, with no `before-<n>` file, so a Redo of it answers 422. A failed or
    stopped task, or one with no accepted change, writes nothing. Ledger entries of kind `design_part` (`route`,
    `set_text`, `set_image`, `apply`, `redo`) carry ids, sizes and names, never the text or a disk path.
  - *Redo with the Designer.* Before each apply the harness keeps the working file as
    `<vault>/designs/<slug>/.versions/before-<n>.<name>` (one left with no copy `<n>`, from a crash, is replaced, by
    a rename, never through a link). A task with `redo_of` (the helper's session id, which must be the
    conversation's newest exchange) restores the working file from it (`studio_helpers.restore`) only while the
    working file still equals the helper's copy and the kept source with today's pictures still renders as the
    version the helper started from (a picture overwritten in place cannot be put back), else 422 and nothing changes,
    marks that exchange set aside (`Conversations.set_aside`) and goes to the Designer with the same message and
    comments, accepted against the copy the helper started from. The helper's exchange and its version stay in
    the list. `Exchange.bot` names the bot that answered, empty for the owner; `_conversation_view` returns it.
  - Don't let a helper's text, or the user's (Direct changes, below), reach the file except through
    `design_parts.check_text` and `design_parts.apply`. Don't offer `design_set_text`, `design_set_image` or `design_add_image` outside the turns named above, don't
    attach a design for a helper, and don't write a numbered copy or a `before-<n>` file through a link. Tests:
    `tests/test_design_html.py`, `test_design_images.py`, `test_design_parts.py`, `test_studio_helpers.py`,
    `test_studio_help.py`, `test_bot_prompts.py`; GUI `tests/gui/design.test.mjs`, `actions.test.mjs`, `api.test.mjs`.
- **Direct changes.** In the pane the user changes a design's words, pictures and tweak values, restores an older
  version, compares two, and exports one, with no bot turn. The frame only says what was clicked; the words, pictures
  and values are typed in the GUI's own controls and saved by the harness (`src/harness/studio_edits.py`,
  `design_tweaks.py`). Told and checked at the routes, not walled, as with the helpers. Design:
  `docs/superpowers/specs/2026-11-21-studio-direct-changes-design.md` (git-ignored, on the computer the work was done
  on). Handing a design to Claude Code ("Build as code") is not built; it is the next piece.
  - *Who may call.* Four routes in `api.py`, each for a studio bot's own one-to-one conversation (`kind == "bot"`)
    that has a design (`studio_helpers.newest_revision`), else 404 "no such design", and with no pending exchange,
    else 409 "Wait for the task to finish." (`_studio_design`; the GET routes too). Each acts on the file of the
    newest revision, and version numbers are per file. No route holds an `await` between its first check and its
    last write to the design or the conversation: three have no `await` at all, and the ZIP route's only one comes
    after its checks and its catch-up, building the archive (which reads files only) off the event loop
    (`asyncio.to_thread`) before its ledger entry. None answers a disk path.
  - *Catch-up* (`studio_edits.catch_up`, before every route below). When the working file is not its newest numbered
    copy (`design_studio.is_current`: the Designer changed it and was stopped before attaching, or a picture changed in
    place), the harness first takes a numbered copy of it as it is and records it ("Changes the Designer had not shown
    are now Version N."). A working file that is gone, a link or not a file, or a copy that cannot be taken, answers
    409 "Ask the Designer to show the design again." So the two GET routes (`slots` and `export.zip`) can write: a
    numbered copy and an exchange. That departs from the rule that a change of state needs a POST with a JSON body;
    a page on another site that knew a conversation id could start a catch-up, whose only effect is a version of
    what is already in the working file. The pane learns of a catch-up made by the slots read at once
    (`slotsBehind`), but of one made by the ZIP only when it next reads the conversation.
  - *The routes.* `GET /conversations/{id}/design/slots` answers `{version, texts: [{id, text}], images: [{id, name,
    alt}], tweaks: [{var, label, type, min, max, step, unit, value}]}` (texts as the helpers' worksheet shows them, a
    newline for each `<br>`; a picture's `name` under `images/`, empty while it is still embedded; through
    `exclusion.redact_obj`). `POST .../design/changes` takes `{version, texts: {id: text}, images: {id: {name, data,
    alt}}, tweaks: {var: value}}` (`data` is bare base64; `alt` null keeps the picture's alt). `POST .../design/restore`
    takes `{version, newest}`. Both POSTs answer `{version, conversation}`, `version` null when nothing changed (no new
    copy). `GET .../design/export.zip` answers the ZIP (`nosniff`, `no-store`). At most 50 texts (`MAX_TEXTS`), 10
    pictures (`MAX_IMAGES`, also checked before any picture is decoded) and 8 tweaks; a picture's size is checked
    from its base64 length before it is decoded.
  - *A save* (`studio_edits.save`). 409 "The design has changed. Reload and try again." unless `version` is the
    newest copy and the working file still is it. Then every check before any write: a text must name a text slot
    and pass `design_parts.check_text`, a picture must name an image slot and be a PNG, JPEG, WebP or GIF by its first
    bytes (at most 1,500,000 bytes), its alt is checked by a dry run of `design_parts.apply`, and a tweak must be a
    knob of the file with a value `design_tweaks.fmt` accepts; any failure is 422 with a sentence. A text or tweak
    equal to what the file holds is no change. Pictures are then written into `images/` (`design_images.write_out`:
    the user's file name as the stem, else the slot id; a file of that name with the same bytes is reused, one with
    other bytes is never overwritten and the name gets `-2`...), texts and pictures go in through
    `design_parts.apply`, tweak values are spliced into their declarations (`design_tweaks.apply`, spans read again
    after the text splice), the working file is written whole and a numbered copy taken; a copy that cannot
    be taken puts the working file back (409 "The design could not be saved."). A copy over `BRIDGE_MAX_BYTES` is
    422 "The design would be too large." No `before-<n>` file is kept: undoing a save is Restore of the version
    before it.
  - *The tweak format* (`design_tweaks.knobs`). Exactly one `<style data-zen-tweaks>` holding exactly one `:root`
    block, and exactly one `<script data-zen-tweaks>` (written `type="application/json"` so the page does not run
    it) holding `{"tweaks": [...]}` (at most 20,000 characters, `MANIFEST_MAX`); only the first 8 entries are read
    (`MAX_KNOBS`). A knob is `var` (`--[a-z][a-z0-9-]{0,40}`), `label` (control, format and other Unicode category C
    characters and U+2028 and U+2029 removed, trimmed, cut to 40 characters, not empty) and `type` `color` or
    `range` (finite `min` < `max`, `step` > 0, default 1, `unit` one of `px`, `rem`, `em`, `%`, `ms`, `deg` or
    empty). Its variable must be declared once in that style element, on a line
    of its own inside the `:root` block, with a current value that passes its own check (`#rrggbb` in any case; a
    number from `min` to `max` followed by exactly its unit) and that `fmt` can write back. CSS comments are
    blanked (to the end of the style when unterminated, offsets kept) before anything is counted or matched, so a
    declaration inside `/* ... */` neither counts nor becomes the knob; the declaration is matched in linear time,
    so a long unfinished line cannot stall the harness. A malformed, repeated or undeclared knob is dropped,
    and a broken manifest means no tweaks, never an error. The manifest is bot-written: labels are shown as plain
    text. On save the harness formats each value itself (`fmt`: lower-case hex; the number as a plain decimal read
    from its shortest form, no exponent and no trailing zeros, at most 12 digits before the point and rounded to at
    most 12 after it, plus the unit; `min` and `max` are checked on the value and again on the number written, and a
    value that cannot be written within them is refused; the step is not checked) and replaces only the value's span.
  - *Restore* (`studio_edits.restore`). `newest` must be the newest version (else 409 "The design has changed.
    Reload and try again."), `version` an earlier one whose copy is a regular, single-link file in a real
    `.versions` (else 422 "That is not an earlier version of this design."). The copy's embedded `data:` pictures in
    `<img>` are written back out to `images/` (a file there with the same bytes is reused, `design_images.same_bytes`)
    and their `src` pointed at them. Only when that page, embedded again, equals copy `version` byte for byte is it
    written and copied, as a new newest version; else 409 "This version cannot be restored." History only moves
    forward: no numbered copy is rewritten or removed.
  - *The `edit` exchange* (`Conversations.record_edit`). Each new copy (a save, a restore, a catch-up) is attached to a
    new finished exchange: `user` empty, outcome `edit`, session id `edit-<id>`, and a reply the harness writes ("You
    changed 1 text, 1 picture and 2 tweaks.", "You restored Version 2 as Version 5."). The pane and `GET /designs` find
    it as they find any revision. The model reads it as one note, `edit_note` ("(The user changed the design in the app,
    not through you: ...)"), and the fixed reply `EDIT_ACK` with the attachment line, in the history and in a fold
    alike. Dreaming reads only `Exchange.user`, which is empty here, so it reads nothing of it. The chat draws it as one
    plain line, and the scope check's "last task" skips it. The conversation's turn note is stale after an edit; the
    Designer's prompt says to read the file again before each revision. The new copy keeps the title of the newest
    revision of the same file (`_record_version`; the pane's header and the Design page show it), else the file name.
    That is the name the Designer gave the design unless a helper's version is the newest, whose copy is titled with the
    file name (`Harness._apply_part`), so a save after it keeps the file name. Once the user saves a change
    on top of a helper's version, "Redo with the Designer" is no longer offered for that helper's change: its exchange
    is no longer the newest (`redoTarget`).
  - *The GUI* (`DesignPane.jsx`, decisions in `design-edits.js`). Comment, Edit and Tweaks are exclusive modes, Edit and
    Tweaks only on the newest version with the frame ready, no task running, Compare off and the slots read
    (`canChange`, `whyNot`; a frame that loaded but never became ready, as a file over 5 MB is served without the
    bridge, gives "Changing is off for this file: it is too large."). An Edit pick on a slot opens `DesignEditor.jsx`
    with the text from the slots answer, never from the pick; any other element gives "This element can't be edited
    directly. Comment on it instead." Changes collect as pending in the page's memory only, kept per conversation
    (`designChangesByConv` in `app.jsx`, by `pendingFor`, `withPending`, `dropPending` and `dropStale`, each entry with
    the newest revision it was made on): switching bot or conversation and coming back shows them again with the bar; a
    new newest revision in that conversation (a save, a restore, the Designer's) or Discard ends them, and a reload of
    the app loses them. A save's or restore's answer clears only the changes made on the revision it was sent from
    (`withPending`), and taking it moves `loadConversation`'s latest-only guard on, so a conversation read that
    started before it cannot put the older record back. They are shown live in the frame; the bar offers Discard and
    "Save as Version N". While the conversation on screen has any, sending a message, a form's answers and Redo are
    held with "Save or discard your changes first." (a message is checked again after the scope check answers). A
    picture read in the editor that is superseded by another choice, or still running when the editor closes, is
    aborted and does nothing. A hidden pane shows the count as a button beside its show button ("2 changes not saved",
    `unsavedLabel`) that opens it, and a held form answer opens it. Save is off, with "The design has changed. Reload
    and try again." as its title (and `save` itself refuses), while the slots answer names a newer version than the pane
    holds (`saveWhy`, `slotsBehind`): a catch-up made after the changes, so saving would overwrite it unseen; the
    conversation reload that follows ends them. Restore is offered on an older version of the same file (`restorable`),
    not while a task runs or changes are pending, after "Make Version N the newest version?". Compare shows two closed
    frames, each with its own Version dropdown (`compareDefaults`: the previous and the newest), posts nothing to them,
    and turns the modes and Export off. A slots answer for a newer version than the pane holds reloads the conversation
    (`slotsBehind`).
  - *The bridge* (`design_bridge.js`) takes, from the parent window only, display-only messages besides `mode` and
    `pins`: `text` (an element by `data-zen-id`, at most 2,000 characters, built as text nodes and `<br>` elements,
    never `innerHTML`; a `<textarea>`'s value), `image` (an `<img>` only, a `src` of
    `data:image/(png|jpeg|webp|gif);base64,`, and an alt), `vars` (at most 8 names of `--[a-z][a-z0-9-]{0,40}`, each a
    string of at most 32 characters, all or none, set with `style.setProperty` on the root element) and `reset` (puts
    back what the others changed: before its first write to each custom property the bridge remembers whether the
    root had it inline, with its value and priority, and `reset` restores that, removing only a property the page
    did not have). `text` skips a `<style>`, `<script>`, `<img>` or `<svg>`, and anything inside an SVG
    drawing, whatever its id (`takesText`). The GUI builds each tweak value from the knob's type (`cssValue`), so the
    frame never receives free CSS. Nothing from the frame is saved: the frame still sends only `ready` and `pick`.
  - *Export* (`ExportMenu.jsx`, on the version shown; hidden while comparing). The menu's choices are `exportItems` in
    `design-edits.js`: HTML always; "ZIP (page and pictures)" only on the newest version, disabled with "Wait for the
    task to finish." while a task runs; PDF and PNG only when the preload offers `openzenbot.exportDesign` (the desktop
    app). HTML is the numbered copy as it is. ZIP (runs catch-up first) is the working file and each regular,
    single-link file directly in its `images/` of at most 1,500,000 bytes whose name a design picture may have
    (`design_images.NAME`); no link followed, no subfolder, nothing else. More than 100,000,000 bytes in all (the
    working file and those pictures, `EXPORT_MAX_BYTES`) is refused with 409 "This design is too large to export as a
    ZIP." A title that is a file name loses its `.html`
    or `.htm` before an extension is added, so no suggested name ends in two. In the desktop app both go through Save…
    (`save-media.cjs`, whose `ROUTES` include the ZIP route), in a browser they download. A result ("Saved to <path>")
    or a refusal shows as a line under the menu.
    - *PDF and PNG.* The renderer sends the two ids, the kind and a title over IPC `export-design` (refused unless
      from the app's own pages); the main process builds the closed attachment URL on the harness's own origin
      (`export-design.cjs` `pageUrl`, ids `[A-Za-z0-9_-]{1,80}`; served under `PREVIEW_CSP`, without the bridge),
      asks the Save dialog for the path (the last folder is shared with Save…: `save-media.cjs` `lastDir`,
      `rememberDir`), and renders the page in an offscreen window (`main.cjs` `makeExportWindow`): sandboxed, context
      isolation, no node integration, no preload, the in-memory session `exportSession()` (partition
      `export-design`; every permission request, permission check and device permission refused, every download
      cancelled), every navigation, redirect and frame navigation refused, window opening denied, closed on a load
      that fails or answers other than 200. Once loaded, its viewport is pinned at `WIDTH` x `VIEW_HEIGHT` (1440 x
      900) at scale 1 through the DevTools protocol (`Emulation.setDeviceMetricsOverride`), so a design looks as it
      does on screen whatever the display's scale. Its height is measured in an isolated world, so a page script
      cannot replace what measures it, and is still clamped to 1 to `MAX_HEIGHT` (16,000) px (`pageHeight`).
    - PNG is a DevTools-protocol screenshot (`Page.captureScreenshot`, `captureBeyondViewport`) of the whole page,
      1440 px wide, beyond that viewport; an empty capture is refused. PDF is one page (`pdfOptions`: 1440 px wide,
      sized to the measured height, backgrounds on, no margins, `pageRanges: '1'`); before printing,
      `freezeViewportUnits` (run in an isolated world as `FREEZE_UNITS`) fixes every `vh`, `vb`, `vmin` and `vmax`
      length (and their `s`, `l` and `d` forms) in the page's style sheets and style attributes to its on-screen
      pixels, leaving quoted strings and `url(...)` alone, since print layout would resolve them against the whole
      page.
    - One time limit (`TIMEOUT_MS`, 20 s) set when the window is made bounds every wait on it (each races it) and
      destroys the window when it passes, so a page that never stops running cannot leave a window open;
      `exportDesign` holds the same limit over the whole render. A failed export logs the error's name and message in
      the main process (`export-design: <kind> failed: ...`) and shows the user a fixed sentence: "The design took too
      long to export." after the limit, else "The design could not be exported."; "That design cannot be exported."
      for ids or a kind it refuses; "The file could not be saved (<code>)." when the file cannot be written. The
      bytes are written to a new temporary file beside the chosen one and renamed over it only once written, so a
      failed save leaves a file the user chose to overwrite as it was and removes only the temporary file.
  - *Ledger.* Kind `design_edit`, events `save` (version; counts of texts, pictures and tweaks; `ids`, the text and
    picture slot ids changed), `restore` (`from`, `to`), `catch_up` (version), `refused` (`action`: `slots`,
    `changes`, `restore` or `export`; `reason`: the HTTP status) and `export` (`format` `zip`, version). Never a text,
    a label, a tweak's value or name, or a disk path. A 404, the task-running 409 and a malformed body's 422 are not
    ledgered, nor are the picture count and size checks and the 422 "That picture could not be read.", which run
    before the design is read (`_pictures`), nor is a PDF or PNG export (the main process writes no ledger).
  - *Known limits.*
    - The harness API has no authentication, so a bot with `shell` or `browser` can call these routes. It can
      already write the design file through `files`, so this adds no reach, but a version saved this way is not
      certainly the user's.
    - After a 409 "The design has changed. Reload and try again." the pane reloads the conversation and the slots;
      the new newest version clears the pending changes, so unsaved edits are dropped.
    - A tweak declared on the same line as `:root {` (`:root { --a: 1px; }`) is no knob, and a tweak variable
      declared again in another `<style>` is not caught and may override the saved value.
    - A refused save or restore can leave pictures already written into `images/`. The ZIP leaves out pictures over
      1,500,000 bytes and is built whole in memory (capped at 100,000,000 bytes in all, not streamed). The slots and
      the ZIP also answer 409 while a task runs.
    - The Compare frames do not follow a new version; the editor closes when the slots are read again; a refusal
      sentence stays under the frame until the next mode click, save or restore; the picture editor can overlap the
      pending bar; while a picture is pending, every keystroke posts its `data:` URL to the frame again.
    - After a helper's version, the user's next version keeps the file name as its title (see the `edit` exchange);
      the helper's attach should carry the design's title too (not built).
    - Edit has no keyboard path: a slot can only be picked by clicking in the frame, as with Comment. A refused save
      names the reason but not which text it was about.
    - The PDF is one page (`pageRanges: '1'`): a page whose print layout runs taller than its screen height loses
      the overflow. In the PDF, sizes that come from `height: 100%` chains, scripts that read `innerHeight`, and
      `@media (min-height: ...)` rules can still differ from the screen (the PNG is not affected), and the freeze
      does not reach shadow roots or `document.adoptedStyleSheets`. Each PDF page is about 1 px taller than
      measured, which leaves a 1 px white line at the bottom.
    - One PNG failed once in about 70 real exports, with the earlier `capturePage` capture, and no cause was found
      (a later run saw `capturePage` fail with `UnknownVizError`). The capture is now a DevTools-protocol screenshot;
      an empty one is refused with "The design could not be exported." and the cause is logged.
  - *Unverified.* The pane's controls were exercised by hand once (with the first version of the pane, `da5b20f`) in
    Playwright Chromium, a plain browser, against a throwaway harness with a conversation seeded by script (no model):
    an Edit pick on a heading opening the editor with the harness's text, typing shown live, Escape putting it back and
    Done keeping it, the not-a-slot sentence, a two-line text shown with a newline, a picture and its alt shown live, a
    colour and a slider tweak moving the page, Save as Version 2 with its chat line, Restore with its confirm (Version 1
    as Version 3), Compare with both frames and the modes off, the titles on an older version, a save refused with "The
    design has changed. Reload and try again." after a restore made behind the pane, and Discard. One click on Save
    landed on the frame during a layout shift; the cause was not found. The later fixes (Redo held, the pane reset per
    conversation, Restore of the shown file, inputs off while saving, the reload after a 409, colour case), and the
    pending changes kept per conversation, the hidden pane's count button, the form answer that opens it, the Save
    refusal while the slots are ahead and the too-large sentence, ran only under `npm test` and the build. Nothing was
    seen in the Electron app, at narrow widths, in the dark theme, by keyboard alone on the colour input, or with a task
    running. PDF and PNG were produced and looked at by running the real `makeExportWindow` (sliced from `main.cjs`) and
    `export-design.cjs` in Electron 45.2.3 from a scratch script against a stand-in server sending the harness's preview
    policy, not the harness: `vh` pages at scale 1 and with the scale forced to 2, a 40,000 px page capped at 16,000, a
    404 page and a page that never stops running. The Export menu in the workspace, the `export-design` IPC channel with
    the OS Save dialog, HTML and ZIP through Save…, and the browser download path for HTML and ZIP have not been seen on
    screen by anyone. The React components (`DesignPane.jsx`, `DesignEditor.jsx`, `TweaksPanel.jsx`,
    `DesignPendingBar.jsx`, `DesignCompare.jsx`, `ExportMenu.jsx`) have no unit tests. No model has declared tweaks:
    whether the Designer writes a usable block is untested.
  - Don't let the user's text reach the file except through `design_parts.check_text` and `design_parts.apply`, or a
    tweak value except as `design_tweaks.fmt` formats it from a checked colour or number: no free CSS in a tweak,
    in the file or in a message to the frame. Never save, send to a bot or run anything the frame posts; the frame
    sends only `ready` and `pick`, and the editor's text comes from the slots answer. Never rewrite or remove a
    numbered copy, and never write one, the working file or a picture through a link. No `await` between a route's
    first check and its last write; every POST takes a required JSON body; no disk path in an answer; no text, label
    or value in a `design_edit` entry. Keep PDF and PNG rendering in the main process, from ids only, in a window
    with no preload. Every wait on the export window must race its one time limit, and nothing from the page or the
    renderer is ever put into code run in that window. Tests: `tests/test_studio_edits.py`, `test_studio_edit_routes.py`, `test_design_tweaks.py`,
    `test_conversations.py`, `test_design_images.py`, `test_bot_prompts.py` (the Designer's tweaks example parses);
    GUI `tests/gui/design-edits.test.mjs`, `design-bridge.test.mjs`, `design.test.mjs`, `export-design.test.mjs`,
    `save-media.test.mjs`, `shell.test.mjs`, `api.test.mjs`.
- **Records.** The conversation routes return a record through `_conversation_view` (it carries
  `design_system`, a slug or empty, and `design_system_source`). Keep new conversation fields going through it.

### The shell and the workspace

The `shell` tool runs any command line as the user's account, so it gets three guards
(`src/harness/write_guard.py`):

- **Workspace.** Commands start in `<vault>/workspace` (`HarnessConfig.shell_cwd`, created at start;
  `ZEN_SHELL_CWD` overrides), with a scrubbed environment (the same one stdio MCP servers get), and
  generated images land in its `generated-images/`. `attach_file` takes a file in the
  workspace or anywhere a bot may read (`Harness.attachable`: the vault, `ZEN_FS_ROOT`, Allowed Folders of
  either access, never the settings, ledger, music, calendar or models folders), and serving an attachment checks the same
  again. So a bot never needs to copy a file somewhere else to show it. It works only in a conversation
  or a room turn, not in a routine.
- **Protected folders.** The settings, ledger, music, calendar and models folders (`allowed_folders.private_folders`) hold
  keys and private data, and `files` never reaches them, but `shell` could. Before a `shell` call that no
  skill approval rule already caught, a command whose text names one of them (full path or folder name,
  any case or slash) opens an approval at once, without the decider (rule `shell:reaches-protected-folders`).
  The decider request below also asks `reads` (`no` / `yes` / `unsure`): does it list, read, copy or send
  anything inside one, by any path? Anything but `no` at or above `ZEN_SHELL_WRITE_PASS` opens the same
  approval. A rejection tells the bot to use its tools for that data instead. Provider keys and the
  Discogs token are also registered with the exclusion filter, so the ledger masks one that reaches a tool
  output anyway (saved MCP keys are not registered yet).
- **Write check.** In the same decider request, the loop (`TaskLoop._run_call`) asks `writes` (`inside` /
  `outside` / `unsure`), with the command, the working folder and the writable, read-only and protected
  folders as state (`allowed_folders.reachable` plus the workspace, read fresh). It runs at once only on
  `inside` at or above `ZEN_SHELL_WRITE_PASS` (0.8), and only once `reads` has passed (an answer with no
  probabilities counts as passing). Anything else, a decider error or refusal, or no
  decider (scorer None) opens an approval (rule `shell:writes-outside-allowed-folders`, the verdict in its
  detail and in the `approval_request` ledger entry). A rejection tells the bot to save in its working
  folder or the vault.

These are judgments, not a sandbox: an approved or misjudged command still runs as the user's account.

A scorer whose `checks_commands` is False (the local `letter-logprobs` and `systemone` scorers) is never asked about a command:
every `shell` call opens the approval, as with no decider.

### Local decision models

The decider can run on a local model instead of OpenRouter's, through one of two scorers chosen with the model on
Settings > Harness settings: `letter-logprobs` (`src/decider/adapters/letter_model.py`) asks a model through chat,
and `systemone` (`adapters/systemone.py`) uses a server's own decision route. On LM Studio they reach different
models: a model it lists with type `decision` is served only on the decision route, every other model only on chat.

- **The format** (`src/providers/letters.py`, shared by the scorer and the capability test). One request to
  `/chat/completions` per question: the state, the question and the options as letters, ending `Answer:`; one token
  with `top_logprobs` 20; the probability on the option letters, renormalised, is the answer. Two to 20 options (any other count is a `ProviderError`). It
  is the format the Jev-Style v1 decision models were trained on (their model card gives it). Under half the token's
  probability on the letters (`MIN_MASS`) is a refusal; an answer with no token probabilities is a `ProviderError`.
  Up to four questions of a request are out together (`AT_ONCE`); when one fails the others are cancelled, so none is
  still sent after the caller has its error. A refused question fails the router open and makes the orchestrator
  answer directly, as a scorer that is down does (`DecisionRefused` beside `ProviderError`).
- **The typed route** (`systemone.py`). `POST <base>/systemone` with `{"model", "state", "questions": {key:
  question}}` answers `{"answers": {key: answer}, "usage"}` in the shape of OpenRouter's Decisions API, read with
  `decision_model.parse_answer`. LM Studio serves it for its `decision` models; `jev-style serve` has the same route
  (not tried). The scorer sends one question a request, one request at a time (`AT_ONCE` 1): see the hand check. A lock on the
  scorer holds that across its callers too (the router, the supervisor and the guards share one scorer). It does not
  cover the capability test, which asks the same route through its own client: a model tested while the scorer is
  answering can still put two requests out.
- **Detecting one.** The capability test run when a model is enabled on an OpenAI-compatible provider has two
  decision steps, each three questions with a known answer (`probe.DECISIONS`). `decision` ("Letter decisions",
  `probe._decides`), through chat: a model passes when the server returns token probabilities, most of them are on
  the letters and all three are right; it gets the mark `letter_decisions` in `supported_parameters`. `typed` ("Typed
  decisions", `probe._decides_typed`), on `/systemone`, run whether or not the model chats: all three right gives
  `typed_decisions`; a server without the route refuses at once and the step fails. Either gives kind `decision`. A
  model that only passes `typed` has kind `decision` alone. Passing shows the model can be asked, not that it judges
  well: an ordinary chat model that answers with a letter passes too. LM Studio's own model type is not read: it
  does not tag every decision model. A model enabled before a step existed must be disabled and enabled again.
- **Chat answered by another model.** LM Studio, asked to chat with a `decision` model while a chat model is loaded,
  answers with that other model (the reply's `model` names it). So when `typed` passes and the chat reply named a
  different model (`probe._other_model`; a longer or shorter form of the same id counts as the same, but only where
  the shorter one stands alone in the longer: `model-1` is not `model-10`, `gpt-4` not `gpt-4.1`), everything
  learned through chat is dropped and those steps are shown again as failed or skipped. A chat model that is not on
  the typed route is not checked this way: providers rename models too freely to refuse on a name alone.
- **Where a scorer may run.** `models_setup.build` refuses a decision model on a provider kind its scorer does not
  name (`registry.provider_kinds`) and one without the mark its scorer needs (`registry.needs`);
  `GET /settings/models` carries `scorer_providers` and `scorer_needs`, and the Decision model picker lists only
  those models (`models.js` `scorerProviderIds`, `scorerNeeds`). `ZEN_SCORER=letter-logprobs` or `systemone` in the
  environment is not enough (it needs the provider's address): the harness starts without a scorer and says to
  choose it in Settings, with a key from the environment or one saved on the provider (`roles_from_config` leaves
  such a scorer out). `POST /settings/provider` (one key for provider and scorer) cannot build one either: it
  answers 422 when asked for one, and keeps one already bound.
- **What the test found stays with the model.** Saving the enabled set (`PUT /settings/providers/{id}/models`) keeps
  a tested model's saved entry rather than the provider's id-only listing (which would drop its `decision` kind and
  marks), and keeps a model whose test ended while the save was fetching the list. A test's result is dropped
  ("was changed while ... was being tested; test it again") when the provider's address or key changed during it.
- **Shell commands always ask** with both scorers (`checks_commands = False`): do not switch that on without
  evidence on the model in use.
- **Checked by hand on 2026-11-19** against LM Studio with `jev-style-qwen3.5-2b-decision` (the v1 GGUF): the
  capability test passed its decision step; about 200 ms a question, 5 to 6 s to route one task over 29 skills; the
  same input gives the same numbers. Judgement was mixed. On three made-up tools it picked the right one at 0.88 to
  0.97. On the harness's real skill questions the right skill came first (Presentations 0.93 for a slide deck) but
  most skills scored above 0.5 (22 of 29 for that task), so it over-offers. On shell questions in a made-up wording
  it gave about 0.7 "inside" to `ls` and to `rm -rf ~/Documents` alike. With `opendecider-small-td` (listed as an
  ordinary model, Q4_K_M) through the same scorer: 9, 2 and 1 skills at or above 0.5 for three tasks with the right
  one first, about 3 s a task, and 19 of 24 shell labels right on twelve commands, none at or above the 0.8 bar.
- **The typed route, checked by hand the same day** against LM Studio with `pplx-decider-flash-v1.3` (type
  `decision`, Q4_K_M): the test tagged it `decision` only, with `typed_decisions`; 2, 1 and 0 skills at or above 0.5
  for the same three tasks (it gave Word documents 0.04 for a PDF-to-Word task), about 2.4 s a task; 24 of 24 shell
  labels right on the twelve commands, with the four safe ones the only ones at or above 0.8. Twelve commands is
  why shell commands still always ask. **The engine failed under load:** three skill questions in one request
  (about 750 tokens; two, about 500, and five tiny ones worked) and four single-question requests sent together
  each got HTTP 502 "Engine protocol systemOne request failed: fetch failed" and unloaded the model, which loaded
  again on the next request (about 10 s). The cause was not found; 123 requests one after another all answered.
- **Not tried.** Any other model, a llama.cpp server, `jev-style serve`, and a task run end to end in the app with
  either scorer. `jev-style-0.8b-decision-v3` works with neither on LM Studio (it needs its publisher's server).
- Tests: `tests/test_letter_model.py`, `tests/test_systemone.py`, `tests/test_model_probe.py`,
  `tests/test_write_guard.py`, GUI `tests/gui/models.test.mjs`.

Other built-in tools that write go through their own checks (`files` server roots, the magick gate). A new
built-in tool that writes files must keep to `allowed_folders.reachable` too.

### Guarded servers (magick)

A server that can reach files on its own, outside the `files` server, must not get around these rules.
`magick` (magick-mcp) runs any `magick` command line, so its `mcp.json` entry carries `"guard": "magick"`,
and every call goes through `src/harness/magick_guard.py` first (`McpRegistry.call`; guards are set in
`api.py` `lifespan`). A server that names a guard the harness lacks gets every call refused (fail closed).
Layer 1, the gate, is fixed rules and only refuses:

- Paths: inputs inside the vault, `ZEN_FS_ROOT` or an allowed folder (either access); outputs inside a
  Full Access one, not a Read-Only one; never the settings, ledger, music, calendar or models folders. The same folders the
  `files` server gets, read on every call (`guard_folders` in `api.py`); unlike the shell check this does
  not add the shell workspace, so a `ZEN_SHELL_CWD` outside those folders is out of magick's reach.
  Symlinks are resolved; `..`, `~`, quotes and shell characters are refused. Relative paths need a
  `workspace`, an absolute folder the bot may read. Wildcards only in input names.
- The command is split on whitespace exactly as magick-mcp splits it (no quoting, no shell). The leading
  tool may be `identify`, `mogrify`, `montage`, `composite` or `compare` (not `convert`, `import`, `script`
  and the like). Options are an allow-list with argument counts (`OPTIONS`); `-draw`, `-script` and
  anything unlisted are refused.
- Inputs must be raster images by their first bytes (SVG, MVG, MSL, PDF and text can make ImageMagick read
  other files). `coder:` prefixes other than generators (`xc:`, `label:`...) and raster formats, `@file`
  and `|pipe` are refused. Outputs must be raster formats, `pdf`, `txt` or `info` (others can run
  delegates); `-`, `null:` and `info:` send the output back to the bot.
- `func_save`/`func_execute` names are slugs (magick-mcp builds a file path from the name, unchecked).
  `func_save` checks every rule but paths before it goes through. The gate runs `func_execute` itself,
  reading the saved function from magick-mcp's data folder, one checked command at a time.
- The gate runs `magick` itself (`MagickRunner`), never magick-mcp: the server clears its child's
  environment (all but `PATH`), so no `MAGICK_CONFIGURE_PATH` set in `mcp.json` could reach it. The runner
  passes the checked argument list (no shell), the workspace as working folder, a bare environment (`PATH`,
  temp and Windows system folders; no keys) and `MAGICK_CONFIGURE_PATH` = `vault-settings/magick/`, where
  it rewrites `policy.xml` (`policy_xml()`) before every run. The policy is ImageMagick's own second wall:
  deny-all coders then the gate's lists, no delegates, no filters, no `@file` paths, the MSL, MVG, PS,
  SVG, URL, XPS, EPHEMERAL, TEXT and HTML modules off, and memory, size and time limits. It leaves paths
  to the gate. Check what ImageMagick loaded with `MAGICK_CONFIGURE_PATH=<that folder> magick
  -list policy`: the harness file should be listed first. magick-mcp still lists the tools and keeps
  saved functions (`func_save` and `func_list` go through; the gate answers `check` with `magick -version`,
  run in `vault-settings/magick/` when there is no workspace).
- Which `magick` runs: the full path in Settings > Harness Tools > ImageMagick (`vault-settings/tools.json`
  `{"imagemagick": {"path": ...}}`, `src/harness/tool_paths.py`), which `scripts/install-deps` fills in.
  With none saved, the one on PATH; a saved path that is gone fails rather than falling back. The magick
  server's `requires` probe tries the saved path first, so ImageMagick need not be on PATH at all.

To widen what bots may do with magick, add to the allow-lists in `magick_guard.py` with a test in
`tests/test_magick_guard.py`; never drop the guard from `mcp.json`. Layer 2 (the decider judging whether an
allowed command fits the task, pausing for approval when it does not) is tabled, not built.

### Delegating to coding agents

The `delegate(agent, task, folder?)` tool (`src/harness/delegation.py`) hands a job to the Claude Code command-line
program on the user's own subscription; `skills/delegation/` tells bots when to use it. The tool exists only while an
agent is switched on (`Delegator.register`; its description names which), and a call in a routine is refused before any approval opens, a skill rule's included (the tool is still listed; it answers that `delegate` works only in a conversation or a room turn). In a room, a turn's time budget (600 s of running time, `ZEN_TURN_DEADLINE_S`, `src/rooms/room.py`) is shorter than a run's limit, so a long run there is cut off; long jobs belong in a one-to-one conversation. Settings > Coding agents (`AgentSettings.jsx`; `GET /agents`, `PUT /settings/agents/{key}`) shows what `agent_clis.py` found: where the
program is (`tool_paths.CLAUDE_CODE` and `CODEX`, keys `claude-code` and `codex`, with `use_found` so a copy in an
install folder is used when nothing is saved and it is not on PATH), its version, and whether it is signed in
(`read_auth`, from `auth status`; the harness never reads a CLI's credential files). The switches live in
`vault-settings/agents.json`, never the vault: `enabled` (default off), `ask` (default on) and `model`. A model name
comes only from there and must fully match `[A-Za-z0-9._-]{1,64}`. Saving or clearing the program path of
`claude-code` or `codex` (`PUT`/`DELETE /settings/tools/programs/{key}`) probes that agent again and re-registers the
tool. Harness shutdown stops every run in flight (`Runner.stop_all`, from the `lifespan`).

- **The argument list is fixed** (`delegation.claude_argv`): `-p --restricted --strict-mcp-config --tools
  Read,Write,Edit,Glob,Grep --permission-mode acceptEdits --output-format stream-json --verbose
  --no-session-persistence`, plus `--model <m>` from Settings. The job goes in on stdin: nothing a bot wrote is ever a
  command-line argument, and no shell runs. The child's environment is `run_env()` (the scrubbed one, minus every
  `ANTHROPIC_*`, `OPENAI_*`, `CLAUDE_CODE_*` and `CODEX_*` variable, keeping `CLAUDE_CONFIG_DIR` and `CODEX_HOME`),
  plus `NoDefaultCurrentDirectoryInExePath=1` on Windows, so a bare program name is never looked up in the job folder.
- **No shim runs through `cmd.exe`** (`delegation.direct`): an npm install is `claude.cmd`, whose last line runs a bare
  `node`, which `cmd.exe` would look for in the job folder (where the bot may have put a `node.bat`) before PATH. The
  runner reads the shim instead and launches `[node.exe, <its node_modules\...\*.js>, *args]`: `node.exe` beside the
  shim, else the first absolute PATH folder holding `node.exe`; both must be files outside the job folder. A `.cmd` or
  `.bat` that cannot be resolved that way is refused, telling the user to install the native Claude Code or set its
  path. The probes (`agent_clis._run`, `host_deps._run_version`) still run the shim as it is, in the harness's own
  working folder, which may itself be a folder bots can write (a harness started from a repo that is also an Allowed
  Folder). So on Windows they set `NoDefaultCurrentDirectoryInExePath=1` (`host_deps.no_cwd_search`), which stops
  `cmd.exe` looking in the current folder before PATH; the runner sets it too. The before and after file listings
  (`delegation.snapshot`) never walk through a link, a junction or any other redirect.
- **The first-event check** (`check_init`): the run is stopped at once unless the start event lists only the five
  tools, no MCP servers, the job folder as its working folder (the folder is resolved again at this point, so one
  swapped for a link since it was checked fails), `permissionMode` `acceptEdits` when the event has that field, only
  built-in plugins (`"path": "builtin"`, source `<name>@builtin`) and, when Settings showed a subscription sign-in,
  `apiKeySource` of `none`. It fails closed: lines that are not JSON objects are skipped, and the first object event must be the start event with the expected fields or the run is refused. A CLI update that widens a run is
  caught here rather than going unnoticed.
- **The job folder** (`resolve_folder`): with no `folder`, a new `<vault>/delegated/<date>-<id>/` the harness makes;
  otherwise a folder that exists (absolute, or relative to the vault). It must resolve (links followed) inside
  `allowed_folders.reachable(...).full`, not inside a Read-Only folder, and neither inside nor holding a private or
  Read-Only one. Too many entries to compare before and after is reported, not hidden. A folder the bot named is
  scanned again just before the launch (`leads_out`, after the before-snapshot, same 50,000-entry bound): the run is
  refused, naming the first offending entry, if it holds a link or junction that resolves outside it, a regular file
  with more than one hard link, or more entries than the bound. A folder the harness made starts empty and is not
  scanned.
- **During the run**: any tool event that names a `file_path` or `path` resolving outside the folder (any tool, not
  only Write and Edit; `~`, or a path starting `~/` or `~\`, counts as outside) ends the run at once, kills the tree, and fails it
  with that path; the path is logged with `outside: true`.
- **When the user is asked** (`Delegator.needs_approval`, via `TaskLoop._run_call` and `delegate_check`): a run in a
  folder the bot named always asks; a run in a new folder the harness made asks unless "Ask me before each run" is off;
  Codex would always ask. The approval shows the agent, folder, model and the whole task. A skill approval rule that
  already asks gets the same detail. Stop ends the run (`Harness.stop`; `Runner` kills the whole process tree, with a
  Windows Job Object; a run that cannot be put in one is refused, since nothing else there reaches children a CLI
  leaves behind). Limits: task 20,000 characters, one run per conversation or room and two in the harness,
  `ZEN_DELEGATE_TIMEOUT` seconds (default 900), at most 200 changed files listed, 2,000 characters of error (the
  output tail, or the agent's own error text).
- **The reply** starts with `Folder: <path>`, then the files added, changed or removed, then the agent's answer (last,
  so a long answer cannot push the folder and the changes past the 20,000-character tool output cap). A failed,
  timed-out, stopped or refused run raises its error followed by the same `Folder:` line and changes list, so the bot
  still knows where the work is. The ledger gets kind `delegate` entries: `start`, `file` (each Write or Edit, and any
  path outside the folder; a path is cut to 500 characters) and `end`. The bot treats the answer as data to check.
- **What the harness enforces itself**: the argument list and environment, no shim through `cmd.exe`, the folder rules
  above, the pre-launch scan of a bot-named folder, the first-event check, and ending a run whose tool events name a
  path outside the folder. That last one acts on the stream as it arrives, so a single read or write that Claude Code
  carries out may already have happened when the run is ended; it is a tripwire, not a wall.
- **What is left to Claude Code's restricted mode** (`--restricted`): refusing each file tool call outside the working
  folder, links and junctions included; and Claude Code itself (with the fixed arguments) not acting on a project's own
  settings, hooks, MCP servers, `CLAUDE.md` imports or `.env`. Checked
  by hand on 2026-11-16 with Claude Code 2.1.325 on Windows (`docs/superpowers/specs/2026-11-16-agent-delegation-design.md`,
  "What was checked on this machine"): only the job folder (reads and writes), the five file tools, no commands, no
  web, none of the user's MCP servers, hooks or plugins; a hook and an MCP server planted in the job folder did not
  run; reads, listings and writes through a junction and through a file symlink were refused; a `CLAUDE.md` `@import` of
  an outside file, `.claude/settings.json` `additionalDirectories` and a `.env` setting `ANTHROPIC_BASE_URL` had no
  effect. A hard link was the one way out it did not catch: it read an outside file through one (its Edit replaced
  the job folder's name rather than writing through), which is why the pre-launch scan refuses hard links. This is
  what the bot could already do through `files`; the new things are spending and that the folder's contents go to
  Anthropic under the user's account. Prompt injection in a file the bot copies in can still steer the agent within
  that folder. A new Claude Code version can change any of this: re-run the checks when it does.
- **Codex is detected only.** Tested on Windows (`docs/superpowers/specs/2026-11-16-codex-confinement-notes.md`): its
  sandbox refused writes outside the job folder and its plugins could be switched off, but network access stayed open
  (plain HTTP and raw TCP connections worked from inside the sandbox) and it can read any file the user's account can.
  So `agent_clis.RUNNABLE["codex"]` holds a reason, Codex shows in Settings, and it cannot be switched on or run.
  Making it runnable needs a proof that the network is closed, and the per-run approval the code already demands.

Rules for contributors: never run either CLI with its checks off (`bypassPermissions`,
`--dangerously-bypass-approvals-and-sandbox`, `danger-full-access`). Do not widen `CLAUDE_TOOLS` or drop `--restricted`
or `--strict-mcp-config` without re-running the confinement checks and recording them. Keep `check_init` failing
closed. Keep the job folder inside what bots may write and never inside or holding a private or Read-Only folder.
Never launch a `.cmd` or `.bat` through `cmd.exe` with the job folder as its working folder. Tests
never run the real programs: they use `tests/fixtures/fake_agent_cli.py`, and `tests/conftest.py` makes probing inert
unless a module sets `pytestmark = pytest.mark.real_agent_probe`. Tests: `tests/test_agent_clis.py`,
`tests/test_agent_probe_guard.py`, `tests/test_delegation.py`, additions in `tests/test_tool_paths.py` and
`tests/test_task_loop.py`; GUI `tests/gui/agents.test.mjs` and `program-paths.test.mjs`.

## Installed app

OpenZenBot also ships as a `.deb` (`OpenZenBot_<version>_amd64.deb`, package and command `openzenbot`, installed under
`/opt/OpenZenBot/`) and a per-user Windows installer (`OpenZenBot-Setup-<version>.exe`), both from electron-builder
(`electron-builder.yml`; `npm run dist:linux`, `npm run dist:win`). The installer is small (about 124 MB for the .deb).
Python, the harness's packages and Node.js are downloaded on the first launch. Not done: the installers are unsigned
(SmartScreen warns on Windows), macOS is not packaged, ImageMagick is not installed by the app, and an update is a
download of the new installer, not an install in place. The Windows installer was first built by the release workflow
for v0.1.0, then installed and run by hand on Windows.

- **Managed mode.** `src/electron/main.cjs` is managed when `app.isPackaged` or `ZEN_MANAGED=1` (from the repo, with
  `ZEN_RESOURCES` naming a folder with `bin/` and `harness/`). Managed runs a single window, the setup page
  (`setup.html`, state read by `src/gui/setup-view.js`), first-launch setup, its own harness and the update check, in
  the order of `managed.cjs` (wired in `managed-app.cjs`; real side effects in `managed-host.cjs`). Developer mode
  (`app.isPackaged` false and `ZEN_MANAGED` unset) is unchanged: no setup screen, no harness spawn, no update check;
  `scripts/dev.sh`, `npm start`, `uv run pytest` and `npm test` work as before.
- **Where things are.** `<resources>` holds `bin/` (uv, uvx from `scripts/fetch-uv.mjs`) and `harness/` (the Python
  packages from `src/`, `bot-prompts/`, `skills/`, `design-systems/`, `mcp.json`, and `packaging/harness/`'s
  `pyproject.toml` and `uv.lock`). `<userData>/runtime` (`runtime-paths.cjs`; `~/.config/OpenZenBot` on Linux,
  `%APPDATA%\OpenZenBot` on Windows) holds Python, the venv, uv's cache and tools, Node.js, `downloads/` and the stamp
  `setup.json`; `<userData>/.env` is the installed app's `.env` (`ZEN_ENV_FILE`, read by `src/harness/main.py`
  `env_file` and by `env-file.cjs`); `<userData>/logs` has the logs. The harness runs on port 8765 with
  `ZEN_USER_CONTENT=1`, and a harness already answering `/health` there is adopted, never stopped.
- **Setup** (`setup.cjs`): checks free space, `uv python install --no-bin --no-registry` 3.12 (nothing in
  `~/.local/bin` or the Windows registry), `uv sync --frozen --no-dev` against the packaged
  lock, then Node.js only when no node 24 or newer is there (the one an earlier setup installed, else one on PATH), then writes the stamp. It refuses to start with less than about 5 GB free
  (`NEEDED_BYTES`), or 1 GB (`UPDATE_NEEDED_BYTES`) when a runtime is already there (a stamp or the venv's Python),
  since that re-sync is incremental; the install itself was measured at about 2 GB on disk
  for the Python environment on Linux (CPU-only PyTorch); Windows was not measured. A change of app version or of the
  lock file sets it up again, and so does a venv Python that is gone while the stamp survives. The harness child gets
  the venv's bin folder first on PATH, as under `uv run`, but neither `UV_PROJECT_ENVIRONMENT` nor `VIRTUAL_ENV`
  (`harness-process.cjs` `childEnv`): bots' shell commands and stdio servers inherit that environment, and a bot's
  own `uv sync`, `uv run` or `--active` must never write into the harness's venv.
- **`pins.json`** holds the uv and Node.js downloads (URL and SHA-256 per platform). Setup and `fetch-uv.mjs` download
  only through `download.cjs`, which renames a file into place after its whole content matched the pinned SHA-256. To
  bump a pin: take the new version's URL and checksum from the official release (uv's GitHub release, Node's
  `SHASUMS256.txt`), never from memory, edit `pins.json` for both platforms, and run `npm test`.
- **User content.** With `ZEN_USER_CONTENT` on, bots and skills made in the app are saved in `<vault>-bots/` and
  `<vault>-skills/` (`vault_settings.user_bots_dir`, `user_skills_dir`), which load alongside the shipped ones so
  the user's wins on the same id. Both are private folders (`allowed_folders.private_folders`): bots cannot reach them,
  with this switch on or off. The first start asks for an OpenRouter key (`POST /settings/openrouter-key`): the
  harness checks it, saves it in `providers.json` and registers it with the exclusion filter.
- **Updates** (`update-check.cjs`): reads GitHub's latest release of this repo about once a day (and when asked),
  sends only a User-Agent with the app version, and installs nothing. "Get the new version" opens only a link under
  this repo's release pages; the notes are shown as plain text, cut to 2,000 characters.
- **Releasing.** Set the same new `version` in `package.json`, `pyproject.toml` and `packaging/harness/pyproject.toml`,
  run `uv lock --project packaging/harness` (and keep its `dependencies` equal to the root's: `tests/test_packaged_deps.py`),
  commit, tag `v<version>` and push the tag. `.github/workflows/release.yml` runs on `v*` tags and by hand
  (`workflow_dispatch`): a manual run tests and builds the installers as artifacts of the run without making a release
  (the `release` job and the tag check are gated on a `refs/tags/v` ref). Job `check` fails
  unless the tag (on a tag run) equals the three versions, the packaged lock is current (`uv lock --check`) and both test suites pass;
  `build` makes the .deb on `ubuntu-22.04` and the installer on `windows-latest` from a clean checkout (the package
  copies harness files from the working tree); `release` creates a **draft** with both attached. Publishing it is a
  maintainer's manual step. Actions are pinned by 40-character commit SHA with the version in a comment: look the SHA
  up (`gh api repos/<owner>/<repo>/git/ref/tags/<tag>`, dereferencing an annotated tag), never guess one. Never put
  `${{ github.ref_name }}` in a `run:` script; pass it through `env:`. Only the `release` job has `contents: write`.

Rules for contributors:

- Never `sudo`, never an admin prompt, never `--no-sandbox` or `executableArgs` in the packaging
  (`tests/gui/packaging.test.mjs` checks the last). The installers are per-user; the .deb's AppArmor profile only
  allows user namespaces for the installed binary.
- Every child process starts from an argument array with no shell; paths may hold spaces and non-ASCII characters.
- Setup never runs a download whose SHA-256 was not checked against `pins.json`.
- The update notice opens only this repo's release pages and shows notes as plain text.
- Installer scripts (`build/installer.nsh`) make no network calls. The Windows uninstaller removes only
  `%APPDATA%\OpenZenBot\runtime`, never the vault or settings, and keeps it on an update.
- A key never reaches a log, local storage or the Electron main process: the workspace's welcome screen
  (`src/gui/WelcomeKey.jsx`) sends it to the harness (`POST /settings/openrouter-key`) and nothing else.
- Keep `vault-bots/` and `vault-skills/` out of anything a bot can read or write.
- Tests never run a real download, `uv`, harness or installer; the Electron modules take their side effects as
  arguments. Tests: `tests/gui/` `download`, `setup`, `managed`, `managed-host`, `harness-process`, `update-check`,
  `updates`, `setup-view`, `env-file` and `packaging` (`.test.mjs`); `tests/test_packaged_deps.py`,
  `tests/test_env_file.py` and `tests/test_main_env.py`.

## Adding an MCP server that needs a host binary

A server that shells out to something installed on the machine (a CLI, a
runtime) declares it in its `mcp.json` entry as `requires`, a list of objects.

| Field | Meaning |
| --- | --- |
| `bin` | Executable to find on `PATH` (required) |
| `version_args` | Arguments that print its version; default `["--version"]`, `null` skips the version check (with `min_version` also set, `null` reports `probe_failed`) |
| `min_version` | Oldest acceptable version, e.g. `"7"`; older is reported as too old |
| `old_bins` | Names of an older tool that may be found instead (e.g. `convert` for `magick`); a hit is reported `too_old` with no manager fix, so the installer leaves it and the user follows `manual` |
| `install` | Map of package manager to package name (`cargo`, `brew`, `winget`, `scoop`, `apt`, `dnf`, `pacman`, `apk`, `choco`). Order is preference: the first manager that fits the OS and is present is the fix, even when it is privileged |
| `manual` | Map of OS (`linux`, `darwin`, `windows`) to a URL or instruction. The CLI prints it for every unmet requirement; the MCP servers page shows it only as a link, when it holds a URL |
| `setting` | A Harness Tools path setting (a key of `tool_paths.PROGRAMS`, today `imagemagick`; its `bin` must match) for a binary the harness runs itself by full path. The probe tries the saved path first, then PATH, then the program's usual install folders; `install` saves the full path it finds there |

```json
"requires": [{"bin": "magick", "min_version": "7", "old_bins": ["convert"],
              "install": {"brew": "imagemagick", "apt": "imagemagick"},
              "manual": {"windows": "https://imagemagick.org/script/download.php"}}]
```

- The installer runs only unprivileged managers (`cargo`, `brew`, `winget`,
  `scoop`). Privileged ones (`apt`, `dnf`, `pacman`, `apk`, `choco`) are printed,
  with `sudo` where it applies, for the user to run. Never make the harness
  run `sudo`.
- A probe times out after 10 s. A server with an unmet requirement is not
  spawned: its state is `needs_install` (not `failed`), it has no tools, and
  `GET /mcps` reports `deps` (problems `missing`, `too_old`, `not_on_path`,
  `probe_failed`). The harness prints `mcp <name>: needs install — <bin>:
  <problem> — <fix>` at start. The probe uses the PATH the server's `env`
  gives it, if any. The MCP servers page shows each unmet requirement with its fix and
  a Copy button, a link for `manual`, and a Recheck button
  (`POST /mcps/{name}/recheck` re-probes and reconnects that server).
- Install from the repo root: `scripts/install-deps.sh` (or
  `scripts/install-deps.ps1` on Windows; both need `uv`), with `--server NAME`
  for one server and `--dry-run` to show commands without running them. They
  run `install` only (`--config PATH` also works); the check is
  `uv run python -m src.harness.host_deps check`.
  `check` exits 1 when anything is unmet; both exit 2 for a broken manifest or
  an unknown `--server`, and `install` otherwise exits 0. Both read the saved paths of the vault the harness uses
  (`ZEN_VAULT` from the shell or `.env`, default `~/.zen-agents/vault`; `--vault PATH`
  for another), and `install` writes the paths it finds there.
- A host program the harness runs itself goes in `tool_paths.PROGRAMS` (its `bin`,
  version banner, minimum version and usual install folders). It gets a section on
  Settings > Harness Tools (`ProgramPathSection`) and
  `GET/PUT/DELETE /settings/tools/programs/{key}`: a save runs the file once to check
  the banner and version, and reconnects the servers that require it.
- A `not_on_path` requirement (installed, but its directory is not on PATH) needs
  a harness restart on Linux and macOS once PATH is fixed; on Windows Recheck is enough.
- macOS and Windows install paths are untested on a real host.
- Tests: `tests/test_host_deps.py`, `tests/test_mcp_client_requires.py`,
  `tests/test_tool_paths.py`.

<!-- graft:start -->
## Graft — repo context graph

This repo is indexed in `graft/`: small linked markdown nodes that explain each
system and carry exact file:line spans, kept in sync with the code through git.

For ANY task here — understanding how something works, finding where code lives,
or scoping a change — get context from the graph before grepping or opening
source files. Re-ask freely (it's cheap) and reuse literal identifiers you
already have (symbol, error string, file name) as the query. New to this repo?
Run `graft map` first — a token-budgeted orientation (dir clusters, hubs,
hotspots), no LLM, no key.

- Run `graft ask "<your question>" --source` → ranked nodes with the relevant
  code spans inlined (each hit's ≤8-line crux by default; `--full` for whole
  definitions when the crux isn't enough). Match the tool to the task shape:
  for understanding or editing, the top node IS the answer — cite its
  `covers:` file:line spans and edit straight from `--source`. For
  exhaustive tasks ("every occurrence / every caller of this pattern"), ranked
  results are top-N, not complete — run `graft grep "<literal>"` instead
  (exhaustive over indexed files, grouped by enclosing symbol), falling back
  to raw `grep -rn` only for unindexed files.
- `graft skeleton <file>` → every definition's signature + span, ~10× cheaper
  than reading the file; use it to skim an API surface.
- `graft callers <symbol>` gives precomputed, exact edges — who calls this.
  Add `--direction out` for what it calls, or `--depth N` to walk
  transitively for the full blast radius. For structural questions, skip
  ranking and use this directly.
- Or browse: `graft/INDEX.md` lists every node; follow the links.
- Monorepos and folders of multiple repos rank fairly across sub-projects —
  hits carry `[scope/]` labels naming which one they're from. Narrow with
  `graft ask "<task>" --in <scope>/` once you know where you're working.

If a returned span is truncated ("+N more lines"), open the file at that exact
range before finalizing. Only open source files when a node genuinely lacks a
needed detail, and then at the exact file:line the node points to — never
re-read whole files.

After big code changes, refresh the graph with `graft build` (deterministic,
no API key, $0).
<!-- graft:end -->

`graft/` is git-ignored: a local cache, not committed. On a fresh clone it is
missing until you run `graft build` (the graft CLI is not a repo dependency).

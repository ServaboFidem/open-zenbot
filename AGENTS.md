## What lives here

You are a contributor or an agent working in this repo. After this page you can
name each top-level system and run the harness and the tests.

| Path | What |
| --- | --- |
| `src/harness/` | Bot, task loop, approvals, pause, tools, HTTP API (`api.py`), entry point (`main.py`), MCP client (`mcp_client.py`), MCP keys (`mcp_keys.py`) and host dependencies (`host_deps.py`, `tool_paths.py`); conversations, routines (`scheduler.py`), the onboarding interview, model roles; the guards (`allowed_folders.py`, `write_guard.py`, `magick_guard.py`); the design studio (`design_studio.py`: numbered copies, comment block, preview bridge `design_bridge.js`; `design_systems.py`: loading and checking design systems; `design_html.py`: the HTML scan; `design_images.py`: pictures as files; `design_parts.py`: slots, worksheet, apply; `studio_helpers.py`: a helper's turn, apply and restore; `studio_help.py`: routing a revision); coding agents: `agent_clis.py` (find, sign-in, switches), `delegation.py` (the `delegate` tool, runner, folder rules) |
| `src/providers/` | Provider interface, registry, provider store (`providers.json`) and secrets, OpenRouter and OpenAI-compatible adapters, model listing and probing, the image provider, Langfuse tracing |
| `src/decider/` | Decider interface, scorer registry, skill and MCP routing (band and floor), skill-to-bot fit, supervisor, `adapters/` |
| `src/context/` | Compaction, the protected (pinned) task, transcript rendering |
| `src/generation/` | Generator boundary: compaction summaries and folds, vault summaries; the `image.generate` tool (`images.py`) |
| `src/memory/` | Exclusion filter (secrets and approvals), ledger (`ledger.py`), recall, ingest, directory import, learning from a crawl, profile memory and store routing, Hindsight store and processes |
| `src/rooms/` | Rooms, turns, handoff, orchestrator |
| `src/music/` | Music discovery: Discogs scan, taste profile, the Soundlore atlas, MusicBrainz and YouTube Music lookups, candidates, the album gate, digests, `music_*` tools, `/music` routes |
| `src/events/` | The built-in calendar: settings and Google keys (`settings.py`), the SQLite store (`store.py`), the Google client (`google.py`), conversion to and from Google's shape (`convert.py`), sign-in (`connect.py`), pull and push (`sync.py`), the service that runs them (`service.py`) and the `/calendar` routes (`routes.py`) |
| `src/skills/` | `SKILL.md` loader and catalog; recorder |
| `src/vault_settings.py` | `settings_dir` (the settings folder beside the vault) and `migrate` |
| `src/gui/` + `src/electron/` | OpenZenBot workspace GUI (built into `dist/`, assets in `src/assets/`; the design pane is `DesignPane.jsx` + `design.js`, the question forms `QuestionForm.jsx` + `question-form.js`, the design system picker `DesignSystemPicker.jsx`, the Design page (in `app.jsx`: a card per design from `GET /designs`, and New design, which opens the studio bot in a fresh conversation), the Settings home screen `SettingsHome.jsx` + `settings-index.js` (icon tiles and a search over a hand-kept index: a new settings section adds an entry to `SECTIONS` and puts `id={anchorId(panel, title)}` on its element; provider and tool cards carry `anchorId(panel, id)` and reach the search through `liveSections`; checked by `tests/gui/settings-index.test.mjs`), the coding agents page `AgentSettings.jsx` + `agents.js`, program paths `program-paths.js`, the Events page `events/` (`EventsArea.jsx`, `CalendarView.jsx`, `CalendarGrid.jsx`, `EventDialog.jsx`, the date rules `dates.js`, the status line `page-state.js`) and the Calendar settings panel `CalendarSettings.jsx` + `calendar-settings.js`, both through `calendar-api.js`) and Electron shell (`frame-nav.cjs` limits where a framed page may go; the installed app's side is in `## Installed app`: `main.cjs`, `managed*.cjs`, `setup.cjs`, `runtime-paths.cjs`, `harness-process.cjs`, `update-check.cjs`, `download.cjs`, `pins.json`, and the setup page `setup-view.js`) |
| `bot-prompts/` | One markdown file per bot identity; the file stem is the bot id (`designer.md` is the studio bot: `studio: true`) |
| `design-systems/` | Shipped design systems, one folder each (`DESIGN.md`, optional `tokens.css`); `ZEN_DESIGN_SYSTEMS` moves it. The user's own live in `<vault>/design-systems/` |
| `scripts/` | Start scripts (`dev.sh`, `dev.ps1`), the GUI build, the host-dependency installers `install-deps.sh` / `install-deps.ps1`, `ubuntu-apparmor-profile.sh` (the AppArmor profile Electron needs on Ubuntu 23.10+), and `fetch-uv.mjs` (downloads the pinned uv into `build/bin/` for the installers). Packaging itself is `electron-builder.yml`, `build/` (icon, the .deb's AppArmor profile `apparmor-profile`, the NSIS include `installer.nsh`) and `packaging/harness/` (the CPU-only `pyproject.toml` and `uv.lock` the installed app uses) |
| `skills/` | Shipped skills, including vendored Anthropic skills under `skills/anthropic/`, `skills/design-studio/` and `skills/delegation/` (when a bot should use `delegate`) |
| `mcp.json` | MCP servers to connect at start; each tool registers as `<server>.<tool>` |
| `tests/` | Offline suite (`uv run pytest`, music tests under `tests/music/`, design studio in `tests/test_design_studio.py` and `tests/test_design_systems.py`; studio helpers in `tests/test_design_html.py`, `tests/test_design_images.py`, `tests/test_design_parts.py`, `tests/test_studio_helpers.py` and `tests/test_studio_help.py`); GUI node tests under `tests/gui/` (`npm test`; the studio's are `design.test.mjs`, `question-form.test.mjs`, `frame-nav.test.mjs`, `api.test.mjs`, `actions.test.mjs`); MCP echo fixture at `tests/fixtures/mcp_echo_server.py`; fake agent CLI at `tests/fixtures/fake_agent_cli.py`; agents in `tests/test_agent_clis.py`, `tests/test_agent_probe_guard.py`, `tests/test_delegation.py`, GUI `tests/gui/agents.test.mjs`; the calendar in `tests/events/` (its Google stand-in `FakeGoogle` is in `tests/events/conftest.py`), GUI `tests/gui/calendar-api.test.mjs`, `events-dates.test.mjs`, `events-page-state.test.mjs` and `calendar-settings.test.mjs` |
| `docs/` | Screenshots, OpenGraph images, and the GitHub wiki as a git submodule (`docs/open-zenbot.wiki`) |
| `.github/` | Workflows: `changelog.yml` appends every push to `main` to `CHANGELOG.md` (don't edit it by hand; `[skip changelog]` in a commit subject leaves it out), `stale.yml`, and `release.yml` (a `v*` tag builds the .deb and the Windows installer and opens a draft release; a manual run only builds them; see `## Installed app`) |
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
folders) are refused by `allowed_folders.check`, and so are the harness's settings, ledger, music and calendar
folders, anything inside them, and anything holding them (`private_folders`); keep both rules when adding
other ways in (`allowed_folders.reachable` gives the same readable, writable and private lists).

### What bots can and cannot reach

The `files` server is rooted at the vault, so a bot can read **and write** anything inside it. Keep
nothing there a bot must not see or change. The harness's own folders sit beside the vault instead. The
vault is `ZEN_VAULT` (default `~/.zen-agents/vault`), and the others take its name: `ZEN_VAULT=~/x/notes`
gives `notes-ledger/`, `notes-music/`, `notes-calendar/` and `notes-settings/`.

| Folder (default under `~/.zen-agents/`) | Holds | Owned by |
| --- | --- | --- |
| `vault/` | Work docs (`docs/`), `delegated/` (job folders for `delegate`), uploads, browser output (`browser/`), anything bots save; `designs/` (studio designs; each folder also holds `images/` and `.versions/`, whose `before-<n>.<name>` files are the working file as it was before a helper's change) and `design-systems/` (the user's own); `workspace/` (the bots' working folder, unless `ZEN_SHELL_CWD` moves it); `memory/`, `sync.json`, learn logs (`learn_log.json`, `learn_holds.json`) | the vault; bots reach it |
| `vault-ledger/` | Ledger (`ledger.jsonl`), rooms, conversations | `src/memory/ledger.py` `ledger_path_for` |
| `vault-music/` | Music database (`music.db`), the Soundlore atlas, cover images | `src/music/settings.py` `music_dir`; bots never reach it (`allowed_folders.private_folders`) |
| `vault-calendar/` | Calendar database (`calendar.db`: events, calendars, sync state) | `src/events/settings.py` `calendar_dir`; a private folder (`allowed_folders.private_folders`), so no bot tool reaches the file. The `/calendar` routes are another matter: see "Bots and the calendar" under `### The calendar and Google` |
| `vault-settings/` | `providers.json` (providers and their keys), `mcp_keys.json`, `tracing.json`, `harness.json` (model roles), `bot-models.json`, `tools.json` (program paths and image generation), `agents.json` (coding agent switches), `allowed_folders.json`, `routines.json`, `music.json`, `music_keys.json`, `music_state.json`, `calendar.json`, `calendar_keys.json`, `magick/policy.xml` | `src/vault_settings.py` `settings_dir` |

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

### The calendar and Google

The Events page shows a built-in calendar, kept by the harness in `<vault>-calendar/calendar.db`
(`src/events/store.py`). That calendar is the master: the page reads and writes only the `/calendar` routes
(`src/gui/calendar-api.js`) and never talks to Google. Google is one account at a time (`Settings.account`,
the primary calendar's id), brought in by pull and, when the user switches it on, sent back by push.

**Unverified (2026-11-17): the whole Google exchange has only run against the test fake (`tests/events/conftest.py`
`FakeGoogle`), never against Google itself.** That includes whether Google accepts a loopback redirect with a path
(`/calendar/google/callback`) on the harness's port. If it does not, the fix is confined to
`Connection.redirect_uri` and the callback route. Assumptions about Google to check by hand before a release:

- Believed right, confirm once:
  - An event id of 32 hex characters plus `g` plus digits is accepted (base32hex, 5 to 1,024 characters).
  - Inserting a reused id answers 409, including when the earlier event was deleted.
  - GET of a deleted event answers 200 with `status: "cancelled"`; DELETE of an already-deleted one answers 410.
  - A PATCH merges `start` and `end` key by key, and a null removes a key (why `to_google(patch=True)` nulls the
    other form; the fake now behaves this way).
  - The token answer to a code exchange carries `scope`, and with `include_granted_scopes=true` it lists the
    combined grant.
  - `prompt=consent` with `access_type=offline` always returns a refresh token.
  - `calendar.calendarlist.readonly` and `calendar.events.readonly` are enough for `calendarList.list` and
    `events.list` on shared and holiday calendars.
  - The `primary` flag is on exactly one calendar-list entry (a list without one is now refused as `provider`).
  - `htmlLink` is on `*.google.com` (otherwise `isGoogleLink` hides the link).
- Not sure:
  - PATCH of a cancelled event: 404, 410 and a 200 that is still cancelled are handled; a 403 or 400 would
    retry on every run.
  - Whether a "Desktop app" client accepts the loopback redirect with a path on a fixed port.
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
- **Not built.** Anything beyond this: finding local events, and the other pieces of the design. Don't count them
  as there.

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
  its selector and a short text, each capped); the GUI only ever adds a chip the user sees. Nothing
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
  - Don't let a helper's text reach the file except through `design_parts.check_text` and `design_parts.apply`.
    Don't offer `design_set_text`, `design_set_image` or `design_add_image` outside the turns named above, don't
    attach a design for a helper, and don't write a numbered copy or a `before-<n>` file through a link. Tests:
    `tests/test_design_html.py`, `test_design_images.py`, `test_design_parts.py`, `test_studio_helpers.py`,
    `test_studio_help.py`, `test_bot_prompts.py`; GUI `tests/gui/design.test.mjs`, `actions.test.mjs`, `api.test.mjs`.
- **Records.** The conversation routes return a record through `_conversation_view` (it carries
  `design_system`, a slug or empty, and `design_system_source`). Keep new conversation fields going through it.

### The shell and the workspace

The `shell` tool runs any command line as the user's account, so it gets three guards
(`src/harness/write_guard.py`):

- **Workspace.** Commands start in `<vault>/workspace` (`HarnessConfig.shell_cwd`, created at start;
  `ZEN_SHELL_CWD` overrides), with a scrubbed environment (the same one stdio MCP servers get), and
  generated images land in its `generated-images/`. `attach_file` takes a file in the
  workspace or anywhere a bot may read (`Harness.attachable`: the vault, `ZEN_FS_ROOT`, Allowed Folders of
  either access, never the settings, ledger, music or calendar folders), and serving an attachment checks the same
  again. So a bot never needs to copy a file somewhere else to show it. It works only in a conversation
  or a room turn, not in a routine.
- **Protected folders.** The settings, ledger, music and calendar folders (`allowed_folders.private_folders`) hold
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

Other built-in tools that write go through their own checks (`files` server roots, the magick gate). A new
built-in tool that writes files must keep to `allowed_folders.reachable` too.

### Guarded servers (magick)

A server that can reach files on its own, outside the `files` server, must not get around these rules.
`magick` (magick-mcp) runs any `magick` command line, so its `mcp.json` entry carries `"guard": "magick"`,
and every call goes through `src/harness/magick_guard.py` first (`McpRegistry.call`; guards are set in
`api.py` `lifespan`). A server that names a guard the harness lacks gets every call refused (fail closed).
Layer 1, the gate, is fixed rules and only refuses:

- Paths: inputs inside the vault, `ZEN_FS_ROOT` or an allowed folder (either access); outputs inside a
  Full Access one, not a Read-Only one; never the settings, ledger, music or calendar folders. The same folders the
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

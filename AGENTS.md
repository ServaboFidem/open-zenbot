## What lives here

You are a contributor or an agent working in this repo. After this page you can
name each top-level system and run the harness and the tests.

| Path | What |
| --- | --- |
| `src/harness/` | Bot, task loop, approvals, pause, tools, HTTP API (`api.py`), entry point (`main.py`), MCP client (`mcp_client.py`) and MCP keys (`mcp_keys.py`) |
| `src/providers/` | Provider interface, registry, secrets, OpenRouter adapter |
| `src/decider/` | Decider interface, scorer registry, band and supervisor logic, `adapters/` |
| `src/generation/` | Generator boundary: summaries, compaction folds, vault summaries |
| `src/memory/` | Exclusion filter (secrets and approvals), ledger (`ledger.py`), recall, ingest, Hindsight store |
| `src/rooms/` | Rooms, turns, handoff, orchestrator |
| `src/music/` | Music discovery: Discogs scan, taste profile, the album gate, digests, `music_*` tools, `/music` routes |
| `src/skills/` | `SKILL.md` loader and catalog; recorder |
| `src/gui/` + `src/electron/` | OpenZenBot workspace GUI (built into `dist/`) and Electron shell |
| `bot-prompts/` | One markdown file per bot identity; the file stem is the bot id |
| `scripts/` | Start scripts (`dev.sh`, `dev.ps1`), the GUI build, and the host-dependency installers `install-deps.sh` / `install-deps.ps1` |
| `skills/` | Shipped skills, including vendored Anthropic skills under `skills/anthropic/` |
| `mcp.json` | MCP servers to connect at start; each tool registers as `<server>.<tool>` |
| `tests/` | Offline suite (`uv run pytest`); GUI node tests under `tests/gui/` (`npm test`); MCP echo fixture at `tests/fixtures/mcp_echo_server.py` |
| `.env.example` | Every environment variable with its default; copy to `.env` (git-ignored) |

Run the harness with `uv sync --group dev`, then `uv run python -m src.harness.main`
(`http://127.0.0.1:8765`). Keep secrets in `.env` or the shell environment, never
in a committed file. Run `uv run pytest` for the offline suite and `npm test`
for the GUI tests. See `README.md` for the full start sequence.

## Adding an MCP server that needs a key

MCP servers live in `mcp.json` at the repo root (Claude Desktop shape). The
harness connects them at start (`src/harness/mcp_client.py`); the GUI's MCP
servers page lists them and asks the user for any keys. **`mcp.json` is
committed: never write a real key, token or password into it**, not even
temporarily. A key that lands there is public in git history and must be
rotated.

1. Pick one variable name per credential: `SERVICE_API_KEY` (or `_TOKEN`,
   `_SECRET`). Not a name the harness supplies itself (`ZEN_VAULT_ROOT`).
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
5. Restart the harness (`mcp.json` is read only at start).

How the key gets supplied. This is the user's job; don't ask them to paste
keys into chat:

- **MCP servers page** (preferred): every `${VAR}` in a server's `env`,
  `headers` or `url` becomes a password field on its card. A save goes to
  `vault-settings/mcp_keys.json` (owner-only) and reconnects that server at
  once. A saved key wins over `.env` for that server only. API:
  `GET /mcps` (each key's name, source and last four characters, never the
  value), `PUT /mcps/{name}/keys` with `{"keys": {VAR: value}}`, and
  `DELETE /mcps/{name}/keys/{VAR}`.
- **`.env`** (git-ignored): `VAR=value`, read at harness start. A variable
  already set in the shell wins over the file.

An unset variable drops the header, env entry or arg that names it. The
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
whose args name either variable is reconnected when the folders change. Nesting rules are
`AllowedFolders.place`: a Read-Only folder may sit inside a Full Access one (or the vault); a Full Access
folder inside a Read-Only one, or any same-access nesting, is refused with the reason. Don't write user folders into `mcp.json`. Broad folders (a drive root, the home folder or above
it, system folders) are refused by `allowed_folders.check`, and so are the harness's settings and ledger
folders, anything inside them, and anything holding them (`private_folders`); keep both rules when adding
other ways in.

### What bots can and cannot reach

The `files` server is rooted at the vault, so a bot can read **and write** anything inside it. Keep
nothing there a bot must not see or change. The harness's own folders sit beside the vault instead:

| Folder (default under `~/.zen-agents/`) | Holds | Owned by |
| --- | --- | --- |
| `vault/` | Work docs (`docs/`), uploads, browser output, anything bots save; `workspace/` (the bots' working folder); memory, `sync.json`, learn logs | the vault; bots reach it |
| `vault-ledger/` | Ledger, rooms, conversations | `src/memory/ledger.py` `ledger_path_for` |
| `vault-music/` | Music database (`music.db`), the Soundlore atlas, cover images | `src/music/settings.py` `music_dir`; bots never reach it (`allowed_folders.private_folders`) |
| `vault-settings/` | Providers and their keys, MCP keys, tracing, model roles, bot models, tools, allowed folders | `src/vault_settings.py` `settings_dir` |

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
been suggested before, not be on the wantlist, and be obscure enough. Don't add a path that saves a card
without the gate. The Discogs token lives in `vault-settings/music_keys.json`; `vault-music/` is as private
as the settings and ledger folders, so keep it out of anything a bot can read or write.

### The shell and the workspace

The `shell` tool runs any command line as the user's account, so it gets three guards
(`src/harness/write_guard.py`):

- **Workspace.** Commands start in `<vault>/workspace` (`HarnessConfig.shell_cwd`, created at start;
  `ZEN_SHELL_CWD` overrides), and generated images land there too. `attach_file` takes a file in the
  workspace or anywhere a bot may read (`Harness.attachable`: the vault, `ZEN_FS_ROOT`, Allowed Folders of
  either access, never the settings or ledger folders), and serving an attachment checks the same again.
  So a bot never needs to copy a file somewhere else to show it.
- **Protected folders.** The settings, ledger and music folders (`allowed_folders.private_folders`) hold
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
  `inside` at or above `ZEN_SHELL_WRITE_PASS` (0.8). Anything else, a decider error or refusal, or no
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
  Full Access one, not a Read-Only one; never the settings or ledger folders. The same folders the `files`
  server gets, read on every call. Symlinks are resolved; `..`, `~`, quotes and shell characters are refused.
  Relative paths need a `workspace`.
- The command is split on whitespace exactly as magick-mcp splits it (no quoting, no shell). Options are an
  allow-list with argument counts (`OPTIONS`); `-draw`, `-script` and anything unlisted are refused.
- Inputs must be raster images by their first bytes (SVG, MVG, MSL, PDF and text can make ImageMagick read
  other files). `coder:` prefixes other than generators (`xc:`, `label:`...) and raster formats, `@file`
  and `|pipe` are refused. Outputs must be raster formats, `pdf`, or `txt` (others can run delegates).
- `func_save`/`func_execute` names are slugs (magick-mcp builds a file path from the name, unchecked).
  The gate runs `func_execute` itself, one checked command at a time.
- The gate runs `magick` itself (`MagickRunner`), never magick-mcp: the server clears its child's
  environment (all but `PATH`), so no `MAGICK_CONFIGURE_PATH` set in `mcp.json` could reach it. The runner
  passes the checked argument list (no shell), the workspace as working folder, a bare environment (`PATH`,
  temp and Windows system folders; no keys) and `MAGICK_CONFIGURE_PATH` = `vault-settings/magick/`, where
  it rewrites `policy.xml` (`policy_xml()`) before every run. The policy is ImageMagick's own second wall:
  deny-all coders then the gate's lists, no delegates, no `@file` paths, the script modules off, and
  memory, size and time limits. It leaves paths to the gate, and `ephemeral:` too (a read modifier, which
  the policy cannot name). Check what ImageMagick loaded with `MAGICK_CONFIGURE_PATH=<that folder> magick
  -list policy`: the harness file should be listed first. magick-mcp still lists the tools and keeps
  saved functions (`func_save` and `func_list` go through; the gate answers `check` with `magick -version`).
- Which `magick` runs: the full path in Settings > Harness Tools > ImageMagick (`vault-settings/tools.json`
  `{"imagemagick": {"path": ...}}`, `src/harness/tool_paths.py`), which `scripts/install-deps` fills in.
  With none saved, the one on PATH; a saved path that is gone fails rather than falling back. The magick
  server's `requires` probe tries the saved path first, so ImageMagick need not be on PATH at all.

To widen what bots may do with magick, add to the allow-lists in `magick_guard.py` with a test in
`tests/test_magick_guard.py`; never drop the guard from `mcp.json`. Layer 2 (the decider judging whether an
allowed command fits the task, pausing for approval when it does not) is tabled, not built.

## Adding an MCP server that needs a host binary

A server that shells out to something installed on the machine (a CLI, a
runtime) declares it in its `mcp.json` entry as `requires`, a list of objects.

| Field | Meaning |
| --- | --- |
| `bin` | Executable to find on `PATH` (required) |
| `version_args` | Arguments that print its version; default `["--version"]`, `null` skips the version check |
| `min_version` | Oldest acceptable version, e.g. `"7"`; older is reported as too old |
| `old_bins` | Names of an older tool that may be present instead (e.g. `convert` for `magick`) |
| `install` | Map of package manager to package name (`cargo`, `brew`, `winget`, `scoop`, `apt`, `dnf`, `pacman`, `apk`, `choco`) |
| `manual` | Map of OS (`linux`, `darwin`, `windows`) to a URL or instruction, shown for every unmet requirement, beside the manager fix |
| `setting` | A Harness Tools path setting (a key of `tool_paths.PROGRAMS`, today `imagemagick`) for a binary the harness runs itself by full path. The probe tries the saved path first, then PATH, then the program's usual install folders; `install` saves the full path it finds there |

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
  `GET /mcps` reports `deps`. The harness prints `mcp <name>: needs install`
  at start. The MCP servers page shows each unmet requirement with its fix and
  a Copy button, a link for `manual`, and a Recheck button
  (`POST /mcps/{name}/recheck` re-probes and reconnects that server).
- Install from the repo root: `scripts/install-deps.sh` (or
  `scripts/install-deps.ps1` on Windows; both need `uv`), with `--server NAME`
  for one server and `--dry-run` to show commands without running them. They
  run `install` only (`--config PATH` also works); the check is
  `uv run python -m src.harness.host_deps check`.
  `check` exits 1 when anything is unmet; `install` exits 0 unless the manifest
  is broken (2). Both read the saved paths of the vault the harness uses
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

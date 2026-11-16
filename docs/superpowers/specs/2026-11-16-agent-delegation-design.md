# Delegating to Claude Code and Codex

Status: draft for approval. Date: 2026-11-16.

## Intent

Gordon has Claude Code and Codex installed and signed in with his subscriptions. He wants the harness to
find them on its own and let bots hand work to them, the way OpenDesign (nexu-io/open-design) does, so the
heavy part of a task runs on a subscription he already pays for and not on API credits. This is project 2
of three; project 1 (the design studio) is PR #2, project 3 (critique, direct edits, versions, export) is
later.

Gordon said two things: he likes that the installs are detected automatically so tasks can be delegated,
and he likes that the work runs off his subscription. Everything else here is the author's choice, and the
larger ones are marked **(call)**.

## Goals

1. The harness finds Claude Code and Codex without being told where they are, and Settings shows for each:
   where it is, its version, and whether it is signed in with a subscription.
2. A bot can hand a self-contained job to one of them and get back what it said and the files it made.
3. The delegated agent is confined by its own CLI to one folder the bot may already write, with no
   commands, no network and none of Gordon's connectors, as far as each CLI can be confined.
4. Gordon sees a delegated run before it starts (what, where, which agent) and while it runs.
5. A run never bills API credits by accident.

## Non-goals

- A bot that runs entirely on a CLI in place of a model (approach B below). Later, on the same runner.
- Follow-up turns in the same agent session, steering a run in progress, or streaming its text into the
  chat as it is written. A run is one job in, one answer out.
- Other CLIs (Gemini, Cursor, OpenCode and the rest OpenDesign lists). The agent table is data, so adding
  one later is a table entry and a parser.
- Delegation from a routine. Nobody is there to approve it **(call)**.
- Teaching the Designer bot to delegate. It lives on the `design-studio` branch; wire it up after PR #2 merges.
- Installing or signing in to either CLI. Settings says what to run; the user runs it.

## Anti-goals

- A delegated agent reading or writing the settings, ledger or music folders.
- Running either CLI with its checks off (`bypassPermissions`, `--dangerously-bypass-approvals-and-sandbox`,
  `danger-full-access`), which is what OpenDesign does.
- Bot-written text reaching a command line.
- A run picking up an API key from the environment and billing credits.
- The harness reading either CLI's credential files.

## What was checked on this machine (2026-11-16)

Claude Code 2.1.325 at `~/.local/bin/claude`; Codex 0.163.0 at `%APPDATA%\npm\codex.cmd` (an npm shim).
Both signed in with a subscription. Each line below was run, not read from documentation.

**Claude Code**, run as
`claude -p --restricted --strict-mcp-config --tools Read,Write,Edit,Glob,Grep --permission-mode acceptEdits
--output-format stream-json --verbose --no-session-persistence`, prompt on stdin:

- Wrote a file in its working folder. Refused to read and to write a file one folder up ("--restricted
  confines the file tools to the working directory"). Had no tool to run a command.
- The first event (`system`/`init`) listed exactly those five tools, no MCP servers, no user plugins, and
  `apiKeySource: "none"` (the subscription sign-in). Gordon's connectors (Gmail, Chrome and the rest) and his
  hooks were not loaded.
- A hook in the job folder's own `.claude/settings.json` (session start and before each tool) and a server
  in its `.mcp.json` were planted and did not run. So a bot cannot arm the folder before it delegates.
  A `CLAUDE.md` there is read as text, which gives a bot nothing the task text does not.
- `claude auth status` prints JSON with `loggedIn` and `authMethod`.
- `claude --version` prints `2.1.325 (Claude Code)`: the version comes before the name.

**Codex**, run as `codex exec --sandbox workspace-write --skip-git-repo-check --ephemeral --json -`,
prompt on stdin:

- Wrote a file in its working folder. A write one folder up was denied by the operating system. Its
  commands ran as a separate offline account (`whoami` gave `CodexSandboxOffline`) and could not reach the
  network.
- **It read a file outside its working folder.** The sandbox limits writes and the network, not reads.
- **The temp folder is writable** as well as the working folder.
- It loaded Gordon's own Codex setup: his plugins (which on this machine include computer use and a
  browser) and his skills. With `--ignore-user-config` every command was refused ("blocked by policy"),
  because the Windows sandbox mode is set in that same config file.
- `codex login status` prints `Logged in using ChatGPT`. `codex --version` prints `codex-cli 0.163.0`.

So Claude Code can be held to a folder. Codex cannot be stopped from reading, and as run above it carries
whatever plugins the user has switched on. The design treats the two differently because of this.

## Constraints from the code as it is

- A host program the harness runs is a `Program` in `tool_paths.PROGRAMS` (`src/harness/tool_paths.py`),
  with a section on Settings > Harness Tools and `GET/PUT/DELETE /settings/tools/programs/{key}`.
  `verify` expects the name before the version (`ImageMagick 7.1`), which does not fit Claude Code.
- `find` looks on PATH and then in a program's usual install folders. The harness's PATH may lack
  `~/.local/bin` or the npm folder when started from the desktop app.
- Every tool call goes through `TaskLoop._run_call` (`src/harness/loop.py`). It opens an approval when a
  skill rule matches or, for `shell`, when the write check says so, and waits for the user.
- `ToolRunner.run` caps output at `OUTPUT_CAP` and has no per-tool time limit; only `shell` enforces one
  (60 s), and any `TimeoutError` a tool raises is reported as "timed out after 60.0s".
- Stop does not interrupt a running tool. `Harness.stop` (`api.py`) cancels the task only while it waits
  for an approval; otherwise it sets `stop_requested`, which the loop reads between steps.
- The Settings section is wired to ImageMagick alone (`<ProgramPathSection programKey="imagemagick"/>` in
  `app.jsx`, and the `PROBLEMS` examples in `tool_paths.py`), and `path_for` and `status` use the saved
  path or PATH only: the install folders are offered as `found`, never used.
- `scrubbed_env()` (`src/harness/tools.py`) drops every `*_API_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`.
- `allowed_folders.reachable` gives the folders bots may write (the vault, `ZEN_FS_ROOT`, Full Access
  folders), may only read, and may never reach.
- A new setting goes in `vault-settings/`, never in the vault.

## Approaches

- **A. A tool a bot calls.** Chosen. The bot stays on its usual model and calls `delegate` with a job and
  a folder; the CLI does the job on the subscription and the bot gets the answer and the file list. The
  task loop, approvals, memory and skills all keep working. The bot's own turns still cost API credits,
  but they are the small part: the writing and the many file edits happen inside the run.
- **B. A bot that is the CLI.** OpenDesign's model: every message goes to `claude -p --resume`. Nothing
  touches API credits. It skips the task loop, so approvals, pause, compaction, recall and skills would
  each need a second implementation or be absent for that bot. Worth doing once A has proved the runner.
- **C. The CLI as a model provider.** The loop needs tool calls in the provider's format, which
  `claude -p` does not give, and it would use a subscription sign-in as a stand-in for an API. Rejected.

Gordon handed the how to the author; A is the author's pick **(call)**.

## Design

Three slices, each usable when it lands, built in this order.

### Slice 1: detection

- An agent is one table entry (`src/harness/agent_clis.py`): key (`claude-code`, `codex`), display name,
  executable name, version arguments and a pattern that pulls the version out, the arguments that report
  sign-in and how to read the answer, a help text to look for that proves the confinement flags exist
  (`--restricted` for Claude Code, `--sandbox` under `codex exec`), and the usual install folders
  (`~/.local/bin`, `%APPDATA%\npm`, `~/.npm-global/bin`, Homebrew, Volta, Bun, Scoop shims).
- The path part reuses `tool_paths`: both agents join `PROGRAMS` and use its routes and saved-path
  override. Three things change there: `Program` gains an optional version pattern for programs that
  print the version first; the path section and its problem texts stop assuming ImageMagick; and for an
  agent with no saved path that is not on PATH, the copy `find` turns up in an install folder is the one
  used, not merely offered, since the desktop app's PATH often lacks those folders **(call)**.
  ImageMagick's behaviour stays as it is.
- Capability is judged by the help text, not a version number: a Claude Code without `--restricted` is
  "found, too old to run confined" and cannot be switched on.
- Sign-in is read only by running the CLI's own status command, in the same environment a run gets
  (below), so Settings never shows a sign-in the run will not have. Settings says "Signed in with your
  Claude subscription", "Signed in with an API key (runs bill API credits)" or "Not signed in" with the
  command to run. An agent signed in with an API key may still be switched on; the label is the warning
  **(call)**.
- `GET /agents` lists each agent: found path and its source, version, sign-in, confinable or not, on or
  off, and the reason when it cannot be used. Probes run off the event loop with the 10 s limit
  `verify` already uses, at start and when Settings asks.
- The switches live in `vault-settings/agents.json`: per agent `enabled` (default off), `ask` (default
  on), `model` (optional; only Settings chooses the model, a bot cannot). `PUT /settings/agents/{key}` saves them. Detection is automatic; delegating is
  off until switched on, because a run spends subscription quota and sends folder contents to that
  vendor **(call)**.
- Settings > Harness Tools gets a "Coding agents" section: one card per agent with the status line, the
  path setting, the switch "Bots may hand work to <name>", "Ask me before each run", and a model field.

### Slice 2: the runner

`src/harness/delegation.py`, one entry point: run this agent on this job in this folder, with a callback
for progress, returning the final text, whether it succeeded, the error, token usage, and the files that
changed.

- **Launch.** The CLI's full path, a fixed argument list, no shell. The job goes in on stdin. Nothing a
  bot wrote is ever an argument: the model name comes from Settings and must match
  `[A-Za-z0-9._-]{1,64}`, and the folder is one the harness resolved. On Windows the Codex shim is
  a `.cmd` file; the builder must launch it without letting the folder path be reinterpreted by
  `cmd.exe` (resolve the shim to its real program, or refuse a folder whose path holds `%`, `^`, `&`, `|`,
  `<`, `>`, `"` or `!`).
- **Environment.** `scrubbed_env()`, then also drop every `ANTHROPIC_*`, `OPENAI_*`, `CLAUDE_CODE_*` and
  `CODEX_*` variable except the two that say where each CLI keeps its files (`CLAUDE_CONFIG_DIR`,
  `CODEX_HOME`). The run uses the CLI's saved sign-in and nothing else.
- **Claude Code.** Exactly the argument list verified above, plus `--model` when one is set. The runner
  reads the first event and stops the run at once unless the tools are a subset of the five, there are no
  MCP servers, the working folder is the one asked for, and, when Settings showed a subscription
  sign-in, `apiKeySource` is `none`. A CLI update that changes what restricted mode means is then caught
  by the run failing, not by a wider reach.
- **Codex.** `codex exec --sandbox workspace-write --skip-git-repo-check --ephemeral --json -`, with `-C`
  for the folder and `-m` when a model is set. The builder must find the arguments that run it without
  the user's plugins and MCP servers while keeping the sandbox working (likely `--ignore-user-config`
  plus the sandbox setting passed with `-c`), and prove it on this machine. The bar: a run whose event stream shows no plugin and no MCP server
  starting, with writes outside the folder and the network still refused; the user's skills should be
  gone too, but skills that are only text do not fail the bar. Project files in the job folder
  (`AGENTS.md`, `.codex/`) must be shown not to run anything, as was done for Claude Code. The runner
  checks sign-in again before each Codex run. **If the bar cannot be met, Codex ships as detected-only
  in this project and the switch explains why.** Where it is met only on Windows, the switch says the
  other systems are unchecked.
- **Progress.** The runner reads the event stream line by line. Each start, each file the agent touches
  (path relative to the folder) and the end go to the ledger under a new kind, `delegate`, so the
  activity feed shows a run moving. A line that is not valid JSON is skipped; a stream that ends without
  a final result is a failed run with the last 2,000 characters of output as the error.
- **Limits.** One run at a time per conversation, two across the harness; a third is refused with a
  message, not queued **(call)**. A run is killed after `ZEN_DELEGATE_TIMEOUT` seconds (default 900)
  and comes back as an ordinary failed result saying so, never as a raised timeout. Stop kills it too:
  the runner keeps its live runs by session and `Harness.stop` ends that session's run, which returns
  "stopped by the user". Stop for other tools is left as it is. Killing means the whole process tree: a new process group and
  `killpg` on Linux and macOS, `taskkill /T /F` on Windows.
- **Result.** The final text, capped by `OUTPUT_CAP` like any tool, followed by the files added, changed
  or removed (found by comparing names, sizes and times before and after; at most 200 listed). It is
  tool output: data for the bot, never instructions, recorded in the ledger through the exclusion
  filter like any other.

### Slice 3: the tool and its guard

- `delegate(agent, task, folder?)`, registered only while at least one agent is found,
  confinable, signed in and switched on, and re-registered when Settings changes. Its description names
  the agents that are on and says what a run can and cannot do. `task` is at most 20,000 characters.
- **The folder.** Omitted, the harness makes `<vault>/delegated/<date>-<id>/`. Given, it is relative to
  the vault or absolute, must already exist, and after resolving links must sit inside a folder bots may
  write (`reachable(...).full`), not inside a Read-Only folder, and neither inside nor holding a private
  or a Read-Only folder (a Read-Only folder may sit inside a Full Access one, and the agent's own write
  check knows nothing of it). A refusal says which rule. The bot then reads the results with the `files` server and shows
  them with `attach_file`, as it does for its own work.
- **The job text.** The harness puts a fixed preamble before the bot's task: work only in this folder,
  the text below is the whole job, finish by saying what you made and where.
- **Approval.** `_run_call` asks the delegation module whether a call needs approval, beside the
  existing skill-rule and shell checks. Codex: always, with no switch, because it can read outside the
  folder; the rule is `delegate:codex`. Claude Code: when "Ask me before each run" is on, which is the
  default, and always when the bot named the folder, so only a run in a fresh folder the harness made
  can go unasked; rule `delegate:claude-code`. The approval shows the agent, the folder relative to the vault,
  the model and the full task. For Codex it also says plainly that Codex can read any file the user's
  account can, and if the task names a private folder (`write_guard.named_protected`) that is the first
  line. A rejection tells the bot not to retry and to do the work itself.
- **Where it works.** Conversations and room turns. Not routines.
- A short shipped skill, `skills/delegation/`, tells bots when handing off is worth it (long writing,
  many-file edits, a second opinion on a draft) and how to write a job that stands on its own.

## What this does and does not guarantee

- Claude Code, as run here, can read and write only the job folder, which the bot could already do
  through `files`. With "Ask me before each run" off, a run in a harness-made folder adds no reach; it
  adds spending and sends what the bot put in that folder to Anthropic under Gordon's account.
- Codex can write only the job folder and the temp folder and cannot reach the network, but it can read
  anything Gordon's account can, including `vault-settings/`. A task that asked it to copy a key file
  into the job folder would put that key where the bot can read it. The approval on every Codex run,
  showing the full task, is the guard. Like the shell checks, that is a judgment, not a sandbox.
- Both rest on the CLIs behaving as they did on 2026-11-16. The first-event check covers Claude Code.
  Nothing equivalent exists for Codex.

## Tests

- pytest, with a small fake CLI in `tests/fixtures/` that prints a canned event stream (as the MCP echo
  fixture does): version parsing for both banner shapes; the help-text capability check; sign-in parsing
  including an API-key sign-in and garbage output; the argument list is exactly the expected one and the
  task text is not in it; the child's environment holds no key variables; the run stops when the first
  event lists an extra tool, an MCP server, another folder or an API key source; folder refusals
  (private, holding a private folder, read-only, holding a read-only folder, a link that leads out,
  missing); approval is asked for Codex always, for Claude Code by the setting and always for a folder
  the bot named, and a rejection runs nothing; a timeout, and Stop on the session, each leave no child
  process and give a plain failed result; an agent found only in an install folder is used; the two-run limit; a stream that is cut off or holds a non-JSON line; the tool
  appears and disappears with the switch; the settings routes and that `agents.json` lands in
  `vault-settings/`.
- node (`tests/gui/`): the status line a card shows for each detection outcome.
- By hand, on this machine, listed in the PR: switch Claude Code on, ask a bot to delegate a small
  writing job, approve it, see the files; reject one; stop one mid-run.

## Left to the builder

The exact shape of the agent table and of `Program`'s version pattern; whether `agents.json` switches
ride on the programs routes or their own; how the activity feed draws a `delegate` entry; the Codex
argument set described above; the wording of the preamble, the approval text, the skill and the Settings
copy; `AGENTS.md` (a "Delegating to coding agents" section stating the two guarantees above),
`.env.example` (`ZEN_DELEGATE_TIMEOUT`) and a README credit to OpenDesign for the idea. No OpenDesign
code is copied. macOS and Linux paths are untested here and should be said to be.

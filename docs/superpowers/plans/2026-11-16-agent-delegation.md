# Agent Delegation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The harness finds Claude Code and Codex, shows them in Settings, and gives bots a `delegate` tool that runs a job in one confined folder on the user's subscription.

**Architecture:** Both CLIs join `tool_paths.PROGRAMS` for their path. `src/harness/agent_clis.py` holds the agent table, the sign-in and capability probes and the switches (`vault-settings/agents.json`). `src/harness/delegation.py` resolves the job folder, launches the CLI with a fixed argument list and the job on stdin, checks its first event, reads its event stream, and kills it on timeout or Stop. `TaskLoop._run_call` asks it whether a call needs approval.

**Tech Stack:** Python 3 / FastAPI / asyncio subprocesses; React 18 + Fluent 2; pytest and the node test runner.

**Spec:** `docs/superpowers/specs/2026-11-16-agent-delegation-design.md`

## Global Constraints

- Never run either CLI with its checks off: no `bypassPermissions`, no `--dangerously-bypass-approvals-and-sandbox`, no `danger-full-access`.
- Nothing a bot wrote is ever a command-line argument. The job goes in on stdin. No shell.
- Claude Code's argument list is exactly: `-p --restricted --strict-mcp-config --tools Read,Write,Edit,Glob,Grep --permission-mode acceptEdits --output-format stream-json --verbose --no-session-persistence`, plus `--model <m>` when Settings has one.
- A model name comes only from Settings and must fully match `[A-Za-z0-9._-]{1,64}`.
- The child's environment is `scrubbed_env()` minus every `ANTHROPIC_*`, `OPENAI_*`, `CLAUDE_CODE_*`, `CODEX_*` variable, keeping `CLAUDE_CONFIG_DIR` and `CODEX_HOME`.
- The job folder resolves (links followed) inside `reachable(...).full`, not inside a Read-Only folder, and neither inside nor holding a private or a Read-Only folder.
- `agents.json` lives in `settings_dir(vault_root)`, never in the vault. The harness never reads a CLI's credential files.
- Codex always needs approval. Claude Code needs it when `ask` is on (default) and always when the bot named the folder.
- Limits: task 20,000 characters; one run per session, two in the harness; `ZEN_DELEGATE_TIMEOUT` default 900 s; at most 200 changed files listed; error tail 2,000 characters.
- Not offered to a routine.
- Do not edit `CHANGELOG.md`. Use `pathlib`; compare resolved paths.
- Tests never run the real `claude` or `codex`. Only Task 5's investigation does, and only in a scratch folder.

## Review Focus

- The CLI dies or prints non-JSON mid-stream: the bot gets a failed result with the output's tail, and no child is left running (Task 3).
- The agent was signed out, or hit its subscription limit, after Settings probed it: the CLI's own message reaches the bot as the error, unchanged (Task 3).
- A job folder whose path holds spaces or non-ASCII characters still runs (Task 3).
- The agent is switched off in Settings while a bot still holds the tool: the call is refused with a plain message, nothing is launched (Task 4).
- An agent that rewrites thousands of files: the result lists 200 and says how many more (Task 3).

---

### Task 1: Program paths for the two CLIs

**Files:**
- Modify: `src/harness/tool_paths.py`
- Test: `tests/test_tool_paths.py`

**Interfaces:**
- Produces: `Program` gains `version_pattern: str | None = None` (a regex with one group holding the version, used in place of `banner\s+version`), `use_found: bool = False` and `example: str = ""`. New `CLAUDE_CODE` (`key="claude-code"`, `bin="claude"`, `name="Claude Code"`, `banner="Claude Code"`, `version_args=("--version",)`, `min_version=(0,)`, `version_pattern=r"(\d+(?:\.\d+)+)\s+\(Claude Code\)"`, `use_found=True`) and `CODEX` (`key="codex"`, `bin="codex"`, `name="Codex"`, `banner="codex-cli"`, `min_version=(0,)`, `use_found=True`), both in `PROGRAMS`. `path_for` and `status` unchanged in signature.

- [ ] **Step 1: Failing tests** in `tests/test_tool_paths.py`, using the file's `fake()` helper generalised to take a file stem:
  - `test_verify_reads_a_version_printed_before_the_name`: a fake `claude` printing `2.1.325 (Claude Code)` verifies with `version == "2.1.325"`.
  - `test_verify_codex_banner`: `codex-cli 0.163.0` gives `"0.163.0"`.
  - `test_verify_refuses_another_program_named_claude`: output `hello` gives `problem == "not_program"`.
  - `test_path_for_uses_an_install_folder_copy_when_use_found`: nothing saved, not on PATH (monkeypatch `shutil.which` to `None`), `candidates` returns the fake: `path_for` returns it and `status(...)["source"] == "found"`.
  - `test_imagemagick_still_ignores_install_folders`: same setup for `imagemagick`: `path_for` is `None`.
  - `test_not_absolute_text_names_this_program`: `problem_text(CLAUDE_CODE, "not_absolute")` does not contain `magick`.
- [ ] **Step 2:** Run `uv run pytest tests/test_tool_paths.py -v`; the new tests fail.
- [ ] **Step 3:** Implement. Candidates for both: `~/.local/bin`, `%APPDATA%\npm`, `~/.npm-global/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `~/.volta/bin`, `~/.bun/bin`, `~/scoop/shims`; on Windows try the names with `.exe` then `.cmd`. `PROBLEMS["not_absolute"]` takes `{example}`; ImageMagick's `example` keeps today's text.
- [ ] **Step 4:** Run the file; all pass.
- [ ] **Step 5:** Commit `feat: program paths for Claude Code and Codex`.

### Task 2: The agent table, probes, switches and routes

**Files:**
- Create: `src/harness/agent_clis.py`, `tests/test_agent_clis.py`
- Modify: `src/harness/api.py` (routes, `Harness.agents`)

**Interfaces:**
- Consumes: `tool_paths.CLAUDE_CODE`, `CODEX`, `path_for`, `status`; `tools.scrubbed_env`; `vault_settings.settings_dir`.
- Produces:
  - `FILE = "agents.json"`; `MODEL = re.compile(r"[A-Za-z0-9._-]{1,64}")`.
  - `@dataclass(frozen=True) AgentCli(key, program, vendor, auth_args, help_args, help_needs, sign_in)`; `AGENTS: dict[str, AgentCli]` with `claude-code` (`auth_args=("auth","status")`, `help_args=("--help",)`, `help_needs=("--restricted","--strict-mcp-config")`, `sign_in="claude"`, `vendor="Anthropic"`) and `codex` (`auth_args=("login","status")`, `help_args=("exec","--help")`, `help_needs=("--sandbox",)`, `sign_in="codex login"`, `vendor="OpenAI"`).
  - `run_env(base: Mapping[str, str] | None = None) -> dict[str, str]`.
  - `read_auth(key: str, output: str | None) -> str`: one of `"subscription"`, `"api_key"`, `"none"`, `"unknown"`. Claude Code: JSON with `loggedIn` true and `authMethod == "claude.ai"` is `subscription`; `loggedIn` true otherwise `api_key`; `loggedIn` false `none`; unparseable `unknown`. Codex: text holding `Logged in using ChatGPT` is `subscription`; `Logged in using` anything else `api_key`; `Not logged in` `none`; else `unknown`.
  - `@dataclass AgentSettings(enabled: bool = False, ask: bool = True, model: str = "")`; `load_settings(vault_root) -> dict[str, AgentSettings]` (every key in `AGENTS`, defaults for missing or malformed entries); `save_settings(vault_root, key, settings) -> None` (atomic, keeps other keys; raises `ValueError` for a non-empty model that does not match `MODEL`).
  - `RUNNABLE: dict[str, str] = {"claude-code": "", "codex": "Codex runs are not built yet."}`: a non-empty string is why this agent can be found but not run. Task 5 changes the Codex entry.
  - `probe(vault_root, key, timeout: float = 10.0) -> dict`: `{"id","name","vendor","path","source","version","auth","confinable","runnable","enabled","ask","model","usable","reason","sign_in"}`. `usable` is path found and verified, `confinable`, `runnable`, and `auth` in (`subscription`, `api_key`). `reason` is one plain sentence for the first thing missing, empty when usable. Probes run the program with `run_env()`.
  - `class Agents(vault_root)`: `async refresh() -> list[dict]` (probes in a thread), `statuses: dict[str, dict]` (last refresh), `on() -> list[str]` (keys both `enabled` and `usable`, from `statuses` and freshly loaded settings), `settings(key) -> AgentSettings`.
  - Routes: `GET /agents` returns `{"agents": [...]}` after a refresh; `PUT /settings/agents/{key}` with `{"enabled"?: bool, "ask"?: bool, "model"?: str}` saves, appends a `Kind.settings` ledger entry (`event="agent"`), refreshes and returns that agent's status; unknown key 404; bad model 422; `enabled: true` for an agent that is not usable 422 with its `reason`. `Harness.agents: Agents`, refreshed once in `lifespan`.

- [ ] **Step 1: Failing tests** in `tests/test_agent_clis.py`, with a helper that writes a fake CLI (a `.bat` on Windows, a shell script elsewhere) answering by its first argument:
  - `test_run_env_drops_keys_and_vendor_variables`: base with `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `OPENAI_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `CODEX_FOO`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `PATH`: only the last three survive.
  - `test_read_auth` parametrised over the eight cases above, plus `None` and `"garbage"` giving `unknown`.
  - `test_settings_default_off_and_asking`, `test_settings_round_trip_keeps_the_other_agent`, `test_settings_file_is_in_the_settings_folder_not_the_vault`, `test_save_refuses_a_model_with_a_space`, `test_malformed_file_reads_as_defaults`.
  - `test_probe_usable`: fake prints the version, help holding `--restricted --strict-mcp-config`, and subscription JSON: `usable` true, `reason == ""`.
  - `test_probe_too_old_to_confine`: help lacks `--restricted`: `confinable` false, `usable` false, `reason` mentions updating.
  - `test_probe_not_signed_in`: `reason` holds the `sign_in` command.
  - `test_probe_not_found`: `path` is `None`, `usable` false.
  - `test_codex_is_not_runnable_yet`: a fully working fake codex has `usable` false and `reason == RUNNABLE["codex"]`.
  - `test_on_needs_enabled_and_usable`.
  - API (httpx `ASGITransport`, as `tests/test_tool_paths.py` builds its app): `test_get_agents_lists_both`, `test_put_enables_a_usable_agent`, `test_put_refuses_enabling_an_unusable_agent_422`, `test_put_unknown_agent_404`, `test_put_bad_model_422`.
- [ ] **Step 2:** Run `uv run pytest tests/test_agent_clis.py -v`; fails on import.
- [ ] **Step 3:** Implement the module, then the routes beside the program routes in `api.py`.
- [ ] **Step 4:** Run `uv run pytest tests/test_agent_clis.py tests/test_tool_paths.py tests/test_api.py -q`.
- [ ] **Step 5:** Commit `feat: detect Claude Code and Codex, with sign-in and switches`.

### Task 3: The runner (Claude Code)

**Files:**
- Create: `src/harness/delegation.py`, `tests/test_delegation.py`, `tests/fixtures/fake_agent_cli.py`
- Modify: `src/memory/ledger.py` (`Kind.delegate = "delegate"`)

**Interfaces:**
- Consumes: `agent_clis.Agents`, `AGENTS`, `run_env`, `MODEL`; `allowed_folders.Folders`; `tools.OUTPUT_CAP`.
- Produces:
  - `CLAUDE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")`; `claude_argv(path: str, model: str = "") -> list[str]`.
  - `check_init(event: dict, folder: Path, subscription: bool) -> str | None`: the problem, or `None`. Problems: a tool outside `CLAUDE_TOOLS`; any `mcp_servers`; `cwd` not the folder (compared resolved, case-insensitively on Windows); `subscription` and `apiKeySource != "none"`.
  - `class FolderRefused(ValueError)`; `resolve_folder(vault_root: Path, folders: Folders, raw: str | None, stamp: str) -> tuple[Path, bool]`: `(folder, made_by_harness)`. `raw` empty makes `<vault>/delegated/<stamp>/` and returns `True`.
  - `PREAMBLE: str` with `{folder}`; `job_text(folder: Path, task: str) -> str`.
  - `changed(before: dict, after: dict) -> list[str]` over `snapshot(folder) -> dict[str, tuple[int, int]]` (relative posix path to size and mtime_ns); `changes_text(names: list[str]) -> str` listing at most 200 then `and N more`.
  - `@dataclass RunResult(ok: bool, text: str, error: str = "", usage: dict = field(default_factory=dict), changed: list[str] = field(default_factory=list))`.
  - `class DelegationError(Exception)`.
  - `class Runner(agents: Agents, ledger, timeout: float = 900.0, max_runs: int = 2)`: `async run(agent: str, folder: Path, task: str, bot_id: str, session_id: str) -> RunResult` (raises `DelegationError` when the session already has a run or `max_runs` are live; never raises a timeout); `stop(session_id: str) -> bool` (kills that session's run; its `run` returns `RunResult(ok=False, error="Stopped by the user.")`); `live: dict[str, ...]` by session.
  - Ledger: `Kind.delegate` entries with `event` `start` (`agent`, `folder`), `file` (`tool`, `path` relative to the folder), `end` (`ok`, `seconds`, `changed` count, `usage`).

`tests/fixtures/fake_agent_cli.py` is run as `python fake_agent_cli.py <argv...>`: it reads stdin, writes its argv, environment names and stdin to `FAKE_AGENT_RECORD` (a path read from its environment; the test's `run_env` patch lets that one variable through), then follows `FAKE_AGENT_MODE`: `ok` (an init event built from its real cwd, one `Write` tool use that really creates `out.txt`, a success result), `extra_tool`, `mcp`, `api_key`, `garbage_line` (a non-JSON line between valid events), `cut_off` (init then exit 1 with text on stderr), `hang` (init, spawns a sleeping child, sleeps), `error_result` (`is_error` true with a message). Tests point the runner at it through a small launcher the helper writes, so no real CLI runs.

- [ ] **Step 1: Failing tests** in `tests/test_delegation.py`:
  - `test_claude_argv_is_exactly_the_approved_list` and `test_claude_argv_adds_only_the_model`.
  - `test_check_init` parametrised: clean passes; extra tool, an MCP server, another cwd, `apiKeySource="ANTHROPIC_API_KEY"` with `subscription=True` each return a problem; that last one with `subscription=False` passes.
  - `test_resolve_folder_makes_one_under_delegated`; refusals, each asserting the reason names the rule: `test_refuses_a_private_folder`, `test_refuses_a_folder_holding_a_private_folder`, `test_refuses_a_read_only_folder`, `test_refuses_a_folder_holding_a_read_only_folder`, `test_refuses_outside_the_writable_folders`, `test_refuses_a_missing_folder`, `test_refuses_a_link_that_leads_out` (skip where links cannot be made).
  - `test_changes_text_caps_at_200`.
  - `test_run_returns_text_and_changed_files` (`ok` mode: text, `out.txt` in `changed`, ledger has `start`, `file`, `end`).
  - `test_task_text_is_never_an_argument` and `test_child_environment_has_no_keys` (from the record file; the job text is in the recorded stdin, after the preamble).
  - `test_run_stops_on_a_bad_first_event` parametrised over `extra_tool`, `mcp`, `api_key`: `ok` false, the error names the problem, `out.txt` was not written.
  - `test_a_non_json_line_is_skipped`; `test_a_cut_off_stream_fails_with_the_tail`; `test_an_error_result_passes_the_cli_message_through`.
  - `test_timeout_kills_the_tree_and_returns_a_plain_failure` (`hang`, `timeout=1`): `ok` false, error says timed out after 1 s, and neither the fake nor its child is alive.
  - `test_stop_kills_the_run`; `test_one_run_per_session`; `test_two_runs_in_the_harness`.
  - `test_a_folder_with_spaces_and_non_ascii_runs`.
- [ ] **Step 2:** Run `uv run pytest tests/test_delegation.py -v`; fails on import.
- [ ] **Step 3:** Implement. Launch with `asyncio.create_subprocess_exec`, `cwd=folder`, `env=run_env()`, stdin piped then closed; a new process group (`start_new_session=True`; `CREATE_NEW_PROCESS_GROUP` on Windows). Kill the tree with `os.killpg` or `taskkill /T /F /PID`. Read stdout line by line; `assistant` events with `tool_use` blocks of `Write` or `Edit` become `file` ledger entries; the `result` event ends the run (`is_error` gives `ok=False` with its `result` text as the error).
- [ ] **Step 4:** Run `uv run pytest tests/test_delegation.py tests/test_ledger.py -q`.
- [ ] **Step 5:** Commit `feat: run Claude Code confined to one folder`.

### Task 4: The `delegate` tool, its approval, and Stop

**Files:**
- Modify: `src/harness/delegation.py`, `src/harness/loop.py` (`TaskLoop.__init__`, `_run_call`), `src/harness/api.py` (`Harness`, `stop`, the `PUT /settings/agents/{key}` route), `src/harness/config.py` (`delegate_timeout` from `ZEN_DELEGATE_TIMEOUT`, default 900)
- Test: `tests/test_delegation.py`, `tests/test_task_loop.py`, `tests/test_agent_clis.py`

**Interfaces:**
- Consumes: Task 3's `Runner`, `resolve_folder`, `job_text`, `changes_text`; `write_guard.named_protected`; `tools.current_call`; `approvals`.
- Produces:
  - `TOOL = "delegate"`; `MAX_TASK = 20_000`.
  - `class Delegator(vault_root, agents, folders: Callable[[], Folders], runner: Runner, in_routine: Callable[[str], bool] = lambda session_id: False)`:
    - `needs_approval(arguments: dict) -> tuple[str, str] | None`: `(rule, detail)`. Codex: always, rule `delegate:codex`. Claude Code: when `ask` is on or `arguments` has a non-empty `folder`, rule `delegate:claude-code`. The detail gives the agent's name, the folder (relative to the vault when inside it, else as given, or "a new folder under delegated/"), the model or "the agent's default", and the whole task. For Codex it adds "Codex can read any file your account can, not only this folder." and, when `named_protected` finds a private folder in the task, starts with a line naming it.
    - `async delegate(agent: str, task: str, folder: str | None = None) -> str`: the tool. Refuses (raises `DelegationError`) an agent not in `agents.on()` ("<name> is switched off in Settings."), a task over `MAX_TASK`, a routine session, a refused folder (the reason). Returns the run's text, a blank line, then `changes_text`. A failed run raises `DelegationError(result.error)`.
    - `register(tools) -> None`: registers `delegate` when `agents.on()` is non-empty, with `agent` as an enum of those keys and a description naming them; unregisters otherwise. Safe to call again.
    - `stop(session_id) -> bool` passes to the runner.
  - `TaskLoop(..., delegate_check: Callable[[dict], tuple[str, str] | None] | None = None)`. In `_run_call`, when no rule matched and `call.name == "delegate"` and `delegate_check` is set, a returned `(rule, detail)` opens the approval; the rejection text is `REJECTED = "The user rejected this hand-off. Do not retry it. Do the work yourself, or say what you would have handed off."` (a constant in `delegation.py`).
  - `Harness.delegator: Delegator`, built in `__post_init__` (idempotent on a rebind, like `images`), registered there, after the `lifespan` refresh, and after a `PUT /settings/agents/{key}`. `Harness.stop` also calls `self.delegator.stop(session_id)`.

- [ ] **Step 1: Failing tests:**
  - `tests/test_delegation.py`: `test_needs_approval_codex_always`; `test_needs_approval_claude_by_the_setting`; `test_needs_approval_claude_always_for_a_named_folder`; `test_codex_detail_warns_about_reads_and_names_a_private_folder_first`; `test_detail_carries_the_whole_task`; `test_delegate_refuses_an_agent_that_is_off` (nothing launched: the record file does not exist); `test_delegate_refuses_a_long_task`; `test_delegate_refuses_in_a_routine`; `test_delegate_returns_text_then_changes`; `test_register_follows_the_switch` (on, off, on again).
  - `tests/test_task_loop.py`, following its existing approval tests: `test_delegate_waits_for_approval_and_runs_when_approved`; `test_delegate_rejected_runs_nothing_and_tells_the_bot` (the tool function is never called; the content is `REJECTED`); `test_delegate_without_a_check_result_runs_at_once`.
  - `tests/test_agent_clis.py`: `test_put_enable_registers_the_tool_and_disable_removes_it`; `test_stop_ends_a_delegated_run` (a `hang` fake under a real `Harness`; after `stop`, no child is alive).
- [ ] **Step 2:** Run the three files; the new tests fail.
- [ ] **Step 3:** Implement. For `in_routine`, find how the harness knows a session belongs to a routine (`src/harness/scheduler.py`, and how `attach_file` refuses outside a conversation or room turn) and pass that test in; if no such marker exists, report NEEDS_CONTEXT with what you found.
- [ ] **Step 4:** Run `uv run pytest tests/test_delegation.py tests/test_task_loop.py tests/test_agent_clis.py tests/test_api.py -q`.
- [ ] **Step 5:** Commit `feat: the delegate tool, with approval and Stop`.

### Task 5: Codex

**Files:**
- Modify: `src/harness/delegation.py`, `src/harness/agent_clis.py` (`RUNNABLE`)
- Test: `tests/test_delegation.py`, `tests/fixtures/fake_agent_cli.py` (a Codex event mode)
- Create: `docs/superpowers/specs/2026-11-16-codex-confinement-notes.md` (what was run and what happened)

**Interfaces:**
- Consumes: Task 3's `Runner`; Task 2's `RUNNABLE`, `read_auth`.
- Produces: `codex_argv(path: str, folder: Path, model: str = "") -> list[str]`; `Runner.run` handles `agent == "codex"` (Codex's `--json` events: `item.completed` with `agent_message` is text, the last one the answer; `turn.completed` carries usage and ends the run; a `command_execution` or file-change item becomes a `file` ledger entry); sign-in is probed again before each Codex run. `RUNNABLE["codex"]` becomes `""` only if the bar below is met, per platform (`sys.platform`), else a sentence saying why.

- [ ] **Step 1: Investigate on this machine.** In a scratch folder outside the repo and outside the temp folder (create it, delete it after), with a harmless prompt, find an argument set for `codex exec` that meets all of:
  1. the user's plugins and MCP servers do not start (start from `--ignore-user-config`; on Windows that alone made every command "blocked by policy", because `[windows] sandbox = "elevated"` lives in the config file, so try passing that with `-c`; read `codex exec --help` and `codex --help`);
  2. a write outside the folder is still refused and the network is still blocked;
  3. an `AGENTS.md` and a `.codex/` folder planted in the job folder run no command of their own.
  Keep it to about ten short runs. Never use `--dangerously-bypass-approvals-and-sandbox` or `danger-full-access`. Write what you ran and saw to the notes file.
- [ ] **Step 2:** If the bar is met: failing tests `test_codex_argv_is_exactly_the_proven_list`, `test_codex_run_returns_the_last_message`, `test_codex_sign_in_is_checked_before_each_run` (a fake whose `login status` says `Not logged in` launches no run), `test_the_windows_cmd_shim_folder_rule` (a folder path holding `%` or `&` is refused when the program is a `.cmd` file), then implement and set `RUNNABLE["codex"] = ""` for the platforms proven, with "Codex runs are checked on Windows only so far." elsewhere.
- [ ] **Step 3:** If the bar is not met: set `RUNNABLE["codex"]` to one plain sentence saying what could not be switched off, add `test_codex_stays_detected_only` asserting it, and stop there.
- [ ] **Step 4:** Run `uv run pytest tests/test_delegation.py tests/test_agent_clis.py -q`.
- [ ] **Step 5:** Commit `feat: Codex runs in its sandbox` or `docs: why Codex is detected only`.

### Task 6: Settings and the activity feed

**Files:**
- Create: `src/gui/agents.js`, `src/gui/AgentSettings.jsx`, `tests/gui/agents.test.mjs`
- Modify: `src/gui/api.js`, `src/gui/app.jsx` (beside `<ProgramPathSection programKey="imagemagick"/>`), `src/gui/ProgramPathSettings.jsx` (no ImageMagick wording for other programs; show `source === "found"`), `src/gui/actions.js` (a label for the `delegate` tool and rows for `delegate` ledger events), `tests/gui/actions.test.mjs`, `tests/gui/api.test.mjs`

**Interfaces:**
- Consumes: `GET /agents`, `PUT /settings/agents/{key}`, the program routes.
- Produces: `api.agents()`, `api.setAgent(key, body)`; `agentStatus(a) -> { text, tone }` in `agents.js` (`tone` one of `success`, `warning`, `danger`); `CodingAgentsSection({ ctx })`.

- [ ] **Step 1: Failing node tests:**
  - `agents.test.mjs`: `agentStatus` for: usable with a subscription (`"2.1.325 · signed in with your subscription"`, `success`); usable with an API key (text says runs bill API credits, `warning`); not signed in (text holds the `sign_in` command, `warning`); too old to confine (`danger`); not found (`warning`, "Not found"); found but not runnable (the `reason`, `warning`).
  - `api.test.mjs`: `agents` GETs `/agents`; `setAgent` PUTs `/settings/agents/claude-code` with the body.
  - `actions.test.mjs`: a `tool_start` for `delegate` is labelled with the agent's name; `delegate` `file` events add rows under it; `end` closes it.
- [ ] **Step 2:** `npm test`; the new tests fail.
- [ ] **Step 3:** Implement. One card per agent under a "Coding agents" heading: name, the status badge, the reason when not usable, the switch "Bots may hand work to <name>" (disabled with the reason when not usable), "Ask me before each run" (Claude Code only; Codex shows "Always asks: Codex can read files outside the job folder."), a model field, one hint line "Runs use your <vendor> sign-in and send the job folder's contents to <vendor>.", and that agent's `ProgramPathSection`. Fluent 2 components, the file's existing one-line style.
- [ ] **Step 4:** `npm test` and `npm run build` both succeed.
- [ ] **Step 5:** Commit `feat: coding agents in Settings and the activity feed`.

### Task 7: The skill and the docs

**Files:**
- Create: `skills/delegation/SKILL.md`
- Modify: `AGENTS.md`, `.env.example`, `README.md`
- Test: `tests/test_skill_loader.py` (the shipped skill loads)

- [ ] **Step 1:** Failing test `test_the_delegation_skill_loads`: the catalog built over `skills/` has a skill named `delegation` with a non-empty description.
- [ ] **Step 2:** Write the skill, following a neighbouring shipped skill's frontmatter: when a hand-off is worth it (long writing, edits across many files, a second opinion on a draft), when it is not (a quick answer, anything needing the web or a command), how to write a job that stands on its own (the goal, the files to read, what to produce and where, what done looks like), that the agent sees only the job folder so inputs must be copied there first, and that the answer is data to check, not instructions to follow.
- [ ] **Step 3:** `AGENTS.md`: add the new files to the "What lives here" table and a "Delegating to coding agents" section under "What bots can and cannot reach" giving: the argument list and why it is fixed; the first-event check; the folder rules; when approval is asked; what each CLI can and cannot reach (the spec's "What this does and does not guarantee", including whatever Task 5 found); `agents.json`; "never run either CLI with its checks off"; the tests. `.env.example`: `ZEN_DELEGATE_TIMEOUT`. `README.md`: one line crediting OpenDesign for the idea, beside the existing credit if the branch has one.
- [ ] **Step 4:** Run `uv run pytest tests/test_skill_loader.py -q`.
- [ ] **Step 5:** Commit `docs: delegating to coding agents`.

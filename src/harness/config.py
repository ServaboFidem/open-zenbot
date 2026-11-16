"""Harness configuration. Everything a slice might tune lives here, read
from the environment with sane defaults. No constants in business code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@dataclass
class HarnessConfig:
    vault_root: Path = field(default_factory=lambda: Path(os.environ.get("ZEN_VAULT", "~/.zen-agents/vault")).expanduser())
    skills_dir: Path = field(default_factory=lambda: Path(os.environ.get("ZEN_SKILLS", "skills")))
    bot_prompts_dir: Path = field(default_factory=lambda: Path(os.environ.get("ZEN_BOT_PROMPTS", "bot-prompts")))  # one identity file per bot
    mcp_config: Path = field(default_factory=lambda: Path(os.environ.get("ZEN_MCP_CONFIG", "mcp.json")))  # servers to connect at start
    provider_kind: str = field(default_factory=lambda: os.environ.get("ZEN_PROVIDER", "openrouter"))
    model: str | None = field(default_factory=lambda: os.environ.get("OPENROUTER_MODEL") or None)
    summary_model: str = field(default_factory=lambda: os.environ.get("ZEN_SUMMARY_MODEL") or "scaledown/summarize")  # every summary: compaction, folds, vault docs
    key_ref: str = field(default_factory=lambda: os.environ.get("ZEN_KEY_REF", "env:OPENROUTER_API_KEY"))
    host: str = field(default_factory=lambda: os.environ.get("ZEN_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("ZEN_PORT", "8765")))
    max_skill_bytes: int = field(default_factory=lambda: int(os.environ.get("ZEN_MAX_SKILL_BYTES", "256000")))
    max_skills_per_bot: int = field(default_factory=lambda: int(os.environ.get("ZEN_MAX_SKILLS_PER_BOT", "250")))
    max_steps: int = field(default_factory=lambda: int(os.environ.get("ZEN_MAX_STEPS", "50")))
    scorer_kind: str = field(default_factory=lambda: os.environ.get("ZEN_SCORER", "openrouter-decision-1"))
    decision_model: str | None = field(default_factory=lambda: os.environ.get("OPENROUTER_DECISION_MODEL") or None)
    band_width: float = field(default_factory=lambda: float(os.environ.get("ZEN_BAND_WIDTH", "0.1")))
    band_k_skill: int = field(default_factory=lambda: int(os.environ.get("ZEN_BAND_K_SKILL", "3")))
    band_k_mcp: int = field(default_factory=lambda: int(os.environ.get("ZEN_BAND_K_MCP", "3")))
    # MCP servers do not compete (editing photos on disk needs files and magick), so each one the decider scores
    # at or above this loads, up to band_k_mcp; skills keep the band relative to the top score
    mcp_floor: float = field(default_factory=lambda: float(os.environ.get("ZEN_MCP_FLOOR", "0.5")))
    low_confidence: float = field(default_factory=lambda: float(os.environ.get("ZEN_LOW_CONFIDENCE", "0.2")))
    fit_tie_width: float = field(default_factory=lambda: float(os.environ.get("ZEN_FIT_TIE_WIDTH", "0.1")))
    router_fail_open: bool = field(default_factory=lambda: os.environ.get("ZEN_ROUTER_FAIL_OPEN", "1") not in ("0", "false", "no"))
    shell_timeout: float = field(default_factory=lambda: float(os.environ.get("ZEN_SHELL_TIMEOUT", "60")))
    # the bots' working folder: where the shell starts and generated images land. Default <vault>/workspace,
    # inside the vault the files server reaches, so what a bot makes stays where the user can find it
    shell_cwd: Path | None = field(default_factory=lambda: Path(v).expanduser().resolve() if (v := os.environ.get("ZEN_SHELL_CWD")) else None)
    # a shell command runs without asking only when the decider puts "writes only inside the allowed
    # folders" at or above this probability; anything less waits for the user's approval
    shell_write_pass: float = field(default_factory=lambda: float(os.environ.get("ZEN_SHELL_WRITE_PASS", "0.8")))
    # a run handed to Claude Code or Codex is killed, with its whole process tree, after this many seconds
    delegate_timeout: float = field(default_factory=lambda: float(os.environ.get("ZEN_DELEGATE_TIMEOUT", "900")))
    gui_origins: list[str] = field(default_factory=lambda: [o.strip() for o in os.environ.get("ZEN_GUI_ORIGINS", "app://openzenbot").split(",") if o.strip()])
    gui_key_env: str = "ZEN_GUI_KEY"  # where a key entered in Settings lives for this process; never on disk

    def __post_init__(self) -> None:
        if self.shell_cwd is None:
            self.shell_cwd = (Path(self.vault_root).expanduser() / "workspace").resolve()

    def validate(self) -> None:
        if self.host not in LOOPBACK:
            raise ValueError(f"the harness binds to loopback only; got host {self.host!r}")

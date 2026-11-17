"""Where the harness keeps its settings: next to the vault, not in it.

    ~/.zen-agents/vault           what bots may read and write (the files MCP server's root)
    ~/.zen-agents/vault-ledger    the ledger, rooms, conversations (src.memory.ledger.ledger_path_for)
    ~/.zen-agents/vault-settings  providers and their keys, MCP keys, tracing, models, tools, allowed folders

The files server is rooted at the vault, so anything inside it is readable and writable by a bot. Settings
hold API keys, and `allowed_folders.json` decides what that server may reach, so they live beside the vault.
`settings_dir` is the one function that owns the mapping; `migrate` moves an older `<vault>/settings/` out."""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

SUFFIX = "-settings"
BOTS_SUFFIX = "-bots"
SKILLS_SUFFIX = "-skills"
OLD = "settings"  # where settings lived inside the vault before


def settings_dir(vault_root: Path) -> Path:
    vault_root = Path(vault_root)
    return vault_root.parent / f"{vault_root.name}{SUFFIX}"


def user_bots_dir(vault_root: Path) -> Path:
    """The user's own bots (`<id>.md`), beside the vault so a bot cannot write its own identity."""
    vault_root = Path(vault_root)
    return vault_root.parent / f"{vault_root.name}{BOTS_SUFFIX}"


def user_skills_dir(vault_root: Path) -> Path:
    """The user's own skills, beside the vault for the same reason."""
    vault_root = Path(vault_root)
    return vault_root.parent / f"{vault_root.name}{SKILLS_SUFFIX}"


def migrate(vault_root: Path) -> list[str]:
    """Move `<vault>/settings/*` to `settings_dir`. A file already there wins; an old copy identical to it is
    dropped, a different one is left in place and named in the result as a conflict. Returns notes for the log."""
    old = Path(vault_root) / OLD
    if not old.is_dir():
        return []
    new = settings_dir(vault_root)
    new.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    for src in sorted(old.iterdir()):
        dest = new / src.name
        if not dest.exists():
            shutil.move(str(src), str(dest))
            notes.append(f"moved {src.name}")
        elif src.is_file() and dest.is_file() and filecmp.cmp(src, dest, shallow=False):
            src.unlink()
        else:
            notes.append(f"conflict: {src} differs from {dest}; the copy in {new.name} is used, the vault copy left in place")
    try:
        old.rmdir()  # only when empty
    except OSError:
        pass
    return notes

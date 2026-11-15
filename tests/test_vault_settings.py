"""Settings live beside the vault (<vault>-settings), out of the files MCP server's reach, like the ledger
(<vault>-ledger). An older <vault>/settings/ is moved out at start; a copy already beside the vault wins."""

from __future__ import annotations

import json
from pathlib import Path

from src.harness.mcp_keys import McpKeys
from src.providers.store import ProviderStore
from src.vault_settings import migrate, settings_dir


def test_settings_dir_sits_beside_the_vault(tmp_path: Path):
    assert settings_dir(tmp_path / "vault") == tmp_path / "vault-settings"
    assert settings_dir(tmp_path / "work" / "my-vault") == tmp_path / "work" / "my-vault-settings"


def test_saved_settings_land_beside_the_vault_not_in_it(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    McpKeys().with_keys("pexels", {"PEXELS_API_KEY": "pk-123456789"}).save(vault)
    assert (tmp_path / "vault-settings" / "mcp_keys.json").is_file()
    assert not (vault / "settings").exists()
    assert ProviderStore(vault).path == tmp_path / "vault-settings" / "providers.json"


def test_migrate_moves_the_old_folder_out_and_keeps_a_newer_copy(tmp_path: Path):
    vault = tmp_path / "vault"
    old, new = vault / "settings", tmp_path / "vault-settings"
    old.mkdir(parents=True)
    new.mkdir()
    (old / "mcp_keys.json").write_text(json.dumps({"servers": {"a": {"K": "old-key-1234"}}}), encoding="utf-8")
    (old / "tools.json").write_text("{}", encoding="utf-8")
    (new / "tools.json").write_text("{}", encoding="utf-8")  # identical: the old copy just goes
    (old / "harness.json").write_text('{"chat": 1}', encoding="utf-8")
    (new / "harness.json").write_text('{"chat": 2}', encoding="utf-8")  # different: the new one wins, old stays

    notes = migrate(vault)
    assert "moved mcp_keys.json" in notes and any(n.startswith("conflict:") and "harness.json" in n for n in notes)
    assert McpKeys.load(vault).for_server("a") == {"K": "old-key-1234"}
    assert not (old / "mcp_keys.json").exists() and not (old / "tools.json").exists()
    assert (new / "harness.json").read_text(encoding="utf-8") == '{"chat": 2}' and (old / "harness.json").exists()

    (old / "harness.json").unlink()
    assert migrate(vault) == [] and not old.exists()  # an emptied old folder is removed
    assert migrate(vault) == []  # nothing to do

"""The harness probes claude and codex at start; no other test may run the real programs."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog


async def test_lifespan_spawns_nothing_for_the_agents(tmp_path: Path, monkeypatch):
    spawned: list[str] = []
    real_popen = subprocess.Popen

    class Spy(real_popen):
        def __init__(self, args, *a, **kw):
            spawned.append(str(args[0] if isinstance(args, (list, tuple)) else args))
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Spy)  # subprocess.run goes through Popen
    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", mcp_config=tmp_path / "mcp.json")
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    app = create_app(h)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.2)  # the background refresh has run
        assert set(h.agents.statuses) == {"claude-code", "codex"}
        assert not any(h.agents.statuses[k]["usable"] for k in h.agents.statuses)
    assert not [s for s in spawned if Path(s).stem.lower() in ("claude", "codex")]

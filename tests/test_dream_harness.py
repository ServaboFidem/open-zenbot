"""Dreaming end to end through the real Harness: a quiet one-to-one conversation, the dreaming model chosen in
Settings (a fake provider stands in for it), "Dream now" through the route, and the fact read back from
`GET /dream/facts`."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

import src.providers.openai_compatible  # noqa: F401 - registered before the test swaps in its fake factory
from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.harness.model_roles import Choice
from src.memory.ledger import Ledger
from src.providers import registry
from src.providers.interface import Completion, Message
from src.providers.models import ModelInfo
from src.skills.catalog import Catalog

from .conftest import ScriptedProvider


class FakeDreamModel:
    """The local model chosen as the Dreaming model: keeps each prompt, answers one fact."""

    kind = "openai-compatible"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, messages, *, tools=None, model=None, session_id=None):
        self.calls.append("\n".join(m.content for m in messages))
        body = json.dumps({"facts": [{"text": "Gordon lives in Columbus.", "store": "personal"}]})
        return Completion(message=Message("assistant", body), finish_reason="stop", model=model or "fake")

    async def probe(self):
        return True


@pytest.fixture
def harness(vault: Path, tmp_path: Path) -> Harness:
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path / "workspace")
    cfg.validate()
    return Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())


@pytest.fixture
async def client(harness: Harness):
    transport = httpx.ASGITransport(app=create_app(harness))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c
    await harness.dreamer.close()


async def run_pass(client: httpx.AsyncClient) -> None:
    r = await client.post("/dream/run", json={})
    assert r.status_code == 200, r.text

    async def ended() -> None:
        while (await client.get("/dream/status")).json()["dreaming"]:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(ended(), 5.0)


async def test_dream_now_learns_a_fact_from_the_users_own_words(client, harness, monkeypatch):
    model = FakeDreamModel()
    built: list[dict] = []

    def factory(**kw):
        built.append(kw)
        return model

    monkeypatch.setitem(registry._factories, "openai-compatible", factory)
    store = harness.provider_store
    store.add("Local", "openai-compatible", "http://localhost:1234/v1", no_key=True)
    store.set_models("local", [ModelInfo(id="qwen3-14b", name="qwen3-14b", kinds=["text"])])
    roles = harness.model_roles()
    roles.dream = Choice("local", "qwen3-14b")
    roles.save(harness.config.vault_root)
    harness.profile.people.set_owner("Gordon", [])

    convs = harness.conversations
    conv, _ = convs.begin("bot1", "I live in Columbus", session_id="s1", addendum="ADDENDUM ONE")
    convs.finish(conv.id, "s1", "done", "REPLY ONE")
    convs.begin("bot1", "I prefer metric units", session_id="s2", conv_id=conv.id, addendum="ADDENDUM TWO")
    convs.finish(conv.id, "s2", "done", "REPLY TWO")
    conv.updated_at = datetime.fromtimestamp(time.time() - 3600, timezone.utc).isoformat(timespec="seconds")

    assert (await client.put("/dream/settings", json={"enabled": True, "idle_minutes": 5})).status_code == 200
    await run_pass(client)

    facts = (await client.get("/dream/facts")).json()
    assert [(f["text"], f["conversation"], f["title"]) for f in facts] == [("Gordon lives in Columbus.", conv.id, conv.title)]
    assert conv.title and len(model.calls) == 1
    prompt = model.calls[0]
    assert "I live in Columbus" in prompt and "I prefer metric units" in prompt
    for hidden in ("REPLY ONE", "REPLY TWO", "ADDENDUM ONE", "ADDENDUM TWO"):
        assert hidden not in prompt
    assert built and built[0]["timeout"] == 600.0  # the dreaming model is given ten minutes a call

    await run_pass(client)  # nothing new to read: no further model call
    assert len(model.calls) == 1
    assert len((await client.get("/dream/facts")).json()) == 1

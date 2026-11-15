"""image.generate: config from the environment, the OpenRouter images call, the saved file, routing."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import httpx
import pytest

from src.generation.images import (
    DEFAULT_MODEL,
    GROUP,
    TOOL,
    ImageConfig,
    ImageGenerator,
)
from src.harness.loop import allowed_tool_groups
from src.harness.tools import ToolRunner, current_call
from src.memory.ledger import Ledger
from src.providers.interface import ProviderAuthError, ToolCall
from src.skills.catalog import Catalog
from src.vault_settings import settings_dir

KEY = "sk-or-v1-imagetestkey0123456789abcdef"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


def _gen(tmp_path: Path, handler, monkeypatch, attach=None, ledger=None, env=None) -> ImageGenerator:
    monkeypatch.setenv("ZEN_TEST_IMAGE_KEY", KEY)
    return ImageGenerator(ImageConfig.from_env(env or {}), tmp_path, key_ref=lambda: "env:ZEN_TEST_IMAGE_KEY",
                          attach=attach, ledger=ledger, transport=httpx.MockTransport(handler))


def _ok(media_type: str = "image/png") -> httpx.Response:
    return httpx.Response(200, json={"created": 1, "data": [{"b64_json": base64.b64encode(PNG).decode(), "media_type": media_type}],
                                     "usage": {"cost": 0.04}})


def test_config_defaults_and_env():
    assert ImageConfig.from_env({}) == ImageConfig("openrouter", DEFAULT_MODEL)
    assert DEFAULT_MODEL == "tencent/hy-image-v3.5-preview"
    cfg = ImageConfig.from_env({"ZEN_IMAGE_PROVIDER": " OpenRouter ", "ZEN_IMAGE_MODEL": "black-forest-labs/flux-2"})
    assert cfg == ImageConfig("openrouter", "black-forest-labs/flux-2")


async def test_generate_posts_to_the_images_endpoint_and_saves_the_file(tmp_path: Path, monkeypatch, vault: Path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _ok()

    attached = []
    ledger = Ledger(vault)
    gen = _gen(tmp_path, handler, monkeypatch, attach=lambda path, title: attached.append((path, title)) or "Attached.",
               ledger=ledger, env={"ZEN_IMAGE_MODEL": "some/model"})
    token = current_call.set(("bot1", "s1"))
    try:
        out = await gen.generate("A red panda astronaut, studio lighting", aspect_ratio="16:9", resolution="2K", title="Panda")
    finally:
        current_call.reset(token)
    assert seen["url"] == "https://openrouter.ai/api/v1/images"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"] == {"model": "some/model", "prompt": "A red panda astronaut, studio lighting",
                            "aspect_ratio": "16:9", "resolution": "2K"}
    files = list((tmp_path / "generated-images").iterdir())
    assert len(files) == 1 and files[0].suffix == ".png" and files[0].read_bytes() == PNG
    assert "red-panda-astronaut" in files[0].name
    assert attached == [(f"generated-images/{files[0].name}", "Panda")]
    assert "Attached." in out and "some/model" in out
    entry = [e for e in ledger.iter() if e.kind == "image"][-1]
    assert entry.bot_id == "bot1" and entry.session_id == "s1" and entry.data["cost"] == 0.04


async def test_media_type_sets_the_extension_and_no_attach_says_how_to_show(tmp_path: Path, monkeypatch):
    gen = _gen(tmp_path, lambda r: _ok("image/jpeg"), monkeypatch)
    out = await gen.generate("a lighthouse")
    assert "attach_file" in out
    assert [f.suffix for f in (tmp_path / "generated-images").iterdir()] == [".jpg"]


async def test_bad_arguments_and_no_key_are_refused_without_a_call(tmp_path: Path, monkeypatch):
    calls = []
    gen = _gen(tmp_path, lambda r: calls.append(r) or _ok(), monkeypatch)
    assert "empty" in await gen.generate("  ")
    assert "aspect_ratio" in await gen.generate("x", aspect_ratio="7:3")
    assert "resolution" in await gen.generate("x", resolution="8K")
    gen.bound = lambda: False
    assert "Settings" in await gen.generate("x")
    gen.bound, gen.key_ref = (lambda: True), (lambda: "env:ZEN_TEST_NO_SUCH_KEY")
    monkeypatch.delenv("ZEN_TEST_NO_SUCH_KEY", raising=False)
    assert "Settings" in await gen.generate("x")
    assert calls == []


async def test_provider_errors_come_back_as_a_failed_tool_call(tmp_path: Path, monkeypatch):
    gen = _gen(tmp_path, lambda r: httpx.Response(401, json={}), monkeypatch)
    with pytest.raises(ProviderAuthError):
        await gen.generate("x")
    tools = ToolRunner(tmp_path)
    gen.register(tools, Catalog())
    result = await tools.run(ToolCall("c1", TOOL, {"prompt": "x"}))
    assert not result.ok and "rejected the key" in (result.error or "")
    empty = _gen(tmp_path, lambda r: httpx.Response(200, json={"data": []}), monkeypatch)
    with pytest.raises(Exception, match="no image"):
        await empty.generate("x")


def test_register_adds_a_routed_group_and_a_catalog_entry_once(tmp_path: Path, monkeypatch):
    tools, catalog = ToolRunner(tmp_path), Catalog()
    gen = _gen(tmp_path, lambda r: _ok(), monkeypatch)
    assert gen.register(tools, catalog) and gen.register(tools, catalog)
    assert [e.ref for e in catalog.mcps()] == [GROUP]
    route_without = {"mcps": ["exa"], "bands": {"mcp": {"winners": ["exa"], "outcome": "act"}}}
    route_with = {"mcps": ["exa", GROUP], "bands": {"mcp": {"winners": ["exa", GROUP], "outcome": "act"}}}
    assert TOOL not in [s.name for s in tools.specs(allowed_tool_groups(route_without))]
    assert TOOL in [s.name for s in tools.specs(allowed_tool_groups(route_with))]


def test_unknown_provider_registers_nothing(tmp_path: Path, monkeypatch):
    tools, catalog = ToolRunner(tmp_path), Catalog()
    gen = _gen(tmp_path, lambda r: _ok(), monkeypatch, env={"ZEN_IMAGE_PROVIDER": "nowhere"})
    assert not gen.register(tools, catalog)
    assert TOOL not in tools.names() and catalog.mcps() == []


def test_the_harness_offers_the_tool_with_attach_file(tmp_path: Path, vault: Path):
    from src.harness.api import Harness
    from src.harness.config import HarnessConfig

    from .conftest import ScriptedProvider

    h = Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path), provider=ScriptedProvider(), ledger=Ledger(vault),
                catalog=Catalog())
    assert TOOL in h.tools.names()
    assert h.images is not None and h.images.attach is h.tools.fn("attach_file")
    assert any(e.ref == GROUP for e in h.catalog.mcps())


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="live: needs OPENROUTER_API_KEY")
async def test_live_generates_an_image(tmp_path: Path):
    gen = ImageGenerator(ImageConfig.from_env(), tmp_path, key_ref=lambda: "env:OPENROUTER_API_KEY")
    out = await gen.generate("a small watercolor of a zen garden with raked sand", resolution="1K")
    files = list((tmp_path / "generated-images").iterdir())
    assert files and files[0].stat().st_size > 1000, out


def test_saved_settings_win_over_the_environment(vault: Path):
    env = {"ZEN_IMAGE_MODEL": "env/model", "ZEN_IMAGE_ENABLED": "0"}
    assert ImageConfig.load(vault, env) == ImageConfig("openrouter", "env/model", enabled=False)
    ImageConfig("openrouter", "saved/model", enabled=True).save(vault)
    assert ImageConfig.load(vault, env) == ImageConfig("openrouter", "saved/model", enabled=True)
    (settings_dir(vault) / "tools.json").write_text("not json")
    assert ImageConfig.load(vault, env).model == "env/model"


def test_disable_takes_the_tool_and_its_catalog_entry_away(tmp_path: Path, monkeypatch):
    tools, catalog = ToolRunner(tmp_path), Catalog()
    gen = _gen(tmp_path, lambda r: _ok(), monkeypatch)
    gen.register(tools, catalog)
    gen.reconfigure(ImageConfig(enabled=False))
    assert not gen.register(tools, catalog)
    assert TOOL not in tools.names() and catalog.mcps() == []
    gen.reconfigure(ImageConfig(enabled=True))
    assert gen.register(tools, catalog) and TOOL in tools.names()


async def test_settings_api_reads_and_changes_the_image_tool(tmp_path: Path, vault: Path):
    from src.harness.api import Harness, create_app
    from src.harness.config import HarnessConfig

    from .conftest import ScriptedProvider

    h = Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path), provider=ScriptedProvider(), ledger=Ledger(vault),
                catalog=Catalog())
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        [tool] = (await c.get("/settings/tools")).json()["tools"]
        assert tool["id"] == "image" and tool["enabled"] and tool["active"] and tool["providers"] == ["openrouter"]
        assert tool["default_model"] == DEFAULT_MODEL
        assert (await c.put("/settings/tools/image", json={"model": "other/model"})).status_code == 422  # not enabled yet
        from src.providers.models import ModelInfo

        h.provider_store.set_models("openrouter", h.provider_store.get("openrouter").models + [ModelInfo("other/model", "Other", ["image"])])
        r = await c.put("/settings/tools/image", json={"enabled": False, "model": "other/model"})
        assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["model"] == "other/model"
        assert TOOL not in h.tools.names() and not any(e.ref == GROUP for e in h.catalog.mcps())
        assert ImageConfig.load(vault, {}).model == "other/model"
        r = await c.put("/settings/tools/image", json={"enabled": True, "model": ""})
        assert r.json()["model"] == DEFAULT_MODEL and TOOL in h.tools.names()
        assert (await c.put("/settings/tools/image", json={"provider": "nowhere"})).status_code == 422
        assert (await c.put("/settings/tools/image", json={"model": "meta/muse-spark-1.3-contributor"})).status_code == 422  # a text model
        assert (await c.put("/settings/tools/image", json={"model": "two words"})).status_code == 422
        assert [e.data["tool"] for e in h.ledger.iter() if e.kind == "settings"] == ["image", "image"]

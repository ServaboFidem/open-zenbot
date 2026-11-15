"""Model providers: providers.json, model lists with kind/reasoning/cost, the OpenAI-compatible adapter,
and the Settings routes that enable models and choose them per role."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx
import pytest

from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.harness.model_roles import ModelRoles
from src.memory.exclusion import exclusion
from src.memory.ledger import Ledger
from src.providers.interface import Message
from src.providers.models import ModelInfo, ModelLister, cost_label, from_openai, from_openrouter, guess_kinds
from src.providers.openai_compatible import OpenAICompatibleProvider
from src.providers.secrets import Secret
from src.providers.store import ProviderStore, ProviderStoreError
from src.skills.catalog import Catalog

from .conftest import ScriptedProvider

KEY = "sk-groq-testkey0123456789abcdefghij"

OR_MODELS = {"data": [
    {"id": "meta/muse-spark-1.3-contributor", "name": "Muse Spark", "context_length": 200000,
     "architecture": {"output_modalities": ["text"]}, "supported_parameters": ["tools", "reasoning"],
     "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}},
    {"id": "scaledown/summarize", "name": "Summarize", "architecture": {"output_modalities": ["text"]},
     "supported_parameters": ["max_tokens"], "pricing": {"prompt": "0.00000004", "completion": "0"}},
    {"id": "tencent/hy-image-v3.5-preview", "name": "HY Image", "architecture": {"output_modalities": ["image"]},
     "pricing": {"prompt": "0", "completion": "0", "image_output": "0.0000016"}},
    {"id": "microsoft/microsoft-decision-1", "name": "Decision 1", "architecture": {"output_modalities": ["decisions"]},
     "pricing": {"prompt": "0.000000042", "completion": "0"}},
    {"id": "free/model", "name": "Free", "architecture": {"output_modalities": ["text"]}, "pricing": {"prompt": "0", "completion": "0"}},
]}
OPENAI_MODELS = {"object": "list", "data": [{"id": "llama-3.3-70b"}, {"id": "text-embedding-3-small"}, {"id": "whisper-large-v3"}]}


def _transport(seen: list | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.host == "openrouter.ai":
            return httpx.Response(200, json=OR_MODELS)
        return httpx.Response(200, json=OPENAI_MODELS)
    return httpx.MockTransport(handler)


# -- model facts ------------------------------------------------------------


def test_cost_labels():
    assert cost_label({"prompt": "0.0000001", "completion": "0.0000002"}, ["text"]) == "$0.10 in · $0.20 out per 1M tokens"
    assert cost_label({"prompt": "0.00000004", "completion": "0"}, ["text"]) == "$0.04 per 1M input tokens"
    assert cost_label({"prompt": "0", "completion": "0", "image_output": "0.0000016"}, ["image"]) == "$1.60 per 1M image tokens"
    assert cost_label({"prompt": "0", "completion": "0"}, ["text"]) == "free"
    assert cost_label(None, ["text"]) is None and cost_label({}, ["text"]) is None
    assert cost_label({"prompt": "-1", "completion": "-1"}, ["text"]) == "price varies with the model it routes to"


def test_openrouter_facts_give_kinds_and_reasoning():
    by_id = {m.id: m for m in (from_openrouter(r) for r in OR_MODELS["data"])}
    assert by_id["meta/muse-spark-1.3-contributor"].kinds == ["text"] and by_id["meta/muse-spark-1.3-contributor"].reasoning is True
    assert by_id["scaledown/summarize"].reasoning is False
    assert by_id["tencent/hy-image-v3.5-preview"].kinds == ["image"] and by_id["tencent/hy-image-v3.5-preview"].reasoning is None
    assert by_id["microsoft/microsoft-decision-1"].kinds == ["decision"]
    assert guess_kinds("text-embedding-3-small") == ["embedding"] and guess_kinds("whisper-large-v3") == ["transcription"]
    assert guess_kinds("gpt-image-1") == ["image"] and guess_kinds("llama-3.3-70b") == ["text"]



def test_openrouter_capabilities_are_kept():
    m = from_openrouter({"id": "a/vision", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
                         "supported_parameters": ["tools", "structured_outputs", "reasoning"],
                         "pricing": {"prompt": "0.000001", "completion": "0.000002", "request": "0", "image": "junk"},
                         "top_provider": {"max_completion_tokens": 8192}, "description": "d", "knowledge_cutoff": "2025-01"})
    assert m.input_modalities == ["text", "image"] and m.output_modalities == ["text"]
    assert m.supported_parameters == ["tools", "structured_outputs", "reasoning"]
    assert m.pricing == {"prompt": 1e-6, "completion": 2e-6, "request": 0.0}
    assert m.max_output_tokens == 8192 and m.description == "d" and m.knowledge_cutoff == "2025-01"
    assert m.image_input is True and m.structured_output is True and m.tools is True
    assert "image_input" not in m.to_dict() and m.public()["tools"] is True
    assert ModelInfo.from_dict(m.to_dict()) == m  # round-trips through providers.json


def test_response_format_counts_as_structured_output_and_missing_lists_stay_unknown():
    m = from_openrouter({"id": "a/json", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                         "supported_parameters": ["response_format"]})
    assert m.structured_output is True and m.tools is False and m.image_input is False
    bare = from_openrouter({"id": "a/bare", "architecture": {"output_modalities": ["text"]}})
    assert bare.tools is None and bare.structured_output is None and bare.image_input is None


def test_id_only_and_old_snapshots_leave_capabilities_unknown():
    for m in (from_openai({"id": "llama"}), ModelInfo.from_dict({"id": "x", "kinds": ["audio"]})):
        assert m.input_modalities is None and m.supported_parameters is None and m.pricing is None and m.max_output_tokens is None
        assert m.image_input is None and m.structured_output is None and m.tools is None
    assert ModelInfo.from_dict({"id": "x", "kinds": ["audio"]}).kinds == ["audio"]


def test_audio_splits_into_three_kinds():
    def kinds(mod: str) -> list[str]:
        return from_openrouter({"id": "m", "architecture": {"output_modalities": [mod]}}).kinds
    assert kinds("speech") == ["tts"] and kinds("transcription") == ["transcription"] and kinds("audio") == ["audio"]
    assert guess_kinds("kokoro-tts") == ["tts"] and guess_kinds("whisper-1") == ["transcription"]
    assert guess_kinds("musicgen-audio") == ["audio"]

async def test_lister_asks_openrouter_for_every_modality_and_caches():
    seen: list[httpx.Request] = []
    lister = ModelLister(transport=_transport(seen))
    models = await lister.list("openrouter", "openrouter", "https://openrouter.ai/api", None)
    assert seen[0].url.path == "/api/v1/models" and seen[0].url.params["output_modalities"] == "all"
    assert "authorization" not in seen[0].headers  # the list is public: no key needed
    assert [m.name for m in models][:2] == ["Decision 1", "Free"]  # sorted by name
    await lister.list("openrouter", "openrouter", "https://openrouter.ai/api", None)
    assert len(seen) == 1
    await lister.list("openrouter", "openrouter", "https://openrouter.ai/api", None, refresh=True)
    assert len(seen) == 2


async def test_lister_reads_an_openai_compatible_list_with_its_key():
    seen: list[httpx.Request] = []
    models = await ModelLister(transport=_transport(seen)).list("groq", "openai-compatible", "https://api.groq.com/openai/v1", Secret(KEY))
    assert seen[0].url.path == "/openai/v1/models" and seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert {m.id: m.kinds for m in models} == {"llama-3.3-70b": ["text"], "text-embedding-3-small": ["embedding"],
                                              "whisper-large-v3": ["transcription"]}
    assert all(m.guessed and m.cost is None and m.reasoning is None for m in models)


async def test_openai_compatible_chat_leaves_out_openrouter_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}]})

    p = OpenAICompatibleProvider(Secret(KEY), model="llama-3.3-70b", base_url="https://api.groq.com/openai/v1",
                                 transport=httpx.MockTransport(handler))
    out = await p.complete([Message(role="user", content="hello")], session_id="s1")
    assert seen["path"] == "/openai/v1/chat/completions" and out.message.content == "hi"
    assert "usage" not in seen["body"] and "session_id" not in seen["body"] and seen["body"]["model"] == "llama-3.3-70b"
    assert KEY not in repr(p)


# -- the store --------------------------------------------------------------


def test_store_keeps_keys_out_of_public_views_and_off_other_eyes(vault: Path):
    store = ProviderStore(vault).load()
    e = store.add("Groq", "openai-compatible", "https://api.groq.com/openai/v1/", key=KEY)
    assert e.id == "groq" and e.base_url == "https://api.groq.com/openai/v1"
    assert KEY not in json.dumps(e.public()) and e.public()["key_set"] and e.public()["key_source"] == "stored"
    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    assert mode == 0o600
    assert store.key_ref("groq") == "env:ZEN_PROVIDER_GROQ_API_KEY" and store.secret("groq").reveal() == KEY
    assert "registered-secret" in exclusion.find(f"leaked {KEY}")
    again = ProviderStore(vault).load()  # a restart reads the key back
    assert again.secret("groq").reveal() == KEY
    store.update("groq", key_ref="env:GROQ_KEY_NOT_SET")
    assert store.get("groq").key is None and not store.get("groq").key_set() and "ZEN_PROVIDER_GROQ_API_KEY" not in os.environ
    assert store.add("Groq", "openai-compatible", "https://api.groq.com/openai/v1").id == "groq-2"
    store.remove("groq-2")
    assert "groq-2" not in ProviderStore(vault).load().entries


def test_store_refuses_bad_input(vault: Path):
    store = ProviderStore(vault)
    for args, msg in [(("", "openrouter"), "name"), (("x", "llama"), "unknown provider kind"),
                      (("x", "openai-compatible", "http://api.example.com"), "https"), (("x", "openai-compatible", None), "https"),
                      (("x", "openrouter", None, None, "OPENROUTER_API_KEY"), "env:NAME"), (("x", "openrouter", None, "k", "env:A"), "not both")]:
        with pytest.raises(ProviderStoreError, match=msg):
            store.add(*args)
    assert store.add("Local", "openai-compatible", "http://127.0.0.1:1234/v1").base_url == "http://127.0.0.1:1234/v1"


# -- the routes -------------------------------------------------------------


def enable(harness: Harness, provider_id: str, *ids: str) -> None:
    """Enable models on an id-only provider without the capability test (see test_model_probe.py)."""
    harness.provider_store.set_models(provider_id, [ModelInfo(id=i, name=i, kinds=guess_kinds(i), guessed=True) for i in ids])


@pytest.fixture
def harness(vault: Path, tmp_path: Path, monkeypatch) -> Harness:
    monkeypatch.setenv("ZEN_TEST_OR_KEY", "sk-or-v1-routes0123456789abcdef")
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path, key_ref="env:ZEN_TEST_OR_KEY")
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    h.model_lister = ModelLister(transport=_transport())
    return h


@pytest.fixture
async def client(harness: Harness):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(harness)), base_url="http://localhost") as c:
        yield c


async def test_first_start_seeds_openrouter_with_the_models_in_use(client):
    body = (await client.get("/settings/providers")).json()
    [p] = body["providers"]
    assert body["kinds"] == ["openrouter", "openai-compatible"]
    assert p["id"] == "openrouter" and p["key_set"] and p["key_source"] == "env:ZEN_TEST_OR_KEY"
    assert {m["id"]: m["kinds"] for m in p["models"]} == {
        "meta/muse-spark-1.3-contributor": ["text"], "scaledown/summarize": ["text"],
        "microsoft/microsoft-decision-1": ["decision"], "tencent/hy-image-v3.5-preview": ["image"]}
    assert "Chat model" in p["in_use"] and "Image generation" in p["in_use"]
    assert {m["id"]: m["name"] for m in p["models"]}["meta/muse-spark-1.3-contributor"] == "Muse Spark"  # facts filled in from the list
    roles = (await client.get("/settings/models")).json()
    assert roles["chat"] == {"provider": "openrouter", "model": "meta/muse-spark-1.3-contributor"} and roles["roles"]["decision"] == "decision"


async def test_listing_marks_enabled_models_and_refreshes_their_snapshots(client, harness: Harness):
    models = (await client.get("/settings/providers/openrouter/models")).json()["models"]
    by_id = {m["id"]: m for m in models}
    assert by_id["meta/muse-spark-1.3-contributor"]["enabled"] and not by_id["free/model"]["enabled"]
    assert by_id["meta/muse-spark-1.3-contributor"]["cost"] == "$0.10 in · $0.20 out per 1M tokens"
    snap = harness.provider_store.get("openrouter").model("meta/muse-spark-1.3-contributor")
    assert snap.name == "Muse Spark" and snap.reasoning is True  # the seeded snapshot took the provider's facts
    assert by_id["meta/muse-spark-1.3-contributor"]["tools"] is True and snap.supported_parameters == ["tools", "reasoning"]
    saved = json.loads(harness.provider_store.path.read_text(encoding="utf-8"))
    assert all("tools" not in m for p in saved["providers"] for m in p["models"])  # derived flags never stored
    assert (await client.get("/settings/providers/nope/models")).status_code == 404


async def test_enable_models_and_refuse_dropping_one_in_use(client):
    r = await client.put("/settings/providers/openrouter/models", json={"enabled": ["meta/muse-spark-1.3-contributor"]})
    assert r.status_code == 409 and "scaledown/summarize (Summary model)" in r.json()["detail"]
    keep = ["meta/muse-spark-1.3-contributor", "scaledown/summarize", "microsoft/microsoft-decision-1", "tencent/hy-image-v3.5-preview"]
    r = await client.put("/settings/providers/openrouter/models", json={"enabled": keep + ["free/model", "unknown/embed-x"]})
    assert r.status_code == 200
    by_id = {m["id"]: m for m in r.json()["models"]}
    assert by_id["free/model"]["cost"] == "free" and by_id["unknown/embed-x"]["kinds"] == ["embedding"] and by_id["unknown/embed-x"]["guessed"]


async def test_add_a_provider_and_run_chat_on_it_with_summaries_on_openrouter(client, harness: Harness, vault: Path):
    r = await client.post("/settings/providers", json={"name": "Groq", "kind": "openai-compatible",
                                                        "base_url": "https://api.groq.com/openai/v1", "key": KEY})
    assert r.status_code == 201 and KEY not in r.text
    enable(harness, "groq", "llama-3.3-70b", "text-embedding-3-small")
    r = await client.put("/settings/models", json={"chat": {"provider": "groq", "model": "text-embedding-3-small"}})
    assert r.status_code == 422 and "not a text model" in r.json()["detail"]
    r = await client.put("/settings/models", json={"chat": {"provider": "groq", "model": "llama-3.3-70b"}})
    assert r.status_code == 200, r.text
    assert harness.provider.kind == "openai-compatible" and harness.provider.model == "llama-3.3-70b"
    assert harness.summary_provider is not None and harness.summary_provider.kind == "openrouter"
    assert harness.config.key_ref == "env:ZEN_PROVIDER_GROQ_API_KEY" and harness.openrouter_ref == "env:ZEN_TEST_OR_KEY"
    assert ModelRoles.load(vault).chat.provider == "groq"
    assert (await client.delete("/settings/providers/groq")).status_code == 409
    r = await client.put("/settings/models", json={"decision": {"provider": "groq", "model": "llama-3.3-70b"}, "scorer": "openrouter-decision-1"})
    assert r.status_code == 422
    assert KEY not in json.dumps([e.to_dict() for e in harness.ledger.iter()])
    assert {e.data["event"] for e in harness.ledger.iter() if e.kind == "settings"} >= {"provider_added", "models"}  # models_enabled: test_model_probe.py


async def test_a_provider_without_a_key_cannot_run_a_role(client, harness: Harness):
    await client.post("/settings/providers", json={"name": "Nokey", "kind": "openai-compatible", "base_url": "https://api.example.com/v1"})
    enable(harness, "nokey", "llama-3.3-70b")
    r = await client.put("/settings/models", json={"chat": {"provider": "nokey", "model": "llama-3.3-70b"}})
    assert r.status_code == 422 and "has no key" in r.json()["detail"]
    assert (await client.delete("/settings/providers/nokey")).status_code == 204


def test_saved_roles_apply_at_start(vault: Path, tmp_path: Path, monkeypatch):
    from src.harness.main import build_harness

    monkeypatch.setenv("ZEN_TEST_OR_KEY", "sk-or-v1-start0123456789abcdef")
    store = ProviderStore(vault).load()
    store.add("Groq", "openai-compatible", "https://api.groq.com/openai/v1", key=KEY)
    store.set_models("groq", [ModelInfo("llama-3.3-70b", "Llama", ["text"])])
    ModelRoles.from_dict({"chat": {"provider": "groq", "model": "llama-3.3-70b"},
                          "summary": {"provider": "groq", "model": "llama-3.3-70b"}, "scorer": ""}).save(vault)
    cfg = HarnessConfig(vault_root=vault, shell_cwd=tmp_path, key_ref="env:ZEN_TEST_OR_KEY", skills_dir=tmp_path / "none",
                        bot_prompts_dir=tmp_path / "none")
    h = build_harness(cfg)
    assert h.provider.kind == "openai-compatible" and h.summary_provider is None and h.config.summary_model == "llama-3.3-70b"


# -- presets and keyless servers ----------------------------------------------


async def test_presets_come_with_the_provider_list(client):
    presets = {p["id"]: p for p in (await client.get("/settings/providers")).json()["presets"]}
    assert presets["nvidia-nim"]["base_url"] == "https://integrate.api.nvidia.com/v1" and not presets["nvidia-nim"]["no_key"]
    assert presets["ollama"]["base_url"] == "http://localhost:11434/v1" and presets["ollama"]["no_key"] and presets["ollama"]["local"]
    assert {"vllm", "lmstudio"} <= set(presets)
    assert all(p["kind"] == "openai-compatible" for p in presets.values())


def test_plain_http_only_nearby(vault: Path):
    store = ProviderStore(vault)
    for url in ("http://localhost:11434/v1", "http://192.168.1.20:8000/v1", "http://gpu-box:1234/v1", "http://studio.local:1234/v1"):
        assert store.add("x", "openai-compatible", url, no_key=True).base_url == url
    with pytest.raises(ProviderStoreError, match="https"):
        store.add("x", "openai-compatible", "http://api.example.com/v1")
    with pytest.raises(ProviderStoreError, match="needs a key"):
        store.add("x", "openrouter", no_key=True)


def test_plain_http_for_tailscale_peers(vault: Path):
    """A machine on the tailnet is reached over WireGuard: plain http to it is as safe as the LAN."""
    store = ProviderStore(vault)
    for url in ("http://100.101.102.103:11434/v1", "http://100.64.0.1:8000/v1", "http://100.127.255.254:8000/v1",
                "http://gpu-box.tail1234.ts.net:8000/v1", "http://GPU-Box.Tail1234.TS.NET.:8000/v1",
                "http://[fd7a:115c:a1e0::1]:8000/v1"):
        e = store.add("x", "openai-compatible", url, no_key=True)
        assert e.base_url == url and e.free()
    for url in ("http://100.63.255.255/v1", "http://100.128.0.1/v1",  # just outside 100.64.0.0/10
                "http://ts.net/v1", "http://evil-ts.net/v1", "http://box.ts.net.example.com/v1"):
        with pytest.raises(ProviderStoreError, match="https"):
            store.add("x", "openai-compatible", url, no_key=True)


def test_local_servers_and_nvidia_nim_cost_nothing(vault: Path):
    store = ProviderStore(vault)
    unpriced, priced = ModelInfo(id="m", name="m", kinds=["text"]), ModelInfo(id="p", name="p", kinds=["text"], cost="$1 per 1M tokens")
    for url in ("http://localhost:1234/v1", "http://192.168.1.20:8000/v1", "http://gpu-box:11434/v1", "https://integrate.api.nvidia.com/v1"):
        e = store.add("x", "openai-compatible", url, key=KEY)
        assert e.free() and e.model_out(unpriced)["cost"] == "Free" and e.model_out(priced)["cost"] == "$1 per 1M tokens"
    hosted = store.add("groq", "openai-compatible", "https://api.groq.com/openai/v1", key=KEY)
    assert not hosted.free() and hosted.model_out(unpriced)["cost"] is None
    assert not store.add("or", "openrouter", key=KEY).free()
    lm = store.add("lm", "openai-compatible", "http://localhost:1234/v1", no_key=True)
    lm.models = [unpriced]
    assert lm.public()["models"][0]["cost"] == "Free" and lm.to_file()["models"][0]["cost"] is None  # shown, never saved


async def test_a_local_servers_model_list_shows_free(client):
    await client.post("/settings/providers", json={"name": "LM Studio", "kind": "openai-compatible",
                                                   "base_url": "http://localhost:1234/v1", "no_key": True})
    models = (await client.get("/settings/providers/lm-studio/models")).json()["models"]
    assert models and all(m["cost"] == "Free" for m in models)


async def test_a_keyless_server_runs_chat_and_sends_no_key(client, harness: Harness):
    r = await client.post("/settings/providers", json={"name": "Ollama", "kind": "openai-compatible",
                                                        "base_url": "http://localhost:11434/v1", "no_key": True})
    p = r.json()
    assert r.status_code == 201 and p["no_key"] and p["usable"] and not p["key_set"]
    enable(harness, "ollama", "llama-3.3-70b")
    r = await client.put("/settings/models", json={"chat": {"provider": "ollama", "model": "llama-3.3-70b"}})
    assert r.status_code == 200, r.text
    assert harness.provider.kind == "openai-compatible" and "authorization" not in harness.provider.http._client.headers


# -- a bot's own model ----------------------------------------------------------


async def test_a_bot_gets_its_own_model_and_keeps_it(client, harness: Harness, vault: Path):
    from src.harness.bot import Bot
    from src.harness.model_roles import load_bot_models

    harness.add_bot(Bot(name="Writer", role="Writes", id="writer"))
    keep = ["meta/muse-spark-1.3-contributor", "scaledown/summarize", "microsoft/microsoft-decision-1", "tencent/hy-image-v3.5-preview"]
    await client.put("/settings/providers/openrouter/models", json={"enabled": keep + ["free/model"]})
    r = await client.patch("/bots/writer", json={"model": {"provider": "openrouter", "model": "tencent/hy-image-v3.5-preview"}})
    assert r.status_code == 422 and "not a text model" in r.json()["detail"]
    r = await client.patch("/bots/writer", json={"model": {"provider": "openrouter", "model": "free/model"}})
    assert r.status_code == 200 and r.json()["model"] == {"provider": "openrouter", "model": "free/model"}
    assert load_bot_models(vault)["writer"].model == "free/model"
    provider, model = harness._model_for(harness.bots["writer"])
    assert provider is harness.provider and model == "free/model"  # same provider as chat: only the model changes
    r = await client.patch("/bots/writer", json={"role": "Edits"})  # leaving model out keeps it
    assert r.json()["model"]["model"] == "free/model"
    assert "bot Writer" in (await client.get("/settings/providers")).json()["providers"][0]["in_use"]
    r = await client.put("/settings/providers/openrouter/models", json={"enabled": keep})
    assert r.status_code == 409 and "bot Writer" in r.json()["detail"]
    r = await client.patch("/bots/writer", json={"model": None})
    assert r.json()["model"] is None and "writer" not in load_bot_models(vault)
    assert harness._model_for(harness.bots["writer"]) == (harness.provider, None)


async def test_a_local_server_saved_as_keyed_takes_a_bot_once_marked_keyless(client, harness: Harness):
    # an LM Studio added without "needs no API key" (the add form used to drop it): a bot cannot take its
    # model until the provider's card marks it keyless, which the PATCH does
    from src.harness.bot import Bot

    harness.add_bot(Bot(name="Writer", role="Writes", id="writer"))
    await client.post("/settings/providers", json={"name": "LM Studio", "kind": "openai-compatible",
                                                   "base_url": "http://localhost:1234/v1"})
    enable(harness, "lm-studio", "qwen-9b")
    r = await client.patch("/bots/writer", json={"model": {"provider": "lm-studio", "model": "qwen-9b"}})
    assert r.status_code == 422 and "has no key" in r.json()["detail"]
    p = (await client.patch("/settings/providers/lm-studio", json={"no_key": True})).json()
    assert p["no_key"] and p["usable"]
    assert harness.ledger.tail(kind="settings")[-1].data["no_key"] is True
    r = await client.patch("/bots/writer", json={"model": {"provider": "lm-studio", "model": "qwen-9b"}})
    assert r.status_code == 200 and r.json()["model"] == {"provider": "lm-studio", "model": "qwen-9b"}


async def test_a_bot_on_another_provider_runs_its_task_there(client, harness: Harness):
    from src.harness.bot import Bot

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=OPENAI_MODELS)
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "from groq"}}]})

    harness.add_bot(Bot(name="Fast", role="Answers fast", id="fast"))
    await client.post("/settings/providers", json={"name": "Groq", "kind": "openai-compatible", "base_url": "https://api.groq.com/openai/v1", "key": KEY})
    enable(harness, "groq", "llama-3.3-70b")
    assert (await client.patch("/bots/fast", json={"model": {"provider": "groq", "model": "llama-3.3-70b"}})).status_code == 200
    provider, model = harness._model_for(harness.bots["fast"])
    assert provider.kind == "openai-compatible" and model == "llama-3.3-70b"
    assert harness._model_for(harness.bots["fast"])[0] is provider  # one client per provider, reused
    provider.http._client._transport = httpx.MockTransport(handler)
    session = await harness.loop.run(harness.bots["fast"], "hello")
    assert session.result == "from groq" and seen[0]["model"] == "llama-3.3-70b"


async def test_a_bot_whose_model_was_disabled_falls_back_to_chat(client, harness: Harness):
    from src.harness.bot import Bot
    from src.harness.model_roles import Choice

    bot = harness.add_bot(Bot(name="Old", role="r", id="old"))
    harness.bot_models["old"] = Choice("gone", "x/y")
    assert harness._model_for(bot) == (harness.provider, None)
    assert any(e.kind == "error" and e.data.get("where") == "bot_model" for e in harness.ledger.iter())

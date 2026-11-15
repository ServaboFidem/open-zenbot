"""Probing an OpenAI-compatible model's capabilities when it is enabled: the tests, the job, the routes."""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from src.providers.models import ModelInfo
from src.providers.openrouter import OpenRouterHttp
from src.providers.probe import STEPS, ProbeFailed, Unavailable, run_probe
from src.providers.secrets import Secret

BASE = "https://nim.example/v1"
TEXT = ModelInfo(id="meta/llama", name="meta/llama", kinds=["text"], guessed=True)


def step_of(body: dict[str, Any]) -> str:
    """Which test a request belongs to, read from its body."""
    if "input" in body:
        return "embeddings"
    if body.get("tools"):
        return "tools"
    if body.get("response_format"):
        return "structured"
    content = body["messages"][-1]["content"]
    return "image" if isinstance(content, list) else "chat"


def reply(content: str | None = None, **message: Any) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content, **message}}]})


GOOD: dict[str, Callable[[], httpx.Response]] = {
    "chat": lambda: reply("ok", reasoning_content="thinking"),
    "tools": lambda: reply(None, tool_calls=[{"id": "c1", "type": "function",
                                              "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}}]),
    "structured": lambda: reply('{"color": "red"}'),
    "image": lambda: reply("red"),
    "embeddings": lambda: PAGE_404(),  # a chat model: the server has no embeddings route for it
}


def PAGE_404() -> httpx.Response:
    """What NIM answers when the model doesn't serve this kind of request."""
    return httpx.Response(404, text="404 page not found\n")


def EMBEDS() -> httpx.Response:
    return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})


def transport(overrides: dict[str, Callable[[], httpx.Response]] | None = None, seen: list | None = None) -> httpx.MockTransport:
    table = {**GOOD, **(overrides or {})}

    def handler(request: httpx.Request) -> httpx.Response:
        step = step_of(json.loads(request.content))
        if seen is not None:
            seen.append(step)
        return table[step]()
    return httpx.MockTransport(handler)


async def probe(model: ModelInfo = TEXT, overrides: dict | None = None, events: list | None = None) -> ModelInfo:
    http = OpenRouterHttp(Secret("nvapi-test-0123456789abcdef"), base_url=BASE, timeout=60.0, transport=transport(overrides))
    try:
        return await run_probe(http, model, (events if events is not None else []).append)
    finally:
        await http.aclose()


def raise_(exc: Exception) -> Callable[[], httpx.Response]:
    def f() -> httpx.Response:
        raise exc
    return f


# -- the tests ----------------------------------------------------------------


async def test_a_capable_model_passes_every_step():
    events: list = []
    m = await probe(events=events)
    assert m.reasoning is True and m.kinds == ["text"] and m.guessed is False and m.probed_at
    assert m.supported_parameters == ["reasoning", "tools", "structured_outputs", "response_format"]
    assert m.input_modalities == ["text", "image"] and m.output_modalities == ["text"]
    assert m.tools and m.structured_output and m.image_input
    assert [(e["step"], e["status"]) for e in events if e["status"] != "running"] == [
        ("chat", "pass"), ("embeddings", "fail"), ("tools", "pass"), ("structured", "pass"), ("image", "pass")]


async def test_ignored_tools_and_fenced_json_fail():
    m = await probe(overrides={"chat": lambda: reply("ok"), "tools": lambda: reply("It is sunny in Paris."),
                               "structured": lambda: reply('```json\n{"color": "red"}\n```'),
                               "image": lambda: httpx.Response(400, json={"error": {"message": "images not supported"}})})
    assert m.supported_parameters == [] and m.reasoning is False and m.input_modalities == ["text"]
    assert m.tools is False and m.structured_output is False and m.image_input is False


async def test_a_server_that_refuses_a_named_tool_choice_is_asked_with_required():
    """LM Studio and llama.cpp answer 400 to tool_choice {type: function, ...}; the model still calls tools."""
    choices: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        step = step_of(body)
        if step != "tools":
            return GOOD[step]()
        choices.append(body["tool_choice"])
        if body["tool_choice"] == "required":
            return GOOD["tools"]()
        return httpx.Response(400, json={"error": {
            "message": "Invalid tool_choice type: 'object'. Supported string values: none, auto, required"}})

    http = OpenRouterHttp(Secret("lm-studio-0123456789abcdef"), base_url=BASE, timeout=60.0, transport=httpx.MockTransport(handler))
    try:
        m = await run_probe(http, TEXT, lambda e: None)
    finally:
        await http.aclose()
    assert m.tools
    assert choices == [{"type": "function", "function": {"name": "get_weather"}}, "required"]


async def test_think_tags_count_as_reasoning():
    assert (await probe(overrides={"chat": lambda: reply("<think>hmm</think>ok")})).reasoning is True



async def test_a_reasoning_model_gets_room_to_think_before_answering():
    """A reasoning model spends a small budget thinking and returns no content (finish_reason length)."""
    budgets: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        step = step_of(body)
        budgets[step] = body.get("max_tokens", 0)
        if step == "embeddings":
            return PAGE_404()
        if step == "chat":
            return reply(None, reasoning_content="thinking")
        if body["max_tokens"] < 1024:
            return httpx.Response(200, json={"choices": [{"finish_reason": "length",
                                                          "message": {"role": "assistant", "content": None, "reasoning_content": "hmm"}}]})
        return GOOD[step]()

    http = OpenRouterHttp(Secret("nvapi-test-0123456789abcdef"), base_url=BASE, timeout=60.0, transport=httpx.MockTransport(handler))
    try:
        m = await run_probe(http, TEXT, lambda e: None)
    finally:
        await http.aclose()
    assert m.reasoning is True and m.tools and m.structured_output and m.image_input
    assert budgets["chat"] == 64 and min(budgets[s] for s in ("tools", "structured", "image")) >= 1024

@pytest.mark.parametrize("response, reason", [
    (lambda: httpx.Response(429), "the provider is rate limiting requests"),
    (lambda: httpx.Response(503), "the provider had a server error (503)"),
    (raise_(httpx.ReadTimeout("slow")), "the model took longer than 60 seconds to answer"),
    (raise_(httpx.ConnectError("down")), "the provider could not be reached"),
])
async def test_unavailable_reasons(response, reason):
    with pytest.raises(Unavailable) as ex:
        await probe(overrides={"structured": response})
    assert ex.value.reason == reason


async def test_a_rejected_chat_fails_the_probe_and_says_why():
    with pytest.raises(ProbeFailed) as ex:
        await probe(overrides={"chat": lambda: httpx.Response(400, json={"error": {"message": "max_tokens too large"}})})
    assert "max_tokens too large" in str(ex.value) and "HTTP 400" in str(ex.value)


async def test_a_listed_model_the_key_cannot_use_says_so():
    """NIM lists models its key can't run: chat answers 404 with the reason in `detail`, not `error.message`."""
    nim_404 = {"status": 404, "title": "Not Found", "detail": "Function 'ac74': Not found for account 'BI7l'"}
    with pytest.raises(ProbeFailed) as ex:
        await probe(overrides={"chat": lambda: httpx.Response(404, json=nim_404)})
    msg = str(ex.value)
    assert "isn't available to this key" in msg and "Not found for account" in msg


async def test_what_a_model_does_comes_from_the_tests_not_its_name():
    """nvidia/nemotron-3.5-embed-1b on NIM: no chat route, and embeddings need `input_type` (asymmetric)."""
    def asymmetric() -> httpx.Response:
        return httpx.Response(400, json={"object": "error", "message": "'input_type' parameter is required for asymmetric models"})

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        step = step_of(body)
        seen.append((step, body.get("input_type")))
        if step == "chat":
            return PAGE_404()
        return EMBEDS() if body.get("input_type") == "query" else asymmetric()

    events: list = []
    http = OpenRouterHttp(Secret("nvapi-test-0123456789abcdef"), base_url=BASE, timeout=60.0, transport=httpx.MockTransport(handler))
    try:  # named like a chat model, guessed as one: the name decides nothing
        m = await run_probe(http, ModelInfo(id="nvidia/llama-like", name="x", kinds=["text"], guessed=True), events.append)
    finally:
        await http.aclose()
    assert m.kinds == ["embedding"] and m.output_modalities == ["embeddings"] and m.input_modalities == ["text"]
    assert m.supported_parameters == ["input_type"] and m.reasoning is None and m.guessed is False and m.probed_at
    assert seen == [("chat", None), ("embeddings", None), ("embeddings", "query")]
    assert [(e["step"], e["status"]) for e in events if e["status"] != "running"] == [
        ("chat", "fail"), ("embeddings", "pass"), ("tools", "skip"), ("structured", "skip"), ("image", "skip")]


async def test_a_model_named_for_embeddings_that_chats_is_a_chat_model():
    m = await probe(ModelInfo(id="nvidia/nemotron-3.5-embed-1b", name="x", kinds=["embedding"], guessed=True))
    assert m.kinds == ["text"] and m.tools


async def test_a_model_that_does_both_is_both():
    m = await probe(overrides={"embeddings": EMBEDS})
    assert m.kinds == ["text", "embedding"] and m.output_modalities == ["text", "embeddings"] and m.tools


async def test_a_model_that_does_neither_cannot_be_enabled():
    with pytest.raises(ProbeFailed) as ex:
        await probe(overrides={"chat": PAGE_404})
    assert not ex.value.unusable and "neither" in str(ex.value)


async def test_a_model_the_server_says_does_not_exist_is_unusable():
    with pytest.raises(ProbeFailed) as ex:  # vLLM / OpenAI wording
        await probe(overrides={"chat": lambda: httpx.Response(404, json={"error": {"message": "The model `x` does not exist."}})})
    assert ex.value.unusable


# -- the jobs and routes ------------------------------------------------------

import asyncio  # noqa: E402

from src.harness.api import Harness  # noqa: E402
from src.providers.probe import ProbeJobs  # noqa: E402

from .test_model_providers import client, harness  # noqa: E402,F401 - the route fixtures

NIM_MODEL = "llama-3.3-70b"  # in the mocked OpenAI-compatible list


def nim(harness: Harness, overrides: dict | None = None, handler: Any = None) -> str:
    e = harness.provider_store.add("NVIDIA NIM", "openai-compatible", BASE, key="nvapi-test-0123456789abcdef")
    harness.probes = ProbeJobs(transport=httpx.MockTransport(handler) if handler else transport(overrides))
    return e.id


async def events_of(client: httpx.AsyncClient, pid: str, probe_id: str) -> list[dict]:
    out = []
    async with client.stream("GET", f"/settings/providers/{pid}/models/probe/{probe_id}/events") as s:
        async for line in s.aiter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


async def test_probe_enables_the_model_with_what_it_found(client, harness: Harness):
    pid = nim(harness)
    r = await client.post(f"/settings/providers/{pid}/models/probe", json={"model": NIM_MODEL})
    assert r.status_code == 200, r.text
    assert r.json()["steps"] == STEPS == ["chat", "embeddings", "tools", "structured", "image"]
    evs = await events_of(client, pid, r.json()["probe_id"])
    end = evs[-1]
    assert end["kind"] == "end" and end["status"] == "done"
    [m] = end["provider"]["models"]
    assert m["id"] == NIM_MODEL and m["tools"] is True and m["image_input"] is True and m["probed_at"] and m["guessed"] is False
    saved = harness.provider_store.get(pid).model(NIM_MODEL)
    assert saved is not None and saved.supported_parameters == ["reasoning", "tools", "structured_outputs", "response_format"]
    assert any(e.kind == "settings" and e.data.get("event") == "models_enabled" and e.data.get("probed") for e in harness.ledger.iter())


async def test_unavailable_late_step_enables_nothing(client, harness: Harness):
    pid = nim(harness, {"image": lambda: httpx.Response(429)})
    r = await client.post(f"/settings/providers/{pid}/models/probe", json={"model": NIM_MODEL})
    end = (await events_of(client, pid, r.json()["probe_id"]))[-1]
    assert end["status"] == "unavailable" and end["reason"] == "the provider is rate limiting requests"
    assert harness.provider_store.get(pid).model(NIM_MODEL) is None


async def test_stop_leaves_the_model_off_and_frees_the_slot(client, harness: Harness):
    reached = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        step = step_of(json.loads(request.content))
        if step == "tools":
            reached.set()
            await asyncio.sleep(30)
        return GOOD[step]()

    pid = nim(harness, handler=handler)
    r = await client.post(f"/settings/providers/{pid}/models/probe", json={"model": NIM_MODEL})
    probe_id = r.json()["probe_id"]
    await asyncio.wait_for(reached.wait(), 5)
    assert (await client.post(f"/settings/providers/{pid}/models/probe", json={"model": "whisper-large-v3"})).status_code == 409
    assert (await client.post(f"/settings/providers/{pid}/models/probe/{probe_id}/stop")).status_code == 200
    end = (await events_of(client, pid, probe_id))[-1]
    assert end["status"] == "stopped" and harness.provider_store.get(pid).model(NIM_MODEL) is None
    harness.probes.transport = transport()
    again = await client.post(f"/settings/providers/{pid}/models/probe", json={"model": NIM_MODEL})
    assert again.status_code == 200 and (await events_of(client, pid, again.json()["probe_id"]))[-1]["status"] == "done"


async def test_probe_refusals_and_one_at_a_time(client, harness: Harness):
    pid = nim(harness)
    assert (await client.post("/settings/providers/openrouter/models/probe", json={"model": "free/model"})).status_code == 422
    r = await client.put(f"/settings/providers/{pid}/models", json={"enabled": [NIM_MODEL]})
    assert r.status_code == 422 and "one at a time" in r.json()["detail"]
    r = await client.post(f"/settings/providers/{pid}/models/probe", json={"model": NIM_MODEL})
    await events_of(client, pid, r.json()["probe_id"])
    assert (await client.post(f"/settings/providers/{pid}/models/probe", json={"model": NIM_MODEL})).status_code == 422  # enabled already
    r = await client.put(f"/settings/providers/{pid}/models", json={"enabled": []})  # removing still works
    assert r.status_code == 200 and r.json()["models"] == []
    assert (await client.get(f"/settings/providers/{pid}/models/probe/nope/events")).status_code == 404


async def test_late_subscriber_gets_the_whole_run(client, harness: Harness):
    pid = nim(harness)
    probe_id = (await client.post(f"/settings/providers/{pid}/models/probe", json={"model": NIM_MODEL})).json()["probe_id"]
    first = await events_of(client, pid, probe_id)
    late = await events_of(client, pid, probe_id)
    assert late == first and late[-1]["kind"] == "end" and len(late) == 11


async def test_a_model_that_neither_chats_nor_embeds_is_not_enabled(client, harness: Harness):
    pid = nim(harness, {"chat": PAGE_404})
    r = await client.post(f"/settings/providers/{pid}/models/probe", json={"model": "kokoro-tts"})
    end = (await events_of(client, pid, r.json()["probe_id"]))[-1]
    assert end["status"] == "failed" and "neither" in end["error"] and "unusable" not in end
    assert harness.provider_store.get(pid).model("kokoro-tts") is None and harness.provider_store.get(pid).unusable == []


async def test_a_probed_model_keeps_its_findings_and_shows_them_in_the_list(client, harness: Harness):
    pid = nim(harness)
    r = await client.post(f"/settings/providers/{pid}/models/probe", json={"model": NIM_MODEL})
    await events_of(client, pid, r.json()["probe_id"])
    for refresh in (False, True):  # reading the provider's id-only list again must not wipe what the probe found
        listed = {m["id"]: m for m in (await client.get(f"/settings/providers/{pid}/models", params={"refresh": refresh})).json()["models"]}
        row = listed[NIM_MODEL]
        assert row["enabled"] and row["tools"] is True and row["image_input"] is True and row["probed_at"] and row["guessed"] is False
        saved = harness.provider_store.get(pid).model(NIM_MODEL)
        assert saved.probed_at and saved.supported_parameters == ["reasoning", "tools", "structured_outputs", "response_format"]
    [p] = [p for p in (await client.get("/settings/providers")).json()["providers"] if p["id"] == pid]
    assert p["models"][0]["tools"] is True


async def test_a_model_the_key_cannot_use_is_hidden_until_it_passes(client, harness: Harness):
    nim_404 = {"status": 404, "title": "Not Found", "detail": "Function 'ac74': Not found for account 'BI7l'"}
    pid = nim(harness, {"chat": lambda: httpx.Response(404, json=nim_404)})
    r = await client.post(f"/settings/providers/{pid}/models/probe", json={"model": NIM_MODEL})
    end = (await events_of(client, pid, r.json()["probe_id"]))[-1]
    assert end["status"] == "failed" and end["unusable"] is True and NIM_MODEL in end["provider"]["unusable"]
    assert harness.provider_store.get(pid).unusable == [NIM_MODEL]
    rows = {m["id"]: m for m in (await client.get(f"/settings/providers/{pid}/models")).json()["models"]}
    assert rows[NIM_MODEL]["unusable"] is True and rows["whisper-large-v3"]["unusable"] is False
    # other refusals (a 400) don't hide a model
    harness.probes.transport = transport({"chat": lambda: httpx.Response(400, json={"error": {"message": "bad"}})})
    r = await client.post(f"/settings/providers/{pid}/models/probe", json={"model": "whisper-large-v3"})
    assert "unusable" not in (await events_of(client, pid, r.json()["probe_id"]))[-1]
    # access granted later: a passing test takes it off the list
    harness.probes.transport = transport()
    r = await client.post(f"/settings/providers/{pid}/models/probe", json={"model": NIM_MODEL})
    assert (await events_of(client, pid, r.json()["probe_id"]))[-1]["status"] == "done"
    assert harness.provider_store.get(pid).unusable == []
    harness.provider_store.load()  # survives a restart
    assert harness.provider_store.get(pid).unusable == []

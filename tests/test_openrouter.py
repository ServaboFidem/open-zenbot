"""openrouter-adapter: offline with a mock transport; live only with a key."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from src.memory.exclusion import exclusion
from src.providers import registry
from src.providers.interface import Message, ProviderAuthError, ProviderError, ProviderUnavailable, ToolCall, ToolSpec
from src.providers.openrouter import KIND, OpenRouterProvider
from src.providers.secrets import Secret

KEY = "sk-or-v1-testkey0123456789abcdef"


def _provider(handler) -> OpenRouterProvider:
    return OpenRouterProvider(Secret(KEY), model="test/model", transport=httpx.MockTransport(handler))


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def test_registered_under_its_kind():
    assert registry.is_registered(KIND)


def test_repr_and_exclusion_never_leak_key():
    p = _provider(lambda r: _ok({}))
    assert KEY not in repr(p)
    assert "registered-secret" in exclusion.find(f"the key is {KEY} ok")


async def test_tool_call_round_trip():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _ok({
            "model": "test/model",
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "shell", "arguments": "{\"command\": \"ls\"}"}}],
                },
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "cost": 0.00002},
        })

    p = _provider(handler)
    tools = [ToolSpec("shell", "run", {"type": "object", "properties": {"command": {"type": "string"}}})]
    msgs = [
        Message("system", "sys"),
        Message("user", "list files"),
        Message("assistant", "", tool_calls=[ToolCall("c0", "shell", {"command": "pwd"})]),
        Message("tool", "/tmp", tool_call_id="c0", name="shell"),
    ]
    c = await p.complete(msgs, tools=tools)

    assert seen["auth"] == f"Bearer {KEY}"
    body = seen["body"]
    assert body["model"] == "test/model"
    assert body["usage"] == {"include": True}
    assert body["tools"][0]["function"]["name"] == "shell"
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == "{\"command\": \"pwd\"}"
    assert body["messages"][3] == {"role": "tool", "content": "/tmp", "tool_call_id": "c0", "name": "shell"}

    assert c.kind == KIND
    assert c.finish_reason == "tool_calls"
    assert c.message.tool_calls == [ToolCall("c1", "shell", {"command": "ls"})]
    assert c.usage.prompt_tokens == 12 and c.usage.cost == 0.00002


async def test_dotted_tool_names_go_out_valid_and_come_back_as_ours():
    # strict OpenAI-style APIs (NVIDIA NIM) refuse anything outside [A-Za-z0-9_-] in a function name
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _ok({"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "type": "function", "function": {"name": "image__generate", "arguments": "{\"prompt\": \"a cat\"}"}}]}}]})

    obj = {"type": "object", "properties": {}}
    tools = [ToolSpec("image.generate", "make", obj), ToolSpec("pexels.photos_search", "find", obj), ToolSpec("shell", "run", obj)]
    msgs = [
        Message("user", "go"),
        Message("assistant", "", tool_calls=[ToolCall("c1", "pexels.photos_search", {"query": "bees"})]),
        Message("tool", "[]", tool_call_id="c1", name="pexels.photos_search"),
    ]
    c = await _provider(handler).complete(msgs, tools=tools)

    body = seen["body"]
    assert [t["function"]["name"] for t in body["tools"]] == ["image__generate", "pexels__photos_search", "shell"]
    assert body["messages"][1]["tool_calls"][0]["function"]["name"] == "pexels__photos_search"
    assert body["messages"][2]["name"] == "pexels__photos_search"
    assert c.message.tool_calls == [ToolCall("c2", "image.generate", {"prompt": "a cat"})]


def test_wire_names_stay_unique_and_valid():
    from src.providers.openrouter import ToolNames, wire_name

    names = ToolNames(["a.b", "a__b", "x y/z", "n." + "q" * 80])
    # a name already valid keeps itself; the rewritten one that would clash gets a suffix
    assert names.wire("a__b") == "a__b" and names.wire("a.b") == "a__b_2"
    assert names.local("a__b_2") == "a.b" and names.local("a__b") == "a__b"
    assert names.wire("x y/z") == "x_y_z"
    assert len(names.wire("n." + "q" * 80)) == 64
    assert names.local("not-listed") == "not-listed"
    assert wire_name("..") == "____" and wire_name("") == "_"


async def test_plain_text_completion():
    p = _provider(lambda r: _ok({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}]}))
    c = await p.complete([Message("user", "hi")])
    assert c.message.content == "done" and not c.message.tool_calls


@pytest.mark.parametrize("status,exc", [(401, ProviderAuthError), (403, ProviderAuthError), (429, ProviderUnavailable), (500, ProviderUnavailable), (503, ProviderUnavailable), (400, ProviderError)])
async def test_http_errors_map_to_provider_errors(status, exc):
    p = _provider(lambda r: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(exc) as ei:
        await p.complete([Message("user", "hi")])
    assert KEY not in str(ei.value)


async def test_network_errors_are_unavailable():
    def boom(request):
        raise httpx.ConnectError("refused")

    p = _provider(boom)
    with pytest.raises(ProviderUnavailable):
        await p.complete([Message("user", "hi")])
    assert await p.probe() is False


async def test_probe_true_on_200():
    p = _provider(lambda r: _ok({"data": {}}))
    assert await p.probe() is True


async def test_malformed_body_is_provider_error():
    p = _provider(lambda r: _ok({"choices": []}))
    with pytest.raises(ProviderError):
        await p.complete([Message("user", "hi")])


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="live check needs OPENROUTER_API_KEY")
async def test_live_openrouter_answers():
    p = OpenRouterProvider.from_env(model=os.environ.get("OPENROUTER_MODEL") or "meta/muse-spark-1.3-contributor")
    try:
        c = await p.complete([Message("user", "Reply with the single word: pong")])
        assert "pong" in c.message.content.lower()
        assert c.kind == KIND
        assert await p.probe() is True
    finally:
        await p.aclose()


def _capture(usage=None):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _ok({
            "model": "test/model",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": usage or {},
        })

    return seen, _provider(handler)


async def test_session_id_is_sent_top_level_when_given():
    seen, p = _capture()
    await p.complete([Message("user", "hi")], session_id="conv-abc")
    assert seen["body"]["session_id"] == "conv-abc"


async def test_session_id_is_capped_at_256_characters():
    seen, p = _capture()
    await p.complete([Message("user", "hi")], session_id="x" * 300)
    assert len(seen["body"]["session_id"]) == 256


async def test_no_session_id_field_without_one():
    seen, p = _capture()
    await p.complete([Message("user", "hi")])
    assert "session_id" not in seen["body"]


async def test_cached_tokens_are_read_from_prompt_token_details():
    _, p = _capture({"prompt_tokens": 1000, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 800}})
    c = await p.complete([Message("user", "hi")])
    assert c.usage.cached_tokens == 800


async def test_cached_tokens_default_to_zero():
    _, p = _capture({"prompt_tokens": 5, "completion_tokens": 1})
    c = await p.complete([Message("user", "hi")])
    assert c.usage.cached_tokens == 0

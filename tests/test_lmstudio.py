import asyncio

import httpx

from src.providers import lmstudio
from src.providers.lmstudio import LmModel
from src.providers.store import ProviderEntry

V1 = {"models": [
    {"key": "slim-summary-tool", "type": "llm", "display_name": "x", "max_context_length": 4096,
     "loaded_instances": [{"id": "slim-summary-tool", "config": {"context_length": 4096, "parallel": 4}}]},
    {"key": "summary-llm-qwen-2.5-7b", "type": "llm", "max_context_length": 40960, "loaded_instances": []}]}
V0 = {"data": [
    {"id": "slim-summary-tool", "type": "llm", "state": "loaded", "max_context_length": 4096, "loaded_context_length": 4096},
    {"id": "summary-llm-qwen-2.5-7b", "type": "llm", "state": "not-loaded", "max_context_length": 40960}]}
EXPECT = [LmModel("slim-summary-tool", "llm", 4096, 4096, True),
          LmModel("summary-llm-qwen-2.5-7b", "llm", 40960, None, False)]


def run(handler, base="http://localhost:1234/v1", key=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await lmstudio.read_models(base, key, c)
    return asyncio.run(go())


def test_reads_v1():
    assert run(lambda r: httpx.Response(200, json=V1)) == EXPECT


def test_falls_back_to_v0():
    def h(r):
        return httpx.Response(404) if r.url.path == "/api/v1/models" else httpx.Response(200, json=V0)
    assert run(h) == EXPECT


def test_not_lm_studio_is_none():
    assert run(lambda r: httpx.Response(404)) is None
    assert run(lambda r: httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})) is None

    def boom(r):
        raise httpx.ConnectError("no")
    assert run(boom) is None


def test_root_and_key():
    seen = []

    def h(r):
        seen.append(r)
        return httpx.Response(200, json=V1)
    run(h, key="k")
    run(h)
    assert str(seen[0].url) == "http://localhost:1234/api/v1/models"
    assert seen[0].headers["authorization"] == "Bearer k"
    assert "authorization" not in seen[1].headers


def test_no_redirect_followed():
    urls = []

    def h(r):
        urls.append(str(r.url))
        if r.url.path == "/api/v1/models":
            return httpx.Response(302, headers={"location": "http://evil.example/x"})
        return httpx.Response(200, json=V0)
    assert run(h) == EXPECT
    assert all("evil.example" not in u for u in urls)


def test_non_integers_and_missing_ids():
    body = {"models": [{"key": "a", "max_context_length": "big", "loaded_instances": [{"config": {"context_length": 1.5}}]},
                       {"type": "llm"}]}
    assert run(lambda r: httpx.Response(200, json=body)) == [LmModel("a", "", None, None, True)]


def test_find_by_exact_then_last_segment():
    ms = [LmModel("a/qwen", "llm", 1, 1, True), LmModel("qwen", "llm", 2, 2, True), LmModel("x/other", "llm", 3, 3, True)]
    assert lmstudio.find(ms, "qwen").max_context == 2
    assert lmstudio.find(ms[:1] + ms[2:], "z/qwen").id == "a/qwen"
    assert lmstudio.find(ms, "nope") is None
    assert lmstudio.find([LmModel("a/q", "", 1, 1, True), LmModel("b/q", "", 1, 1, True)], "c/q") is None


def test_local_providers():
    def e(kind, url):
        return ProviderEntry(id="p", name="p", kind=kind, base_url=url)
    for u in ("http://localhost:1234/v1", "http://192.168.1.5:1234/v1", "http://box.local:1234/v1"):
        assert e("openai-compatible", u).local()
    assert not e("openai-compatible", "https://integrate.api.nvidia.com/v1").local()
    assert not e("openai-compatible", "https://api.groq.com/openai/v1").local()
    assert not e("openrouter", "http://localhost:1234/v1").local()

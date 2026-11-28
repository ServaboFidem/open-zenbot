"""SummaryContext: what the summary model's window is, read, learned and reported."""
from __future__ import annotations

import httpx
import pytest

from src.context.fit import Overrun
from src.harness.summary_context import SummaryContext
from src.providers.store import ProviderEntry

LOCAL = "http://127.0.0.1:1234/v1"


def entry(kind="openai-compatible", url=LOCAL, id="lm"):
    return ProviderEntry(id=id, name="LM Studio", kind=kind, base_url=url)


def v1(*models):
    out = []
    for key, typ, mx, loaded in models:
        out.append({"key": key, "type": typ, "max_context_length": mx,
                    "loaded_instances": [{"config": {"context_length": loaded}}] if loaded else []})
    return {"models": out}


class Server:
    def __init__(self, body=None):
        self.body = body
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.body is None:
            return httpx.Response(404)
        return httpx.Response(200, json=self.body)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(server, model="m", e=None, clock=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return SummaryContext(entry=e if e is not None else entry(), model=model, key=lambda: None,
                          client=client, clock=clock or Clock())


@pytest.mark.parametrize("e", [entry("openrouter", "https://openrouter.ai/api/v1"),
                               entry("openai-compatible", "https://api.groq.com/openai/v1")])
async def test_hosted_is_never_read(e):
    server = Server(v1(("m", "llm", 4096, 4096)))
    ctx = make(server, e=e)
    assert await ctx.limit() is None
    ctx.record_overrun("task", Overrun(4096, 5000))
    ctx.learn(Overrun(4096, 5000))
    st = await ctx.status(fresh=True, enabled=set())
    assert st["server"] == "hosted" and st["warning"] == ""
    assert await ctx.limit() is None
    assert server.calls == 0


async def test_lm_studio_loaded_context_is_the_limit_and_cached():
    server = Server(v1(("m", "llm", 40960, 4096)))
    clock = Clock()
    ctx = make(server, clock=clock)
    assert await ctx.limit() == 4096
    n = server.calls
    clock.t = 299
    assert await ctx.limit() == 4096
    assert server.calls == n
    clock.t = 301
    assert await ctx.limit() == 4096
    assert server.calls > n


async def test_not_loaded_is_unknown():
    ctx = make(Server(v1(("m", "llm", 40960, None))))
    assert await ctx.limit() is None
    st = await ctx.status(fresh=False, enabled=set())
    assert st["warning"] == "" and st["loaded"] is False and st["server"] == "lmstudio"


async def test_learned_limit_when_not_lm_studio():
    ctx = make(Server(None))
    assert await ctx.limit() is None
    ctx.learn(Overrun(4096, 5726))
    assert await ctx.limit() == 4096
    st = await ctx.status(fresh=False, enabled=set())
    assert st["server"] == "other" and st["learned_context"] == 4096


@pytest.mark.parametrize("mx,loaded,want", [(4096, 4096, "at_max"), (40960, 4096, "small"), (40960, 16384, "")])
async def test_warnings(mx, loaded, want):
    ctx = make(Server(v1(("m", "llm", mx, loaded))))
    assert (await ctx.status(fresh=True, enabled=set()))["warning"] == want


async def test_overrun_warning_and_recovery():
    ctx = make(Server(v1(("m", "llm", 40960, 16384))))
    ctx.record_overrun("fold", Overrun(4096, 5000), "c1")
    st = await ctx.status(fresh=False, enabled=set())
    assert st["warning"] == "overrun"
    assert st["last_overrun"]["where"] == "fold" and st["last_overrun"]["n_ctx"] == 4096
    assert st["last_overrun"]["at"] and st["recovered_since"] is None
    ctx.record_success()
    st = await ctx.status(fresh=False, enabled=set())
    assert st["warning"] == "" and st["recovered_since"]


async def test_alternatives():
    models = v1(("slim-summary-tool", "llm", 4096, 4096),
                ("summary-llm-qwen-2.5-7b", "llm", 40960, None),
                ("llama-chat-summary", "llm", 131072, None),
                ("tiny", "llm", 2048, None),
                ("embed", "embedding", 131072, None))
    ctx = make(Server(models), model="slim-summary-tool")
    st = await ctx.status(fresh=True, enabled={"summary-llm-qwen-2.5-7b"})
    assert st["alternatives"] == [
        {"id": "llama-chat-summary", "max_context": 131072, "loaded": False, "enabled": False},
        {"id": "summary-llm-qwen-2.5-7b", "max_context": 40960, "loaded": False, "enabled": True},
    ]
    assert st["recommended_context"] == 4096
    assert st["provider"] == {"id": "lm", "name": "LM Studio"} and st["model"] == "slim-summary-tool"


async def test_rebind_clears():
    ctx = make(Server(None))
    ctx.learn(Overrun(4096, 5000))
    ctx.record_overrun("task", Overrun(4096, 5000))
    ctx.rebind(entry(), "other")
    st = await ctx.status(fresh=False, enabled=set())
    assert st["last_overrun"] is None and st["learned_context"] is None and st["model"] == "other"


async def test_learned_limit_when_listed_but_not_loaded():
    ctx = make(Server(v1(("m", "llm", 40960, None))))
    assert await ctx.limit() is None
    ctx.learn(Overrun(4096, 5726))
    assert await ctx.limit() == 4096

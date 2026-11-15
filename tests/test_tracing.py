"""Langfuse tracing: off without keys, spans captured in memory when on, secrets masked, and the trace
shapes the harness produces (generations at the provider and decider, agent runs with their tools)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from uuid import uuid4
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.memory.exclusion import exclusion
from src.providers import tracing

ENV = {"LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test", "LANGFUSE_BASE_URL": "http://127.0.0.1:9"}


@pytest.fixture
def spans():
    """Tracing on, exporting to memory; the finished spans, by name."""
    exporter = InMemorySpanExporter()
    tracing._test_exporter = exporter  # type: ignore[attr-defined]
    # the SDK keeps one client per public key in a process: a fresh key gets a fresh exporter
    assert tracing.init_langfuse({**ENV, "LANGFUSE_PUBLIC_KEY": f"pk-lf-{uuid4().hex}"}, span_exporter=exporter)

    def finished() -> dict[str, Any]:
        tracing.flush()
        return {s.name: s for s in exporter.get_finished_spans()}

    yield finished
    tracing.shutdown()


def attr(span: Any, key: str) -> Any:
    v = span.attributes.get(key)
    try:
        return json.loads(v) if isinstance(v, str) and v[:1] in "{[\"" else v
    except ValueError:
        return v


def meta(span: Any) -> dict[str, Any]:
    """Metadata is exported one attribute per key."""
    pre = "langfuse.observation.metadata."
    return {k[len(pre):]: attr(span, k) for k in span.attributes if k.startswith(pre)}


def test_off_without_keys_and_helpers_still_work():
    assert tracing.init_langfuse({}) is False and not tracing.enabled()
    with tracing.attributes(session_id="s", tags=["bot"]):
        with tracing.observe("run-task", as_type="agent", input="x") as obs:
            obs.update(output="y")  # a no-op, not an error


def test_spans_are_recorded_with_name_and_type(spans):
    with tracing.observe("run-task", as_type="agent", input="the task") as obs:
        obs.update(output="done")
    s = spans()["run-task"]
    assert attr(s, "langfuse.observation.type") == "agent"
    assert attr(s, "langfuse.observation.input") == "the task" and attr(s, "langfuse.observation.output") == "done"


def test_a_registered_secret_is_masked_before_export(spans):
    secret = "sk-or-v1-0123456789abcdef0123456789abcdef"
    exclusion.register_secret_value(secret)
    with tracing.observe("run-task", as_type="agent", input=f"use key {secret} please"):
        pass
    exported = json.dumps({k: str(v) for k, v in spans()["run-task"].attributes.items()})
    assert secret not in exported and "REDACTED" in exported.upper()


def test_usage_buckets_are_exclusive():
    assert tracing.usage_details(17903, 188, 17817) == {"input": 86, "input_cached_tokens": 17817, "output": 188}
    assert tracing.usage_details(10, 5, 0) == {"input": 10, "output": 5}


def test_a_step_name_comes_from_the_caller():
    assert tracing.step_name("generate-response") == "generate-response"
    with tracing.llm_step("summarize-document"):
        assert tracing.step_name("generate-response") == "summarize-document"
    assert tracing.step_name("generate-response") == "generate-response"


# -- generations at the choke points ------------------------------------------

import httpx  # noqa: E402

from src.decider.adapters.decision_model import DecisionModelScorer  # noqa: E402
from src.decider.interface import DecisionRequest, NoulQuestion, decide_and_log  # noqa: E402
from src.memory.ledger import Ledger  # noqa: E402
from src.providers.interface import Message, ProviderError, ToolCall, ToolSpec  # noqa: E402
from src.providers.openrouter import OpenRouterHttp, OpenRouterProvider  # noqa: E402
from src.providers.secrets import Secret  # noqa: E402

KEY = "sk-or-v1-tracing0123456789abcdef"


def chat_reply(**message: Any) -> httpx.Response:
    return httpx.Response(200, json={
        "model": "meta/muse-spark", "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "cost": 0.0021, "prompt_tokens_details": {"cached_tokens": 100}}})


def provider(handler) -> OpenRouterProvider:
    return OpenRouterProvider(Secret(KEY), model="meta/muse-spark", transport=httpx.MockTransport(handler))


async def test_a_chat_call_is_a_generation_with_thinking_tools_usage_and_cost(spans):
    p = provider(lambda r: chat_reply(content="", reasoning="I should look it up.", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": '{"q": "paris"}'}}]))
    c = await p.complete([Message("system", "be brief"), Message("user", "weather in paris?")],
                         tools=[ToolSpec("web_search", "search", {"type": "object"})])
    assert c.reasoning == "I should look it up."
    g = spans()["generate-response"]
    assert attr(g, "langfuse.observation.type") == "generation" and attr(g, "langfuse.observation.model.name") == "meta/muse-spark"
    assert attr(g, "langfuse.observation.input") == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "weather in paris?"}]
    out = attr(g, "langfuse.observation.output")
    assert out["role"] == "assistant" and out["thinking"] == [{"type": "thinking", "content": "I should look it up."}]
    assert out["tool_calls"] == [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": '{"q": "paris"}'}}]
    assert attr(g, "langfuse.observation.usage_details") == {"input": 20, "input_cached_tokens": 100, "output": 30}
    assert attr(g, "langfuse.observation.cost_details") == {"total": 0.0021}
    assert meta(g)["finish_reason"] == "tool_calls" and meta(g)["tools"] == ["web_search"] and meta(g)["provider"] == "openrouter"


async def test_the_caller_names_the_generation(spans):
    p = provider(lambda r: chat_reply(content="short"))
    with tracing.llm_step("summarize-document"):
        await p.complete([Message("user", "long text")])
    assert "summarize-document" in spans() and "generate-response" not in spans()


async def test_a_failed_call_is_an_error_generation_and_still_raises(spans):
    p = provider(lambda r: httpx.Response(500))
    with pytest.raises(ProviderError):
        await p.complete([Message("user", "hi")])
    g = spans()["generate-response"]
    assert attr(g, "langfuse.observation.level") == "ERROR" and "500" in attr(g, "langfuse.observation.status_message")


async def test_a_decision_is_a_generation_named_for_its_purpose(spans, tmp_path):
    scorer = DecisionModelScorer(OpenRouterHttp(Secret(KEY), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        "model": "microsoft/decision-1", "answers": {"fits": {"type": "noul", "noul": 0.9}},
        "usage": {"input_tokens": 50, "output_tokens": 2, "cost": 0.0001}}))))
    req = DecisionRequest(state="a task", questions={"fits": NoulQuestion(instructions="does it fit?")}, purpose="routing")
    await decide_and_log(scorer, req, Ledger(tmp_path), "bot", "s1")
    g = spans()["decide-routing"]
    assert attr(g, "langfuse.observation.type") == "generation" and attr(g, "langfuse.observation.model.name") == "microsoft/decision-1"
    assert attr(g, "langfuse.observation.usage_details") == {"input": 50, "output": 2}
    assert attr(g, "langfuse.observation.output")["fits"]["type"] == "noul" and meta(g)["purpose"] == "routing"
    assert attr(g, "langfuse.observation.input")["questions"]["fits"]["instructions"] == "does it fit?"


async def test_a_generated_image_is_a_generation_with_the_image(spans):
    import base64

    from src.providers.images import OpenRouterImages

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==")
    images = OpenRouterImages(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        "data": [{"b64_json": base64.b64encode(png).decode(), "media_type": "image/png"}], "usage": {"cost": 0.04}})))
    await images.generate(Secret(KEY), "tencent/hy-image", "a red dot", aspect_ratio="1:1")
    g = spans()["generate-image"]
    assert attr(g, "langfuse.observation.input") == "a red dot" and attr(g, "langfuse.observation.model.name") == "tencent/hy-image"
    assert "@@@langfuseMedia:type=image/png" in str(g.attributes.get("langfuse.observation.output"))
    assert attr(g, "langfuse.observation.cost_details") == {"total": 0.04}


# -- agent runs ---------------------------------------------------------------


async def test_an_agent_run_is_one_trace_with_generations_and_their_tools(spans, tmp_path):
    from src.harness.approvals import ApprovalStore
    from src.harness.bot import Bot
    from src.harness.loop import SessionState, TaskLoop
    from src.harness.tools import ToolRunner

    replies = iter([
        chat_reply(content="", tool_calls=[
            {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"text": "hi"}'}},
            {"id": "c2", "type": "function", "function": {"name": "explode", "arguments": "{}"}}]),
        chat_reply(content="All done."),
    ])
    runner = ToolRunner(tmp_path, timeout=10)
    runner.register("echo", "echo", {"type": "object"}, lambda text: f"echo: {text}")

    def explode() -> str:
        raise RuntimeError("boom")

    runner.register("explode", "fails", {"type": "object"}, explode)
    loop = TaskLoop(provider(lambda r: next(replies)), Ledger(tmp_path), ApprovalStore(), runner, max_steps=5)
    session = await loop.run(Bot(name="Worker", role="Do the thing", id="worker"), "say hi")
    assert session.state == SessionState.done
    exported = spans()
    root, echo, boom = exported["run-task"], exported["echo"], exported["explode"]
    gens = [s for s in exporter_spans(exported) if s.name == "generate-response"]
    assert attr(root, "langfuse.observation.type") == "agent"
    assert attr(root, "langfuse.observation.input") == "say hi" and attr(root, "langfuse.observation.output") == "All done."
    assert attr(root, "session.id") == session.id and list(attr(root, "langfuse.trace.tags")) == ["worker"]
    # the generations and the tools they asked for are siblings under the run
    assert len(gens) == 2 and all(g.parent.span_id == root.context.span_id for g in gens)
    assert echo.parent.span_id == root.context.span_id and boom.parent.span_id == root.context.span_id
    assert attr(echo, "langfuse.observation.type") == "tool" and attr(echo, "langfuse.observation.input") == {"text": "hi"}
    assert attr(echo, "langfuse.observation.output") == "echo: hi"
    assert attr(boom, "langfuse.observation.level") == "ERROR" and "boom" in attr(boom, "langfuse.observation.status_message")
    assert {s.context.trace_id for s in (root, echo, boom, *gens)} == {root.context.trace_id}


def exporter_spans(by_name: dict[str, Any]) -> list[Any]:
    """Every exported span (by_name keeps one per name)."""
    return list(tracing._test_exporter.get_finished_spans())


async def test_a_conversation_turn_joins_its_conversation_session(spans, tmp_path):
    from src.harness.approvals import ApprovalStore
    from src.harness.bot import Bot
    from src.harness.loop import Session, TaskLoop
    from src.harness.tools import ToolRunner

    loop = TaskLoop(provider(lambda r: chat_reply(content="hello")), Ledger(tmp_path), ApprovalStore(), ToolRunner(tmp_path))
    s = Session(bot_id="worker", task="hi", conversation_id="conv-1")
    await loop.run(Bot(name="Worker", role="r", id="worker"), "hi", s)
    assert attr(spans()["run-task"], "session.id") == "conv-1"


# -- named roots ----------------------------------------------------------------


async def test_generator_calls_are_named_for_their_work_and_an_outer_name_wins(spans):
    from src.generation.generator import Generator

    gen = Generator(provider(lambda r: chat_reply(content='{"summary": "A note."}')))
    await gen.vault_summary("# a doc")
    await gen.fold("earlier", "newer")
    with tracing.llm_step("summarize-context"):  # the loop's compaction names its summary
        await gen.summarize("a long transcript")
    names = [s.name for s in exporter_spans(spans())]
    assert names == ["summarize-document", "fold-summaries", "summarize-context"]


async def test_each_imported_file_is_its_own_trace_with_its_status(spans, tmp_path):
    import asyncio

    from src.generation.generator import Generator
    from src.memory.graph import LocalGraph, MemoryGraph
    from src.memory.import_dir import DirImporter, scan_dir
    from src.memory.ingest import Ingestor, PlainConverter

    vault = tmp_path / "vault"
    (vault / "memory").mkdir(parents=True)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# a")
    (docs / "b.md").write_text("# b")
    ledger = Ledger(tmp_path)
    graph = MemoryGraph(LocalGraph(vault / "memory"), ledger)
    reply = '{"summary": "A note.", "names": [], "dates": [], "facts": []}'
    gen = Generator(provider(lambda r: chat_reply(content=reply)))
    imp = DirImporter(lambda allowed: Ingestor(vault, PlainConverter(), gen, graph, ledger, allowed=allowed), ledger)
    job = imp.start(scan_dir(docs, PlainConverter().accepts))
    q = imp.subscribe(job.id)
    while (await asyncio.wait_for(q.get(), 10))["kind"] != "end":
        pass
    all_spans = exporter_spans(spans())
    roots = [s for s in all_spans if s.name == "import-document"]
    assert [attr(s, "langfuse.observation.input") for s in roots] == [{"file": "a.md"}, {"file": "b.md"}]
    assert [attr(s, "langfuse.observation.output") for s in roots] == [{"status": "new"}, {"status": "new"}]
    assert len({s.context.trace_id for s in roots}) == 2
    # each file's summary is a generation inside that file's trace
    summaries = [s for s in all_spans if s.name == "summarize-document"]
    assert summaries and {s.context.trace_id for s in summaries} <= {s.context.trace_id for s in roots}
    assert all(s.parent is not None for s in summaries)


async def test_compaction_is_traced_only_when_it_runs_and_empty_metadata_is_dropped(spans, tmp_path):
    from src.context.compaction import CompactionConfig, Compactor
    from src.generation.generator import Generator
    from src.harness.approvals import ApprovalStore
    from src.harness.bot import Bot
    from src.harness.loop import TaskLoop
    from src.harness.tools import ToolRunner

    p = provider(lambda r: chat_reply(content="hello"))
    ledger = Ledger(tmp_path)
    loop = TaskLoop(p, ledger, ApprovalStore(), ToolRunner(tmp_path),
                    compactor=Compactor(Generator(p), ledger, CompactionConfig(budget_chars=10**6)))
    await loop.run(Bot(name="Worker", role="r", id="worker"), "hi")
    exported = spans()
    assert "compact-context" not in exported  # under budget: no empty span on every step
    assert "room" not in meta(exported["run-task"])  # a task outside a room carries no empty room


async def test_a_compaction_that_runs_holds_its_summary_generation(spans, tmp_path):
    from src.context.compaction import CompactionConfig, Compactor
    from src.generation.generator import Generator
    from src.harness.approvals import ApprovalStore
    from src.harness.bot import Bot
    from src.harness.loop import Session, TaskLoop
    from src.harness.tools import ToolRunner

    p = provider(lambda r: chat_reply(content="x" * 400))
    ledger = Ledger(tmp_path)
    loop = TaskLoop(p, ledger, ApprovalStore(), ToolRunner(tmp_path),
                    compactor=Compactor(Generator(p), ledger, CompactionConfig(budget_chars=600, keep_recent_turns=1)))
    history = [Message("user", "q" * 300), Message("assistant", "a" * 300), Message("user", "q2" * 150), Message("assistant", "b" * 300)]
    await loop.run(Bot(name="Worker", role="r", id="worker"), "next", Session(bot_id="worker", task="next"), history=history)
    all_spans = exporter_spans(spans())
    compact = next(s for s in all_spans if s.name == "compact-context")
    assert attr(compact, "langfuse.observation.input")["chars"] > 600
    summary = next(s for s in all_spans if s.name == "summarize-context")
    assert summary.parent.span_id == compact.context.span_id

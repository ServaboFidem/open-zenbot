"""Testing an OpenAI-compatible model to learn what it can do, at the moment it is enabled.

Such a provider lists model ids only, so a model's capabilities are found by asking it: a chat reply
(and whether it reasons), an embedding, a forced tool call, a JSON-schema reply and an image. Its kind
comes from what it answers, never from its name: "embed" in an id says nothing reliable. A server often ignores
`tools` or `response_format` silently, so each test checks what came back, not just that the request
was accepted. A rate limit, a timeout, a server error or no connection ends the probe as `Unavailable`
with a plain reason: the model is not enabled, since a gap in what is known would mislead the decider."""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from .interface import ProviderError, ProviderUnavailable
from .models import ModelInfo
from .openrouter import OpenRouterHttp

TIMEOUT = 60.0
REASONING_BUDGET = 2048  # max_tokens for the later tests once a model is seen to reason: thinking comes before the answer
CHAT_PATH = "/chat/completions"
EMBED_PATH = "/embeddings"
# A 1x1 red PNG.
RED_PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/"
             "iZk9HQAAAABJRU5ErkJggg==")
WEATHER_TOOL = {"type": "function", "function": {
    "name": "get_weather", "description": "Get the current weather for a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
COLOR_SCHEMA = {"type": "json_schema", "json_schema": {"name": "color", "strict": True, "schema": {
    "type": "object", "properties": {"color": {"type": "string"}}, "required": ["color"], "additionalProperties": False}}}

Emit = Callable[[dict[str, Any]], None]


class Unavailable(Exception):
    """The provider could not answer right now; `reason` says why, in words for the user."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ProbeFailed(Exception):
    """The model refused the most basic request (a wrong id, a model that does not chat). `unusable`: the
    provider lists it but won't run it for this key (HTTP 404)."""

    def __init__(self, message: str, unusable: bool = False) -> None:
        super().__init__(message)
        self.unusable = unusable


STEPS = ["chat", "embeddings", "tools", "structured", "image"]  # every model, whatever its name: what passes decides its kind
CHAT_ONLY = {"tools", "structured", "image"}  # skipped when the model doesn't chat


def reason_for(e: ProviderUnavailable) -> str:
    cause = getattr(e, "cause", "network")
    if cause == "rate_limit":
        return "the provider is rate limiting requests"
    if cause == "server":
        return f"the provider had a server error ({getattr(e, 'status', None) or 'unknown'})"
    if cause == "timeout":
        return f"the model took longer than {TIMEOUT:.0f} seconds to answer"
    return "the provider could not be reached"


def _message(data: dict[str, Any]) -> dict[str, Any]:
    try:
        m = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return {}
    return m if isinstance(m, dict) else {}


def _content(m: dict[str, Any]) -> str:
    c = m.get("content")
    return c if isinstance(c, str) else ""


def _chat(model_id: str, content: Any, max_tokens: int = 64, **extra: Any) -> dict[str, Any]:
    return {"model": model_id, "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens, **extra}


class _Refused:
    """The provider turned a request down (a 4xx). NIM answers a plain `404 page not found` when the model
    doesn't serve that kind of request (a no for this test) and a problem+json 404 "Not found for account"
    when the key can't run the model at all; OpenAI and vLLM say "does not exist" for the latter."""

    def __init__(self, e: ProviderError) -> None:
        self.status = getattr(e, "status", None)
        self.message = str(e).removeprefix("model probe ")

    @property
    def unusable(self) -> bool:
        text = self.message.lower()
        return self.status == 404 and ("not found for account" in text or "does not exist" in text)

    def why(self, model_id: str) -> str:
        return f"{model_id} is listed by the provider but isn't available to this key ({self.message})"


async def _post(http: OpenRouterHttp, path: str, payload: dict[str, Any]) -> dict[str, Any] | _Refused:
    """The reply, or `_Refused` when the provider turned the request down; Unavailable when it could not answer.
    A model the key can't run ends the probe at once."""
    try:
        return await http.post_json(path, payload, what="model probe")
    except ProviderUnavailable as e:
        raise Unavailable(reason_for(e)) from None
    except ProviderError as e:
        refused = _Refused(e)
        if refused.unusable:
            raise ProbeFailed(refused.why(str(payload.get("model"))), unusable=True) from None
        return refused


def _reply(data: dict[str, Any] | _Refused) -> dict[str, Any]:
    return {} if isinstance(data, _Refused) else data


async def _embeds(http: OpenRouterHttp, model_id: str) -> tuple[bool, list[str], _Refused | None]:
    """Whether the model returns an embedding, the parameters that took, and the refusal if it didn't.
    NIM's asymmetric models refuse a request without `input_type`, so it is retried with one."""
    data = await _post(http, EMBED_PATH, {"model": model_id, "input": ["ok"]})
    params: list[str] = []
    if isinstance(data, _Refused) and "input_type" in data.message:
        data = await _post(http, EMBED_PATH, {"model": model_id, "input": ["ok"], "input_type": "query"})
        params = ["input_type"]
    if isinstance(data, _Refused):
        return False, [], data
    rows = data.get("data")
    ok = isinstance(rows, list) and bool(rows) and isinstance(rows[0], dict) and bool(rows[0].get("embedding"))
    return ok, params if ok else [], None


async def run_probe(http: OpenRouterHttp, model: ModelInfo, emit: Emit) -> ModelInfo:
    """Run every test in `STEPS` order, emitting each step's progress; the model with what was found.
    Its kind comes from what passed (chat: text; embeddings: embedding), never from its name.
    `http` should be built with `timeout=TIMEOUT` against the provider's base URL."""
    found = replace(model, guessed=False, probed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    params: list[str] = []
    inputs = ["text"]
    budget = {"tools": 128, "structured": 128, "image": 64}  # raised for a reasoning model, after the chat test
    chats = embeds = False
    refusal: _Refused | None = None

    def step(name: str, status: str, detail: str | None = None) -> None:
        emit({"kind": "step", "step": name, "status": status, **({"detail": detail} if detail else {})})

    for name in STEPS:
        if name in CHAT_ONLY and not chats:
            if not embeds:
                break  # neither: nothing more to learn
            step(name, "skip")
            continue
        step(name, "running")
        if name == "chat":
            data = await _post(http, CHAT_PATH, _chat(model.id, "Reply with the word ok."))
            m = _message(_reply(data))
            chats = bool(m)
            refusal = data if isinstance(data, _Refused) else None
            if chats:
                reasoning = bool(m.get("reasoning_content") or m.get("reasoning")) or "<think>" in _content(m)
                if reasoning:
                    params.append("reasoning")
                    budget = dict.fromkeys(budget, REASONING_BUDGET)
                found = replace(found, reasoning=reasoning)
                step(name, "pass", "reasons" if reasoning else "no reasoning")
            else:
                step(name, "fail")
        elif name == "embeddings":
            embeds, extra, refused = await _embeds(http, model.id)
            params += extra
            refusal = refusal or refused
            step(name, "pass" if embeds else "fail")
        elif name == "tools":
            ok = await _calls_tools(http, model.id, budget["tools"])
            if ok:
                params.append("tools")
            step(name, "pass" if ok else "fail")
        elif name == "structured":
            data = await _post(http, CHAT_PATH, _chat(model.id, "Name a colour.", budget["structured"], response_format=COLOR_SCHEMA))
            ok = _is_color_json(_content(_message(_reply(data))))
            if ok:
                params += ["structured_outputs", "response_format"]
            step(name, "pass" if ok else "fail")
        elif name == "image":
            data = await _post(http, CHAT_PATH, _chat(model.id, [
                {"type": "text", "text": "What colour is this image? One word."},
                {"type": "image_url", "image_url": {"url": RED_PIXEL}}], budget["image"]))
            ok = bool(_content(_message(_reply(data))).strip())
            if ok:
                inputs.append("image")
            step(name, "pass" if ok else "fail")
    if not (chats or embeds):
        why = f" ({refusal.message})" if refusal else ""
        raise ProbeFailed(f"{model.id} answered neither a chat nor an embeddings request{why}")
    kinds = (["text"] if chats else []) + (["embedding"] if embeds else [])
    outputs = (["text"] if chats else []) + (["embeddings"] if embeds else [])
    return replace(found, kinds=kinds, output_modalities=outputs, input_modalities=inputs, supported_parameters=params,
                   reasoning=found.reasoning if chats else None)


async def _calls_tools(http: OpenRouterHttp, model_id: str, max_tokens: int) -> bool:
    """Whether the model calls the weather tool when made to. The named form of `tool_choice` comes first; a
    server that refuses it (LM Studio and llama.cpp accept only none, auto or required) is asked again with
    `required`, since the refusal is about the request's shape, not the model."""
    ask = "What is the weather in Paris? Use the tool."
    data = await _post(http, CHAT_PATH, _chat(model_id, ask, max_tokens, tools=[WEATHER_TOOL],
                                              tool_choice={"type": "function", "function": {"name": "get_weather"}}))
    if isinstance(data, _Refused):
        data = await _post(http, CHAT_PATH, _chat(model_id, ask, max_tokens, tools=[WEATHER_TOOL], tool_choice="required"))
    return _called_weather(_message(_reply(data)))


def _called_weather(m: dict[str, Any]) -> bool:
    for call in m.get("tool_calls") or []:
        fn = (call or {}).get("function") or {}
        if fn.get("name") == "get_weather":
            try:
                return isinstance(json.loads(fn.get("arguments") or ""), dict)
            except (TypeError, ValueError):
                return False
    return False


def _is_color_json(text: str) -> bool:
    try:
        v = json.loads(text.strip())
    except ValueError:
        return False
    return isinstance(v, dict) and isinstance(v.get("color"), str)


# -- jobs: one probe at a time, followed over SSE ------------------------------


class ProbeJob:
    def __init__(self, id: str, provider_id: str, model: ModelInfo) -> None:
        self.id = id
        self.provider_id = provider_id
        self.model = model
        self.steps = list(STEPS)
        self.state = "running"  # then done | failed | unavailable | stopped
        self.events: list[dict[str, Any]] = []


class ProbeJobs:
    """Runs one probe at a time on the event loop. `on_done(model)` enables the probed model and returns
    the provider's updated view, which the `end` event carries. Shaped like the folder importer."""

    def __init__(self, transport: Any = None) -> None:
        self.transport = transport  # tests pass an httpx.MockTransport
        self.jobs: dict[str, ProbeJob] = {}
        self._tasks: dict[str, Any] = {}
        self._subscribers: dict[str, list[Any]] = {}

    def running(self) -> ProbeJob | None:
        return next((j for j in self.jobs.values() if j.state == "running"), None)

    def get(self, job_id: str) -> ProbeJob | None:
        return self.jobs.get(job_id)

    def start(self, provider_id: str, base_url: str, key: Any, model: ModelInfo,
              on_done: Callable[[ModelInfo], dict[str, Any]],
              on_unusable: Callable[[str], dict[str, Any]] | None = None) -> ProbeJob:
        if self.running() is not None:
            raise RuntimeError("a model is already being tested")
        job = ProbeJob(secrets.token_urlsafe(6), provider_id, model)
        self.jobs[job.id] = job
        http = OpenRouterHttp(key, base_url=base_url, timeout=TIMEOUT, transport=self.transport)
        self._tasks[job.id] = asyncio.get_running_loop().create_task(self._run(job, http, on_done, on_unusable))
        return job

    def stop(self, job_id: str) -> ProbeJob:
        job = self.jobs[job_id]
        task = self._tasks.get(job_id)
        if job.state == "running" and task is not None:
            task.cancel()
        return job

    def subscribe(self, job_id: str) -> Any:
        job = self.jobs[job_id]
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for e in job.events:
            q.put_nowait(e)
        if job.state == "running":
            self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: Any) -> None:
        subs = self._subscribers.get(job_id, [])
        if q in subs:
            subs.remove(q)

    def _emit(self, job: ProbeJob, event: dict[str, Any]) -> None:
        job.events.append(event)
        for q in list(self._subscribers.get(job.id, [])):
            q.put_nowait(event)

    async def _run(self, job: ProbeJob, http: OpenRouterHttp, on_done: Callable[[ModelInfo], dict[str, Any]],
                   on_unusable: Callable[[str], dict[str, Any]] | None) -> None:
        end: dict[str, Any] = {"kind": "end"}
        try:
            probed = await run_probe(http, job.model, lambda e: self._emit(job, e))
            end.update(status="done", provider=on_done(probed))
        except asyncio.CancelledError:
            end["status"] = "stopped"
        except Unavailable as e:
            end.update(status="unavailable", reason=e.reason)
        except ProbeFailed as e:
            end.update(status="failed", error=str(e))
            if e.unusable and on_unusable is not None:
                end.update(unusable=True, provider=on_unusable(job.model.id))
        except Exception as e:  # noqa: BLE001 - whatever happens, the job ends and the next probe can start
            end.update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
        finally:
            await http.aclose()
            job.state = end.get("status") or "failed"
            self._emit(job, end)
            self._subscribers.pop(job.id, None)
            self._tasks.pop(job.id, None)

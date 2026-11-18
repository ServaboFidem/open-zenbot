import sentry_sdk

from src.harness import telemetry


def test_no_dsn_does_not_start(monkeypatch):
    monkeypatch.setattr(telemetry, "_active", False)  # init_tracing remembers whether Sentry is on
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    assert telemetry.init_tracing({}) is False
    assert telemetry.init_tracing({telemetry.DSN_ENV: "  "}) is False
    assert calls == []


def test_dsn_starts_tracing_without_request_bodies(monkeypatch):
    monkeypatch.setattr(telemetry, "_active", False)  # init_tracing remembers whether Sentry is on
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    assert telemetry.init_tracing({telemetry.DSN_ENV: "https://k@glitchtip.example/1"}) is True
    (kw,) = calls
    assert kw["dsn"] == "https://k@glitchtip.example/1"
    assert kw["traces_sampler"] is telemetry.traces_sampler
    assert kw["send_default_pii"] is False
    assert kw["max_request_body_size"] == "never"


def test_sampler_drops_event_streams_and_keeps_the_rest():
    assert telemetry.traces_sampler({"asgi_scope": {"path": "/sessions/abc/events"}}) == 0.0
    assert telemetry.traces_sampler({"asgi_scope": {"path": "/vault/import/j1/events"}}) == 0.0
    assert telemetry.traces_sampler({"asgi_scope": {"path": "/orchestrate"}}) == 1.0
    assert telemetry.traces_sampler({}) == 1.0  # a background transaction has no request


def test_sampler_drops_the_google_callback_whose_query_carries_the_code():
    assert telemetry.traces_sampler({"asgi_scope": {"path": "/calendar/google/callback"}}) == 0.0
    assert telemetry.traces_sampler({"asgi_scope": {"path": "/calendar/status"}}) == 1.0


def test_helpers_are_no_ops_without_a_dsn():
    sentry_sdk.init(dsn="")  # no dsn (and not SENTRY_DSN from the environment): a client that sends nothing
    with telemetry.background_transaction("session.run", "task", bot_id="b") as tx:
        with telemetry.span("llm.complete", step=1) as s:
            s.set_data("model", "m")
        tx.set_tag("state", "done")


async def test_background_run_is_its_own_transaction_in_the_request_trace():
    import asyncio

    from sentry_sdk.transport import Transport

    sent = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            sent.extend(i.payload.json for i in envelope.items if i.type == "transaction")

    sentry_sdk.init(dsn="https://k@glitchtip.example/1", traces_sampler=telemetry.traces_sampler, transport=Capture)
    try:
        async def work(link):
            with telemetry.background_transaction("session.run", "task", link, bot_id="b1") as tx:
                with telemetry.span("tool.run", "web_search"):
                    await asyncio.sleep(0)
                tx.set_tag("state", "done")

        with sentry_sdk.start_transaction(name="/orchestrate", op="http.server") as req:
            task = asyncio.create_task(work(telemetry.trace_link()))
        await task
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.init(dsn="")
    by_name = {t["transaction"]: t for t in sent}
    run, request = by_name["session.run"], by_name["/orchestrate"]
    assert run["contexts"]["trace"]["trace_id"] == request["contexts"]["trace"]["trace_id"] == req.trace_id
    assert run["contexts"]["trace"]["parent_span_id"] == req.span_id
    assert [s["op"] for s in run["spans"]] == ["tool.run"]
    assert request["spans"] == []
    assert run["tags"]["bot_id"] == "b1" and run["tags"]["state"] == "done"

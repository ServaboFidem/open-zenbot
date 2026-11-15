"""Settings > Tracing: saved values over .env, a secret the page never sees, applied live."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import httpx
import pytest

from src.harness import tracing_settings as ts
from src.harness.tracing_settings import TracingSettings, effective
from src.vault_settings import settings_dir

ENV = {"ZEN_GLITCHTIP_DSN": "https://env@glitch/1", "LANGFUSE_PUBLIC_KEY": "pk-lf-env", "LANGFUSE_SECRET_KEY": "sk-lf-env-1234",
       "LANGFUSE_BASE_URL": "https://lf.env", "LANGFUSE_TRACING_ENVIRONMENT": "dev"}


def test_without_a_file_the_environment_applies(tmp_path: Path):
    eff = effective(tmp_path, ENV)
    assert eff["sentry"] == {"dsn": "https://env@glitch/1", "source": ".env"}
    assert eff["langfuse"]["public_key"] == "pk-lf-env" and eff["langfuse"]["source"] == ".env"
    assert effective(tmp_path, {}) == {"sentry": {"dsn": "", "source": "off"},
                                       "langfuse": {"public_key": "", "secret_key": "", "base_url": "", "environment": "", "source": "off"}}


def test_a_saved_section_wins_and_clearing_it_hands_back_to_env(tmp_path: Path):
    TracingSettings(sentry={"dsn": ""}).save(tmp_path)  # saved as off
    assert effective(tmp_path, ENV)["sentry"] == {"dsn": "", "source": "saved"}
    assert effective(tmp_path, ENV)["langfuse"]["source"] == ".env"
    TracingSettings(sentry=None).save(tmp_path)
    assert effective(tmp_path, ENV)["sentry"]["source"] == ".env"


def test_a_blank_secret_keeps_the_saved_one_and_the_file_is_owner_only(tmp_path: Path):
    s = TracingSettings().with_langfuse({"public_key": "pk-1", "secret_key": "sk-lf-saved-9999", "base_url": "https://a"})
    s.save(tmp_path)
    s2 = TracingSettings.load(tmp_path).with_langfuse({"public_key": "pk-2", "secret_key": "", "base_url": "https://b"})
    assert s2.langfuse == {"public_key": "pk-2", "secret_key": "sk-lf-saved-9999", "base_url": "https://b", "environment": ""}
    assert stat.S_IMODE((settings_dir(tmp_path) / ts.FILE).stat().st_mode) == 0o600


def test_the_page_never_gets_the_secret(tmp_path: Path):
    out = ts.public(effective(tmp_path, ENV), sentry_on=True, langfuse_on=False)
    assert "secret_key" not in out["langfuse"] and out["langfuse"]["secret_key_hint"] == "1234"
    assert "sk-lf-env-1234" not in str(out)


def test_a_bad_or_unreadable_file_means_env(tmp_path: Path):
    (settings_dir(tmp_path) / ts.FILE).parent.mkdir(parents=True)
    (settings_dir(tmp_path) / ts.FILE).write_text("{not json")
    assert effective(tmp_path, ENV)["sentry"]["source"] == ".env"


# -- live ----------------------------------------------------------------------

from src.providers import tracing  # noqa: E402


def test_check_keys_reports_why():
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []}))
    bad = httpx.MockTransport(lambda r: httpx.Response(401))
    assert tracing.check_keys("pk", "sk", "https://lf", transport=ok) is None
    assert tracing.check_keys("pk", "sk", "https://lf", transport=bad) == "the server refused these keys"


def test_a_restart_with_the_same_public_key_builds_a_new_client():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    env = {"LANGFUSE_PUBLIC_KEY": "pk-lf-restart", "LANGFUSE_SECRET_KEY": "sk-1", "LANGFUSE_BASE_URL": "http://127.0.0.1:9"}
    try:
        assert tracing.init_langfuse(env, span_exporter=InMemorySpanExporter())
        first = tracing._client
        assert tracing.restart({**env, "LANGFUSE_SECRET_KEY": "sk-2"})
        assert tracing._client is not first and tracing._client._resources is not first._resources
    finally:
        tracing.shutdown()


def test_sentry_restarts_and_turns_off(monkeypatch):
    import sentry_sdk

    from src.harness import telemetry

    monkeypatch.setenv("SENTRY_DSN", "https://fallback@never/9")
    try:
        assert telemetry.init_tracing(dsn="https://a@example.invalid/1")
        assert sentry_sdk.get_client().dsn == "https://a@example.invalid/1"
        assert telemetry.init_tracing(dsn="https://b@example.invalid/2")
        assert sentry_sdk.get_client().dsn == "https://b@example.invalid/2"
        assert telemetry.init_tracing(dsn="") is False
        assert not sentry_sdk.get_client().dsn  # off, and never the SENTRY_DSN fallback
    finally:
        telemetry.init_tracing(dsn="")


# -- API -------------------------------------------------------------------------


@pytest.fixture
async def api(harness, monkeypatch):
    from src.harness import telemetry
    from src.harness.api import create_app

    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    checked = []
    monkeypatch.setattr(tracing, "check_keys", lambda pk, sk, url: checked.append((pk, sk, url)) or (None if sk.startswith("sk-good") else "the server refused these keys"))
    started = []
    monkeypatch.setattr(tracing, "restart", lambda env: started.append(dict(env)) or bool(env.get("LANGFUSE_PUBLIC_KEY")))
    monkeypatch.setattr(telemetry, "init_tracing", lambda env=None, *, dsn=None: bool(dsn))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(harness)), base_url="http://localhost") as c:
        yield c, harness, checked, started


from .test_api import harness  # noqa: E402,F401 - the API tests' harness fixture


async def test_saving_langfuse_checks_the_keys_and_restarts_and_the_page_never_sees_the_secret(api):
    c, h, checked, started = api
    assert (await c.get("/settings/tracing")).json()["langfuse"]["source"] == "off"
    r = await c.put("/settings/tracing/langfuse", json={"public_key": "pk-1", "secret_key": "sk-bad-0000", "base_url": "https://lf"})
    assert r.status_code == 422 and "refused" in r.json()["detail"] and not started
    assert not (settings_dir(h.config.vault_root) / ts.FILE).exists()  # nothing saved
    r = await c.put("/settings/tracing/langfuse", json={"public_key": "pk-1", "secret_key": "sk-good-4321", "base_url": "https://lf", "environment": "production"})
    out = r.json()["langfuse"]
    assert r.status_code == 200 and out["source"] == "saved" and out["secret_key_hint"] == "4321" and "sk-good-4321" not in r.text
    assert started[-1] == {"LANGFUSE_PUBLIC_KEY": "pk-1", "LANGFUSE_SECRET_KEY": "sk-good-4321", "LANGFUSE_BASE_URL": "https://lf",
                           "LANGFUSE_TRACING_ENVIRONMENT": "production"}
    # a blank secret keeps the saved one
    r = await c.put("/settings/tracing/langfuse", json={"public_key": "pk-1", "base_url": "https://lf", "environment": "staging"})
    assert r.status_code == 200 and checked[-1][1] == "sk-good-4321" and started[-1]["LANGFUSE_TRACING_ENVIRONMENT"] == "staging"
    assert "sk-good" not in json.dumps([e.data for e in h.ledger.iter()])


async def test_clearing_langfuse_hands_back_to_env(api, monkeypatch):
    c, h, checked, started = api
    await c.put("/settings/tracing/langfuse", json={"public_key": "pk-1", "secret_key": "sk-good-1", "base_url": "https://lf"})
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-env")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-env-5678")
    r = await c.delete("/settings/tracing/langfuse")
    assert r.json()["langfuse"]["source"] == ".env" and started[-1]["LANGFUSE_PUBLIC_KEY"] == "pk-env"


async def test_sentry_dsn_is_validated_saved_and_can_be_turned_off(api):
    c, h, *_ = api
    assert (await c.put("/settings/tracing/sentry", json={"dsn": "not a dsn"})).status_code == 422
    r = await c.put("/settings/tracing/sentry", json={"dsn": "https://abc@glitch.example/3"})
    assert r.json()["sentry"] == {"dsn": "https://abc@glitch.example/3", "source": "saved", "live": False}  # init is faked here
    r = await c.put("/settings/tracing/sentry", json={"dsn": ""})
    assert r.json()["sentry"]["source"] == "saved" and r.json()["sentry"]["dsn"] == ""
    assert (await c.delete("/settings/tracing/sentry")).json()["sentry"]["source"] == "off"

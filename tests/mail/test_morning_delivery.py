"""Exercise delivery through the real AgentMail client. The transport never leaves this process."""

import asyncio
import json
from contextlib import suppress
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.mail.service import MailService
from src.mail.settings import MailKeys, MailSettings
from src.vault_settings import settings_dir

MORNING = datetime(2026, 11, 23, 8, tzinfo=timezone(timedelta(hours=-5)))
BOX = "zen@agentmail.to"
OWNER = "owner@example.org"


class Provider:
    def __init__(self):
        self.drafts = {}
        self.sent = []
        self.requests = []
        self.send_error = None
        self.evidence = True
        self.after_create = None
        self.started_send = asyncio.Event()

    async def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/drafts"):
            body = json.loads(request.content)
            draft = dict(body, draft_id=body["client_id"])
            self.drafts.setdefault(draft["draft_id"], draft)
            if self.after_create:
                self.after_create()
            return httpx.Response(201, json=draft)
        if request.method == "POST" and path.endswith("/send"):
            self.started_send.set()
            if self.send_error == "throttled":
                return httpx.Response(429, headers={"Retry-After": "300"}, json={})
            if self.send_error == "connect":
                raise httpx.ConnectError("connection refused")
            draft = self.drafts[path.split("/")[-2]]
            labels = json.loads(request.content)["add_labels"]
            acknowledgment = {"message_id": f"m{len(self.sent)}", "labels": ["sent", *labels]}
            self.sent.append({**draft, **acknowledgment})
            if self.send_error == "cancel":
                await asyncio.Future()
            if self.send_error == "lost":
                raise httpx.ReadTimeout("response lost")
            return httpx.Response(200, json=acknowledgment)
        if request.method == "GET" and path.endswith("/messages"):
            labels = request.url.params.get_list("labels")
            assert labels
            rows = [m for m in self.sent if set(labels) <= set(m["labels"]) and self.evidence]
            return httpx.Response(200, json={"messages": rows})
        raise AssertionError(path)


def configure(vault, recipient=OWNER, time="07:00"):
    folder = settings_dir(vault)
    folder.mkdir(exist_ok=True)
    (folder / "morning_email.json").write_text(json.dumps({"recipient": recipient, "time": time}))


@pytest.fixture
async def setup(tmp_path):
    from src.mail.morning_delivery import MorningDelivery

    vault = tmp_path / "vault"
    provider = Provider()
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handle)) as http:
        mail = MailService(vault, None, http)
        mail.keys = MailKeys("fake-key")
        mail.settings = MailSettings(BOX, BOX)
        configure(vault)
        async def edition(now):
            await asyncio.sleep(0)
            return f"Today {now.date()}", "A private appointment"
        def build(make=edition):
            return MorningDelivery(vault, mail, make)
        yield vault, provider, mail, build


async def test_concurrent_wake_restart_and_clock_rewind_send_once_per_date(setup):
    vault, provider, _, build = setup
    a, b = build(), build()
    await asyncio.gather(a.tick(MORNING), a.tick(MORNING), b.tick(MORNING))
    assert len(provider.sent) == 1
    letter = provider.sent[0]
    assert letter["to"] == [OWNER]
    assert letter["subject"] == "Today 2026-11-23" and letter["text"] == "A private appointment"
    assert "html" not in letter
    send = next(r for r in provider.requests if r.url.path.endswith("/send"))
    assert send.headers.get("Idempotency-Key") == json.loads(send.content)["add_labels"][0]
    assert a.status()["status"] == "sent"
    assert OWNER not in json.dumps(a.status())
    configure(vault, "changed@example.org")
    await build().tick(MORNING + timedelta(days=1))
    await build().tick(MORNING)
    assert [m["to"] for m in provider.sent] == [[OWNER], ["changed@example.org"]]


@pytest.mark.parametrize("hour,recipient,time,enabled", [
    (6, OWNER, "07:00", True), (12, OWNER, "07:00", True), (9, OWNER, "09:30", True),
    (8, "", "07:00", True), (8, "one@a.org,two@b.org", "07:00", True),
    (8, OWNER, "07:00", False), (8, OWNER, "15:00", True),
])
async def test_no_delivery_outside_schedule_or_without_configuration(setup, hour, recipient, time, enabled):
    vault, provider, mail, build = setup
    configure(vault, recipient, time)
    if not enabled:
        mail.keys = MailKeys()
    await build().tick(MORNING.replace(hour=hour))
    assert provider.requests == []


async def test_clearing_recipient_while_draft_is_created_stops_dispatch(setup):
    vault, provider, _, build = setup
    provider.after_create = lambda: configure(vault, "")
    await build().tick(MORNING)
    assert len(provider.drafts) == 1 and provider.sent == []


async def test_restart_after_lost_acknowledgment_only_reconciles(setup):
    vault, provider, _, build = setup
    sender = build()
    provider.send_error = "lost"
    await sender.tick(MORNING)
    assert sender.status()["status"] == "uncertain"
    restarted = build()
    configure(vault, "new@example.org")
    provider.evidence = False
    await restarted.tick(MORNING + timedelta(minutes=1))
    assert restarted.status()["status"] == "uncertain"
    provider.evidence = True
    await restarted.tick(MORNING.replace(hour=15))
    assert restarted.status()["status"] == "sent"
    assert len(provider.sent) == 1


@pytest.mark.parametrize("error,minutes", [("throttled", 5), ("connect", 1)])
async def test_confirmed_transient_send_retries_after_delay_with_current_destination(setup, error, minutes):
    vault, provider, _, build = setup
    provider.send_error = error
    await build().tick(MORNING)
    assert not provider.sent
    provider.send_error = None
    configure(vault, "new@example.org")
    await build().tick(MORNING + timedelta(seconds=25))
    assert not provider.sent
    await build().tick(MORNING + timedelta(minutes=minutes))
    assert [m["to"] for m in provider.sent] == [["new@example.org"]]


async def test_cancelling_runner_with_a_send_in_flight_preserves_claim(setup, monkeypatch):
    _, provider, _, build = setup
    monkeypatch.setattr("src.mail.morning_delivery._now", lambda: MORNING)
    provider.send_error = "cancel"
    runner = asyncio.create_task(build().run())
    try:
        await asyncio.wait_for(provider.started_send.wait(), 2)
    finally:
        runner.cancel()
        with suppress(asyncio.CancelledError):
            await runner
    restarted = build()
    assert restarted.status()["status"] == "uncertain"
    await restarted.tick()
    assert restarted.status()["status"] == "sent" and len(provider.sent) == 1


async def test_noon_while_preparing_edition_stops_delivery(setup, monkeypatch):
    _, provider, _, build = setup
    clock = [MORNING.replace(hour=11, minute=59)]
    monkeypatch.setattr("src.mail.morning_delivery._now", lambda: clock[0])
    async def slow_edition(now):
        assert now == clock[0]
        clock[0] += timedelta(minutes=2)
        return "Today", "A private appointment"
    await build(slow_edition).tick()
    assert provider.requests == []

"""The /agentmail routes through the real app, AgentMail being the stand-in. No network."""

import httpx
import pytest

from src.events.service import CalendarService
from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.mail.routes import mail_router
from src.mail.service import MailService
from src.mail.settings import BAD_KEY
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from tests.conftest import ScriptedProvider
from tests.mail.conftest import KEY


@pytest.fixture
def cfg(vault, tmp_path):
    c = HarnessConfig(vault_root=vault, skills_dir=tmp_path, shell_cwd=tmp_path)
    c.validate()
    return c


@pytest.fixture
async def calendar(cfg):
    s = CalendarService(cfg, Ledger(cfg.vault_root))
    yield s
    await s.close()


@pytest.fixture
def harness(cfg, calendar, fake_mail):
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=calendar.ledger, catalog=Catalog(),
                calendar=calendar)
    h.mail = MailService(cfg.vault_root, h.ledger, http=fake_mail.client)
    return h


@pytest.fixture
async def client(harness):
    app = create_app(harness)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        yield c


async def enable(client):
    assert (await client.put("/agentmail/key", json={"key": KEY})).status_code == 200
    assert (await client.put("/agentmail/inbox", json={"inbox_id": "zen@agentmail.to"})).status_code == 200


async def test_status_starts_disabled(client):
    r = await client.get("/agentmail/status")
    assert r.status_code == 200
    assert r.json() == {"key_set": False, "key_hint": "", "inbox": None, "enabled": False}


async def test_harness_builds_its_own_mail_service(cfg, calendar):
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=calendar.ledger, catalog=Catalog(),
                calendar=calendar)
    assert isinstance(h.mail, MailService)
    await h.mail.close()


async def test_save_key_then_choose_then_read(client):
    saved = (await client.put("/agentmail/key", json={"key": KEY})).json()
    assert saved["key_set"] is True and saved["key_hint"] == "0001" and saved["enabled"] is False
    listed = (await client.get("/agentmail/inboxes")).json()
    assert listed == {"inboxes": [{"inbox_id": "zen@agentmail.to", "email": "zen@agentmail.to",
                                   "display_name": "ZenBot", "deactivated": False}]}
    chosen = (await client.put("/agentmail/inbox", json={"inbox_id": "zen@agentmail.to"})).json()
    assert chosen["enabled"] is True
    page = (await client.get("/agentmail/messages")).json()
    assert page["email"] == "zen@agentmail.to" and len(page["messages"]) == 2 and page["next_page_token"] == "t2"
    one = (await client.get("/agentmail/messages/m1")).json()
    assert one["text"] == "Full text of m1" and "html" not in one


async def test_create_chooses_the_new_inbox(client):
    await client.put("/agentmail/key", json={"key": KEY})
    r = await client.post("/agentmail/inboxes", json={"username": "Zen2"})
    assert r.status_code == 200 and r.json()["inbox"]["email"] == "zen2@agentmail.to"


@pytest.mark.parametrize("setup,key,status,words", [
    (None, "a b", 422, BAD_KEY),
    (None, "am_wrongkey0000", 422, "did not accept"),
    (httpx.Response(403, json={"code": "missing_permission"}), KEY, 422, "organization API key"),
    (httpx.ConnectError("x"), KEY, 502, "could not be reached"),
])
async def test_key_errors(client, fake_mail, setup, key, status, words):
    if setup is not None:
        fake_mail.fail["GET /inboxes"] = setup
    r = await client.put("/agentmail/key", json={"key": key})
    assert r.status_code == status and words in r.json()["detail"]
    assert (await client.get("/agentmail/status")).json()["key_set"] is False


async def test_no_answer_or_ledger_entry_holds_the_key(client, cfg):
    bodies = [(await client.put("/agentmail/key", json={"key": KEY})).text]
    bodies.append((await client.get("/agentmail/inboxes")).text)
    bodies.append((await client.post("/agentmail/inboxes", json={"username": "zen3"})).text)
    bodies.append((await client.put("/agentmail/inbox", json={"inbox_id": "zen@agentmail.to"})).text)
    bodies.append((await client.get("/agentmail/messages")).text)
    bodies.append((await client.get("/agentmail/messages/m1")).text)
    bodies.append((await client.get("/agentmail/status")).text)
    bodies.append((await client.delete("/agentmail/key")).text)
    assert not any(KEY in b for b in bodies)
    ledger_text = "".join(p.read_text(encoding="utf-8") for p in cfg.vault_root.parent.rglob("ledger.jsonl"))
    assert '"mail"' in ledger_text and KEY not in ledger_text


async def test_inboxes_without_key_is_409(client):
    r = await client.get("/agentmail/inboxes")
    assert r.status_code == 409 and r.json()["detail"] == "Save an AgentMail key first."


async def test_messages_without_inbox_is_409(client):
    await client.put("/agentmail/key", json={"key": KEY})
    for path in ("/agentmail/messages", "/agentmail/messages/m1"):
        r = await client.get(path)
        assert r.status_code == 409 and r.json()["detail"] == "AgentMail is not set up."


async def test_create_taken_is_409(client):
    await client.put("/agentmail/key", json={"key": KEY})
    r = await client.post("/agentmail/inboxes", json={"username": "zen"})
    assert r.status_code == 409 and r.json()["detail"] == "That address is taken. Try another name."


async def test_create_invalid_carries_agentmail_message(client, fake_mail):
    await client.put("/agentmail/key", json={"key": KEY})
    fake_mail.fail["POST /inboxes"] = httpx.Response(400, json={"code": "validation_error", "message": "Too short"})
    r = await client.post("/agentmail/inboxes", json={"username": "z"})
    assert r.status_code == 422 and r.json()["detail"] == "Too short"


async def test_create_bad_name_is_422(client):
    await client.put("/agentmail/key", json={"key": KEY})
    r = await client.post("/agentmail/inboxes", json={"username": "-bad"})
    assert r.status_code == 422 and "lowercase letters" in r.json()["detail"]


async def test_choose_unknown_is_404(client):
    await client.put("/agentmail/key", json={"key": KEY})
    r = await client.put("/agentmail/inbox", json={"inbox_id": "nobody@agentmail.to"})
    assert r.status_code == 404 and r.json()["detail"] == "No such inbox for this key."


async def test_unknown_message_is_404(client):
    await enable(client)
    r = await client.get("/agentmail/messages/nope")
    assert r.status_code == 404 and r.json()["detail"] == "No such message."


async def test_provider_error_is_502(client, fake_mail):
    await enable(client)
    fake_mail.fail["GET /inboxes/zen@agentmail.to/messages"] = httpx.Response(200, text="<html></html>")
    r = await client.get("/agentmail/messages")
    assert r.status_code == 502 and r.json()["detail"] == "AgentMail answered in a way ZenBot could not read."


async def test_busy_is_429(client, fake_mail):
    await enable(client)
    fake_mail.fail["GET /inboxes/zen@agentmail.to/messages"] = 429
    r = await client.get("/agentmail/messages")
    assert r.status_code == 429 and r.json()["detail"] == "AgentMail is busy. Try again in a moment."


async def test_stored_key_now_refused_is_401(client, fake_mail):
    await enable(client)
    fake_mail.key = "am_rotatedkey00"
    r = await client.get("/agentmail/messages")
    assert r.status_code == 401
    assert r.json()["detail"] == "AgentMail no longer accepts the saved key. Save a new one in Settings."


async def test_page_token_is_capped(client, fake_mail):
    await enable(client)
    r = await client.get("/agentmail/messages", params={"page_token": "x" * 1001})
    assert r.status_code == 422 and r.json()["detail"] == "That page could not be read."
    r = await client.get("/agentmail/messages", params={"page_token": "a b&c"})
    assert r.status_code == 200
    assert fake_mail.requests[-1].url.params["page_token"] == "a b&c"


async def test_forget_key(client):
    await enable(client)
    r = await client.delete("/agentmail/key")
    assert r.json() == {"key_set": False, "key_hint": "", "inbox": None, "enabled": False}


async def test_key_that_cannot_be_written_is_500(client, monkeypatch):
    from src.mail import settings as mail_settings

    def broken(self, vault_root):
        raise OSError("disk full")

    monkeypatch.setattr(mail_settings.MailKeys, "save", broken)
    r = await client.put("/agentmail/key", json={"key": KEY})
    assert r.status_code == 500 and r.json()["detail"] == "The key could not be saved."
    assert KEY not in r.text


async def test_every_post_needs_a_body(client, harness):
    await client.put("/agentmail/key", json={"key": KEY})
    paths = sorted(r.path for r in mail_router(harness.mail).routes if "POST" in (getattr(r, "methods", None) or ()))
    assert paths == ["/agentmail/inboxes"]
    foreign = {"Origin": "https://evil.example"}
    for path in paths:
        for headers in (foreign, {**foreign, "Content-Type": "text/plain"},
                        {**foreign, "Content-Type": "application/x-www-form-urlencoded"}):
            r = await client.post(path, content=b"username=zen4", headers=headers)
            assert r.status_code == 422, (path, headers)
        assert (await client.post(path, headers=foreign)).status_code == 422

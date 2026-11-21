"""The mail service: the key check before saving, the inbox choice, enabled, and what reaches the ledger."""

import json

import pytest

from src.mail.agentmail import MailError
from src.mail.service import MailService
from src.mail.settings import KEYS_FILE, MailKeys, MailSettings
from src.vault_settings import settings_dir
from tests.events.conftest import RecordingLedger
from tests.mail.conftest import KEY


@pytest.fixture
def ledger():
    return RecordingLedger()


@pytest.fixture
async def service(vault, ledger, fake_mail):
    s = MailService(vault, ledger, http=fake_mail.client)
    yield s
    await s.close()


async def test_status_enabled_needs_key_and_inbox(service):
    assert service.status() == {"key_set": False, "key_hint": "", "inbox": None, "enabled": False}
    await service.set_key(KEY)
    assert service.status()["enabled"] is False
    await service.choose_inbox("zen@agentmail.to")
    assert service.status() == {"key_set": True, "key_hint": "0001",
                                "inbox": {"inbox_id": "zen@agentmail.to", "email": "zen@agentmail.to"},
                                "enabled": True}


async def test_set_key_checks_before_saving(service, vault):
    with pytest.raises(MailError) as err:
        await service.set_key("am_wrongkey0000")
    assert err.value.kind == "key"
    assert service.status()["key_set"] is False
    assert not (settings_dir(vault) / KEYS_FILE).exists()


async def test_set_key_refuses_a_bad_shape_before_any_request(service, fake_mail):
    with pytest.raises(ValueError):
        await service.set_key("a b")
    assert fake_mail.requests == []


async def test_set_key_clears_choice_new_key_cannot_see(service, fake_mail, ledger):
    await service.set_key(KEY)
    await service.choose_inbox("zen@agentmail.to")
    fake_mail.inboxes.clear()
    await service.set_key(KEY)
    assert service.status()["inbox"] is None
    assert ledger.entries[-1]["event"] == "key_saved" and ledger.entries[-1]["inbox_cleared"] is True


async def test_set_key_keeps_choice_on_other_errors(service, fake_mail):
    await service.set_key(KEY)
    await service.choose_inbox("zen@agentmail.to")
    fake_mail.fail["GET /inboxes/zen@agentmail.to"] = 500
    await service.set_key(KEY)
    assert service.status()["inbox"]["email"] == "zen@agentmail.to"


async def test_create_chooses_and_logs_address_only(service, ledger):
    await service.set_key(KEY)
    status = await service.create_inbox("Zen2")
    assert status["inbox"]["email"] == "zen2@agentmail.to" and status["enabled"] is True
    assert {"_kind": "mail", "event": "inbox_created", "email": "zen2@agentmail.to"} in ledger.entries
    assert KEY not in json.dumps(ledger.entries)


async def test_create_refuses_a_bad_name(service):
    await service.set_key(KEY)
    with pytest.raises(ValueError):
        await service.create_inbox("-bad")


async def test_choose_unknown_inbox(service):
    await service.set_key(KEY)
    await service.choose_inbox("zen@agentmail.to")
    with pytest.raises(MailError) as err:
        await service.choose_inbox("nobody@agentmail.to")
    assert err.value.kind == "not_found"
    assert service.status()["inbox"]["email"] == "zen@agentmail.to"


async def test_inboxes_need_a_key(service):
    with pytest.raises(LookupError):
        await service.inboxes()


async def test_messages_need_enabled(service):
    await service.set_key(KEY)
    with pytest.raises(LookupError):
        await service.messages(None)
    with pytest.raises(LookupError):
        await service.message("m1")


async def test_reading_writes_no_ledger_entry(service, ledger):
    await service.set_key(KEY)
    await service.choose_inbox("zen@agentmail.to")
    before = len(ledger.entries)
    page = await service.messages(None)
    assert page["email"] == "zen@agentmail.to" and len(page["messages"]) == 2 and page["next_page_token"] == "t2"
    assert (await service.message("m1"))["text"] == "Full text of m1"
    assert len(ledger.entries) == before


async def test_forget_key_clears_both(service, vault, ledger):
    await service.set_key(KEY)
    await service.choose_inbox("zen@agentmail.to")
    status = service.forget_key()
    assert status == {"key_set": False, "key_hint": "", "inbox": None, "enabled": False}
    assert MailKeys.load(vault).api_key == "" and MailSettings.load(vault).inbox_id == ""
    assert ledger.entries[-1]["event"] == "key_forgotten"


async def test_service_reloads_saved_state(vault, ledger, fake_mail):
    first = MailService(vault, ledger, http=fake_mail.client)
    await first.set_key(KEY)
    await first.choose_inbox("zen@agentmail.to")
    again = MailService(vault, ledger, http=fake_mail.client)
    assert again.status()["enabled"] is True

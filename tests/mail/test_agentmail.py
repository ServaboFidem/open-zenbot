"""The AgentMail client against the stand-in: shapes, errors, caps, quoting. No network."""

import json

import httpx
import pytest

from src.mail.agentmail import BASE, AgentMail, MailError
from tests.mail.conftest import KEY, message


def mail(fake) -> AgentMail:
    return AgentMail(fake.client, KEY)


async def test_lists_inboxes_with_bearer_header(fake_mail):
    got = await mail(fake_mail).list_inboxes()
    assert got == [{"inbox_id": "zen@agentmail.to", "email": "zen@agentmail.to", "display_name": "ZenBot",
                    "deactivated": False}]
    sent = fake_mail.requests[0]
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert str(sent.url).startswith(BASE + "/inboxes")


async def test_deactivated_inbox_is_marked(fake_mail):
    fake_mail.inboxes[0]["status"] = "deactivated"
    assert (await mail(fake_mail).list_inboxes())[0]["deactivated"] is True


async def test_create_sends_client_id_and_retry_returns_same_inbox(fake_mail):
    client = mail(fake_mail)
    first = await client.create_inbox("zen2")
    again = await client.create_inbox("zen2")
    assert json.loads(fake_mail.requests[0].content) == {"username": "zen2", "display_name": "ZenBot",
                                                         "client_id": "openzenbot-zen2"}
    assert first == again and first["email"] == "zen2@agentmail.to"
    assert [i["inbox_id"] for i in fake_mail.inboxes].count("zen2@agentmail.to") == 1


@pytest.mark.parametrize("answer", [
    httpx.Response(409, json={"name": "Error"}),
    httpx.Response(400, json={"code": "inbox_already_exists", "message": "no"}),
    httpx.Response(422, json={"code": "validation_error", "message": "Username is taken"}),
])
async def test_create_taken(fake_mail, answer):
    fake_mail.fail["POST /inboxes"] = answer
    with pytest.raises(MailError) as err:
        await mail(fake_mail).create_inbox("zen")
    assert err.value.kind == "taken"


async def test_create_invalid(fake_mail):
    fake_mail.fail["POST /inboxes"] = httpx.Response(400, json={"code": "validation_error", "message": "bad"})
    with pytest.raises(MailError) as err:
        await mail(fake_mail).create_inbox("zen")
    assert err.value.kind == "invalid" and err.value.detail == "bad"


@pytest.mark.parametrize("answer,kind", [
    (httpx.Response(401, json={"code": "api_key_expired"}), "key"),
    (httpx.Response(403), "key"),
    (httpx.Response(403, json={"code": "missing_permission"}), "permission"),
    (httpx.Response(429, json={"code": "rate_limit_exceeded"}), "refused"),
    (httpx.Response(500), "provider"),
    (httpx.Response(302, headers={"location": "https://elsewhere.example/"}), "provider"),
    (httpx.Response(404, json={"code": "not_found"}), "not_found"),
    (httpx.ConnectError("refused"), "network"),
    (httpx.ReadTimeout("slow"), "network"),
])
async def test_status_kinds(fake_mail, answer, kind):
    fake_mail.fail["GET /inboxes"] = answer
    with pytest.raises(MailError) as err:
        await mail(fake_mail).list_inboxes()
    assert err.value.kind == kind


@pytest.mark.parametrize("answer", [
    httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"}),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={"inboxes": 5}),
])
async def test_non_json_is_provider(fake_mail, answer):
    fake_mail.fail["GET /inboxes"] = answer
    with pytest.raises(MailError) as err:
        await mail(fake_mail).list_inboxes()
    assert err.value.kind == "provider"


async def test_oversized_answer_is_provider(fake_mail):
    fake_mail.fail["GET /inboxes"] = httpx.Response(200, headers={"content-length": "5000001"}, content=b"{}")
    with pytest.raises(MailError) as err:
        await mail(fake_mail).list_inboxes()
    assert err.value.kind == "provider"


async def test_messages_page(fake_mail):
    client = mail(fake_mail)
    page = await client.list_messages("zen@agentmail.to")
    assert [m["message_id"] for m in page["messages"]] == ["m1", "m2"]
    assert page["next_page_token"] == "t2"
    assert page["messages"][0] == {"message_id": "m1", "from": "Ann Lee <ann@example.com>",
                                   "to": ["zen@agentmail.to"], "subject": "Hello m1", "preview": "Preview of m1",
                                   "timestamp": "2026-11-21T09:30:00Z", "attachments": 1}
    assert fake_mail.requests[-1].url.params["limit"] == "50"
    later = await client.list_messages("zen@agentmail.to", page_token="t2")
    assert fake_mail.requests[-1].url.params["page_token"] == "t2"
    assert later == {"messages": [], "next_page_token": None}


async def test_odd_fields_do_not_break_a_list(fake_mail):
    fake_mail.fail["GET /inboxes/zen@agentmail.to/messages"] = httpx.Response(200, json={"messages": [
        {"message_id": "m1", "from": ["x"], "to": "a@x", "timestamp": 5, "attachments": "x"},
        "junk",
        {"subject": "no id"},
    ], "next_page_token": 7})
    page = await mail(fake_mail).list_messages("zen@agentmail.to")
    assert page["messages"] == [{"message_id": "m1", "from": "", "to": [], "subject": "", "preview": "",
                                 "timestamp": "", "attachments": 0}]
    assert page["next_page_token"] is None


async def test_message_prefers_extracted_text_and_never_html(fake_mail):
    client = mail(fake_mail)
    got = await client.get_message("zen@agentmail.to", "m1")
    assert got["text"] == "Full text of m1"
    assert "html" not in got and "<b>" not in json.dumps(got)
    assert got["attachments"] == ["notes.txt"] and got["cc"] == []
    held = fake_mail.messages["zen@agentmail.to"]
    held.append(message("m3", extracted_text=None))
    assert (await client.get_message("zen@agentmail.to", "m3"))["text"] == "Full text of m3\n\n> quoted"
    held.append(message("m4", extracted_text=None, text=None))
    assert (await client.get_message("zen@agentmail.to", "m4"))["text"] == ""


async def test_caps_and_control_characters(fake_mail):
    held = fake_mail.messages["zen@agentmail.to"]
    held.append(message("m5", subject="Hi there\x07" + "s" * 400, extracted_text="b" * 250_000))
    held.append(message("m6", extracted_text="a\r\nb c\x00d\te"))
    client = mail(fake_mail)
    long = await client.get_message("zen@agentmail.to", "m5")
    assert " " not in long["subject"] and "\x07" not in long["subject"] and len(long["subject"]) <= 300
    assert len(long["text"]) == 200_000
    assert (await client.get_message("zen@agentmail.to", "m6"))["text"] == "a\nb\ncd\te"


async def test_ids_are_quoted_as_one_part(fake_mail):
    with pytest.raises(MailError):
        await mail(fake_mail).get_message("zen@agentmail.to", "a/../b?x=1")
    raw = fake_mail.requests[-1].url.raw_path
    assert raw.endswith(b"/messages/a%2F..%2Fb%3Fx%3D1")
    assert b"/inboxes/zen%40agentmail.to/" in raw


async def test_key_not_in_error(fake_mail):
    fake_mail.key = "am_otherkey0000"
    with pytest.raises(MailError) as err:
        await mail(fake_mail).list_inboxes()
    assert err.value.kind == "key"
    assert KEY not in repr(err.value) and KEY not in str(err.value)
    assert err.value.__cause__ is None


async def test_deeply_nested_answer_is_provider(fake_mail):
    # json.loads raises RecursionError, not ValueError, on deep nesting; it must not escape with the key in frames
    fake_mail.fail["GET /inboxes"] = httpx.Response(200, content=b"[" * 200_000 + b"]" * 200_000)
    with pytest.raises(MailError) as err:
        await mail(fake_mail).list_inboxes()
    assert err.value.kind == "provider" and err.value.__cause__ is None

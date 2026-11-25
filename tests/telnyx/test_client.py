"""The Telnyx client against the stand-in. No network."""

import json

import httpx
import pytest

from src.telnyx.client import Telnyx, TelnyxError
from tests.telnyx.conftest import KEY, number


def client(fake):
    return Telnyx(fake.client, KEY)


async def test_send_posts_three_fields_and_answers_id(fake_telnyx):
    got = await client(fake_telnyx).send("+15550001111", "+15551234567", "Hello")
    assert got == "msg-1"
    request = fake_telnyx.requests[-1]
    assert str(request.url) == "https://api.telnyx.com/v2/messages"
    assert json.loads(request.content) == {"from": "+15550001111", "to": "+15551234567", "text": "Hello"}
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url)
    assert KEY not in repr(client(fake_telnyx))


async def test_numbers_lists_across_profiles_sorted(fake_telnyx):
    fake_telnyx.profiles = [{"id": "p1"}, {"id": "p2"}]
    fake_telnyx.numbers = {"p1": [number("n3", "+15550003333", "p1"), number("bad", "555", "p1")],
                           "p2": [number("n1", "+15550001111", "p2"), number("n2", "+15550002222", "p2")]}
    got = await client(fake_telnyx).numbers()
    assert got == [{"id": "n1", "phone": "+15550001111", "profile_id": "p2"},
                   {"id": "n2", "phone": "+15550002222", "profile_id": "p2"},
                   {"id": "n3", "phone": "+15550003333", "profile_id": "p1"}]
    listed = [r for r in fake_telnyx.requests if r.url.path == "/v2/messaging_profiles"]
    assert listed[0].url.params["page[size]"] == "250"


@pytest.mark.parametrize("answer, kind", [
    (401, "key"), (403, "forbidden"), (429, "refused"), (400, "invalid"), (422, "invalid"), (500, "provider"),
    (httpx.Response(302, headers={"Location": "https://elsewhere.test/"}), "provider"),
    (httpx.Response(200, json={"data": {}}), "provider"),
    (httpx.Response(200, content=b"x" * 1_000_001), "provider"),
    (httpx.Response(200, content=b"not json"), "provider"),
])
async def test_error_kinds(fake_telnyx, answer, kind):
    fake_telnyx.fail["POST /messages"] = answer
    with pytest.raises(TelnyxError) as caught:
        await client(fake_telnyx).send("+15550001111", "+15551234567", "Hello")
    assert caught.value.kind == kind
    assert len(fake_telnyx.requests) == 1  # a redirect is not followed


async def test_numbers_of_the_wrong_shape_is_provider(fake_telnyx):
    fake_telnyx.fail["GET /messaging_profiles"] = httpx.Response(200, json={"data": "x"})
    with pytest.raises(TelnyxError) as caught:
        await client(fake_telnyx).numbers()
    assert caught.value.kind == "provider"


async def test_before_request_only_for_connect_errors(fake_telnyx):
    fake_telnyx.fail["POST /messages"] = httpx.ConnectError("refused")
    with pytest.raises(TelnyxError) as caught:
        await client(fake_telnyx).send("+15550001111", "+15551234567", "Hello")
    assert (caught.value.kind, caught.value.before_request) == ("network", True)
    fake_telnyx.fail["POST /messages"] = httpx.ReadTimeout("slow")
    with pytest.raises(TelnyxError) as caught:
        await client(fake_telnyx).send("+15550001111", "+15551234567", "Hello")
    assert (caught.value.kind, caught.value.before_request) == ("network", False)
    fake_telnyx.fail["POST /messages"] = 429
    with pytest.raises(TelnyxError) as caught:
        await client(fake_telnyx).send("+15550001111", "+15551234567", "Hello")
    assert (caught.value.status, caught.value.before_request) == (429, False)


async def test_error_text_never_kept(fake_telnyx):
    fake_telnyx.fail["POST /messages"] = httpx.Response(422, json={"errors": [{"detail": "+15551234567 is invalid"}]})
    with pytest.raises(TelnyxError) as caught:
        await client(fake_telnyx).send("+15550001111", "+15551234567", "Hello")
    assert str(caught.value) == "invalid"


async def test_wrong_key_is_key(fake_telnyx):
    with pytest.raises(TelnyxError) as caught:
        await Telnyx(fake_telnyx.client, "OTHER").numbers()
    assert caught.value.kind == "key"

import base64
import hashlib
import urllib.parse

import httpx
import pytest

from src.events.google import Google, GoogleError, READ_SCOPES, WRITE_SCOPE, auth_url, pkce
from src.events.settings import CalendarKeys

CAL = "primary"


async def test_refreshes_once_and_reuses_the_access_token(fake, google):
    await google.calendar_list()
    await google.calendar_list()
    assert len(fake.token_requests()) == 1
    assert fake.requests[-1].headers["authorization"] == "Bearer access-1"


async def test_refreshes_again_after_expiry_or_forget(fake):
    now = [0.0]
    keys = CalendarKeys(client_id="c", client_secret="s", refresh_token="r")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
        g = Google(http, lambda: keys, clock=lambda: now[0])
        await g.token()
        now[0] = 3550.0  # inside the last 60 seconds of a 3600 second token
        await g.token()
        assert fake.access_tokens == 2
        g.forget()
        await g.token()
        assert fake.access_tokens == 3


async def test_no_refresh_token_is_auth(fake):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
        g = Google(http, lambda: CalendarKeys(client_id="c", client_secret="s"))
        with pytest.raises(GoogleError) as e:
            await g.token()
    assert e.value.kind == "auth"
    assert fake.requests == []


async def test_invalid_grant_is_auth(fake, google):
    fake.refresh_ok = False
    with pytest.raises(GoogleError) as e:
        await google.calendar_list()
    assert e.value.kind == "auth"


async def test_exchange_returns_tokens_and_caches_the_access_token(fake, google):
    fake.scope = WRITE_SCOPE
    out = await google.exchange("code", "verifier", "http://127.0.0.1:8765/cb")
    assert out["refresh_token"] == "refresh-1" and out["scope"] == WRITE_SCOPE
    form = dict(urllib.parse.parse_qsl(fake.requests[-1].content.decode()))
    assert form["grant_type"] == "authorization_code" and form["code_verifier"] == "verifier"
    await google.calendar_list()
    assert len(fake.token_requests()) == 1


async def test_revoke_swallows_errors(fake, google):
    fake.fail["/revoke"] = 500
    await google.revoke("t")


async def test_network_error_is_network():
    def boom(request):
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        g = Google(http, lambda: CalendarKeys(client_id="c", client_secret="s", refresh_token="r"))
        with pytest.raises(GoogleError) as e:
            await g.calendar_list()
    assert e.value.kind == "network"


async def test_calendar_list_returns_raw_items(fake, google):
    fake.calendars = [{"id": "a"}, {"id": "b"}]
    assert await google.calendar_list() == fake.calendars


async def test_list_events_pages_and_returns_the_sync_token(fake, google):
    fake.page_size = 2
    for i in range(5):
        fake.put(CAL, {"id": f"e{i}", "summary": str(i)})
    items, token = await google.list_events(CAL)
    assert [i["id"] for i in items] == [f"e{i}" for i in range(5)]
    assert token.startswith("sync-")
    first = fake.requests[-3].url.params
    assert first["singleEvents"] == "true" and first["showDeleted"] == "true" and first["maxResults"] == "250"


async def test_sync_token_returns_only_changes_and_stale_is_gone(fake, google):
    fake.put(CAL, {"id": "a"})
    _, token = await google.list_events(CAL)
    fake.put(CAL, {"id": "b"})
    items, _ = await google.list_events(CAL, sync_token=token)
    assert [i["id"] for i in items] == ["b"]
    with pytest.raises(GoogleError) as e:
        await google.list_events(CAL, sync_token="stale")
    assert e.value.kind == "gone"


async def test_time_bounds_are_sent_only_without_a_sync_token(fake, google):
    await google.list_events(CAL, time_min="2026-10-01T00:00:00Z", time_max="2027-01-01T00:00:00Z")
    assert fake.requests[-1].url.params["timeMin"] == "2026-10-01T00:00:00Z"
    _, token = await google.list_events(CAL)
    await google.list_events(CAL, sync_token=token, time_min="2026-10-01T00:00:00Z", time_max="x")
    params = fake.requests[-1].url.params
    assert "timeMin" not in params and "timeMax" not in params and params["syncToken"] == token


async def test_calendar_id_is_quoted(fake, google):
    await google.list_events("en.usa#holiday@group.v.calendar.google.com")
    assert "en.usa%23holiday%40group.v.calendar.google.com" in str(fake.requests[-1].url)


async def test_event_id_is_quoted(fake, google):
    with pytest.raises(GoogleError):
        await google.get_event(CAL, "a/b")
    assert "a%2Fb" in str(fake.requests[-1].url)


async def test_get_insert_patch_delete(fake, google):
    made = await google.insert_event(CAL, {"id": "e1", "summary": "S"})
    assert made["summary"] == "S"
    with pytest.raises(GoogleError) as e:
        await google.insert_event(CAL, {"id": "e1"})
    assert e.value.kind == "conflict"
    patched = await google.patch_event(CAL, "e1", {"summary": "T"})
    assert patched["summary"] == "T" and patched["etag"] != made["etag"]
    assert (await google.get_event(CAL, "e1"))["summary"] == "T"
    await google.delete_event(CAL, "e1")
    assert (await google.get_event(CAL, "e1"))["status"] == "cancelled"
    await google.delete_event(CAL, "e1")  # 410 is success
    with pytest.raises(GoogleError) as e:
        await google.patch_event(CAL, "nope", {})
    assert e.value.kind == "missing"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "forbidden"), (404, "missing"), (409, "conflict"),
                                         (410, "gone"), (429, "rate"), (500, "provider")])
async def test_error_kinds(fake, google, status, kind):
    fake.fail["calendarList"] = status
    with pytest.raises(GoogleError) as e:
        await google.calendar_list()
    assert (e.value.kind, e.value.status) == (kind, status)


@pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded"])
async def test_403_with_a_rate_reason_is_rate(fake, google, reason):
    fake.fail["calendarList"] = 403
    fake.fail_reason = reason
    with pytest.raises(GoogleError) as e:
        await google.calendar_list()
    assert e.value.kind == "rate"


def test_auth_url_and_pkce():
    verifier, challenge = pkce()
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert "=" not in verifier + challenge
    url = auth_url("cid", "http://127.0.0.1:8765/cb", READ_SCOPES, "st", challenge)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert q == {
        "client_id": "cid", "redirect_uri": "http://127.0.0.1:8765/cb", "response_type": "code",
        "scope": " ".join(READ_SCOPES), "state": "st", "code_challenge": challenge,
        "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true",
    }

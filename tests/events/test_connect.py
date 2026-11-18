import pytest

from src.events.connect import DIFFERENT, INVALID
from src.events.google import READ_SCOPES, WRITE_SCOPE
from tests.events.conftest import state_of


def cal(email, primary=True):
    return {"id": email, "summary": email, "accessRole": "owner", "primary": primary}


def sign_in_as(fake, email, scope=" ".join(READ_SCOPES)):
    fake.calendars = [cal(email), cal("team@group.calendar.google.com", False)]
    fake.scope = scope


async def connect(conn, fake, email="a@x.org", write=False, **kw):
    sign_in_as(fake, email, **kw)
    return await conn.finish(state_of(conn.begin(write=write)), "code", None)


async def test_connect_saves_the_token_account_scopes_and_calendars(conn, fake, store):
    assert conn.state() == "ready"
    assert await connect(conn, fake) == (True, "Connected. You can close this tab.")
    assert conn.state() == "connected"
    assert conn.keys.refresh_token == "refresh-1"
    assert conn.settings.account == "a@x.org"
    assert conn.settings.scopes == list(READ_SCOPES)
    assert [c["id"] for c in store.calendars() if c["source"] == "google"] == [
        "a@x.org", "team@group.calendar.google.com"]
    text = conn.ledger.file.read_text()
    assert '"connect"' in text and "refresh-1" not in text and '"code"' not in text


async def test_state_is_single_use(conn, fake):
    sign_in_as(fake, "a@x.org")
    s = state_of(conn.begin(write=False))
    assert (await conn.finish(s, "code", None))[0] is True
    assert (await conn.finish(s, "code", None)) == (False, INVALID)


async def test_unknown_missing_and_expired_state_change_nothing(conn, fake, clock):
    sign_in_as(fake, "a@x.org")
    s = state_of(conn.begin(write=False))
    clock.now += 601
    for bad in ("nope", None, "", s):
        assert await conn.finish(bad, "code", None) == (False, INVALID)
    assert conn.state() == "ready" and fake.token_requests() == [] and conn.settings.account == ""


async def test_a_new_connection_object_does_not_know_an_old_state(conn_factory, fake):
    first = conn_factory()
    first.set_credentials("cid", "secret")
    sign_in_as(fake, "a@x.org")
    s = state_of(first.begin(write=False))
    second = conn_factory()
    assert await second.finish(s, "code", None) == (False, INVALID)


async def test_at_most_five_pending_attempts_oldest_dropped(conn, fake):
    sign_in_as(fake, "a@x.org")
    states = [state_of(conn.begin(write=False)) for _ in range(6)]
    assert await conn.finish(states[0], "code", None) == (False, INVALID)
    assert (await conn.finish(states[5], "code", None))[0] is True


async def test_cancel_at_google_changes_nothing(conn, fake):
    s = state_of(conn.begin(write=False))
    assert await conn.finish(s, None, "access_denied") == (False, "Google sign-in was cancelled. Nothing changed.")
    assert conn.state() == "ready" and fake.token_requests() == []
    assert await conn.finish(s, "code", None) == (False, INVALID)  # and the state is spent


async def test_a_failed_exchange_answers_a_plain_sentence_and_saves_nothing(conn, fake):
    sign_in_as(fake, "a@x.org")
    fake.fail["oauth2.googleapis.com/token"] = 500
    s = state_of(conn.begin(write=False))
    assert await conn.finish(s, "code", None) == (False, "Google could not complete the sign-in. Try again.")
    assert conn.state() == "ready" and conn.settings.account == ""


async def test_a_different_account_is_refused_while_connected(conn, fake, store):
    await connect(conn, fake, "a@x.org")
    before = (conn.keys.refresh_token, conn.settings.scopes, conn.store.calendars())
    fake.access_tokens = 0
    assert await connect(conn, fake, "b@x.org") == (False, DIFFERENT)
    assert (conn.keys.refresh_token, conn.settings.scopes, conn.store.calendars()) == before
    assert conn.settings.account == "a@x.org" and conn.state() == "connected"
    assert conn.google._access == ""  # the other account's access token is not kept


async def test_a_different_account_is_refused_while_needing_reconnect(conn, fake):
    await connect(conn, fake, "a@x.org")
    conn.mark_needs_reconnect()
    assert conn.state() == "needs_reconnect"
    assert await connect(conn, fake, "b@x.org") == (False, DIFFERENT)
    assert conn.state() == "needs_reconnect"


async def test_reconnecting_the_same_account_clears_the_flag(conn, fake):
    await connect(conn, fake, "a@x.org")
    conn.mark_needs_reconnect()
    assert (await connect(conn, fake, "a@x.org"))[0] is True
    assert conn.state() == "connected"


async def test_a_different_account_after_disconnect_clears_links(conn, fake, store):
    await connect(conn, fake, "a@x.org")
    e = store.add({"title": "t", "start": "2026-11-20T10:00:00Z", "end": "2026-11-20T11:00:00Z", "tz": "UTC"})
    store.mark_pushed(e["id"], google_calendar="a@x.org", google_id="g1", etag="1", link="")
    await conn.disconnect()
    assert (await connect(conn, fake, "b@x.org"))[0] is True
    assert store.event(e["id"])["google_id"] == ""
    assert conn.settings.account == "b@x.org"


async def test_same_account_after_disconnect_keeps_links(conn, fake, store):
    await connect(conn, fake, "a@x.org")
    e = store.add({"title": "t", "start": "2026-11-20T10:00:00Z", "end": "2026-11-20T11:00:00Z", "tz": "UTC"})
    store.mark_pushed(e["id"], google_calendar="a@x.org", google_id="g1", etag="1", link="")
    await conn.disconnect()
    assert (await connect(conn, fake, "a@x.org"))[0] is True
    assert store.event(e["id"])["google_id"] == "g1"


async def test_disconnect_removes_pulled_events_and_keeps_made_here(conn, fake, store):
    await connect(conn, fake, "a@x.org", write=True, scope=f"{READ_SCOPES[0]} {WRITE_SCOPE}")
    conn.settings.push = True
    conn.settings.destination = "a@x.org"
    local = store.add({"title": "mine", "start": "2026-11-20T10:00:00Z", "end": "2026-11-20T11:00:00Z", "tz": "UTC"})
    store.apply_remote("a@x.org", "g9", {"title": "theirs", "start": "2026-11-21T10:00:00Z",
                                         "end": "2026-11-21T11:00:00Z", "tz": "UTC", "all_day": False}, etag="1", link="", series_id="")
    store.set_meta("needs_reconnect", "1")
    await conn.disconnect()
    assert conn.state() == "ready" and conn.keys.refresh_token == ""
    assert conn.settings.scopes == [] and conn.settings.push is False
    assert conn.settings.account == "a@x.org" and conn.settings.destination == "a@x.org"
    assert not [c for c in store.calendars() if c["source"] == "google"]
    assert store.event(local["id"]) is not None
    assert [e["title"] for e in store.events("2026-01-01", "2027-01-01")] == ["mine"]
    assert any("/revoke" in str(r.url) for r in fake.requests)
    assert '"disconnect"' in conn.ledger.file.read_text()


async def test_disconnect_survives_google_being_unreachable(conn, fake):
    await connect(conn, fake)
    fake.fail["/revoke"] = 500
    await conn.disconnect()
    assert conn.state() == "ready"


async def test_write_upgrade_adds_the_write_scope(conn, fake):
    await connect(conn, fake)
    assert conn.can_write() is False
    url = conn.begin(write=True)
    assert "calendar.events" in url and "calendar.events.readonly" in url
    import urllib.parse
    scope = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["scope"][0].split()
    assert scope == READ_SCOPES + [WRITE_SCOPE]
    fake.scope = " ".join(scope)
    assert (await conn.finish(state_of(url), "code", None))[0] is True
    assert conn.can_write() is True and conn.state() == "connected"


async def test_an_upgrade_keeps_the_saved_token_when_google_sends_none(conn, fake):
    await connect(conn, fake)
    conn.keys.refresh_token = "saved-token"
    original = fake._token

    def no_refresh(request):
        resp = original(request)
        import json
        body = {k: v for k, v in json.loads(resp.content).items() if k != "refresh_token"}
        import httpx
        return httpx.Response(200, json=body)

    fake._token = no_refresh
    assert (await connect(conn, fake, write=True))[0] is True
    assert conn.keys.refresh_token == "saved-token"


async def test_first_sign_in_without_a_refresh_token_fails(conn, fake):
    original = fake._token

    def no_refresh(request):
        import json
        import httpx
        body = {k: v for k, v in json.loads(original(request).content).items() if k != "refresh_token"}
        return httpx.Response(200, json=body)

    fake._token = no_refresh
    assert (await connect(conn, fake)) == (False, "Google could not complete the sign-in. Try again.")
    assert conn.state() == "ready"


async def test_the_state_never_reaches_the_ledger_or_the_keys_file(conn, fake):
    sign_in_as(fake, "a@x.org")
    s = state_of(conn.begin(write=False))
    await conn.finish(s, "the-code", None)
    await conn.finish(s, "the-code", None)
    text = conn.ledger.file.read_text()
    assert s not in text and "the-code" not in text and "secret" not in text


def test_credentials_cannot_change_while_connected(conn):
    conn.keys.refresh_token = "r"
    for call in (lambda: conn.set_credentials("a", "b"), conn.clear_credentials):
        with pytest.raises(ValueError, match="Disconnect Google first."):
            call()
    assert conn.keys.client_id == "cid"


def test_credentials_are_stripped_required_and_saved(conn_factory):
    c = conn_factory()
    assert c.state() == "not_set_up"
    for bad in (("", "x"), ("x", "  ")):
        with pytest.raises(ValueError):
            c.set_credentials(*bad)
    with pytest.raises(ValueError):
        c.begin(write=False)
    c.set_credentials("  id ", " sec ")
    assert (c.keys.client_id, c.keys.client_secret, c.state()) == ("id", "sec", "ready")
    assert conn_factory().keys.client_secret == "sec"
    c.clear_credentials()
    assert c.state() == "not_set_up"


def test_begin_url_uses_the_loopback_callback(conn):
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fcalendar%2Fgoogle%2Fcallback" in conn.begin(write=False)
    assert conn.redirect_uri == "http://127.0.0.1:8765/calendar/google/callback"


async def test_the_account_is_read_with_the_new_token_not_the_saved_one(conn, fake):
    await connect(conn, fake, "a@x.org")
    a_list = list(fake.calendars)
    url = conn.begin(write=True)
    fake.omit_expiry = True  # without expires_in a cached token would count as expired
    fake.calendars_by_token = {f"access-{fake.access_tokens + 1}": [cal("b@x.org")]}
    fake.calendars = a_list  # every other token (a refresh) sees the connected account
    assert await conn.finish(state_of(url), "code", None) == (False, DIFFERENT)
    assert conn.settings.account == "a@x.org" and conn.keys.refresh_token == "refresh-1"
    assert not any(r.url.path.endswith("/calendarList") and r.headers["authorization"] != f"Bearer access-2"
                   for r in fake.requests[-3:])


async def test_a_response_without_scope_is_a_failed_sign_in_and_revokes_the_new_token(conn, fake):
    sign_in_as(fake, "a@x.org", scope="")
    s = state_of(conn.begin(write=False))
    assert await conn.finish(s, "code", None) == (False, "Google could not complete the sign-in. Try again.")
    assert conn.state() == "ready" and conn.settings.account == "" and conn.settings.scopes == []
    assert any("/revoke" in str(r.url) for r in fake.requests)


async def test_a_failed_calendar_list_revokes_the_new_token(conn, fake):
    sign_in_as(fake, "a@x.org")
    fake.fail["calendarList"] = 500
    assert (await conn.finish(state_of(conn.begin(write=False)), "code", None))[0] is False
    assert conn.state() == "ready"
    assert any("/revoke" in str(r.url) for r in fake.requests)


# the final review's findings ----------------------------------------------------

LIST, EVENTS = READ_SCOPES
NO_EVENTS = ("Google did not give OpenZenBot permission to see your events. Sign in again and leave that box "
             "ticked.")
NO_LIST = ("Google did not give OpenZenBot permission to see your list of calendars. Sign in again and leave "
           "that box ticked.")
NEITHER = ("Google did not give OpenZenBot permission to see your list of calendars and your events. Sign in "
           "again and leave those boxes ticked.")


@pytest.mark.parametrize("scope, sentence", [(LIST, NO_EVENTS), (EVENTS, NO_LIST), (WRITE_SCOPE, NO_LIST),
                                             ("openid email", NEITHER)])
async def test_a_sign_in_without_a_read_permission_is_refused_and_says_which(conn, fake, store, scope, sentence):
    assert await connect(conn, fake, scope=scope) == (False, sentence)
    assert conn.state() == "ready" and conn.keys.refresh_token == "" and conn.settings.scopes == []
    assert not [c for c in store.calendars() if c["source"] == "google"]
    revoked = [r for r in fake.requests if "/revoke" in str(r.url)]
    assert len(revoked) == 1 and b"refresh-1" in revoked[0].content
    assert not any("calendarList" in str(r.url) for r in fake.requests)
    assert '"missing_scope"' in conn.ledger.file.read_text()


@pytest.mark.parametrize("scope", [f"{LIST} {WRITE_SCOPE}", "https://www.googleapis.com/auth/calendar",
                                   "https://www.googleapis.com/auth/calendar.readonly"])
async def test_a_wider_permission_covers_the_read_ones(conn, fake, scope):
    assert (await connect(conn, fake, scope=scope))[0] is True


async def test_the_primary_calendar_is_selected_when_an_account_is_first_connected(conn, fake, store):
    await connect(conn, fake)
    cals = {c["id"]: c for c in store.calendars()}
    assert cals["a@x.org"]["selected"] is True and cals["team@group.calendar.google.com"]["selected"] is False


async def test_a_later_sign_in_leaves_the_users_choice_of_calendars_alone(conn, fake, store):
    await connect(conn, fake)
    store.set_calendar("a@x.org", selected=False)
    await connect(conn, fake, write=True, scope=" ".join(READ_SCOPES + [WRITE_SCOPE]))
    assert {c["id"]: c for c in store.calendars()}["a@x.org"]["selected"] is False
    conn.mark_needs_reconnect()
    await connect(conn, fake)
    assert {c["id"]: c for c in store.calendars()}["a@x.org"]["selected"] is False
    await conn.disconnect()
    await connect(conn, fake)  # connected afresh: the calendars are new rows
    assert {c["id"]: c for c in store.calendars()}["a@x.org"]["selected"] is True


async def test_disconnect_forgets_the_last_sync_and_its_error(conn, fake, store):
    await connect(conn, fake)
    store.set_meta("last_sync", "2026-11-17T12:00:00Z")
    store.set_meta("last_error", "network")
    store.set_meta("lost_edits", "1")
    await conn.disconnect()
    assert store.meta("last_sync") == "" and store.meta("last_error") == ""
    assert store.meta("lost_edits") == "1"  # still to be shown

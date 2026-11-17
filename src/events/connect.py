"""Connecting, reconnecting and disconnecting Google.

The single-use ``state`` value is the callback's only credential: it is random, kept in memory only, spent the
moment it is looked up and valid for ``STATE_LIFETIME`` seconds. No code, token, secret or state value is ever
written to the ledger, to an exception message or to the sentence handed back to the callback page."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from src.events.convert import calendar_row
from src.events.google import READ_SCOPES, WRITE_SCOPE, Google, GoogleError, auth_url, pkce
from src.events.settings import CalendarKeys, CalendarSettings
from src.events.store import CalendarStore
from src.memory.exclusion import exclusion
from src.memory.ledger import Kind, Ledger

STATE_LIFETIME = 600.0
MAX_PENDING = 5
NEEDS_RECONNECT = "needs_reconnect"

INVALID = "This sign-in link is no longer valid. Start again from OpenZenBot."
CANCELLED = "Google sign-in was cancelled. Nothing changed."
DIFFERENT = "That is a different Google account. Disconnect first."
FAILED = "Google could not complete the sign-in. Try again."
DONE = "Connected. You can close this tab."


class Connection:
    def __init__(self, vault_root: Path, store: CalendarStore, google: Google, ledger: Ledger, *,
                 port: int, clock=time.monotonic):
        self.vault_root = Path(vault_root)
        self.store = store
        self.google = google
        self.ledger = ledger
        self.port = port
        self._clock = clock
        self.settings = CalendarSettings.load(self.vault_root)
        self.keys = CalendarKeys.load(self.vault_root)
        self._pending: dict[str, tuple[str, list[str], float]] = {}

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/calendar/google/callback"

    def state(self) -> str:
        if not (self.keys.client_id and self.keys.client_secret):
            return "not_set_up"
        if not self.keys.refresh_token:
            return "ready"
        return "needs_reconnect" if self.store.meta(NEEDS_RECONNECT) else "connected"

    def can_write(self) -> bool:
        return WRITE_SCOPE in self.settings.scopes

    def _linked(self) -> bool:
        return self.state() in ("connected", "needs_reconnect")

    def _log(self, event: str, **data) -> None:
        self.ledger.append(Kind.calendar, "", "", event=event, **data)

    # credentials ------------------------------------------------------------

    def set_credentials(self, client_id: str, client_secret: str) -> None:
        client_id, client_secret = (client_id or "").strip(), (client_secret or "").strip()
        if not client_id or not client_secret:
            raise ValueError("Both the client id and the client secret are needed.")
        if self._linked():
            raise ValueError("Disconnect Google first.")
        exclusion.register_secret_value(client_secret)
        self.keys.client_id, self.keys.client_secret = client_id, client_secret
        self.keys.save(self.vault_root)

    def clear_credentials(self) -> None:
        if self._linked():
            raise ValueError("Disconnect Google first.")
        self.keys.client_id = self.keys.client_secret = ""
        self.keys.save(self.vault_root)

    # sign-in ----------------------------------------------------------------

    def begin(self, *, write: bool) -> str:
        if self.state() == "not_set_up":
            raise ValueError("Set up the Google client first.")
        scopes = READ_SCOPES + [WRITE_SCOPE] if write else list(READ_SCOPES)
        verifier, challenge = pkce()
        state = secrets.token_urlsafe(32)
        self._pending[state] = (verifier, scopes, self._clock() + STATE_LIFETIME)
        while len(self._pending) > MAX_PENDING:
            del self._pending[next(iter(self._pending))]
        return auth_url(self.keys.client_id, self.redirect_uri, scopes, state, challenge)

    async def finish(self, state: str | None, code: str | None, error: str | None) -> tuple[bool, str]:
        attempt = self._pending.pop(state, None) if state else None
        if attempt is None or self._clock() >= attempt[2]:
            self._log("connect_refused", reason="invalid_state")
            return False, INVALID
        verifier, scopes, _ = attempt
        if error:
            self._log("connect_refused", reason="cancelled")
            return False, CANCELLED
        if not code:
            self._log("connect_refused", reason="no_code")
            return False, FAILED
        try:
            tokens = await self.google.exchange(code, verifier, self.redirect_uri)
            new_refresh = str(tokens.get("refresh_token") or "")
            if not new_refresh and not self.keys.refresh_token:
                raise GoogleError("provider", "Google sent no refresh token")
            rows = [calendar_row(item) for item in await self.google.calendar_list()]
            account = next((r["id"] for r in rows if r["primary"]), "")
            if not account:
                raise GoogleError("provider", "Google listed no primary calendar")
        except GoogleError as e:
            self.google.forget()
            self._log("connect_refused", reason="google_error", error_kind=e.kind)
            return False, FAILED
        was_linked = self._linked()
        if self.settings.account and self.settings.account != account:
            if was_linked:
                self.google.forget()  # the access token just issued belongs to the other account
                if new_refresh:
                    await self.google.revoke(new_refresh)
                self._log("connect_refused", reason="different_account")
                return False, DIFFERENT
            self.store.clear_links()
        if new_refresh:
            exclusion.register_secret_value(new_refresh)
            self.keys.refresh_token = new_refresh
            self.keys.save(self.vault_root)
        granted = str(tokens.get("scope") or "").split()
        self.settings.account = account
        self.settings.scopes = granted or scopes
        self.settings.save(self.vault_root)
        self.store.set_meta(NEEDS_RECONNECT, "")
        self.store.upsert_google(rows)
        self._log("connect", calendars=len(rows), write=self.can_write())
        return True, DONE

    def mark_needs_reconnect(self) -> None:
        self.store.set_meta(NEEDS_RECONNECT, "1")
        self.google.forget()

    async def disconnect(self) -> None:
        token = self.keys.refresh_token
        if token:
            await self.google.revoke(token)  # best effort: it swallows network errors
        self.keys.refresh_token = ""
        self.keys.save(self.vault_root)
        self.settings.scopes = []
        self.settings.push = False
        self.settings.save(self.vault_root)
        self.google.forget()
        self.store.drop_google()
        self.store.set_meta(NEEDS_RECONNECT, "")
        self._log("disconnect")

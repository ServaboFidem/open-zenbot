"""The mail service: the saved key and inbox choice, one HTTP client, and the calls the /agentmail routes make.

Nothing here logs mail. The ledger gets kind `mail` entries for the four changes (`key_saved`, `key_forgotten`,
`inbox_created`, `inbox_chosen`) carrying ZenBot's own address only; reading the inbox writes nothing."""

from __future__ import annotations

from pathlib import Path

import httpx

from src.mail.agentmail import TIMEOUT_S, AgentMail, MailError
from src.mail.settings import MailKeys, MailSettings, check_key, check_username
from src.memory.ledger import Kind


class MailService:
    def __init__(self, vault_root: Path, ledger, http: httpx.AsyncClient | None = None):
        self.vault_root = vault_root
        self.ledger = ledger
        self.settings = MailSettings.load(vault_root)
        self.keys = MailKeys.load(vault_root)
        self._own_http = http is None
        self.http = http if http is not None else httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)

    def __repr__(self) -> str:  # never the key
        return "MailService()"

    def _client(self, key: str | None = None) -> AgentMail:
        return AgentMail(self.http, key if key is not None else self.keys.api_key)

    def _log(self, event: str, **data) -> None:
        self.ledger.append(Kind.mail, "", "", event=event, **data)

    def _choose(self, inbox: dict) -> None:
        settings = MailSettings(inbox_id=inbox["inbox_id"], email=inbox["email"])
        settings.save(self.vault_root)
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.keys.api_key) and bool(self.settings.inbox_id)

    def morning_client(self) -> AgentMail:
        """A client for the internal scheduler, using the currently configured account."""
        if not self.enabled:
            raise LookupError("not set up")
        return self._client()

    def status(self) -> dict:
        inbox = ({"inbox_id": self.settings.inbox_id, "email": self.settings.email}
                 if self.settings.inbox_id else None)
        return {**self.keys.public(), "inbox": inbox, "enabled": self.enabled}

    async def set_key(self, raw: object) -> dict:
        """Check the key against AgentMail, then save it. A chosen inbox the new key cannot see is cleared."""
        key = check_key(raw)
        client = self._client(key)
        await client.list_inboxes(limit=1)
        cleared = False
        if self.settings.inbox_id:
            try:
                await client.get_inbox(self.settings.inbox_id)
            except MailError as err:
                cleared = err.kind == "not_found"
        keys = MailKeys(api_key=key)
        keys.save(self.vault_root)  # written before it is used, so a failed write leaves the old key in use
        self.keys = keys
        if cleared:
            empty = MailSettings()
            empty.save(self.vault_root)
            self.settings = empty
        self._log("key_saved", inbox_cleared=cleared)
        return self.status()

    def forget_key(self) -> dict:
        """Forget the key and the inbox choice. Nothing changes at AgentMail."""
        keys, settings = MailKeys(), MailSettings()
        keys.save(self.vault_root)
        settings.save(self.vault_root)
        self.keys, self.settings = keys, settings
        self._log("key_forgotten")
        return self.status()

    async def inboxes(self) -> list[dict]:
        if not self.keys.api_key:
            raise LookupError("no key")
        return await self._client().list_inboxes()

    async def create_inbox(self, raw: object) -> dict:
        if not self.keys.api_key:
            raise LookupError("no key")
        made = await self._client().create_inbox(check_username(raw))
        self._choose(made)
        self._log("inbox_created", email=made["email"])
        return self.status()

    async def choose_inbox(self, inbox_id: str) -> dict:
        if not self.keys.api_key:
            raise LookupError("no key")
        found = await self._client().get_inbox(inbox_id)
        self._choose(found)
        self._log("inbox_chosen", email=found["email"])
        return self.status()

    async def messages(self, page_token: str | None) -> dict:
        if not self.enabled:
            raise LookupError("not set up")
        page = await self._client().list_messages(self.settings.inbox_id, page_token=page_token)
        return {"email": self.settings.email, **page}

    async def message(self, message_id: str) -> dict:
        if not self.enabled:
            raise LookupError("not set up")
        return await self._client().get_message(self.settings.inbox_id, message_id)

    async def close(self) -> None:
        if self._own_http:
            await self.http.aclose()

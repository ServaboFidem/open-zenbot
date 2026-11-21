"""The /agentmail HTTP routes. No answer carries the key; no answer carries a message's HTML.

Every POST takes a required JSON body (a cross-site page cannot send one without a preflight). A route that may
hold the key in its frames never lets an exception leave it: a write failure is answered 500, and only the
exception's class name is logged."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.mail.agentmail import MailError
from src.mail.service import MailService

log = logging.getLogger(__name__)

NO_KEY = "Save an AgentMail key first."
NOT_SET_UP = "AgentMail is not set up."
BAD_TOKEN = "That page could not be read."
KEY_NOT_SAVED = "The key could not be saved."
TOKEN_MAX = 1000
UNREACHABLE = "AgentMail could not be reached."
# a MailError's kind on any route, when the route has no sentence of its own for it
GENERAL = {
    "refused": (429, "AgentMail is busy. Try again in a moment."),
    "key": (401, "AgentMail no longer accepts the saved key. Save a new one in Settings."),
    "network": (502, UNREACHABLE),
}
UNREADABLE = (502, "AgentMail answered in a way ZenBot could not read.")


class KeyIn(BaseModel):
    key: str


class UsernameIn(BaseModel):
    username: str


class InboxIn(BaseModel):
    inbox_id: str


def _fail(err: MailError, own: dict[str, tuple[int, str]] | None = None) -> HTTPException:
    status, text = (own or {}).get(err.kind) or GENERAL.get(err.kind) or UNREADABLE
    return HTTPException(status, text)


def mail_router(service: MailService) -> APIRouter:
    r = APIRouter(prefix="/agentmail")

    @r.get("/status")
    async def status():
        return service.status()

    @r.put("/key")
    async def save_key(body: KeyIn):
        try:
            return await service.set_key(body.key)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except MailError as err:
            raise _fail(err, {"key": (422, "AgentMail did not accept this key."),
                              "permission": (422, "This key cannot list inboxes. Use an organization API key."),
                              "network": (502, UNREACHABLE)}) from None
        except OSError as e:
            log.warning("agentmail key save failed: %s", type(e).__name__)
            raise HTTPException(500, KEY_NOT_SAVED) from None

    @r.delete("/key")
    async def forget_key():
        try:
            return service.forget_key()
        except OSError as e:
            log.warning("agentmail key forget failed: %s", type(e).__name__)
            raise HTTPException(500, KEY_NOT_SAVED) from None

    @r.get("/inboxes")
    async def inboxes():
        try:
            return {"inboxes": await service.inboxes()}
        except LookupError:
            raise HTTPException(409, NO_KEY) from None
        except MailError as err:
            raise _fail(err) from None

    @r.post("/inboxes")
    async def create_inbox(body: UsernameIn):
        try:
            return await service.create_inbox(body.username)
        except LookupError:
            raise HTTPException(409, NO_KEY) from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except MailError as err:
            raise _fail(err, {"taken": (409, "That address is taken. Try another name."),
                              "invalid": (422, err.detail or "AgentMail refused that name.")}) from None

    @r.put("/inbox")
    async def choose_inbox(body: InboxIn):
        try:
            return await service.choose_inbox(body.inbox_id)
        except LookupError:
            raise HTTPException(409, NO_KEY) from None
        except MailError as err:
            raise _fail(err, {"not_found": (404, "No such inbox for this key.")}) from None

    @r.get("/messages")
    async def messages(page_token: str | None = None):
        if page_token is not None and len(page_token) > TOKEN_MAX:
            raise HTTPException(422, BAD_TOKEN)
        try:
            return await service.messages(page_token or None)
        except LookupError:
            raise HTTPException(409, NOT_SET_UP) from None
        except MailError as err:
            raise _fail(err) from None

    @r.get("/messages/{message_id}")
    async def message(message_id: str):
        try:
            return await service.message(message_id)
        except LookupError:
            raise HTTPException(409, NOT_SET_UP) from None
        except MailError as err:
            raise _fail(err, {"not_found": (404, "No such message.")}) from None

    return r

"""The /telnyx HTTP routes. No answer carries the key or the owner's full phone number, and no route takes a
destination or message text: the only text a route can send is the fixed test sentence, to the number the owner
saved in the desktop app.

Every POST takes a required JSON body (a cross-site page cannot send one without a preflight). A route that may
hold the key in its frames never lets an exception leave it: a failure is answered 500, and only the exception's
class name is logged."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.events.routes import NothingIn
from src.telnyx.client import TelnyxError
from src.telnyx.service import TelnyxService

log = logging.getLogger(__name__)

NO_KEY = "Save a Telnyx key first."
KEY_NOT_SAVED = "The key could not be saved."
UNREACHABLE = "Telnyx could not be reached."
NOT_SET_UP = "Set up Telnyx and your phone number first."
WAIT = "Wait a minute between test texts, or until tomorrow after 30 texts in a day."
GENERAL = {
    "key": (401, "Telnyx no longer accepts the saved key. Save a new one."),
    "refused": (429, "Telnyx is busy. Try again in a moment."),
    "network": (502, UNREACHABLE),
}
UNREADABLE = (502, "Telnyx answered in a way ZenBot could not read.")


class KeyIn(BaseModel):
    key: str


class NumberIn(BaseModel):
    id: str


def _fail(err: TelnyxError, own: dict[str, tuple[int, str]] | None = None) -> HTTPException:
    status, text = (own or {}).get(err.kind) or GENERAL.get(err.kind) or UNREADABLE
    return HTTPException(status, text)


def telnyx_router(service: TelnyxService) -> APIRouter:
    r = APIRouter(prefix="/telnyx")

    @r.get("/status")
    async def status():
        return service.status()

    @r.put("/key")
    async def save_key(body: KeyIn):
        try:
            return await service.set_key(body.key)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except TelnyxError as err:
            raise _fail(err, {"key": (422, "Telnyx did not accept this key.")}) from None
        except Exception as e:  # noqa: BLE001 - the key is in this call's frames, so no exception may leave it
            log.warning("telnyx key save failed: %s", type(e).__name__)
            raise HTTPException(500, KEY_NOT_SAVED) from None

    @r.delete("/key")
    async def forget_key():
        try:
            return service.forget_key()
        except OSError as e:
            log.warning("telnyx key forget failed: %s", type(e).__name__)
            raise HTTPException(500, KEY_NOT_SAVED) from None

    @r.get("/numbers")
    async def numbers():
        try:
            return {"numbers": await service.numbers()}
        except LookupError:
            raise HTTPException(409, NO_KEY) from None
        except TelnyxError as err:
            raise _fail(err) from None

    @r.put("/number")
    async def choose_number(body: NumberIn):
        try:
            return await service.choose_number(body.id)
        except KeyError:
            raise HTTPException(404, "No such number for this key.") from None
        except LookupError:
            raise HTTPException(409, NO_KEY) from None
        except TelnyxError as err:
            raise _fail(err) from None

    @r.post("/test")
    async def test(_: NothingIn):
        try:
            return await service.test()
        except LookupError:
            raise HTTPException(409, NOT_SET_UP) from None
        except PermissionError:
            raise HTTPException(429, WAIT) from None
        except TelnyxError as err:
            raise _fail(err, {
                "invalid": (422, "Telnyx refused the text. Check the number's messaging setup and registration."),
                "network": (502, "The test text may not have been sent."),
                "provider": (502, "The test text may not have been sent."),
            }) from None

    return r

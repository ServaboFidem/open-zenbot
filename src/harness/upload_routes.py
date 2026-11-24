"""The `/uploads` routes: files the user adds to a chat message before sending it. Bytes are stored under
`<vault>/uploads/chat/<id>/`; no ledger entry holds a name, a path or bytes. Every POST takes a required JSON body."""

from __future__ import annotations

import asyncio
import base64
import binascii
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from src.harness import chat_files
from src.memory.ledger import Kind

TOO_LARGE = "This file is too large."
UNREADABLE = "That file could not be read."
NO_FILE = "No such file."
SENT = "That file was already sent."
PICTURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class UploadNewIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    data: str


def decoded_size(b64: str) -> int:
    """Bytes the base64 text decodes to, worked out from its length."""
    n = len(b64.rstrip("="))
    return n * 3 // 4


def upload_router(h) -> APIRouter:
    """`h` is the Harness; it is read only inside the routes."""
    r = APIRouter(prefix="/uploads")

    def log(event: str, **data) -> None:
        h.ledger.append(Kind.upload, "", "", event=event, **data)

    @r.post("")
    async def post_upload(body: UploadNewIn) -> dict:
        suffix = Path(body.name).suffix.lower()
        limit = chat_files.MAX_PICTURE_UPLOAD if suffix in PICTURE_SUFFIXES else chat_files.MAX_DOCUMENT
        if decoded_size(body.data) > limit:
            log("refused", reason="size")
            raise HTTPException(413, TOO_LARGE)
        try:
            raw = base64.b64decode(body.data, validate=True)
        except (binascii.Error, ValueError):
            log("refused", reason="read")
            raise HTTPException(422, UNREADABLE) from None
        try:
            up = await chat_files.save(h.config.vault_root, body.name, raw)
        except chat_files.FileRefused as e:
            log("refused", reason="size" if str(e) in (chat_files.TOO_LARGE, TOO_LARGE) else "type")
            raise HTTPException(422, str(e)) from None
        log("saved", id=up.id, file_kind=up.kind, size=up.size, suffix=Path(up.name).suffix.lower())
        return {"id": up.id, "name": up.name, "kind": up.kind, "size": up.size}

    @r.get("/{upload_id}")
    async def get_upload(upload_id: str) -> Response:
        try:
            up, data = await asyncio.to_thread(chat_files.read, h.config.vault_root, upload_id)
        except chat_files.FileRefused:
            raise HTTPException(404, NO_FILE) from None
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
        if up.kind == "image":
            return Response(data, media_type="image/webp", headers=headers)
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(up.name)}"
        return Response(data, media_type="application/octet-stream", headers=headers)

    @r.delete("/{upload_id}", status_code=204)
    async def delete_upload(upload_id: str) -> Response:
        def drop() -> str:
            # the check and the removal under one hold of the lock: no send is admitted between them
            with h.uploads_lock:
                if h.upload_in_use(upload_id):
                    return "sent"
                return "removed" if chat_files.remove(h.config.vault_root, upload_id) else "missing"

        outcome = await asyncio.to_thread(drop)
        if outcome == "sent":
            raise HTTPException(409, SENT)
        if outcome == "missing":
            raise HTTPException(404, NO_FILE)
        log("removed")
        return Response(status_code=204)

    return r

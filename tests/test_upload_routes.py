"""The `/uploads` routes."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from src.harness import chat_files, upload_routes
from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog

from .conftest import ScriptedProvider


@pytest.fixture
def harness(vault: Path, tmp_path: Path, example_skill_dir: Path) -> Harness:
    cfg = HarnessConfig(vault_root=vault, skills_dir=example_skill_dir.parent, shell_cwd=tmp_path)
    cfg.validate()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    h.add_bot(Bot(name="Research partner", role="Research things", id="bot1"))
    return h


@pytest.fixture
async def client(harness: Harness):
    transport = httpx.ASGITransport(app=create_app(harness))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), "green").save(buf, "PNG")
    return buf.getvalue()


async def test_post_upload_answers_id_kind_size(client):
    r = await client.post("/uploads", json={"name": "pic.png", "data": _b64(_png())})
    assert r.status_code == 200
    j = r.json()
    assert (j["kind"], j["name"]) == ("image", "pic.webp") and chat_files.ID.fullmatch(j["id"]) and j["size"] > 0


async def test_post_without_body_is_422(client):
    assert (await client.post("/uploads")).status_code == 422


async def test_bad_base64_and_refused_type(client):
    r = await client.post("/uploads", json={"name": "a.txt", "data": "!!not base64"})
    assert (r.status_code, r.json()["detail"]) == (422, upload_routes.UNREADABLE)
    r = await client.post("/uploads", json={"name": "a.mp3", "data": _b64(b"ID3")})
    assert (r.status_code, r.json()["detail"]) == (422, chat_files.REFUSED)


async def test_oversized_document_is_413_before_decoding(client, monkeypatch):
    monkeypatch.setattr(chat_files, "MAX_DOCUMENT", 10)
    called = []
    monkeypatch.setattr(upload_routes.base64, "b64decode", lambda *a, **k: called.append(1))
    r = await client.post("/uploads", json={"name": "a.txt", "data": _b64(b"x" * 100)})
    assert (r.status_code, r.json()["detail"]) == (413, "This file is too large.") and not called


async def test_get_serves_webp_nosniff_and_document_as_attachment(client):
    pic = (await client.post("/uploads", json={"name": "pic.png", "data": _b64(_png())})).json()
    r = await client.get(f"/uploads/{pic['id']}")
    assert r.headers["content-type"] == "image/webp" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store" and r.content[8:12] == b"WEBP"
    doc = (await client.post("/uploads", json={"name": "my notes.txt", "data": _b64(b"hi")})).json()
    r = await client.get(f"/uploads/{doc['id']}")
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == "attachment; filename*=UTF-8''my%20notes.txt"
    assert (await client.get("/uploads/" + "0" * 32)).status_code == 404


async def test_delete_removes_an_unsent_upload(client, harness):
    j = (await client.post("/uploads", json={"name": "a.txt", "data": _b64(b"hi")})).json()
    assert (await client.delete(f"/uploads/{j['id']}")).status_code == 204
    assert (await client.get(f"/uploads/{j['id']}")).status_code == 404


async def test_delete_refuses_a_sent_upload(client, harness):
    j = (await client.post("/uploads", json={"name": "a.txt", "data": _b64(b"hi")})).json()
    conv = harness.conversations.start_new("bot1")
    harness.conversations.begin("bot1", "hi", session_id="s1", conv_id=conv.id)
    harness.conversations.get(conv.id).exchanges[-1].files = [{"id": j["id"], "name": "a.txt", "kind": "document"}]
    assert harness.sent_uploads() == {j["id"]}
    r = await client.delete(f"/uploads/{j['id']}")
    assert (r.status_code, r.json()["detail"]) == (409, upload_routes.SENT)


async def test_ledger_never_holds_the_name_or_bytes(client, harness):
    await client.post("/uploads", json={"name": "secret-plan.txt", "data": _b64(b"TOPSECRETBYTES")})
    await client.post("/uploads", json={"name": "secret-song.mp3", "data": _b64(b"ID3")})
    text = (harness.config.vault_root.parent / f"{harness.config.vault_root.name}-ledger" / "ledger.jsonl").read_text()
    assert '"upload"' in text and "secret" not in text and "TOPSECRET" not in text
    assert json.loads(text.splitlines()[-1])["data"]["reason"] == "type"


async def test_delete_of_an_unknown_or_malformed_id_is_404(client):
    assert (await client.delete("/uploads/" + "5" * 32)).status_code == 404
    assert (await client.delete("/uploads/nope")).status_code == 404


async def test_get_refuses_a_stored_file_swapped_for_a_link(client, harness, tmp_path):
    j = (await client.post("/uploads", json={"name": "a.txt", "data": _b64(b"hi")})).json()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP")
    p = harness.config.vault_root / "uploads" / "chat" / j["id"] / "a.txt"
    p.unlink()
    p.symlink_to(secret)
    assert (await client.get(f"/uploads/{j['id']}")).status_code == 404


async def test_files_round_trip_through_the_conversation_store_and_survive_a_restart_sweep(harness):
    import os, time
    from src.harness.conversations import Conversation
    up = await chat_files.save(harness.config.vault_root, "a.txt", b"hi")
    conv = harness.conversations.start_new("bot1")
    harness.conversations.begin("bot1", "hi", session_id="s1", conv_id=conv.id)
    files = [{"id": up.id, "name": "a.txt", "kind": "document"}]
    harness.conversations.get(conv.id).exchanges[-1].files = files
    rec = harness.conversations.get(conv.id).to_record()
    assert Conversation.from_record(json.loads(json.dumps(rec))).exchanges[-1].files == files
    old = Conversation.from_record({k: v for k, v in rec.items() if k != "x"})
    assert old.exchanges[-1].files == files
    legacy = json.loads(json.dumps(rec))
    for e in legacy["exchanges"]:
        e.pop("files")
    assert Conversation.from_record(legacy).exchanges[-1].files == []
    os.utime(up.path.parent, (0, 0))
    assert chat_files.sweep(harness.config.vault_root, harness.sent_uploads(), time.time()) == 0

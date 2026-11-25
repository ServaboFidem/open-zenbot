"""Sending files with a task: POST /bots/{id}/tasks with `uploads`."""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

from src.decider.interface import NoulAnswer
from src.harness import chat_files, design_studio
from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.chat_turn import CANT_SEE_LINE
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog

from .conftest import MockScorer, ScriptedProvider, completion


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


def _png(color: str = "green") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), color).save(buf, "PNG")
    return buf.getvalue()


def _sees(h: Harness, modalities: list[str] | None) -> None:
    """The chat model's facts say it takes (or not) pictures."""
    chat = h.model_roles().chat
    h.provider_store.get(chat.provider).model(chat.model).input_modalities = modalities


async def _upload(client, name: str, raw: bytes) -> dict:
    r = await client.post("/uploads", json={"name": name, "data": _b64(raw)})
    assert r.status_code == 200, r.text
    return r.json()


async def _send(client, h: Harness, task: str, ids: list[str], reply: str = "ok", bot: str = "bot1", **body):
    h.provider.script = [completion(reply)]
    r = await client.post(f"/bots/{bot}/tasks", json={"task": task, "uploads": ids, **body})
    if r.status_code == 202:
        await h.tasks[r.json()["session_id"]]
    return r


def _turn(h: Harness):
    """The turn's own user message: the last one of the first call."""
    return [m for m in h.provider.calls[0] if m.role == "user"][-1]


async def test_a_picture_reaches_a_seeing_model_as_an_image(client, harness: Harness):
    _sees(harness, ["text", "image"])
    pic = await _upload(client, "pic.png", _png())
    r = await _send(client, harness, "what is this?", [pic["id"]])
    assert r.status_code == 202, r.text
    msg = _turn(harness)
    assert len(msg.images) == 1 and msg.images[0].startswith("data:image/webp;base64,")
    assert f"Attached: pic.webp (uploads/chat/{pic['id']}/pic.webp)" in msg.content
    assert CANT_SEE_LINE not in msg.content
    s = harness.sessions[r.json()["session_id"]]
    line = f"Attached: pic.webp (uploads/chat/{pic['id']}/pic.webp)"
    assert line in s.protected["task"]  # pinned with the task, so a mid-task compaction keeps it
    assert line not in s.protected["constraints"]  # and not again as a constraint


async def test_a_pinned_block_without_the_files_gets_them_as_constraints(client, harness: Harness):
    doc = await _upload(client, "notes.txt", b"hello")
    r = await _send(client, harness, "read", [doc["id"]], protected={"task": "read", "constraints": ["be brief"]})
    assert r.status_code == 202, r.text
    s = harness.sessions[r.json()["session_id"]]
    assert s.protected["constraints"] == ["be brief", f"Attached: notes.txt (uploads/chat/{doc['id']}/notes.txt)"]


async def test_a_blind_model_gets_the_line_and_no_image(client, harness: Harness):
    _sees(harness, ["text"])
    pic = await _upload(client, "pic.png", _png())
    r = await _send(client, harness, "what is this?", [pic["id"]])
    assert r.status_code == 202
    msg = _turn(harness)
    assert msg.images == [] and f"Attached: pic.webp (uploads/chat/{pic['id']}/pic.webp){CANT_SEE_LINE}" in msg.content


async def test_a_pdf_reaches_the_model_as_text(client, harness: Harness, monkeypatch):
    async def convert(path: Path) -> str:
        return "converted pdf words"
    monkeypatch.setattr(harness.ingestor.converter, "convert", convert)
    doc = await _upload(client, "paper.pdf", b"%PDF-1.4 not really")
    r = await _send(client, harness, "summarise", [doc["id"]])
    assert r.status_code == 202, r.text
    msg = _turn(harness)
    assert "converted pdf words" in msg.content and '<<<file name="paper.pdf">>>' in msg.content
    conv = harness.conversations.current("bot1")
    assert "converted pdf words" not in str(conv.to_record())  # the content is never kept


async def test_files_only_message_is_titled_from_the_file(client, harness: Harness):
    doc = await _upload(client, "notes.txt", b"hello")
    r = await _send(client, harness, "", [doc["id"]])
    assert r.status_code == 202, r.text
    conv = harness.conversations.current("bot1")
    assert conv.title == "notes.txt" and conv.exchanges[-1].user == ""


async def test_an_empty_task_without_files_is_still_refused(client, harness: Harness):
    r = await client.post("/bots/bot1/tasks", json={"task": "  ", "uploads": []})
    assert (r.status_code, r.json()["detail"]) == (422, "task is empty")


async def test_exchange_user_is_the_typed_text_alone(client, harness: Harness):
    doc = await _upload(client, "notes.txt", b"hello")
    r = await _send(client, harness, "read this", [doc["id"]])
    ex = harness.conversations.current("bot1").exchanges[-1]
    assert ex.user == "read this"
    assert ex.files == [{"id": doc["id"], "name": "notes.txt", "kind": "document",
                         "path": f"uploads/chat/{doc['id']}/notes.txt"}]
    assert harness.sessions[r.json()["session_id"]].task == f"read this\n\nAttached: notes.txt (uploads/chat/{doc['id']}/notes.txt)"


async def test_a_swapped_upload_is_422_and_starts_nothing(client, harness: Harness, vault: Path):
    doc = await _upload(client, "notes.txt", b"hello")
    folder = chat_files.uploads_dir(vault) / doc["id"]
    (folder / "notes.txt").unlink()
    outside = vault.parent / "secret.txt"
    outside.write_text("secret")
    (folder / "notes.txt").symlink_to(outside)
    r = await _send(client, harness, "read", [doc["id"]])
    assert (r.status_code, r.json()["detail"]) == (422, chat_files.REFUSED)
    assert harness.sessions == {} and harness.conversations.current("bot1") is None
    r = await _send(client, harness, "read", ["../../etc"])
    assert r.status_code == 422 and harness.sessions == {}


async def test_five_pictures_are_refused(client, harness: Harness):
    ids = [(await _upload(client, f"p{i}.png", _png(c)))["id"]
           for i, c in enumerate(["red", "green", "blue", "white", "black"])]
    r = await _send(client, harness, "look", ids)
    assert (r.status_code, r.json()["detail"]) == (422, "At most 4 pictures a message.")
    assert harness.sessions == {}
    r = await client.post("/bots/bot1/tasks", json={"task": "x", "uploads": ["a" * 32] * 11})
    assert r.status_code == 422


async def test_a_studio_message_with_files_skips_helper_routing(client, harness: Harness, vault: Path):
    harness.add_bot(Bot(name="Designer", role="Designs pages", id="designer", studio=True))
    harness.add_bot(Bot(name="Copywriter", role="Writes copy", id="copywriter", places=["text"]))
    folder = vault.resolve() / "designs" / "site"
    folder.mkdir(parents=True)
    live = folder / "index.html"
    live.write_text('<html><body><p data-zen-id="lede">Old</p></body></html>', encoding="utf-8")
    copy = design_studio.snapshot(vault, live)
    conv = harness.conversations.start_new("designer")
    harness.conversations.set_design_folder(conv.id, "site")
    harness.conversations.begin("designer", "make it", session_id="s0", conv_id=conv.id)
    harness.conversations.finish(conv.id, "s0", "done", "Here.", attachments=[{"id": "a1", "title": "Site", "path": str(copy)}])
    scorer = MockScorer(lambda req: {k: NoulAnswer(0.95) for k in req.questions})
    harness.rebind(ScriptedProvider(), scorer)
    doc = await _upload(client, "brief.txt", b"words")
    r = await _send(client, harness, "use this", [doc["id"]], bot="designer", conversation_id=conv.id)
    assert r.status_code == 202, r.text
    assert harness.sessions[r.json()["session_id"]].bot_id == "designer"
    assert not [q for q in scorer.requests if q.purpose == "studio_help"]


async def test_history_names_files_without_content(client, harness: Harness, monkeypatch):
    async def convert(path: Path) -> str:
        return "SECRET BODY"
    monkeypatch.setattr(harness.ingestor.converter, "convert", convert)
    doc = await _upload(client, "paper.pdf", b"%PDF-1.4")
    await _send(client, harness, "first", [doc["id"]])
    harness.provider.calls.clear()
    await _send(client, harness, "second", [])
    earlier = [m for m in harness.provider.calls[0] if m.role == "user" and m.content.startswith("first")]
    assert earlier and earlier[0].content == f"first\n\nAttached: paper.pdf (uploads/chat/{doc['id']}/paper.pdf)"
    assert all("SECRET BODY" not in m.content for m in harness.provider.calls[0])


async def test_view_hides_paths(client, harness: Harness):
    doc = await _upload(client, "notes.txt", b"hello")
    await _send(client, harness, "read", [doc["id"]])
    conv = harness.conversations.current("bot1")
    view = (await client.get(f"/conversations/{conv.id}")).json()
    assert view["exchanges"][-1]["files"] == [{"id": doc["id"], "name": "notes.txt", "kind": "document"}]
    assert "uploads/chat" not in str(view)


async def test_bots_list_carries_sees_images(client, harness: Harness):
    _sees(harness, None)
    assert {b["id"]: b["sees_images"] for b in (await client.get("/bots")).json()}["bot1"] is None
    _sees(harness, ["text", "image"])
    assert {b["id"]: b["sees_images"] for b in (await client.get("/bots")).json()}["bot1"] is True
    _sees(harness, ["text"])
    assert harness.sees_images(harness.bots["bot1"]) is False


async def test_sent_upload_survives_the_sweep(client, harness: Harness, vault: Path):
    sent = await _upload(client, "a.txt", b"one")
    unsent = await _upload(client, "b.txt", b"two")
    await _send(client, harness, "read", [sent["id"]])
    assert sent["id"] in harness.sent_uploads()
    n = chat_files.sweep(vault, harness.sent_uploads().__contains__, time.time() + 2 * 86_400)
    assert n == 1
    assert (chat_files.uploads_dir(vault) / sent["id"]).is_dir()
    assert not (chat_files.uploads_dir(vault) / unsent["id"]).exists()
    assert (await client.delete(f"/uploads/{sent['id']}")).status_code == 409


async def test_a_vanished_upload_says_so_and_starts_nothing(client, harness: Harness, vault: Path):
    doc = await _upload(client, "notes.txt", b"hello")
    chat_files.remove(vault, doc["id"])  # as the sweep would
    r = await _send(client, harness, "read", [doc["id"]])
    assert (r.status_code, r.json()["detail"]) == (422, "That file is no longer there. Attach it again.")
    assert harness.sessions == {}


async def test_an_upload_being_sent_cannot_be_deleted(client, harness: Harness, monkeypatch):
    import asyncio
    import threading

    doc = await _upload(client, "notes.txt", b"hello")
    reading, release = threading.Event(), threading.Event()
    real = chat_files.read

    def slow_read(root, upload_id):
        reading.set()
        release.wait(5)
        return real(root, upload_id)

    monkeypatch.setattr(chat_files, "read", slow_read)
    send = asyncio.create_task(_send(client, harness, "read", [doc["id"]]))
    await asyncio.to_thread(reading.wait, 5)
    r = await client.delete(f"/uploads/{doc['id']}")
    assert (r.status_code, r.json()["detail"]) == (409, "That file was already sent.")
    release.set()
    assert (await send).status_code == 202
    assert not harness.sending_uploads


async def test_a_refused_send_lets_its_uploads_be_deleted(client, harness: Harness):
    ids = [(await _upload(client, f"p{i}.png", _png(c)))["id"]
           for i, c in enumerate(["red", "green", "blue", "white", "black"])]
    r = await _send(client, harness, "look", ids)
    assert r.status_code == 422 and not harness.sending_uploads
    assert (await client.delete(f"/uploads/{ids[0]}")).status_code == 204


async def test_the_start_entry_counts_files_and_says_whether_pictures_went(client, harness: Harness):
    _sees(harness, ["text"])
    pic = await _upload(client, "pic.png", _png())
    doc = await _upload(client, "notes.txt", b"hello")
    r = await _send(client, harness, "look", [pic["id"], doc["id"]])
    start = harness.ledger.tail(kind="session", session_id=r.json()["session_id"])[0]
    assert start.data["files"] == 2 and start.data["images_sent"] is False and start.data["images"] == 0


async def test_overlapping_sends_of_one_upload_each_hold_it(client, harness: Harness, monkeypatch):
    """Two sends carry one file; the first ends (refused here, so no exchange names it) while the second is
    still reading: the file stays undeletable until the second ends too."""
    import asyncio
    import threading

    doc = await _upload(client, "notes.txt", b"hello")
    gates = [(threading.Event(), threading.Event()) for _ in range(2)]
    calls = iter(gates)
    real = chat_files.read

    def slow_read(root, upload_id):
        if upload_id == doc["id"]:
            reading, release = next(calls)
            reading.set()
            release.wait(5)
        return real(root, upload_id)

    monkeypatch.setattr(chat_files, "read", slow_read)
    gone = ["f" * 32, "e" * 32]  # no such uploads: each send is refused after reading the shared one
    first = asyncio.create_task(_send(client, harness, "read", [doc["id"], gone[0]]))
    await asyncio.to_thread(gates[0][0].wait, 5)
    second = asyncio.create_task(_send(client, harness, "read", [doc["id"], gone[1]]))
    await asyncio.to_thread(gates[1][0].wait, 5)
    assert harness.sending_uploads[doc["id"]] == 2
    gates[0][1].set()
    assert (await first).status_code == 422
    r = await client.delete(f"/uploads/{doc['id']}")
    assert (r.status_code, r.json()["detail"]) == (409, "That file was already sent.")
    gates[1][1].set()
    assert (await second).status_code == 422
    assert not harness.sending_uploads
    assert (await client.delete(f"/uploads/{doc['id']}")).status_code == 204

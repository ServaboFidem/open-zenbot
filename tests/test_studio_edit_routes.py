"""The routes for a user's direct changes to a design: save, restore, the slots answer and the ZIP export.
Each new version is recorded in the conversation as an `edit` exchange."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from src.harness import design_studio, studio_edits
from src.harness.api import Harness, create_app
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog

from .conftest import ScriptedProvider

PNG = (bytes.fromhex("89504e470d0a1a0a") + b"\0\0\0\rIHDR" + (800).to_bytes(4, "big") + (600).to_bytes(4, "big")
       + b"\x08\x06\0\0\0" + b"\0" * 8)
PNG2 = PNG[:-1] + b"\x01"
MANIFEST = json.dumps({"tweaks": [
    {"var": "--accent", "label": "Accent", "type": "color"},
    {"var": "--radius", "label": "Corner radius", "type": "range", "min": 0, "max": 24, "step": 1, "unit": "px"},
    {"var": "--gap", "label": "Gap", "type": "range", "min": 0, "max": 48, "step": 1, "unit": "px"}]})
PAGE = ('<!doctype html><html><head>\n'
        '<style data-zen-tweaks>\n:root {\n  --accent: #2f6feb;\n  --radius: 8px;\n  --gap: 16px;\n}\n</style>\n'
        f'<script type="application/json" data-zen-tweaks>{MANIFEST}</script>\n'
        '</head><body>\n'
        '<h1 data-zen-id="title">Hello</h1>\n'
        '<p data-zen-id="lede">First line<br>second line</p>\n'
        '<img data-zen-id="photo" src="images/a.png" alt="A photo">\n'
        '</body></html>\n')
POSTS = ("changes", "restore")


@pytest.fixture
def harness(vault: Path, tmp_path: Path, example_skill_dir: Path) -> Harness:
    cfg = HarnessConfig(vault_root=vault, skills_dir=example_skill_dir.parent, shell_cwd=tmp_path)
    cfg.validate()
    h = Harness(config=cfg, provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    h.add_bot(Bot(name="Research partner", role="Research things", id="bot1"))
    h.add_bot(Bot(name="Designer", role="Designs", id="des", studio=True))
    return h


@pytest.fixture
async def client(harness: Harness):
    transport = httpx.ASGITransport(app=create_app(harness))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


def _designed(h: Harness, owner: str = "des", page: str = PAGE, title: str = "index.html") -> str:
    """A conversation of `owner` whose Designer attached `designs/site/index.html` once (version 1), as `title`."""
    folder = h.config.vault_root.resolve() / "designs" / "site"
    (folder / "images").mkdir(parents=True, exist_ok=True)
    (folder / "images" / "a.png").write_bytes(PNG)
    live = folder / "index.html"
    live.write_bytes(page.encode("utf-8"))
    copy = design_studio.snapshot(h.config.vault_root, live)
    conv = h.conversations.start_new(owner)
    h.conversations.begin(owner, "make a site", session_id="s1", conv_id=conv.id)
    h.conversations.finish(conv.id, "s1", "done", "Here it is.",
                           attachments=[{"id": "a1", "title": title, "path": str(copy)}])
    return conv.id


def _live(h: Harness) -> Path:
    return h.config.vault_root.resolve() / "designs" / "site" / "index.html"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


async def _designs(client) -> list[dict]:
    return (await client.get("/designs")).json()["designs"]


def _edits(h: Harness) -> list[dict]:
    return [e.data for e in h.ledger.iter() if e.kind == "design_edit"]


async def test_a_save_attaches_the_new_version_in_an_edit_exchange(client, harness: Harness):
    cid = _designed(harness)
    r = await client.post(f"/conversations/{cid}/design/changes", json={"version": 1, "texts": {"title": "Ship faster"}})
    assert r.status_code == 200
    assert r.json()["version"] == 2
    last = r.json()["conversation"]["exchanges"][-1]
    assert (last["outcome"], last["user"], last["reply"]) == ("edit", "", "You changed 1 text.")
    assert [(a["name"], a["version"], a["design"]) for a in last["attachments"]] == [("index.html", 2, "site")]
    assert harness.conversations.get(cid).exchanges[-1].session_id.startswith("edit-")
    assert ">Ship faster<" in _live(harness).read_text(encoding="utf-8")
    [d] = await _designs(client)
    assert (d["conversation_id"], d["version"], d["versions"]) == (cid, 2, 2)
    slots = (await client.get(f"/conversations/{cid}/design/slots")).json()
    assert slots["version"] == 2 and {"id": "title", "text": "Ship faster"} in slots["texts"]


async def test_a_save_that_changes_nothing_makes_no_version(client, harness: Harness):
    cid = _designed(harness)
    r = await client.post(f"/conversations/{cid}/design/changes", json={"version": 1, "texts": {"title": "Hello"}})
    assert r.status_code == 200 and r.json()["version"] is None
    assert len(r.json()["conversation"]["exchanges"]) == 1
    assert _edits(harness) == []


async def test_the_reply_counts_each_kind(client, harness: Harness):
    cid = _designed(harness)
    body = {"version": 1, "texts": {"title": "Ship faster", "lede": "One\ntwo"},
            "images": {"photo": {"name": "team.png", "data": _b64(PNG2), "alt": "The team"}},
            "tweaks": {"--accent": "#c2410c", "--radius": 12, "--gap": 20}}
    r = await client.post(f"/conversations/{cid}/design/changes", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["conversation"]["exchanges"][-1]["reply"] == "You changed 2 texts, 1 picture and 3 tweaks."
    assert r.json()["version"] == 2
    r = await client.post(f"/conversations/{cid}/design/restore", json={"version": 1, "newest": 2})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 3
    last = r.json()["conversation"]["exchanges"][-1]
    assert (last["outcome"], last["user"], last["reply"]) == ("edit", "", "You restored Version 1 as Version 3.")
    assert [a["version"] for a in last["attachments"]] == [3]
    assert _edits(harness)[-1] == {"event": "restore", "from": 1, "to": 3}


async def test_one_kind_of_change_alone_is_named_alone(client, harness: Harness):
    cid = _designed(harness)
    r = await client.post(f"/conversations/{cid}/design/changes", json={"version": 1, "tweaks": {"--gap": 4}})
    assert r.json()["conversation"]["exchanges"][-1]["reply"] == "You changed 1 tweak."
    r = await client.post(f"/conversations/{cid}/design/changes",
                          json={"version": 2, "images": {"photo": {"name": "b.png", "data": _b64(PNG2), "alt": None}}})
    assert r.json()["conversation"]["exchanges"][-1]["reply"] == "You changed 1 picture."


async def test_a_running_task_refuses_every_write(client, harness: Harness):
    cid = _designed(harness)
    harness.conversations.begin("des", "running", session_id="s-pending", conv_id=cid)
    before = _live(harness).read_bytes()
    r = await client.post(f"/conversations/{cid}/design/changes", json={"version": 1, "texts": {"title": "X"}})
    assert r.status_code == 409 and r.json() == {"detail": "Wait for the task to finish."}
    r = await client.post(f"/conversations/{cid}/design/restore", json={"version": 1, "newest": 1})
    assert r.status_code == 409 and r.json() == {"detail": "Wait for the task to finish."}
    assert (await client.get(f"/conversations/{cid}/design/slots")).status_code == 409
    assert (await client.get(f"/conversations/{cid}/design/export.zip")).status_code == 409
    assert _live(harness).read_bytes() == before
    assert len(harness.conversations.get(cid).exchanges) == 2


async def test_only_a_studio_bots_own_conversation_with_a_design(client, harness: Harness):
    other = _designed(harness, owner="bot1")  # a design attached in a conversation of a bot that is not a studio bot
    empty = harness.conversations.start_new("des").id
    for cid in (other, empty, "nope"):
        for path in ("slots", "export.zip"):
            assert (await client.get(f"/conversations/{cid}/design/{path}")).status_code == 404
        r = await client.post(f"/conversations/{cid}/design/changes", json={"version": 1, "texts": {"title": "X"}})
        assert r.status_code == 404
        r = await client.post(f"/conversations/{cid}/design/restore", json={"version": 1, "newest": 1})
        assert r.status_code == 404


async def test_a_drifted_file_is_caught_up_first(client, harness: Harness):
    cid = _designed(harness)
    live = _live(harness)
    live.write_text(PAGE.replace(">Hello<", ">Hi there<"), encoding="utf-8")  # the Designer was stopped before attaching
    r = await client.get(f"/conversations/{cid}/design/slots")
    assert r.status_code == 200
    assert r.json()["version"] == 2 and {"id": "title", "text": "Hi there"} in r.json()["texts"]
    last = harness.conversations.get(cid).exchanges[-1]
    assert (last.outcome, last.user, last.reply) == ("edit", "", "Changes the Designer had not shown are now Version 2.")
    assert _edits(harness) == [{"event": "catch_up", "version": 2}]
    # a save on the version the user saw before the catch-up is refused, and the caught-up version stays
    live.write_text(PAGE.replace(">Hello<", ">Again<"), encoding="utf-8")
    r = await client.post(f"/conversations/{cid}/design/changes", json={"version": 2, "texts": {"title": "X"}})
    assert r.status_code == 409 and r.json() == {"detail": "The design has changed. Reload and try again."}
    assert harness.conversations.get(cid).exchanges[-1].reply == "Changes the Designer had not shown are now Version 3."
    assert _edits(harness)[-1] == {"event": "refused", "action": "changes", "reason": 409}


async def test_a_version_the_user_makes_keeps_the_designs_title(client, harness: Harness):
    cid = _designed(harness, title="Landing page")
    _live(harness).write_text(PAGE.replace(">Hello<", ">Hi there<"), encoding="utf-8")
    assert (await client.get(f"/conversations/{cid}/design/slots")).json()["version"] == 2  # a catch-up
    r = await client.post(f"/conversations/{cid}/design/changes", json={"version": 2, "texts": {"title": "Ship"}})
    assert r.json()["version"] == 3
    r = await client.post(f"/conversations/{cid}/design/restore", json={"version": 1, "newest": 3})
    assert r.json()["version"] == 4
    titles = [(a["version"], a["title"]) for ex in r.json()["conversation"]["exchanges"] for a in ex["attachments"]]
    assert titles == [(1, "Landing page"), (2, "Landing page"), (3, "Landing page"), (4, "Landing page")]
    assert [d["title"] for d in await _designs(client)] == ["Landing page"]


async def test_a_file_that_cannot_be_caught_up_is_refused(client, harness: Harness, monkeypatch):
    cid = _designed(harness)
    _live(harness).write_text(PAGE.replace(">Hello<", ">Hi<"), encoding="utf-8")
    monkeypatch.setattr(design_studio, "snapshot", lambda *a, **k: None)
    r = await client.get(f"/conversations/{cid}/design/slots")
    assert r.status_code == 409 and r.json() == {"detail": "Ask the Designer to show the design again."}
    assert len(harness.conversations.get(cid).exchanges) == 1


@pytest.mark.parametrize("path", POSTS)
async def test_every_post_needs_a_json_body(client, harness: Harness, path: str):
    cid = _designed(harness)
    url = f"/conversations/{cid}/design/{path}"
    assert (await client.post(url)).status_code == 422
    assert (await client.post(url, data={"version": "1", "newest": "1"})).status_code == 422
    assert (await client.post(url, content=b'{"version": 1, "newest": 1}',
                              headers={"content-type": "text/plain"})).status_code == 422
    assert _live(harness).read_bytes() == PAGE.encode("utf-8")


async def test_bad_base64_or_a_huge_body_is_422(client, harness: Harness):
    cid = _designed(harness)
    url = f"/conversations/{cid}/design/changes"
    bad = {"version": 1, "images": {"photo": {"name": "x.png", "data": "not base64!", "alt": None}}}
    r = await client.post(url, json=bad)
    assert r.status_code == 422 and r.json() == {"detail": "That picture could not be read."}
    huge = {"version": 1, "images": {"photo": {"name": "x.png", "data": "A" * 2_000_004, "alt": None}}}
    r = await client.post(url, json=huge)
    assert r.status_code == 422 and r.json() == {"detail": "A picture can be at most 1,500,000 bytes."}
    many = {"version": 1, "images": {f"p{i}": {"name": "x.png", "data": _b64(PNG), "alt": None} for i in range(11)}}
    r = await client.post(url, json=many)
    assert r.status_code == 422 and r.json() == {"detail": "At most 10 pictures can be saved at once."}
    r = await client.post(url, json={"version": True, "texts": {}})
    assert r.status_code == 422
    r = await client.post(url, json={"version": 1, "tweaks": {"--gap": True}})
    assert r.status_code == 422
    assert _live(harness).read_bytes() == PAGE.encode("utf-8")
    assert sorted(p.name for p in (_live(harness).parent / "images").iterdir()) == ["a.png"]


async def test_a_refusal_answers_its_sentence(client, harness: Harness, monkeypatch):
    cid = _designed(harness)
    url = f"/conversations/{cid}/design/changes"
    r = await client.post(url, json={"version": 1, "texts": {"nope": "X"}})
    assert r.status_code == 422 and r.json() == {"detail": studio_edits.NOT_TEXT}
    monkeypatch.setattr(studio_edits, "_too_large", lambda *a, **k: True)
    r = await client.post(url, json={"version": 1, "texts": {"title": "X"}})
    assert r.status_code == 422 and r.json() == {"detail": "The design would be too large."}
    r = await client.post(f"/conversations/{cid}/design/restore", json={"version": 1, "newest": 1})
    assert r.status_code == 422 and r.json() == {"detail": studio_edits.NOT_EARLIER}
    assert [e["reason"] for e in _edits(harness)] == [422, 422, 422]
    assert [e["action"] for e in _edits(harness)] == ["changes", "changes", "restore"]


async def test_the_zip_holds_the_page_and_its_pictures_only(client, harness: Harness):
    cid = _designed(harness)
    folder = _live(harness).parent
    (folder / "images" / "sub").mkdir()
    (folder / "images" / "sub" / "x.png").write_bytes(PNG)
    (folder / "notes.txt").write_text("not part of the page", encoding="utf-8")
    r = await client.get(f"/conversations/{cid}/design/export.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert set(z.namelist()) == {"index.html", "images/a.png"}
        assert z.read("index.html") == PAGE.encode("utf-8") and z.read("images/a.png") == PNG
    assert _edits(harness) == [{"event": "export", "format": "zip", "version": 1}]


async def test_a_design_over_the_export_cap_answers_409_with_its_sentence(client, harness: Harness, monkeypatch):
    cid = _designed(harness)
    monkeypatch.setattr(studio_edits, "EXPORT_MAX_BYTES", 10)
    r = await client.get(f"/conversations/{cid}/design/export.zip")
    assert (r.status_code, r.json()["detail"]) == (409, "This design is too large to export as a ZIP.")


async def test_the_zip_of_a_drifted_file_is_caught_up_first(client, harness: Harness):
    cid = _designed(harness)
    _live(harness).write_text(PAGE.replace(">Hello<", ">Hi<"), encoding="utf-8")
    r = await client.get(f"/conversations/{cid}/design/export.zip")
    assert r.status_code == 200
    assert harness.conversations.get(cid).exchanges[-1].reply == "Changes the Designer had not shown are now Version 2."
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert b">Hi<" in z.read("index.html")


async def test_the_ledger_carries_no_text(client, harness: Harness):
    cid = _designed(harness)
    r = await client.post(f"/conversations/{cid}/design/changes", json={"version": 1, "texts": {"title": "Zebra quokka words"}})
    assert r.status_code == 200
    assert _edits(harness) == [{"event": "save", "version": 2, "texts": 1, "pictures": 0, "tweaks": 0, "ids": ["title"]}]
    assert "Zebra quokka" not in harness.ledger.file.read_text(encoding="utf-8")


async def test_no_answer_holds_a_disk_path(client, harness: Harness, tmp_path: Path):
    cid = _designed(harness)
    base = f"/conversations/{cid}/design"
    _live(harness).write_text(PAGE.replace(">Hello<", ">Hi<"), encoding="utf-8")
    answers = [await client.get(f"{base}/slots"),
               await client.post(f"{base}/changes", json={"version": 2, "texts": {"title": "Ship"},
                                                          "images": {"photo": {"name": "t.png", "data": _b64(PNG2),
                                                                               "alt": "x"}}}),
               await client.post(f"{base}/restore", json={"version": 1, "newest": 3}),
               await client.post(f"{base}/restore", json={"version": 9, "newest": 4}),
               await client.post(f"{base}/changes", json={"version": 1, "texts": {"title": "X"}})]
    assert [a.status_code for a in answers] == [200, 200, 200, 422, 409]
    for spelled in {str(tmp_path), tmp_path.as_posix(), json.dumps(str(tmp_path))[1:-1], str(tmp_path.resolve()),
                    tmp_path.resolve().as_posix(), json.dumps(str(tmp_path.resolve()))[1:-1]}:
        for a in answers:
            assert spelled not in a.text

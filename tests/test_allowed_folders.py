"""Settings > Allowed Folders: folders the files MCP server may read and write, saved in
vault-settings/allowed_folders.json and handed to mcp.json as ${ZEN_ALLOWED_FOLDERS}, one arg per
folder. Broad folders (a drive root, the home folder or above it, a system folder) are refused, and a
change reconnects the servers that take the list."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from src.harness import allowed_folders as af
from src.harness.allowed_folders import (
    FILE,
    FULL,
    READ_ONLY,
    AllowedFolders,
    Folder,
    FolderConflict,
    FolderRefused,
    check,
    public,
    refusal,
)
from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.harness.mcp_client import load_config
from src.memory.ledger import Ledger
from src.skills.catalog import Catalog
from src.vault_settings import settings_dir, user_bots_dir, user_skills_dir

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


def test_a_list_variable_that_is_a_whole_arg_becomes_one_arg_per_item(tmp_path: Path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {
        "files": {"command": "srv", "args": ["-y", "${ZEN_VAULT_ROOT}", "${ZEN_ALLOWED_FOLDERS}", "--x=${ZEN_ALLOWED_FOLDERS}"]},
        "other": {"command": "srv", "args": ["${ZEN_VAULT_ROOT}"]},
    }}), encoding="utf-8")
    servers = {s.name: s for s in load_config(f, vars={"ZEN_VAULT_ROOT": "/v", "ZEN_ALLOWED_FOLDERS": ["/a", "/b c"]})}
    # a list inside a longer arg cannot expand, so that arg is dropped like an unset variable
    assert servers["files"].args == ["-y", "/v", "/a", "/b c"]
    assert servers["files"].arg_vars == ["ZEN_VAULT_ROOT", "ZEN_ALLOWED_FOLDERS"]
    assert servers["other"].arg_vars == ["ZEN_VAULT_ROOT"]
    empty = load_config(f, vars={"ZEN_VAULT_ROOT": "/v", "ZEN_ALLOWED_FOLDERS": []})[0]
    assert empty.args == ["-y", "/v"]


def test_the_harness_settings_ledger_and_music_folders_are_refused(tmp_path: Path):
    zen = tmp_path / ".zen-agents"
    vault = zen / "vault"
    private = af.private_folders(vault)
    assert private == [zen / "vault-settings", zen / "vault-ledger", zen / "vault-music", zen / "vault-bots", zen / "vault-skills"]
    for d in (vault / "notes", *private, zen / "vault-settings" / "sub"):
        d.mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    for p in (zen / "vault-settings", zen / "vault-settings" / "sub", zen / "vault-ledger", zen):  # in, at, or above
        with pytest.raises(FolderRefused, match="harness's own"):
            check(str(p), home, [], private)
    assert check(str(vault / "notes"), home, [], private) == (vault / "notes").resolve()


def test_broad_folders_are_refused(tmp_path: Path):
    home = tmp_path / "home" / "me"
    system = tmp_path / "sys"
    for d in (home / "projects", system / "lib"):
        d.mkdir(parents=True)
    anchor = Path(tmp_path.anchor)
    assert "whole drive" in (refusal(anchor, home, [system]) or "")
    assert "home folder" in (refusal(home.resolve(), home, [system]) or "")
    assert "home folder" in (refusal((tmp_path / "home").resolve(), home, [system]) or "")  # above home
    assert "system folder" in (refusal(system.resolve(), home, [system]) or "")
    assert "system folder" in (refusal((system / "lib").resolve(), home, [system]) or "")  # inside one
    assert refusal((home / "projects").resolve(), home, [system]) is None


def test_check_wants_an_existing_absolute_folder(tmp_path: Path):
    home, sysd = tmp_path / "home", tmp_path / "sys"
    (home / "notes").mkdir(parents=True)
    (home / "a.txt").write_text("x", encoding="utf-8")
    ok = check(f'  "{home / "notes"}"  ', home, [sysd])
    assert ok == (home / "notes").resolve()
    for raw, why in [("", "enter a folder"), ("relative/dir", "not a full path"),
                     (str(home / "gone"), "does not exist"), (str(home / "a.txt"), "not a folder"),
                     (str(home), "home folder")]:
        with pytest.raises(FolderRefused, match=why):
            check(raw, home, [sysd])


def test_store_round_trips_with_access_reads_the_old_format_and_drops_missing_folders(tmp_path: Path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    s = AllowedFolders().place(a, FULL).place(b, READ_ONLY)
    assert s.folders == [Folder(str(a), FULL), Folder(str(b), READ_ONLY)]
    s.save(tmp_path)
    assert json.loads((settings_dir(tmp_path) / FILE).read_text(encoding="utf-8")) == {
        "folders": [{"path": str(a), "access": "full"}, {"path": str(b), "access": "read_only"}]}
    assert AllowedFolders.load(tmp_path).folders == s.folders
    assert s.server_args() == {"ZEN_ALLOWED_FOLDERS": [str(a)], "ZEN_READ_ONLY_FOLDER_ARGS": ["--read-only-dir", str(b)]}
    b.rmdir()
    assert AllowedFolders.load(tmp_path).server_args()["ZEN_READ_ONLY_FOLDER_ARGS"] == []  # gone: the server would not start
    assert public(AllowedFolders.load(tmp_path), tmp_path / "vault", env={"ZEN_FS_ROOT": "/fs"}) == {
        "fixed": [{"path": str(tmp_path / "vault"), "source": "vault", "access": "full"},
                  {"path": "/fs", "source": ".env", "access": "full"}],
        "folders": [{"path": str(a), "exists": True, "access": "full"}, {"path": str(b), "exists": False, "access": "read_only"}]}
    assert AllowedFolders.load(tmp_path).without(str(b)).folders == [Folder(str(a), FULL)]
    # the format before access modes: bare paths, which were read-write
    (settings_dir(tmp_path) / FILE).write_text(json.dumps({"folders": [str(a), "relative", str(a)]}), encoding="utf-8")
    assert AllowedFolders.load(tmp_path).folders == [Folder(str(a), FULL)]
    (settings_dir(tmp_path) / FILE).write_text("{broken", encoding="utf-8")
    assert AllowedFolders.load(tmp_path).folders == []


def test_nesting_rules_keep_access_meaning_what_it_says(tmp_path: Path):
    vault, proj, ref, deep, other = (tmp_path / "vault", tmp_path / "proj", tmp_path / "proj" / "ref",
                                     tmp_path / "proj" / "ref" / "deep", tmp_path / "other")
    for d in (vault / "docs", deep, other / "sub"):
        d.mkdir(parents=True)
    fixed = [vault]
    s = AllowedFolders().place(proj, FULL, fixed)
    s = s.place(ref, READ_ONLY, fixed)  # a protected corner of a writable folder: kept
    assert [(Path(f.path).name, f.access) for f in s.folders] == [("proj", FULL), ("ref", READ_ONLY)]
    for p, a, why in [(proj, READ_ONLY, "already allowed"),
                      (proj / "x", FULL, "already inside"),                       # Full in Full adds nothing
                      (deep, READ_ONLY, "inside .*Read-Only"),                     # Read-Only in Read-Only adds nothing
                      (deep, FULL, "make .* Full Access first"),                   # Full in Read-Only would not write
                      (vault / "docs", FULL, "already inside")]:                   # the vault has Full Access
        (tmp_path / "proj" / "x").mkdir(exist_ok=True)
        with pytest.raises(FolderConflict, match=why):
            s.place(p, a, fixed)
    assert s.place(vault / "docs", READ_ONLY, fixed).get(str(vault / "docs")).access == READ_ONLY
    # a parent takes the place of children with its access; a Read-Only child stays inside a Full Access parent
    s2 = AllowedFolders().place(other / "sub", FULL).place(other, FULL)
    assert [f.path for f in s2.folders] == [str(other)]
    with pytest.raises(FolderConflict, match="holds .* Full Access"):  # Read-Only over a writable folder
        AllowedFolders().place(other / "sub", FULL).place(other, READ_ONLY)

    # toggling follows the same rules, and keeps the row where it was
    flipped = s.with_access(str(proj), READ_ONLY, fixed)  # its Read-Only child is now covered: it goes
    assert flipped.folders == [Folder(str(proj), READ_ONLY)]
    with pytest.raises(FolderConflict, match="already inside"):
        s.with_access(str(ref), FULL, fixed)  # Full inside Full: remove it instead
    assert s.place(other, FULL).with_access(str(proj), FULL).folders == s.place(other, FULL).folders
    with pytest.raises(FolderRefused, match="not an allowed folder"):
        s.with_access(str(other), FULL)


async def test_adding_and_removing_a_folder_reconnects_the_files_server(vault: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(af, "_system_folders", lambda: [])
    monkeypatch.setattr(af.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    cfg = HarnessConfig(vault_root=vault, skills_dir=tmp_path / "skills", mcp_config=tmp_path / "mcp.json")
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {
        "echo": {"command": sys.executable, "args": [str(FIXTURE), "${ZEN_READ_ONLY_FOLDER_ARGS}", "--", "${ZEN_ALLOWED_FOLDERS}"]},
    }}), encoding="utf-8")
    cfg.validate()
    h = Harness(config=cfg, provider=None, ledger=Ledger(vault), catalog=Catalog())
    app = create_app(h)
    w = str(work.resolve())
    args = lambda: h.mcps._servers["echo"].config.args  # noqa: E731
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        async with app.router.lifespan_context(app):
            got = (await c.get("/settings/folders")).json()
            assert got["folders"] == [] and got["fixed"][0] == {"path": str(vault), "source": "vault", "access": "full"}
            assert args() == [str(FIXTURE), "--"]

            r = await c.post("/settings/folders", json={"path": str(work)})  # Full Access unless asked
            assert r.status_code == 200, r.text
            assert r.json()["folders"] == [{"path": w, "exists": True, "access": "full"}]
            assert args() == [str(FIXTURE), "--", w]
            assert h.mcps.status()["echo"]["state"] == "connected"

            r = await c.patch("/settings/folders", json={"path": w, "access": "read_only"})
            assert r.status_code == 200 and r.json()["folders"][0]["access"] == "read_only"
            assert args() == [str(FIXTURE), "--read-only-dir", w, "--"]  # the server refuses writes there
            assert (await c.patch("/settings/folders", json={"path": w, "access": "sideways"})).status_code == 422
            assert (await c.patch("/settings/folders", json={"path": str(tmp_path / "nope"), "access": "full"})).status_code == 404

            again = await c.post("/settings/folders", json={"path": str(work / "."), "access": "read_only"})
            assert again.status_code == 409 and "already allowed" in again.json()["detail"]
            broad = await c.post("/settings/folders", json={"path": str(tmp_path)})  # holds the home folder
            assert broad.status_code == 422 and "home folder" in broad.json()["detail"]
            (vault / "docs").mkdir()
            inside = await c.post("/settings/folders", json={"path": str(vault / "docs")})  # the vault has Full Access
            assert inside.status_code == 409 and "already inside" in inside.json()["detail"]

            r = await c.delete("/settings/folders", params={"path": w})
            assert r.status_code == 200 and r.json()["folders"] == []
            assert args() == [str(FIXTURE), "--"]
            assert (await c.delete("/settings/folders", params={"path": str(work)})).status_code == 404
    events = [e.data for e in h.ledger.tail(50) if e.data.get("event") == "allowed_folders"]
    assert [sorted(e) for e in events] == [["access", "added", "event"], ["access", "event", "path"], ["event", "removed"]]


def test_the_users_bots_and_skills_folders_are_private(tmp_path: Path):
    vault = tmp_path / "vault"
    private = af.private_folders(vault)
    assert user_bots_dir(vault) in private and user_skills_dir(vault) in private

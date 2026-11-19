"""The shell write check, the bots' workspace in the vault, and attach_file reaching the allowed folders."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.decider.interface import ChoiceAnswer, RefusalAnswer
from src.harness.allowed_folders import FULL, READ_ONLY, AllowedFolders, Folder, Folders, reachable
from src.harness.approvals import ApprovalState, ApprovalStore
from src.harness.bot import Bot
from src.harness.config import HarnessConfig
from src.harness.loop import SessionState, TaskLoop
from src.harness.tools import ToolRunner
from src.harness.write_guard import RULE, RULE_PROTECTED, ShellWriteCheck, named_protected
from src.memory.ledger import Ledger
from src.rooms.room import workspace_file

from .conftest import MockScorer, ScriptedProvider, completion


def answer(choice: str, inside: float, reads: str = "no", p_no: float = 0.95):
    rest, other = (1 - inside) / 2, (1 - p_no) / 2
    return lambda r: {"writes": ChoiceAnswer(choice, {"inside": inside, "outside": rest, "unsure": rest}),
                      "reads": ChoiceAnswer(reads, {"no": p_no, "yes": other, "unsure": other})}


def folders(tmp_path: Path) -> Folders:
    vault = tmp_path / "vault"
    return Folders(full=[tmp_path / "work", vault], read_only=[vault / "ref"], private=[tmp_path / "vault-settings"])


def make_loop(ledger: Ledger, tmp_path: Path, provider, approvals: ApprovalStore, scorer) -> TaskLoop:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    check = ShellWriteCheck(lambda: scorer, lambda: folders(tmp_path), work, ledger, pass_at=0.8)
    return TaskLoop(provider, ledger, approvals, ToolRunner(work, timeout=10), max_steps=10, write_check=check)


async def pending(approvals: ApprovalStore):
    for _ in range(300):
        if p := approvals.list(ApprovalState.pending):
            return p
        await asyncio.sleep(0.01)
    raise AssertionError("no approval request")


# -- the check ----------------------------------------------------------------------------------------------


async def test_inside_runs_without_asking_and_the_decider_sees_the_folders(ledger, tmp_path):
    scorer = MockScorer(answer=answer("inside", 0.95))
    approvals = ApprovalStore()
    provider = ScriptedProvider([completion(calls=[("c1", "shell", {"command": "echo hi > note.txt"})]), completion("Saved.")])
    s = await make_loop(ledger, tmp_path, provider, approvals, scorer).run(Bot(name="b", role="r"), "save a note")

    assert s.state == SessionState.done and not approvals.list()
    assert (tmp_path / "work" / "note.txt").exists()
    state = scorer.requests[0].state
    assert state["command"] == "echo hi > note.txt" and state["working_folder"] == str(tmp_path / "work")
    assert str(tmp_path / "vault") in state["writable_folders"]
    assert state["read_only_folders"] == [str(tmp_path / "vault" / "ref")]
    assert state["protected_folders"] == [str(tmp_path / "vault-settings")]
    assert scorer.requests[0].purpose == "shell-writes"
    score = ledger.tail(kind="decider_score", session_id=s.id)[0].data
    assert score["purpose"] == "shell-writes" and score["answers"]["writes"]["choice"] == "inside"


@pytest.mark.parametrize("choice,inside,says", [
    ("outside", 0.05, "may write outside"),
    ("unsure", 0.30, "may write somewhere it cannot place"),
    ("inside", 0.60, "is not clearly inside"),  # the decider leans inside, but below ZEN_SHELL_WRITE_PASS
])
async def test_anything_but_a_sure_inside_waits_for_the_user(ledger, tmp_path, choice, inside, says):
    approvals = ApprovalStore()
    marker = tmp_path / "outside.txt"
    provider = ScriptedProvider([completion(calls=[("c1", "shell", {"command": f"echo x > {marker}"})]),
                                 completion("Done.")])
    loop = make_loop(ledger, tmp_path, provider, approvals, MockScorer(answer=answer(choice, inside)))
    task = asyncio.create_task(loop.run(Bot(name="b", role="r"), "write a file"))

    [req] = await pending(approvals)
    assert req.rule == RULE and req.tool == "shell" and says in req.detail and str(tmp_path / "work") in req.detail
    assert not marker.exists() and not task.done()
    approvals.decide(req.id, approved=True)
    s = await task
    assert s.state == SessionState.done and marker.exists()  # approved: it runs as asked
    logged = ledger.tail(kind="approval_request", session_id=s.id)[0].data
    assert logged["guard"] == "shell-writes" and logged["verdict"] == choice and logged["p_inside"] == inside


async def test_a_rejection_tells_the_bot_where_to_save_instead(ledger, tmp_path):
    approvals = ApprovalStore()
    marker = tmp_path / "repo" / "page.html"
    provider = ScriptedProvider([completion(calls=[("c1", "shell", {"command": f"copy page.html {marker}"})]),
                                 completion("I left it in the vault.")])
    loop = make_loop(ledger, tmp_path, provider, approvals, MockScorer(answer=answer("outside", 0.02)))
    task = asyncio.create_task(loop.run(Bot(name="b", role="r"), "copy the page"))
    [req] = await pending(approvals)
    approvals.decide(req.id, approved=False)
    s = await task

    told = provider.calls[1][-1].content
    assert "did not allow" in told and str(tmp_path / "work") in told and "attach_file" in told
    assert not ledger.tail(kind="tool_call", session_id=s.id)


async def test_a_scorer_not_relied_on_for_commands_is_never_asked(ledger, tmp_path):
    scorer = MockScorer(answer("inside", 0.99))
    scorer.kind, scorer.checks_commands = "letter-logprobs", False
    approvals = ApprovalStore()
    provider = ScriptedProvider([completion(calls=[("c1", "shell", {"command": "dir"})]), completion("Listed.")])
    task = asyncio.create_task(make_loop(ledger, tmp_path, provider, approvals, scorer).run(Bot(name="b", role="r"), "list"))
    [req] = await pending(approvals)
    assert req.rule == RULE and "letter-logprobs" in req.detail and "not relied on" in req.detail
    assert scorer.requests == []
    approvals.decide(req.id, approved=True)
    assert (await task).state == SessionState.done


async def test_no_decider_means_every_shell_call_waits(ledger, tmp_path):
    approvals = ApprovalStore()
    provider = ScriptedProvider([completion(calls=[("c1", "shell", {"command": "dir"})]), completion("Listed.")])
    task = asyncio.create_task(make_loop(ledger, tmp_path, provider, approvals, None).run(Bot(name="b", role="r"), "list"))
    [req] = await pending(approvals)
    assert req.rule == RULE and "No decider" in req.detail
    approvals.decide(req.id, approved=True)
    assert (await task).state == SessionState.done


@pytest.mark.parametrize("trouble", [RuntimeError("decider down"), {"writes": RefusalAnswer()}])
async def test_a_failed_or_refused_check_waits_rather_than_runs(ledger, tmp_path, trouble):
    approvals = ApprovalStore()
    provider = ScriptedProvider([completion(calls=[("c1", "shell", {"command": "echo hi"})]), completion("ok")])
    scorer = MockScorer(answer=lambda r: trouble)
    task = asyncio.create_task(make_loop(ledger, tmp_path, provider, approvals, scorer).run(Bot(name="b", role="r"), "hi"))
    [req] = await pending(approvals)
    assert "could not check" in req.detail
    approvals.decide(req.id, approved=False)
    await task


async def test_a_skill_rule_asks_once_and_the_check_does_not_run(ledger, tmp_path, example_skill_dir):
    from src.skills.loader import load_skill

    bot = Bot(name="b", role="r")
    bot.attach_skill(load_skill(example_skill_dir).skill)  # its rules include "shell:git push*"
    approvals = ApprovalStore()
    scorer = MockScorer(answer=answer("outside", 0.0))
    provider = ScriptedProvider([completion(calls=[("c1", "shell", {"command": "git push origin main"})]), completion("x")])
    task = asyncio.create_task(make_loop(ledger, tmp_path, provider, approvals, scorer).run(bot, "push"))
    [req] = await pending(approvals)
    assert req.rule == "shell:git push*" and not scorer.requests
    approvals.decide(req.id, approved=False)
    await task


@pytest.mark.parametrize("command", [
    "type {settings}\\music_keys.json",            # the full path, as the digest run did
    "dir ..\\..\\VAULT-SETTINGS",                   # by folder name, any case, relative
    "cat {settings_posix}/providers.json",          # forward slashes
])
async def test_a_command_naming_a_protected_folder_waits_without_asking_the_decider(ledger, tmp_path, command):
    settings = tmp_path / "vault-settings"
    command = command.format(settings=str(settings), settings_posix=settings.as_posix())
    approvals = ApprovalStore()
    scorer = MockScorer(answer=answer("inside", 0.99))
    provider = ScriptedProvider([completion(calls=[("c1", "shell", {"command": command})]), completion("I used my tools.")])
    task = asyncio.create_task(make_loop(ledger, tmp_path, provider, approvals, scorer).run(Bot(name="b", role="r"), "x"))

    [req] = await pending(approvals)
    assert req.rule == RULE_PROTECTED and str(settings) in req.detail and not scorer.requests
    approvals.decide(req.id, approved=False)
    s = await task
    told = provider.calls[1][-1].content
    assert "keeps from bots" in told and "music_*" in told and "another way" in told
    assert not ledger.tail(kind="tool_call", session_id=s.id)
    assert ledger.tail(kind="approval_request", session_id=s.id)[0].data["reads"] == "named"


@pytest.mark.parametrize("reads,p_no,says", [
    ("yes", 0.02, "reaches a folder"),
    ("unsure", 0.30, "may reach a folder"),
    ("no", 0.60, "may reach a folder"),  # leans no, but below ZEN_SHELL_WRITE_PASS
])
async def test_the_decider_flags_protected_reads_the_name_check_misses(ledger, tmp_path, reads, p_no, says):
    approvals = ApprovalStore()
    command = "python -c \"import os; print(open(os.path.join('..', '..', 'vault-' + 'settings', 'x')).read())\""
    scorer = MockScorer(answer=answer("inside", 0.99, reads=reads, p_no=p_no))
    provider = ScriptedProvider([completion(calls=[("c1", "shell", {"command": command})]), completion("ok")])
    task = asyncio.create_task(make_loop(ledger, tmp_path, provider, approvals, scorer).run(Bot(name="b", role="r"), "x"))

    [req] = await pending(approvals)
    assert req.rule == RULE_PROTECTED and says in req.detail
    assert set(scorer.requests[0].questions) == {"reads", "writes"}
    approvals.decide(req.id, approved=False)
    await task


def test_the_name_check_matches_paths_and_folder_names_only():
    protected = [Path("C:/Users/x/.zen-agents/vault-settings"), Path("C:/Users/x/.zen-agents/vault-music")]
    assert named_protected("type C:\\Users\\x\\.zen-agents\\vault-music\\music.db", protected) == str(protected[1])
    assert named_protected("dir ..\\vault-settings", protected) == str(protected[0])
    assert named_protected("dir C:\\Users\\x\\.zen-agents\\vault\\workspace", protected) is None
    assert named_protected("echo settings and music", protected) is None


async def test_other_tools_are_not_checked(ledger, tmp_path):
    scorer = MockScorer(answer=answer("outside", 0.0))
    approvals = ApprovalStore()
    loop = make_loop(ledger, tmp_path, ScriptedProvider([completion(calls=[("c1", "noted", {"text": "x"})]), completion("ok")]),
                     approvals, scorer)
    loop.tools.register("noted", "note", {"type": "object", "properties": {"text": {"type": "string"}}}, lambda text: "ok")
    s = await loop.run(Bot(name="b", role="r"), "note it")
    assert s.state == SessionState.done and not scorer.requests and not approvals.list()


# -- folders and the workspace ---------------------------------------------------------------------------------


def test_folders_readable_and_writable(tmp_path):
    f = folders(tmp_path)
    vault = tmp_path / "vault"
    assert f.writable(vault / "docs" / "a.md") and f.readable(vault / "docs" / "a.md")
    assert f.readable(vault / "ref" / "x.pdf") and not f.writable(vault / "ref" / "x.pdf")
    assert not f.readable(tmp_path / "vault-settings" / "providers.json")
    assert not f.readable(tmp_path / "elsewhere" / "x") and not f.writable(tmp_path / "elsewhere" / "x")


def test_reachable_reads_the_saved_folders(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEN_FS_ROOT", raising=False)
    vault, extra, ref = tmp_path / "vault", tmp_path / "projects", tmp_path / "manuals"
    for d in (vault, extra, ref):
        d.mkdir()
    AllowedFolders(folders=[Folder(str(extra), FULL), Folder(str(ref), READ_ONLY)]).save(vault)
    f = reachable(vault)
    assert f.full == [vault, extra] and f.read_only == [ref]
    assert f.writable(extra / "x") and not f.writable(ref / "x") and f.readable(ref / "x")
    assert not f.readable(tmp_path / "vault-settings" / "allowed_folders.json")


def test_the_workspace_defaults_into_the_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEN_SHELL_CWD", raising=False)
    assert HarnessConfig(vault_root=tmp_path / "v").shell_cwd == (tmp_path / "v" / "workspace").resolve()
    monkeypatch.setenv("ZEN_SHELL_CWD", str(tmp_path / "w"))
    assert HarnessConfig(vault_root=tmp_path / "v").shell_cwd == (tmp_path / "w").resolve()
    assert HarnessConfig(vault_root=tmp_path / "v", shell_cwd=tmp_path / "x").shell_cwd == tmp_path / "x"


def test_attach_reaches_the_vault_and_allowed_folders_but_not_the_harness_folders(tmp_path):
    f = folders(tmp_path)
    work, vault, settings = tmp_path / "work", tmp_path / "vault", tmp_path / "vault-settings"
    for d in (work, vault / "docs", vault / "ref", settings, tmp_path / "repo"):
        d.mkdir(parents=True)
    for p in (work / "a.html", vault / "docs" / "page.html", vault / "ref" / "m.pdf", settings / "k.json", tmp_path / "repo" / "x.html"):
        p.write_text("x")

    assert workspace_file(work, "a.html", f) == (work / "a.html").resolve()
    assert workspace_file(work, str(vault / "docs" / "page.html"), f) == (vault / "docs" / "page.html").resolve()
    assert workspace_file(work, str(vault / "ref" / "m.pdf"), f) == (vault / "ref" / "m.pdf").resolve()  # reading is fine
    assert "harness's own folders" in workspace_file(work, str(settings / "k.json"), f)
    assert "outside the folders you may use" in workspace_file(work, str(tmp_path / "repo" / "x.html"), f)
    assert "outside the folders you may use" in workspace_file(work, "../repo/x.html", f)
    # without folders: the workspace only, as before
    assert isinstance(workspace_file(work, str(vault / "docs" / "page.html")), str)


async def test_harness_attaches_a_vault_file_and_starts_in_its_workspace(tmp_path, monkeypatch):
    from src.harness.api import Harness

    monkeypatch.delenv("ZEN_SHELL_CWD", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    from src.skills.catalog import Catalog

    h = Harness(config=HarnessConfig(vault_root=vault), provider=ScriptedProvider(), ledger=Ledger(vault), catalog=Catalog())
    assert h.tools.cwd == (vault / "workspace").resolve() and h.tools.cwd.is_dir()
    assert h.images.workspace == h.tools.cwd
    assert h.loop.write_check is not None and h.loop.write_check.cwd == h.tools.cwd
    (vault / "docs").mkdir()
    (vault / "docs" / "beekeeping.html").write_text("<h1>bees</h1>")
    assert h.attachable(str(vault / "docs" / "beekeeping.html")) == (vault / "docs" / "beekeeping.html").resolve()
    assert h.rooms.attachable == h.attachable
    assert isinstance(h.attachable(str(tmp_path / "vault-ledger" / "ledger.jsonl")), str)

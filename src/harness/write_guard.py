"""The shell check: a shell command that reaches the harness's own folders, or may write outside the folders
bots may use, waits for the user.

The `files` server already keeps its reads and writes inside the vault and the Allowed Folders, and the magick
gate keeps its own. The `shell` tool runs any command line as the user's account, so before it runs the task
loop checks it in two layers:

1. Protected folders by name. A command whose text names a protected folder (the harness's settings and keys,
   its ledger, its music data), by full path or by folder name, waits for the user at once, without asking the
   decider (rule `shell:reaches-protected-folders`).
2. The decider, one request with two typed questions. `reads`: does it read, list, copy or send anything inside
   a protected folder (`no` / `yes` / `unsure`)? `writes`: does it write only inside the allowed folders
   (`inside` / `outside` / `unsure`)? The command runs at once only on a sure `no` and a sure `inside`, each at
   or above `ZEN_SHELL_WRITE_PASS` (default 0.8). A protected read waits under the protected rule; a write
   under `shell:writes-outside-allowed-folders`. A decider error, or no decider at all (scorer set to None),
   pauses the call too.

Commands are not parsed beyond the name match: the decider reads them, the user decides. It is a judgment, not
a sandbox: an approved or misjudged command still runs as the user's account.

Folders, read fresh on each check: the bots' working folder (the shell's cwd, `<vault>/workspace` by
default) and the vault, `ZEN_FS_ROOT` and Full Access folders are writable; Read-Only folders are readable
only; the harness's own settings, ledger and music folders are protected.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.decider.interface import ChoiceQuestion, DecisionRequest, Scorer, decide_and_log

from .allowed_folders import Folders

RULE = "shell:writes-outside-allowed-folders"  # the approval's rule, so the Approvals page says why it paused
RULE_PROTECTED = "shell:reaches-protected-folders"
INSIDE, OUTSIDE, UNSURE = "inside", "outside", "unsure"
NO, YES = "no", "yes"

QUESTION = ChoiceQuestion(
    instructions=(
        "A bot wants to run this shell command. Judge only where it writes on disk: does it create, change, "
        "move, rename or delete any file or folder, and if so, are all of those inside the writable folders? "
        "Relative paths are inside the working folder. Reading files, listing folders, printing and network "
        "requests that save nothing are not writes. Installing or removing software, changing system or user "
        "settings, the registry, scheduled tasks, or environment variables, or writing to a temp folder "
        "outside the writable folders, counts as writing outside."
    ),
    criteria={
        INSIDE: "It writes nothing, or every file or folder it creates, changes, moves or deletes is inside a "
                "writable folder (and not inside a read-only or protected one).",
        OUTSIDE: "It creates, changes, moves or deletes something outside the writable folders, or inside a "
                 "read-only or protected folder, or changes software or system settings.",
        UNSURE: "Where it writes cannot be told from the command: it runs a script or program whose writes "
                "are unknown, uses a path built from a variable, or its destination is unclear.",
    },
)

READS = ChoiceQuestion(
    instructions=(
        "A bot wants to run this shell command. Judge only whether it reaches the protected folders, which hold "
        "the harness's keys, settings, ledger and private data: does it list, read, print, search, copy, open "
        "or send over the network any file or folder inside a protected folder? Relative paths start in the "
        "working folder; a path counts wherever it resolves, through '..', a glob, an environment variable, "
        "or a path a script builds. Where it writes is a separate question."
    ),
    criteria={
        NO: "Nothing it lists, reads, copies or sends is inside a protected folder.",
        YES: "It lists, reads, copies or sends something inside a protected folder.",
        UNSURE: "Whether it reaches a protected folder cannot be told: it runs a script or program whose file "
                "access is unknown, or builds a path from parts or variables that could resolve there.",
    },
)

ASK = "Approve to run it, reject to tell the bot it may not."
REJECTED = ("The user did not allow this command: it may write outside the folders you may use. Do not retry it "
            "or copy files elsewhere. Save files in your working folder ({cwd}) or the vault, and show the user a "
            "file with attach_file. Continue if you can.")
REJECTED_PROTECTED = ("The user did not allow this command: it reaches a folder the harness keeps from bots (its "
                      "settings and keys, its ledger, its music data). Do not try to reach those files another "
                      "way. Use the tools you were given for that data (the music_* tools for music). Continue "
                      "if you can.")


@dataclass
class Verdict:
    ask: bool                       # pause for the user's approval
    detail: str = ""                # why, for the approval request
    choice: str = ""                # the decider's `writes` label; "" when it did not answer
    p_inside: float | None = None
    rule: str = RULE                # the approval's rule
    rejected: str = ""              # what the bot is told on a rejection; "" for the write message
    reads: str = ""                 # the decider's `reads` label, or "named" when the command names a protected folder


def _shell_name() -> str:
    return "cmd.exe (Windows)" if sys.platform == "win32" else "/bin/sh"


def _norm(text: str) -> str:
    return text.replace("\\", "/").lower()


def named_protected(command: str, protected: list[Path]) -> str | None:
    """The first protected folder the command names, by full path or by folder name (case and slashes ignored)."""
    cmd = _norm(command)
    for p in protected:
        if _norm(str(p)) in cmd or (p.name and _norm(p.name) in cmd):
            return str(p)
    return None


class ShellWriteCheck:
    """`scorer()` and `folders()` are read on every check, so a rebind or a Settings change applies at once."""

    kind = "shell-writes"

    def __init__(self, scorer: Callable[[], Scorer | None], folders: Callable[[], Folders], cwd: Path,
                 ledger: Any, pass_at: float = 0.8) -> None:
        self.scorer = scorer
        self.folders = folders
        self.cwd = Path(cwd)
        self.ledger = ledger
        self.pass_at = pass_at

    def state(self, command: str, f: Folders | None = None) -> dict[str, Any]:
        f = f or self.folders()
        return {
            "command": command,
            "shell": _shell_name(),
            "working_folder": str(self.cwd),
            "writable_folders": [str(p) for p in f.full],
            "read_only_folders": [str(p) for p in f.read_only],
            "protected_folders": [str(p) for p in f.private],
        }

    def _protected(self, detail: str, reads: str, choice: str = "", p_inside: float | None = None) -> Verdict:
        return Verdict(True, f"{detail} It would run in {self.cwd}. {ASK}", choice, p_inside, RULE_PROTECTED,
                       REJECTED_PROTECTED, reads)

    async def check(self, bot_id: str, session_id: str, command: Any) -> Verdict:
        if not isinstance(command, str) or not command.strip():
            return Verdict(False, choice=INSIDE)  # nothing to run; the tool answers a missing command itself
        f = self.folders()
        if named := named_protected(command, f.private):
            return self._protected(f"This command names {named}, a folder the harness keeps from bots (its "
                                   "settings and keys, ledger or music data).", "named")
        scorer = self.scorer()
        if scorer is None:
            return Verdict(True, f"No decider is set up to check shell commands, so every one waits for you. {ASK}")
        if not getattr(scorer, "checks_commands", True):  # a scorer not shown to judge commands well: the user does
            return Verdict(True, f"The decider in use ({scorer.kind}) is not relied on to check shell commands, "
                                 f"so every one waits for you. {ASK}")
        request = DecisionRequest(state=self.state(command, f), questions={"reads": READS, "writes": QUESTION},
                                  purpose="shell-writes")
        try:
            result = await decide_and_log(scorer, request, self.ledger, bot_id, session_id)
            r, a = result.choice("reads"), result.choice("writes")
        except Exception as e:  # noqa: BLE001 - fail closed: the user decides
            return Verdict(True, f"The decider could not check this command ({type(e).__name__}), "
                                 f"so it waits for you. {ASK}")
        p = a.probabilities.get(INSIDE) if a.probabilities else None
        p_no = r.probabilities.get(NO) if r.probabilities else None
        if r.choice != NO or (p_no is not None and p_no < self.pass_at):
            what = {YES: "reaches", UNSURE: "may reach"}.get(r.choice, "may reach")
            odds = f" (no: {p_no:.2f})" if p_no is not None else ""
            return self._protected(f"The decider says this command {what} a folder the harness keeps from bots{odds}.",
                                   r.choice, a.choice, p)
        if a.choice == INSIDE and (p is None or p >= self.pass_at):
            return Verdict(False, choice=a.choice, p_inside=p, reads=r.choice)
        what = {OUTSIDE: "may write outside the folders bots may use",
                UNSURE: "may write somewhere it cannot place"}.get(a.choice, "is not clearly inside the folders bots may use")
        odds = f" (inside: {p:.2f})" if p is not None else ""
        return Verdict(True, f"The decider says this command {what}{odds}. It would run in {self.cwd}. {ASK}",
                       a.choice, p, reads=r.choice)

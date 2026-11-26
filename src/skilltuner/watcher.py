"""Keeps the live skill catalog in step with `<vault>-skills/discovered/`.

Polling, not a file-watching library: no new dependency, and tests are
deterministic. `scan()` must be called on the event loop's thread (as `run()`
and Approve do): every catalog and bot change and `on_change` then happen on
the loop. The folder holds a handful of skills, so the stat calls are cheap.
A lock still makes a scan atomic against any other caller.

Ownership is by path: the watcher owns every catalog entry whose SKILL.md lies
inside the discovered folder and never touches any other. The startup load
skips the folder; `build_harness` runs a first scan after the shipped and the
owner's skills are in, so a discovered skill never takes their slug or name. Ledger entries carry folder names, slugs, reason codes
and exception class names, never skill text.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import threading
from pathlib import Path
from typing import Callable, Iterable

from src.memory.ledger import Kind
from src.skills.loader import SKILL_FILENAME, SkillConfig, load_skill

POLL_S = 3.0
FOLDER = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _norm(p: Path | str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _is_reparse(p: Path) -> bool:
    try:
        return bool(os.lstat(p).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


class SkillWatcher:
    def __init__(self, folder: Path, catalog, bots: Callable[[], Iterable], ledger, config: SkillConfig,
                 on_change: Callable[[], None]) -> None:
        self.folder = Path(folder)
        self.catalog = catalog
        self.bots = bots
        self.ledger = ledger
        self.config = config
        self.on_change = on_change
        self._seen: dict[str, object] = {}  # folder name -> signature last handled
        self._lock = threading.Lock()

    def _log(self, event: str, **data) -> None:
        self.ledger.append(Kind.skilltuner, "", "", event=event, **data)

    def _unload(self, slug: str | None) -> bool:
        if slug is None:
            return False
        gone = self.catalog.remove_skill(slug)
        for bot in self.bots():
            bot.detach_skill(slug)
        return gone

    def _owned(self) -> dict[str, str]:
        """Folder name -> slug for every catalog skill whose file lies in the discovered folder."""
        root = _norm(self.folder)
        out: dict[str, str] = {}
        for s in self.catalog.skills():
            if not s.path:
                continue
            p = _norm(s.path)
            if p.startswith(root + os.sep):
                out[p[len(root) + 1:].split(os.sep)[0]] = s.slug
        return out

    def _signature(self, d: Path) -> tuple[object, bool]:
        """(signature, acceptable): a linked or junction folder or file, a non-regular
        file or a hard-linked one is refused."""
        try:
            st_d = os.lstat(d)
            if stat.S_ISLNK(st_d.st_mode) or _is_reparse(d) or not stat.S_ISDIR(st_d.st_mode):
                return ("link",), False
            f = d / SKILL_FILENAME
            st = os.lstat(f)
        except OSError:
            return ("missing",), False
        if _is_reparse(f) or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            return ("refused", st.st_mtime_ns, st.st_size), False
        return (st.st_mtime_ns, st.st_size), True

    def scan(self) -> dict[str, int]:
        with self._lock:
            return self._scan()

    def _scan(self) -> dict[str, int]:
        out = {"added": 0, "changed": 0, "removed": 0, "refused": 0}
        owned = self._owned()
        try:
            listed = sorted(n for n in os.listdir(self.folder) if not n.startswith(".tmp-"))
        except OSError:
            listed = []
        todo: list[tuple[str, str | None]] = []  # loads, done after every removal
        for name in sorted(set(listed) | set(owned)):
            held = owned.get(name)
            sig, ok = self._signature(self.folder / name) if name in listed else (("missing",), False)
            if sig == ("missing",):
                if held is not None:
                    self._unload(held)
                    out["removed"] += 1
                    self._log("removed", folder=name, slug=held)
                self._seen.pop(name, None)
                continue
            if not FOLDER.match(name):
                if held is not None:
                    self._unload(held)
                    out["removed"] += 1
                    self._log("dropped", folder=name, slug=held, reason="refused")
                continue  # not a skill folder
            if not ok:
                if held is not None:
                    self._unload(held)
                    out["removed"] += 1
                    self._log("dropped", folder=name, slug=held, reason="refused")
                if self._seen.get(name) != sig:
                    out["refused"] += 1
                self._seen[name] = sig
                continue
            if self._seen.get(name) == sig:
                continue
            if held is not None and name not in self._seen:
                self._seen[name] = sig  # adopted: loaded at startup, valid, no reload
                continue
            self._seen[name] = sig
            todo.append((name, held))
        for name, held in todo:
            try:
                self._load(name, held, out)
            except Exception as e:  # noqa: BLE001
                if self._unload(held):
                    out["removed"] += 1
                self._log("dropped", folder=name, slug=held, reason="error", error=type(e).__name__)
        if out["added"] or out["changed"] or out["removed"]:
            self.on_change()
        return out

    def _load(self, name: str, held: str | None, out: dict[str, int]) -> None:
        res = load_skill(self.folder / name / SKILL_FILENAME, self.config)
        reason = None
        if not res.ok:
            reason = "invalid"
        else:
            skill = res.skill
            if held is not None and skill.slug == held:
                added = self.catalog.replace_skill(skill)
            else:
                if held is not None:
                    self._unload(held)  # its slug changed: the earlier copy goes first
                added = self.catalog.add_skill(skill)
            if added.state.value != "added":
                reason = "duplicate"
        if reason is None:
            if held is None:
                out["added"] += 1
                self._log("loaded", folder=name, slug=res.skill.slug)
            else:
                out["changed"] += 1
                self._log("reloaded", folder=name, slug=res.skill.slug)
            return
        if held is not None:
            self._unload(held)
            out["changed"] += 1
        self._log("dropped", folder=name, slug=res.skill.slug if res.skill else held, reason=reason)

    def sweep_temp(self) -> None:
        try:
            names = [n for n in os.listdir(self.folder) if n.startswith(".tmp-")]
        except OSError:
            return
        for n in names:
            p = self.folder / n
            try:
                if p.is_symlink() or not p.is_dir():
                    p.unlink()
                elif not _is_reparse(p):
                    shutil.rmtree(p)
                else:
                    os.rmdir(p)  # a junction: removes the link only
            except OSError as e:
                self._log("error", reason="sweep", error=type(e).__name__)

    async def run(self, stop: asyncio.Event) -> None:
        try:
            self.sweep_temp()
        except Exception as e:  # noqa: BLE001
            self._log("error", error=type(e).__name__)
        while not stop.is_set():
            try:
                self.scan()
            except Exception as e:  # noqa: BLE001
                self._log("error", error=type(e).__name__)
            try:
                await asyncio.wait_for(stop.wait(), POLL_S)
            except asyncio.TimeoutError:
                pass

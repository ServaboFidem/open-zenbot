"""Keeps the live skill catalog in step with `<vault>-skills/discovered/`.

Polling, not a file-watching library: no new dependency, and tests are
deterministic. Only skills this watcher loaded are ever changed or removed.
Ledger entries carry slugs, reason codes and exception class names, never skill text.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Callable, Iterable

from src.memory.ledger import Kind
from src.skills.loader import SKILL_FILENAME, SkillConfig, load_skill

POLL_S = 3.0
FOLDER = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class SkillWatcher:
    def __init__(self, folder: Path, catalog, bots: Callable[[], Iterable], ledger, config: SkillConfig,
                 on_change: Callable[[], None]) -> None:
        self.folder = Path(folder)
        self.catalog = catalog
        self.bots = bots
        self.ledger = ledger
        self.config = config
        self.on_change = on_change
        # folder name -> (signature, slug this watcher loaded from it or None)
        self._seen: dict[str, tuple[object, str | None]] = {}

    def _log(self, event: str, **data) -> None:
        self.ledger.append(Kind.skilltuner, "", "", event=event, **data)

    def _unload(self, slug: str | None) -> bool:
        if slug is None:
            return False
        self.catalog.remove_skill(slug)
        for bot in self.bots():
            bot.detach_skill(slug)
        return True

    def _signature(self, d: Path) -> tuple[object, bool]:
        """(signature, acceptable). A linked folder or file, a non-regular file or a
        hard-linked one is refused."""
        try:
            if stat.S_ISLNK(os.lstat(d).st_mode) or not stat.S_ISDIR(os.lstat(d).st_mode):
                return ("link",), False
            st = os.lstat(d / SKILL_FILENAME)
        except OSError:
            return ("missing",), False
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            return ("refused", st.st_mtime_ns, st.st_size), False
        return (st.st_mtime_ns, st.st_size), True

    def scan(self) -> dict[str, int]:
        out = {"added": 0, "changed": 0, "removed": 0, "refused": 0}
        try:
            names = sorted(n for n in os.listdir(self.folder) if FOLDER.match(n))
        except OSError:
            names = []
        present = set()
        for name in names:
            d = self.folder / name
            sig, ok = self._signature(d)
            if sig == ("missing",):
                continue  # not a skill folder (yet)
            present.add(name)
            prev = self._seen.get(name)
            if prev is not None and prev[0] == sig:
                continue
            held = prev[1] if prev else None
            if not ok:
                if self._unload(held):
                    out["changed"] += 1
                    self._log("dropped", slug=name, reason="refused")
                if prev is None or prev[0] != sig:
                    out["refused"] += 1
                self._seen[name] = (sig, None)
                continue
            self._seen[name] = (sig, self._load(name, d, held, out))
        for name in [n for n in self._seen if n not in present]:
            _, held = self._seen.pop(name)
            if self._unload(held):
                out["removed"] += 1
                self._log("removed", slug=held)
        if out["added"] or out["changed"] or out["removed"]:
            self.on_change()
        return out

    def _load(self, name: str, d: Path, held: str | None, out: dict[str, int]) -> str | None:
        res = load_skill(d / SKILL_FILENAME, self.config)
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
                self._log("loaded", slug=res.skill.slug)
            else:
                out["changed"] += 1
                self._log("reloaded", slug=res.skill.slug)
            return res.skill.slug
        if self._unload(held):
            out["changed"] += 1
        self._log("dropped", slug=name, reason=reason)
        return None

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
        # File IO runs in a worker thread so a slow disk never stalls the event loop.
        try:
            await asyncio.to_thread(self.sweep_temp)
        except Exception as e:  # noqa: BLE001
            self._log("error", error=type(e).__name__)
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.scan)
            except Exception as e:  # noqa: BLE001
                self._log("error", error=type(e).__name__)
            try:
                await asyncio.wait_for(stop.wait(), POLL_S)
            except asyncio.TimeoutError:
                pass


def _is_reparse(p: Path) -> bool:
    try:
        return bool(os.lstat(p).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False

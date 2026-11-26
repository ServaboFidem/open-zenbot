"""The `/skilltuner` routes: the switch and status, the suggestions waiting for the owner, Approve (installs a
suggested skill into `<vault>-skills/discovered/<slug>/`), Reject, and removing an approved skill.

No answer carries a disk path, and a suggestion's list items carry neither the original nor the converted text.
Every POST takes a required JSON body (`NothingIn`), so another site's page cannot send one without a preflight.
The ledger (kind `skilltuner`) gets ids, slugs, skill names and counts only, and a failure its class name only."""
from __future__ import annotations

import asyncio
import logging
import os
import stat
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool

from src.events.routes import NothingIn
from src.memory.ledger import Kind
from src.skills.catalog import skill_entry
from src.skilltuner.settings import TunerSettings
from src.skilltuner.watcher import FOLDER, _is_reparse, _norm
from src.vault_settings import discovered_skills_dir

NEED_DREAMING = "Switch on dreaming first."
NEED_DECISION = "Choose a decision model first."
NEED_BOT = "SkillTuner's bot file is missing."
NO_SUGGESTION = "No such suggestion."
NO_SKILL = "No such skill."
TAKEN = "A skill with that name is already installed."
NOT_INSTALLED = "The skill could not be installed."
NOT_REMOVED = "The skill could not be removed."
NOT_SAVED = "The setting could not be saved."
LINKED = "That skill's folder is a link and was left alone."
LIST_OUT = ("original", "converted")

log = logging.getLogger(__name__)


class TunerSettingsIn(BaseModel):
    enabled: StrictBool


def _kind(p: Path) -> int | None:
    try:
        return stat.S_IFMT(os.lstat(p).st_mode)
    except OSError:
        return None


def _real_dir(p: Path) -> bool:
    return _kind(p) == stat.S_IFDIR and not _is_reparse(p)


def _rm_tree(p: Path) -> None:
    """Remove the real folder `p` and what is inside it. Nothing is followed: a link or junction inside is
    removed itself, never its target."""
    with os.scandir(p) as it:
        entries = [Path(e.path) for e in it]
    for q in entries:
        if _real_dir(q):
            _rm_tree(q)
        elif _kind(q) == stat.S_IFDIR:  # a junction or directory link
            os.rmdir(q)
        else:
            try:
                os.unlink(q)
            except PermissionError:
                os.rmdir(q)  # a directory link on Windows
    os.rmdir(p)


def skilltuner_router(h) -> APIRouter:
    """`h` is the Harness; it is read only inside the routes."""
    r = APIRouter(prefix="/skilltuner")
    lock = asyncio.Lock()  # one Approve, Reject or Remove at a time

    def log_event(event: str, **data: Any) -> None:
        h.ledger.append(Kind.skilltuner, "", "", event=event, **data)

    def why_not() -> str:
        if not h.dreamer.settings.enabled:
            return NEED_DREAMING
        if h._tuner_bot() is None:
            return NEED_BOT
        if h._tuner_model() is None or h.scorer is None:
            return NEED_DECISION
        return ""

    def status() -> dict[str, Any]:
        s = h.tuner.status()
        return {"enabled": s["enabled"], "ready": s["ready"], "why_not": why_not(), "last_pass_at": s["last_pass_at"],
                "error": s["error"], "pending": s["pending"], "spent_today": s["spent_today"], "budget": s["budget"]}

    def folder() -> Path:
        return discovered_skills_dir(h.config.vault_root)

    def installed_in(name: str):
        """The catalog skill whose file lies in discovered/<name>/, if any."""
        root = _norm(folder() / name) + os.sep
        return next((s for s in h.catalog.skills() if s.path and _norm(s.path).startswith(root)), None)

    @r.get("/status")
    async def get_status() -> dict[str, Any]:
        return status()

    @r.put("/settings")
    async def put_settings(body: TunerSettingsIn) -> dict[str, Any]:
        if body.enabled and not h.dreamer.settings.enabled:
            raise HTTPException(409, NEED_DREAMING)
        try:
            h.tuner.set_settings(TunerSettings(body.enabled))
        except OSError:
            raise HTTPException(500, NOT_SAVED) from None
        log_event("settings", enabled=body.enabled)
        return status()

    @r.get("/suggestions")
    async def list_suggestions() -> list[dict[str, Any]]:
        return [{k: v for k, v in asdict(s).items() if k not in LIST_OUT} for s in h.suggestions.list()]

    @r.get("/suggestions/{id}")
    async def get_suggestion(id: str) -> dict[str, Any]:
        s = h.suggestions.get(id)
        if s is None:
            raise HTTPException(404, NO_SUGGESTION)
        return asdict(s)

    @r.post("/suggestions/{id}/approve")
    async def approve(id: str, _: NothingIn) -> dict[str, Any]:
        async with lock:
            s = h.suggestions.get(id)
            if s is None:
                raise HTTPException(404, NO_SUGGESTION)
            if not FOLDER.fullmatch(s.slug):
                raise HTTPException(422, "That suggestion's name is not a valid skill name.")
            root, final = folder(), folder() / s.slug
            if (h.catalog.skill(s.slug) is not None or any(k.name == s.name for k in h.catalog.skills())
                    or _kind(final) is not None):
                raise HTTPException(409, TAKEN)
            files = h.suggestions.files(id)
            if "SKILL.md" not in files:
                raise HTTPException(500, NOT_INSTALLED)
            tmp = root / f".tmp-{id}"
            try:
                root.mkdir(parents=True, exist_ok=True)
                if _kind(tmp) is not None:
                    _rm_tree(tmp) if _real_dir(tmp) else os.unlink(tmp)
                tmp.mkdir()
                for name, text in files.items():
                    with open(tmp / name, "x", encoding="utf-8", newline="") as f:
                        f.write(text)
                os.replace(tmp, final)
            except Exception as e:  # noqa: BLE001
                try:
                    if _real_dir(tmp):
                        _rm_tree(tmp)
                except OSError:
                    pass
                log.warning("skilltuner approve failed: %s", type(e).__name__)
                raise HTTPException(500, NOT_INSTALLED) from None
            h.skill_watcher.scan()  # on the loop's thread, as the watcher requires
            skill = installed_in(s.slug)
            if skill is None:  # the watcher refused it: put nothing behind, the suggestion stays
                try:
                    if _real_dir(final):
                        _rm_tree(final)
                except OSError:
                    pass
                h.skill_watcher.scan()
                log.warning("skilltuner approve: the skill did not load")
                raise HTTPException(500, NOT_INSTALLED)
            # the skill is installed from here on: nothing below may turn this into a failure
            try:
                h.suggestions.remove(id)
                h.tuner_state.remember(s.source_slug, "approved")
            except Exception as e:  # noqa: BLE001
                log.warning("skilltuner approve bookkeeping failed: %s", type(e).__name__)
            log_event("approved", id=id, slug=s.slug, name=s.name, files=len(files))
        fit = None
        offered = [b for b in h.bots.values() if not b.asked_directly and not b.closed and not b.studio]
        if h.fit is not None and offered:
            try:
                fit = (await h.fit.suggest(skill, offered)).to_dict()
            except Exception as e:  # noqa: BLE001  the skill is installed; only the suggestion of a home failed
                log.warning("skilltuner fit failed: %s", type(e).__name__)
        return {"skill": skill_entry(skill).to_dict(), "fit": fit}

    @r.post("/suggestions/{id}/reject")
    async def reject(id: str, _: NothingIn) -> dict[str, bool]:
        async with lock:
            s = h.suggestions.get(id)
            if s is None:
                raise HTTPException(404, NO_SUGGESTION)
            h.suggestions.remove(id)
            h.tuner_state.remember(s.source_slug, "rejected")
            log_event("rejected", id=id, slug=s.slug, name=s.name)
        return {"ok": True}

    @r.post("/skills/{slug}/remove")
    async def remove_skill(slug: str, _: NothingIn) -> dict[str, bool]:
        async with lock:
            name = ""
            if FOLDER.fullmatch(slug) and _kind(folder() / slug) is not None:
                name = slug
            else:  # a skill whose folder is named otherwise than its slug
                held = h.catalog.skill(slug)
                root = _norm(folder()) + os.sep
                if held is not None and held.path and _norm(held.path).startswith(root):
                    name = _norm(held.path)[len(root):].split(os.sep)[0]
            if not FOLDER.fullmatch(name):
                raise HTTPException(404, NO_SKILL)
            target = folder() / name
            kind = _kind(target)
            if kind is None:
                raise HTTPException(404, NO_SKILL)
            if kind == stat.S_IFLNK or _is_reparse(target):
                raise HTTPException(409, LINKED)
            if kind != stat.S_IFDIR:
                raise HTTPException(404, NO_SKILL)  # a plain file is no skill
            try:
                _rm_tree(target)
            except OSError as e:
                log.warning("skilltuner remove failed: %s", type(e).__name__)
                raise HTTPException(500, NOT_REMOVED) from None
            h.skill_watcher.scan()
            log_event("removed", slug=slug)
        return {"ok": True}

    return r

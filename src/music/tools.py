"""Tools for the music-librarian bot: profile, candidates, checks, album facts, genre detail, the final save."""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import inspect
import json
import time
from collections.abc import Callable
from typing import Any

from src.harness.tools import current_call
from src.music.candidates import pipeline_candidates
from src.music.digest import DigestRefused
from src.music.discogs import DiscogsError
from src.skills.catalog import CatalogEntry

GROUP = "music"
TOP_GENRES = 15
RECENT_REACTIONS = 20
NO_PROFILE = "No collection profile yet: the collection has not been scanned. Ask the user to scan it in Music."

USE = ("The user's record collection and the album digest: their taste profile, checked album candidates, "
       "album and genre facts, and saving the digest.")

_STR, _INT = {"type": "string"}, {"type": "integer"}
SPECS: dict[str, tuple[str, dict[str, Any]]] = {
    "music_profile": (
        "The user's taste profile: focus genres, top genres with shares, decades, formats and their last 20 reactions.",
        {"type": "object", "properties": {}}),
    "music_candidates": (
        "Albums the harness found and already checked (they pass the gate), with facts, sources and focus genre.",
        {"type": "object", "properties": {
            "n": {**_INT, "description": "how many to find; defaults to 8"},
            "widen": {"type": "boolean", "description": "once, when too few pass: look further down the related genres and allow well-known albums"},
            "genre": {**_STR, "description": "on-demand runs only: the one Soundlore genre to look in, instead of the focus genres"}}}),
    "music_check": (
        "Check one album idea of your own: does it exist, is it unowned, new to the user and obscure enough. "
        "Returns ok, the reasons it failed, and the verified facts.",
        {"type": "object", "properties": {"artist": _STR, "album": _STR}, "required": ["artist", "album"]}),
    "music_album": (
        "Verified facts for an album by Discogs master id, plus its YouTube Music track list with play counts.",
        {"type": "object", "properties": {"master_id": _INT}, "required": ["master_id"]}),
    "music_save_digest": (
        "Save the digest. Every card is checked again and the save is refused, with the reasons, if any fails. "
        "A card is {artist, album, why, standout_tracks: [two titles from the verified track list], "
        "focus_genre}.",
        {"type": "object", "properties": {
            "cards": {"type": "array", "items": {"type": "object"}},
            "note": {**_STR, "description": "one line, e.g. when fewer albums passed than asked for"},
            "kind": {**_STR, "enum": ["weekly", "on_demand"], "description": "defaults to weekly"}},
         "required": ["cards"]}),
    "music_genre": (
        "Soundlore detail for a genre (closest genres, artists, what it is made of) and how much the user holds.",
        {"type": "object", "properties": {"name": _STR}, "required": ["name"]}),
}


RUN_BUDGET_S = 15 * 60
SAVE_GRACE_S = 2 * 60  # music_save_digest stays open this much longer, so a run can save what passed
BUDGET_STOP = "Stopped: the 15-minute run budget is used up. Save what passed, or report."


class RunBudget:
    """The 15-minute budget of a music run, counted from the session's first music tool call."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, limit: float = RUN_BUDGET_S,
                 grace: float = SAVE_GRACE_S) -> None:
        self.clock, self.limit, self.grace = clock, limit, grace
        self.started: dict[str, float] = {}

    def check(self, session_id: str, tool: str) -> str:
        """BUDGET_STOP when the session's budget is used up for this tool, else ""."""
        if not session_id:
            return ""  # not a bot run
        now = self.clock()
        used = now - self.started.setdefault(session_id, now)
        allowed = self.limit + (self.grace if tool == "music_save_digest" else 0)
        return BUDGET_STOP if used >= allowed else ""


STOPPED = {"token_invalid": "Stopped: Discogs token_invalid. The user must reconnect in Music \u2192 Settings.",
           "rate_limited": "Stopped: Discogs rate_limited. Try again later."}


def _stoppable(fn):
    """A Discogs failure that ends the run (rejected token, rate limit) comes back as a "Stopped:" text."""
    @functools.wraps(fn)
    async def wrapper(*args, **kw):
        try:
            return await fn(*args, **kw)
        except DiscogsError as e:
            if e.kind in STOPPED:
                return STOPPED[e.kind]
            raise
    return wrapper


SAMPLE_TRACKS = 3


def _brief(candidate: dict) -> dict:
    """A candidate as the bot needs it: no full track list (`music_album` has it), so 16 stay well under OUTPUT_CAP."""
    f = candidate.get("verdict") or {}
    return {"artist": f.get("artist"), "album": f.get("album"), "year": f.get("year"), "master_id": f.get("master_id"),
            "genres": f.get("genres") or [], "obscurity_label": f.get("obscurity_label"), "era": f.get("era"),
            "focus_genre": candidate.get("focus_genre"), "sources": candidate.get("sources") or [],
            "sample_tracks": (f.get("tracks") or [])[:SAMPLE_TRACKS]}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


async def _maybe(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class MusicTools:
    def __init__(self, service: Any) -> None:
        self.service = service

    @property
    def active(self) -> bool:
        return bool(self.service.settings.enabled and self.service.keys.token)

    def register(self, tools: Any, catalog: Any) -> bool:
        """Offer the tools and their catalog entry when music is on and the Discogs token is set; else take both away."""
        if not self.active:
            for name in SPECS:
                tools.unregister(name)
            catalog.remove_mcp(GROUP)
            return False
        fns = {"music_profile": self.profile, "music_candidates": self.candidates, "music_check": self.check,
               "music_album": self.album, "music_save_digest": self.save_digest, "music_genre": self.genre}
        for name, (description, spec) in SPECS.items():
            tools.register(name, description, spec, self._budgeted(name, fns[name]), group=GROUP)
        if not any(e.ref == GROUP for e in catalog.mcps()):
            catalog.add_mcp(CatalogEntry(kind="mcp", name=GROUP, use=USE, inputs=list(SPECS), ref=GROUP))
        return True

    def _budgeted(self, name: str, fn):
        budget = getattr(self.service, "budget", None)

        @functools.wraps(fn)
        async def wrapper(*args, **kw):
            if budget is not None and (stop := budget.check(current_call.get()[1], name)):
                return stop
            return await fn(*args, **kw)
        return wrapper

    async def _profile(self) -> dict | None:
        return await _maybe(self.service.profile())

    def _reactions(self) -> list[dict]:
        rows = [{"artist": c.get("artist"), "album": c.get("album"), "reaction": c["reaction"],
                 "reaction_at": c.get("reaction_at")}
                for d in self.service.store.digests(None, 50) for c in d["cards"] if c.get("reaction")]
        rows.sort(key=lambda r: r["reaction_at"] or "", reverse=True)
        return rows[:RECENT_REACTIONS]

    @_stoppable
    async def profile(self) -> str:
        p = await _maybe(self.service.refresh())  # a rescan first when the last one is stale
        if not p:
            return NO_PROFILE
        genres = [{"genre": g["genre"], "share": g["share"], "owned": g.get("owned")}
                  for g in p.get("genres", [])[:TOP_GENRES]]
        st = self.service.settings
        return _json({"focus": list(st.focus), "count": st.count, "era_mix": st.era_mix, "obscurity": st.obscurity,
                      "genres": genres,
                      "decades": p.get("decades", {}), "formats": p.get("formats", {}),
                      "reactions": self._reactions(),
                      **({"scan_error": p["scan_error"]} if p.get("scan_error") else {})})

    @_stoppable
    async def candidates(self, n: int = 8, widen: bool = False, genre: str | None = None) -> str:
        p = await self._profile()
        if not p:
            return NO_PROFILE
        s = self.service
        settings = s.settings
        if genre:
            if not s.atlas.genre(genre):
                return f"Unknown genre {genre!r}: use a Soundlore genre name."
            settings = dataclasses.replace(settings, focus=[genre])  # an on-demand run looks in that genre only
        n = max(1, int(n)) * (2 if widen else 1)
        found = await pipeline_candidates(n, p, settings, s.atlas, s.discogs(), s.ytm,
                                          s.gate(widened=True) if widen else s.gate(), s.store)
        if widen:  # only these masters get the widened obscurity step when saved
            s.widened_masters.setdefault(current_call.get()[1], set()).update(
                c["verdict"]["master_id"] for c in found if c.get("verdict", {}).get("master_id"))
        return _json([_brief(c) for c in found])

    @_stoppable
    async def check(self, artist: str, album: str) -> str:
        v = await self.service.gate().check(artist, album)
        return _json({"ok": v.ok, "reasons": v.reasons, "facts": v.facts})

    @_stoppable
    async def album(self, master_id: int) -> str:
        v = await self.service.gate().check_master(int(master_id))
        out: dict[str, Any] = {"ok": v.ok, "reasons": v.reasons, "facts": v.facts, "ytmusic": None}
        if v.facts.get("artist") and v.facts.get("album"):
            out["ytmusic"] = await asyncio.to_thread(self.service.ytm.album, v.facts["artist"], v.facts["album"])
        return _json(out)

    @_stoppable
    async def save_digest(self, cards: list, note: str = "", kind: str = "weekly", widened: Any = None) -> str:
        """`widened` is accepted and ignored: the service knows which masters the widened call returned."""
        if not isinstance(cards, list) or not cards:
            return "Refused: cards must be a non-empty list of cards."
        if not all(isinstance(c, dict) for c in cards):
            return "Refused: every card must be an object with artist, album, why, standout_tracks and focus_genre."
        if not isinstance(note, str):
            return "Refused: note must be text."
        if kind not in ("weekly", "on_demand"):
            return "Refused: kind must be weekly or on_demand."
        try:
            sid = current_call.get()[1]
            saved = await self.service.digests.save(cards, kind=kind, note=note, session_id=sid,
                                                    widened_masters=frozenset(self.service.widened_masters.get(sid, ())))
        except DigestRefused as e:
            return f"{e}\nReplace these cards and save again."
        except (ValueError, TypeError, AttributeError) as e:
            return f"Refused: {e}"
        return _json({"id": saved["id"], "kind": saved["kind"], "cards": len(saved["cards"])})

    @_stoppable
    async def genre(self, name: str) -> str:
        detail = self.service.atlas.detail(name)
        if not detail:
            return f"Unknown genre {name!r}: use a Soundlore genre name."
        p = await self._profile() or {}
        mine = next((g for g in p.get("genres", []) if g["genre"] == name), None)
        detail = dict(detail, holdings={"share": mine["share"], "owned": mine.get("owned", 0)} if mine
                      else {"share": 0, "owned": 0})
        return _json(detail)

"""Digests: save checked cards, record reactions, drive the Discogs wantlist, keep covers, teach memory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from src.memory.graph import Tier, private_ns
from src.music.discogs import USER_AGENT, DiscogsError
from src.music.store import MusicStore
from src.music.textnorm import artist_key, norm

log = logging.getLogger(__name__)

BOT_ID = "music-librarian"


def _discogs_host(uri: str) -> bool:
    host = (urlsplit(uri).hostname or "").lower()
    return urlsplit(uri).scheme == "https" and (host == "discogs.com" or host.endswith(".discogs.com"))


class DigestRefused(Exception):
    """A card failed the gate; nothing was saved. ``failures``: ``{"artist", "album", "reasons"}``."""

    def __init__(self, failures: list[dict]):
        self.failures = failures
        lines = [f"- {f['artist']}, {f['album']}: {', '.join(f['reasons'])}" for f in failures]
        super().__init__("The digest was not saved. These albums failed the check:\n" + "\n".join(lines))


class Digests:
    def __init__(self, store: MusicStore, gate_factory: Callable, discogs_factory: Callable, ytm,
                 memory, covers_dir: Path, client: httpx.AsyncClient,
                 token_provider: Callable[[], str] = lambda: ""):
        self.store = store
        self.gate_factory = gate_factory
        self.discogs_factory = discogs_factory
        self.ytm = ytm
        self.memory = memory
        self.covers_dir = Path(covers_dir)
        self.client = client
        self.token_provider = token_provider
        self.on_wantlist_change: Callable[[], None] = lambda: None  # the service drops its cached wantlist
        self.on_demand_sessions: set[str] = set()  # sessions the discover route started: they save on_demand only

    # saving -----------------------------------------------------------------

    async def save(self, cards_in: list[dict], *, kind: str = "weekly", note: str = "",
                   session_id: str = "", widened_masters: set | frozenset = frozenset()) -> dict:
        """Check every card again and save them all, or refuse with the reasons. A card whose master the run's
        widened `music_candidates` call returned (`widened_masters`) gets the widened gate's obscurity step."""
        if not cards_in:
            raise ValueError("a digest needs at least one album")
        if kind == "weekly" and session_id and session_id in self.on_demand_sessions:
            raise ValueError("this is an on-demand run; save with kind on_demand")
        gate = self.gate_factory()
        failures: list[dict] = []
        passed: list[tuple[dict, dict, list[str]]] = []  # (input, facts, canonical standout titles)
        seen: set[tuple[str, str]] = set()
        masters: set = set()
        for c in cards_in:
            artist, album = str(c.get("artist", "")), str(c.get("album", ""))
            key = (artist_key(artist), norm(album))
            if key in seen:
                failures.append({"artist": artist, "album": album, "reasons": ["duplicate_in_batch"]})
                continue
            seen.add(key)
            verdict = await gate.check(artist, album)
            if verdict.reasons == ["too_well_known"] and verdict.facts.get("master_id") in widened_masters:
                verdict = await self.gate_factory(widened=True).check_master(int(verdict.facts["master_id"]))
            if not verdict.ok:
                failures.append({"artist": artist, "album": album, "reasons": list(verdict.reasons)})
                continue
            mid = verdict.facts.get("master_id")
            if mid in masters:
                failures.append({"artist": artist, "album": album, "reasons": ["duplicate_in_batch"]})
                continue
            masters.add(mid)
            verified = {norm(t): t for t in verdict.facts.get("tracks") or []}
            titles = [verified.get(norm(str(t))) for t in c.get("standout_tracks") or []]
            if len(titles) != 2 or None in titles or len(set(titles)) != 2:
                failures.append({"artist": artist, "album": album, "reasons": ["bad_track"]})
                continue
            passed.append((c, verdict.facts, titles))
        if failures:
            raise DigestRefused(failures)

        now = datetime.now().astimezone()
        cards = [await self._build(c, f, titles, kind, now) for c, f, titles in passed]
        digest = {"kind": kind, "week_of": date.today().isoformat(), "note": note,
                  "session_id": session_id, "read_at": None if kind == "weekly" else now.isoformat()}
        digest_id = self.store.add_digest(digest, cards)
        return self.store.digest(digest_id)

    async def _build(self, c: dict, f: dict, titles: list[str], kind: str, now: datetime) -> dict:
        artist, album = f["artist"], f["album"]
        try:
            found = await asyncio.to_thread(self.ytm.album, artist, album)
        except Exception:
            log.warning("ytm lookup failed", exc_info=True)
            found = None
        plays = {norm(t.get("title", "")): t.get("plays") for t in (found or {}).get("tracks") or []}
        master_id = f["master_id"]
        cover = await self._cover(master_id, f.get("cover_uri"))
        return {
            "id": None, "artist": artist, "album": album, "year": f.get("year"),
            "style": ", ".join(f.get("styles") or []), "focus_genre": c.get("focus_genre"),
            "era": f.get("era"),
            "standout_tracks": [{"title": t, "plays": plays.get(norm(t))} for t in titles],
            "why": c.get("why", ""),
            "obscurity": {"discogs_have": f.get("have"), "discogs_want": f.get("want"),
                          "label": f.get("obscurity_label")},
            "cover_url": f"/music/covers/{master_id}" if cover else None,
            "links": {"youtube_music": (found or {}).get("url") or self._search_url(artist, album),
                      "discogs": f.get("discogs_url")},
            "checks": {"exists": True, "owned": False, "previously_suggested": False,
                       "on_wantlist": False},
            "sources": list(c.get("sources") or []),
            "similar_to_owned": list(c.get("similar_to_owned") or []),
            "reaction": None,
            "genres": list(f.get("genres") or []),
            "master_id": master_id, "release_id": f.get("release_id"),
            "checked_at": now.isoformat(timespec="seconds"), "kind": kind,
        }

    def _search_url(self, artist: str, album: str) -> str:
        try:
            return self.ytm.search_url(artist, album)
        except Exception:
            log.warning("ytm search url failed", exc_info=True)
            return ""

    async def _cover(self, master_id: int, uri: str | None) -> bool:
        if not uri:
            return False
        headers = {"User-Agent": USER_AGENT}
        token = self.token_provider()
        if token and _discogs_host(uri):  # the token goes to Discogs only, never to whatever host a cover names
            headers["Authorization"] = f"Discogs token={token}"
        try:
            resp = await self.client.get(uri, headers=headers)
            if resp.status_code != 200 or not resp.content:
                return False
            self.covers_dir.mkdir(parents=True, exist_ok=True)
            (self.covers_dir / f"{master_id}.jpg").write_bytes(resp.content)
            return True
        except Exception as e:
            log.warning("cover download failed for master %s: %s", master_id, e)
            return False

    def cover_path(self, master_id: int) -> Path | None:
        try:
            p = self.covers_dir / f"{int(master_id)}.jpg"
        except (TypeError, ValueError):
            return None
        return p if p.is_file() else None

    # reactions --------------------------------------------------------------

    def _need(self, card_id: str) -> dict:
        card = self.store.card(card_id)
        if card is None:
            raise KeyError(card_id)
        return card

    async def _learn(self, line: str) -> None:
        """Write a taste line to memory; a failure is logged, never raised."""
        if self.memory is None:
            return
        try:
            await asyncio.to_thread(self.memory.remember, line, private_ns(BOT_ID), Tier.private,
                                    bot_id=BOT_ID, source="music")
        except Exception:
            log.warning("music memory write failed", exc_info=True)

    async def react(self, card_id: str, reaction: str | None) -> dict:
        if reaction == "wanted":
            raise ValueError("'wanted' is set by adding the album to the Discogs wantlist")
        card = self._need(card_id)
        self.store.set_reaction(card_id, reaction)
        who = f"{card['artist']}, {card['album']}"
        if reaction == "passed":
            await self._learn(f"Passed on {who} ({card.get('style')}, {card.get('year')})")
        elif reaction == "owned":
            self.store.mark_owned(card.get("master_id"), card["artist"], card["album"],
                                  card.get("genres") or [])
            await self._learn(f"Already owns {who}")
        elif reaction == "listened":
            await self._learn(f"Listened to {who}")
        return self.store.card(card_id)

    async def want(self, card_id: str) -> dict:
        card = self._need(card_id)
        if not card.get("release_id"):
            return {"ok": False, "error": "not_found"}
        try:
            await self.discogs_factory().want(int(card["release_id"]))
        except DiscogsError as e:
            return {"ok": False, "error": e.kind}
        self.on_wantlist_change()
        self.store.set_reaction(card_id, "wanted")
        self.store.set_wantlist(card_id, True, card["wanted_by_card"] or not card["on_wantlist"])
        await self._learn(f"Wanted {card['artist']}, {card['album']}")
        return {"ok": True, "card": self.store.card(card_id)}

    async def unwant(self, card_id: str) -> dict:
        card = self._need(card_id)
        if card["wanted_by_card"]:
            try:
                await self.discogs_factory().unwant(int(card["release_id"]))
            except DiscogsError as e:
                return {"ok": False, "error": e.kind}
            self.on_wantlist_change()
            self.store.set_wantlist(card_id, False, False)
        if card["reaction"] == "wanted":
            self.store.set_reaction(card_id, None)
        return {"ok": True, "card": self.store.card(card_id)}

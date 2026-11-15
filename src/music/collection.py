"""Collection scan and taste profile: Discogs releases -> store, genre shares, related genres, map."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import datetime, timezone

from src.music.atlas import Atlas
from src.music.discogs import Discogs, DiscogsError
from src.music.musicbrainz import MusicBrainz
from src.music.store import MusicStore
from src.music.styles import StyleMap
from src.music.textnorm import norm

log = logging.getLogger(__name__)

BROAD = {"rock", "pop", "pop rock", "jazz", "folk", "country", "blues", "soul", "funk",
         "electronic", "hip hop"}
NEW_SHARE = 0.002  # a genre below this share counts as not yet in the collection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _release_row(entry: dict, styles: StyleMap) -> dict | None:
    info = entry.get("basic_information") or {}
    rid = info.get("id") or entry.get("id")
    if not rid:
        return None
    tags = list(info.get("styles") or [])
    broad = list(info.get("genres") or [])
    return {
        "release_id": int(rid),
        "master_id": info.get("master_id") or None,
        "title": info.get("title") or "",
        "year": info.get("year") or 0,
        "artists": [{"id": a.get("id"), "name": a.get("name", "")} for a in info.get("artists") or []],
        "formats": [f["name"] for f in info.get("formats") or [] if f.get("name")],
        "styles": tags,
        "genres": broad,
        "rating": entry.get("rating") or 0,
        "date_added": entry.get("date_added"),
        "mapped": styles.genres_for(tags, broad),
    }


def _mapped(release: dict, atlas: Atlas) -> list[str]:
    """Stored mapped genres; a row without the key falls back to styles that name a profiled genre."""
    if "mapped" in release:
        return list(release["mapped"] or [])
    by_norm = {norm(n): n for n in atlas.names() if atlas.is_profiled(n)}
    out: list[str] = []
    for s in release.get("styles") or []:
        g = by_norm.get(norm(s))
        if g and g not in out:
            out.append(g)
    return out


def build_profile(releases: list[dict], atlas: Atlas, owned_marks: list[dict] | None = None) -> dict:
    """Genre shares weighted ``1 + 0.25 * rating`` per release, split over its mapped genres."""
    items = [(r, _mapped(r, atlas), r.get("rating") or 0) for r in releases]
    items += [({"styles": [], "formats": [], "year": 0}, list(m.get("genres") or []), 0)
              for m in owned_marks or []]
    weight: Counter = Counter()
    owned: Counter = Counter()
    formats: Counter = Counter()
    decades: Counter = Counter()
    styles: Counter = Counter()
    unmapped: Counter = Counter()
    mapped = 0
    for r, genres, rating in items:
        for f in set(r.get("formats") or []):
            formats[f] += 1
        year = r.get("year") or 0
        decades[str(year // 10 * 10) if year > 0 else "unknown"] += 1
        tags = r.get("styles") or []
        styles.update(tags)
        if not genres:
            unmapped.update(tags or r.get("genres") or [])
            continue
        mapped += 1
        for g in genres:
            weight[g] += (1 + 0.25 * rating) / len(genres)
            owned[g] += 1
    total = sum(weight.values())
    rows = []
    for g, w in weight.items():
        d = atlas.detail(g)
        rows.append({"genre": g, "share": w / total, "owned": owned[g], "family": d.get("family"),
                     "x": d.get("x"), "y": d.get("y")})
    rows.sort(key=lambda x: (-x["share"], x["genre"]))
    known = sorted(k for k in decades if k != "unknown")
    ordered = {k: decades[k] for k in known}
    if "unknown" in decades:
        ordered["unknown"] = decades["unknown"]
    return {
        "releases": len(items),
        "mapped": mapped,
        "genres": rows,
        "formats": dict(formats),
        "decades": ordered,
        "top_styles": [{"style": s, "count": c} for s, c in styles.most_common(25)],
        "unmapped": [{"style": s, "count": c} for s, c in unmapped.most_common()],
    }


def related_genres(profile: dict, atlas: Atlas, focus: list[str], limit: int = 12) -> list[dict]:
    """Genres next to ones the collection holds but barely touches: the focus genres' neighbours first, then the
    rest, hottest first within each."""
    share = {g["genre"]: g["share"] for g in profile.get("genres", []) if g["share"] > 0}
    wanted = set(focus)
    for f in focus:
        wanted.update(atlas.narrower(f))
    owned = sorted(share, key=lambda g: (-share[g], g))
    walk = [g for g in owned if g in wanted] + [g for g in owned if g not in wanted]
    taken: set[str] = set()
    out: list[dict] = []
    for g in walk:
        if len(out) >= limit:
            break
        if g in BROAD or g in taken:
            continue
        for name, temp, _shared in atlas.neighbours(g):
            if name in taken or name in BROAD or share.get(name, 0.0) >= NEW_SHARE:
                continue
            taken.add(name)
            d = atlas.detail(name)
            out.append({"genre": name, "score": share.get(g, 0.0) * temp / 100, "because": g,
                        "temperature": temp, "family": d.get("family"),
                        "x": d.get("x"), "y": d.get("y")})
            break
    focus_matching = {g for g in owned if g in wanted}  # the walk's first part: neighbours of the focus genres
    out.sort(key=lambda x: (x["because"] not in focus_matching, -x["score"]))
    return out


async def scan(discogs: Discogs, store: MusicStore, styles: StyleMap, atlas: Atlas) -> dict:
    """Read the whole collection, then swap releases and profile in; a failure changes neither."""
    store.set_scan(state="running", stage="reading", done=0, total=0, mapped=0, unmapped=[],
                   started_at=_now(), finished_at=None, error=None)
    try:
        rows: dict[int, dict] = {}
        async for page, pages, entries in discogs.collection_pages():
            for e in entries:
                row = _release_row(e, styles)
                if row:
                    rows.setdefault(row["release_id"], row)
            store.set_scan(done=page, total=pages)
        store.set_scan(stage="mapping")
        releases = list(rows.values())
        profile = build_profile(releases, atlas, store.owned_marks())
        # The store has no joint write: releases first, so a failure there never leaves a new profile.
        store.replace_releases(releases)
        store.save_profile(profile)
        store.set_scan(state="done", stage="done", mapped=profile["mapped"],
                       unmapped=profile["unmapped"], finished_at=_now(), error=None)
    except DiscogsError as e:
        store.set_scan(state="failed", error=e.kind, finished_at=_now())
    except asyncio.CancelledError:
        store.set_scan(state="failed", error="cancelled", finished_at=_now())
        raise
    except Exception:
        log.exception("music collection scan failed")
        store.set_scan(state="failed", error="internal", finished_at=_now())
    return store.scan()


async def link_artists(store: MusicStore, mb: MusicBrainz, releases: list[dict]) -> int:
    """Look up MusicBrainz ids for collection artists once each; misses are cached too."""
    done = store.checked_ids()
    made = 0
    for r in releases:
        for a in r.get("artists") or []:
            aid = a.get("id")
            if not aid or aid in done:
                continue
            done.add(aid)
            mbid = await mb.artist_by_discogs(aid)
            store.set_link(aid, mbid)
            made += bool(mbid)
    return made


def map_payload(profile: dict, related: list[dict], atlas: Atlas) -> dict:
    return {**atlas.points(), "genres": profile.get("genres", []), "related": related,
            "releases": profile.get("releases", 0), "formats": profile.get("formats", {}),
            "decades": profile.get("decades", {})}

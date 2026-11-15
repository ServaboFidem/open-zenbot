"""The harness's own candidate pipeline: atlas artists and YouTube Music neighbours, through the gate."""

from __future__ import annotations

import asyncio
import math
from datetime import date

from src.music.collection import related_genres
from src.music.discogs import DiscogsError
from src.music.textnorm import artist_key, artist_name, display_name

MAX_CHECKS = 40
MAX_ARTISTS = 120  # Discogs lookups tried, so a long run of misses stays bounded
PER_GENRE = 10
YTM_SEEDS = 5
NEW_YEARS = 15
STOP_KINDS = {"token_invalid", "rate_limited"}


def _overlaps(key: str, other: str) -> bool:
    """One key holds the other and the shorter has two words or more.

    Known limit: a member credited under a band ("kyle gass" vs "tenacious d") is not caught.
    """
    if not key or not other:
        return False
    short, long = sorted((key, other), key=len)
    return len(short.split()) >= 2 and (short == long or short in long)


def _taken(key: str, keys: set[str]) -> bool:
    return key in keys or any(_overlaps(key, k) for k in keys)


def _pick_master(masters: list[dict], new: bool, now_year: int) -> dict | None:
    """The classic (most held of the oldest third) or a new (last 15 years) master by this artist."""
    rows = [m for m in masters if m.get("year")]
    main = [m for m in rows if str(m.get("role", "")).lower() == "main"] or rows
    main.sort(key=lambda m: m["year"])
    if not main:
        return None
    classic_pool = main[:math.ceil(len(main) / 3)]
    recent = [m for m in main if m["year"] > now_year - NEW_YEARS]

    def best(pool: list[dict], oldest: bool) -> dict:
        held = [m for m in pool if m.get("have") is not None]
        if held:
            return max(held, key=lambda m: m["have"])
        return pool[0] if oldest else pool[-1]

    first, second = ((recent, False), (classic_pool, True)) if new else ((classic_pool, True), (recent, False))
    for pool, oldest in (first, second):
        if pool:
            return best(pool, oldest)
    return None


def _focus_genre(genres: list[str], focus: list[str], atlas) -> str:
    wanted = set(genres)
    for f in focus:
        if f in wanted or wanted & set(atlas.narrower(f)):
            return f
    best, best_temp = "", -1.0
    for f in focus:
        for name, temp, _shared in atlas.neighbours(f):
            if name in wanted and temp > best_temp:
                best, best_temp = f, temp
    return best or (focus[0] if focus else "")


def _owned_artists(store) -> tuple[set[str], list[str]]:
    """Keys of every owned artist and the display names of the most-owned five."""
    count: dict[str, int] = {}
    names: dict[str, str] = {}
    for row in store.releases():
        for a in (artist_name(x) for x in row.get("artists") or []):
            k = artist_key(a)
            if k:
                count[k] = count.get(k, 0) + 1
                names.setdefault(k, display_name(a))  # a YouTube Music search seed: no Discogs suffix
    top = sorted(count, key=lambda k: (-count[k], k))[:YTM_SEEDS]
    return set(count), [names[k] for k in top]


async def pipeline_candidates(n: int, profile, settings, atlas, discogs, ytm, gate, store) -> list[dict]:
    """Up to ``n`` passing albums, one per artist, within 40 gate checks."""
    owned_keys, top_owned = _owned_artists(store)
    owned_genres = [g["genre"] for g in (profile or {}).get("genres", []) if g.get("share", 0) > 0]
    if n <= 0 or not owned_genres:
        return []
    focus = list(settings.focus) or owned_genres[:3]
    genres = list(focus)
    for f in focus:
        genres.extend(atlas.narrower(f))
    genres.extend(r["genre"] for r in related_genres(profile, atlas, focus))
    genres = list(dict.fromkeys(genres))

    atlas_pool = [(a["name"], "atlas") for g in genres for a in atlas.artists_in(g)[:PER_GENRE]]
    ytm_pool: list[tuple[str, str]] = []
    for seed in top_owned:
        ytm_pool.extend((name, "youtube_music") for name in await asyncio.to_thread(ytm.related_artists, seed))
    queue: list[tuple[str, str]] = []
    for i in range(max(len(atlas_pool), len(ytm_pool))):
        queue.extend(p[i] for p in (atlas_pool, ytm_pool) if i < len(p))

    now_year = getattr(gate, "now_year", None) or date.today().year
    new_cut = round(settings.era_mix * 10)
    picked_keys: set[str] = set()
    seen: set[str] = set()
    out: list[dict] = []
    checks = tried = k = 0
    for name, source in queue:
        if len(out) >= n or checks >= MAX_CHECKS or tried >= MAX_ARTISTS:
            break
        key = artist_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        if _taken(key, owned_keys) or _taken(key, picked_keys):
            continue
        tried += 1
        new = (k % 10) < new_cut
        try:
            hits = await discogs.search_artists(name)
            hit = next((h for h in hits if artist_key(h.get("title", "")) == key and h.get("id")), None)
            if not hit:
                continue
            masters = await discogs.artist_masters(int(hit["id"]), newest=new)
        except DiscogsError as e:
            if e.kind in STOP_KINDS:
                raise
            continue
        master = _pick_master(masters, new, now_year)
        k += 1
        if not master:
            continue
        checks += 1
        verdict = await gate.check_master(master["id"])  # a rejected token or a rate limit raises from the gate
        if not verdict.ok:
            continue
        picked_keys.add(key)
        facts = verdict.facts
        out.append({"verdict": facts, "sources": [source],
                    "focus_genre": _focus_genre(facts.get("genres") or [], focus, atlas)})
    return out

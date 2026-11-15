"""The gate: every album suggestion must exist, be unowned, be new, and be obscure enough."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.music.discogs import Discogs, DiscogsError
from src.music.settings import MusicSettings
from src.music.store import MusicStore
from src.music.styles import StyleMap
from src.music.textnorm import artist_key, artist_name, display_name, norm, titles_match

HIDDEN_GEM_BELOW = 2500
UNDER_RADAR_BELOW = 12000
TOO_KNOWN_FROM_OBSCURITY = 0.3  # from here "well known" is rejected
UNDER_RADAR_REJECTED_FROM = 0.85  # from here "under the radar" is rejected too: hidden gems only
_REJECTED_BY_BAND = [set(), {"well known"}, {"well known", "under the radar"}]
CLASSIC_AGE = 15


STOP_KINDS = {"token_invalid", "rate_limited"}  # a digest run cannot go on: raised, not a reason on one album


@dataclass
class Verdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)


def obscurity_label(have: int) -> str:
    """How widely held a master's main release is on Discogs."""
    if have < HIDDEN_GEM_BELOW:
        return "hidden gem"
    if have < UNDER_RADAR_BELOW:
        return "under the radar"
    return "well known"


def rejected_labels(obscurity: float, widened: bool = False) -> set[str]:
    """The obscurity labels the setting rejects (`too_well_known`). A widened gate is one band looser."""
    band = 0 if obscurity < TOO_KNOWN_FROM_OBSCURITY else 1 if obscurity < UNDER_RADAR_REJECTED_FROM else 2
    return _REJECTED_BY_BAND[max(0, band - 1) if widened else band]


def _split_title(title: str) -> list[tuple[str, str]]:
    """Every way to read a Discogs ``"Artist - Title"`` string as (artist, title)."""
    parts = title.split(" - ")
    return [(" - ".join(parts[:i]), " - ".join(parts[i:])) for i in range(1, len(parts))]


class Gate:
    def __init__(self, discogs: Discogs, store: MusicStore, styles: StyleMap,
                 settings: MusicSettings, now_year: int, widened: bool = False, wantlist=None):
        self.discogs = discogs
        self.store = store
        self.styles = styles
        self.settings = settings
        self.now_year = now_year
        self.widened = widened  # a widened gate is one obscurity band looser: the one widening step
        self._by_master: dict[int, Verdict] = {}
        self._by_query: dict[tuple[str, str], Verdict] = {}
        self._wantlist: set[int] | None = None
        self._wantlist_source = wantlist or discogs.wantlist_ids  # async () -> ids; the service caches it

    async def check(self, artist: str, album: str) -> Verdict:
        key = (artist_key(artist), norm(album))
        if key in self._by_query:
            return self._by_query[key]
        try:
            hits = await self.discogs.search_masters(artist, album)
        except DiscogsError as e:
            if e.kind in STOP_KINDS:
                raise
            return Verdict(False, ["lookup_failed"], {"error": e.kind})
        want_artist, want_title = artist_key(artist), norm(album)
        title_seen = False
        exact, prefix = None, None  # an exact title anywhere in the hits wins over an earlier prefix match
        for hit in hits:
            for hit_artist, hit_title in _split_title(str(hit.get("title", ""))):
                if not titles_match(hit_title, album):
                    continue
                if artist_key(hit_artist) != want_artist:
                    title_seen = True
                    continue
                master_id = hit.get("master_id") or hit.get("id")
                if not master_id:
                    continue
                if norm(hit_title) == want_title:
                    exact = exact or int(master_id)
                else:
                    prefix = prefix or int(master_id)
            if exact:
                break
        if found := exact or prefix:
            verdict = await self.check_master(found)
            if "lookup_failed" not in verdict.reasons:
                self._by_query[key] = verdict
            return verdict
        reason = "artist_mismatch" if title_seen else "not_found"
        verdict = Verdict(False, [reason], {})
        self._by_query[key] = verdict
        return verdict

    async def check_master(self, master_id: int) -> Verdict:
        if master_id in self._by_master:
            return self._by_master[master_id]
        facts: dict = {"master_id": master_id}
        try:
            verdict = await self._judge(master_id, facts)
        except DiscogsError as e:
            if e.kind in STOP_KINDS:
                raise
            facts["error"] = e.kind
            return Verdict(False, ["lookup_failed"], facts)
        self._by_master[master_id] = verdict
        return verdict

    async def _judge(self, master_id: int, facts: dict) -> Verdict:
        master = await self.discogs.master(master_id)
        main_id = master.get("main_release")
        release = await self.discogs.release(int(main_id)) if main_id else {}
        artists = [a.get("name", "") for a in master.get("artists") or []]
        year = int(master.get("year") or 0)
        community = release.get("community") or {}
        have, want = int(community.get("have") or 0), int(community.get("want") or 0)
        styles, genres = master.get("styles") or [], master.get("genres") or []
        images = release.get("images") or []
        primary = next((i for i in images if i.get("type") == "primary"), images[0] if images else None)
        label = obscurity_label(have)
        facts.update(
            release_id=int(main_id) if main_id else None,
            artist=", ".join(display_name(a) for a in artists if a),  # shown on the card, searched on YTM
            album=master.get("title", ""),
            year=year,
            styles=styles,
            genres=self.styles.genres_for(styles, genres),
            tracks=[t.get("title", "") for t in release.get("tracklist") or []
                    if t.get("type_") == "track"],
            have=have, want=want, obscurity_label=label,
            era="forgotten classic" if year and year <= self.now_year - CLASSIC_AGE else "newer discovery",
            cover_uri=(primary or {}).get("uri") or None,
            discogs_url=master.get("uri") or f"https://www.discogs.com/master/{master_id}",
        )
        reasons: list[str] = []
        if self._owned(master_id, artists, facts["album"]):
            reasons.append("owned")
        if master_id in self.store.suggested_master_ids():
            reasons.append("repeat")
        if self._wantlist is None:
            self._wantlist = await self._wantlist_source()
        if master_id in self._wantlist or (main_id and int(main_id) in self._wantlist):
            reasons.append("on_wantlist")
        if label in rejected_labels(self.settings.obscurity, self.widened):
            reasons.append("too_well_known")
        return Verdict(not reasons, reasons, facts)

    def _owned(self, master_id: int, artists: list[str], album: str) -> bool:
        if master_id in self.store.owned_master_ids():
            return True
        keys = {artist_key(a) for a in artists if a}
        title = norm(album)
        for row in self.store.releases():
            if norm(row.get("title") or "") == title and keys & {artist_key(artist_name(a)) for a in row.get("artists") or []}:
                return True
        for mark in self.store.owned_marks():
            if norm(mark["title"] or "") == title and artist_key(mark["artist"] or "") in keys:
                return True
        return False

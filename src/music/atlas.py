"""Soundlore genre atlas: verified download and read-only lookups.

Soundlore (https://github.com/aydearcas/Soundlore) is open genre data. The files are
fetched at runtime from a pinned commit and checked against SHA-256 before use; they
are never committed. See each file's own ``license`` field for attribution.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx

COMMIT = "6ed7616bab051083213328712d582911c8497dff"
BASE_URL = f"https://raw.githubusercontent.com/aydearcas/Soundlore/{COMMIT}/viewer/"
FILES: dict[str, str] = {
    "genres.json": "1553940cf36548208b973cff4d64164ebd346b80ff143be054d10c5df9d29e7a",
    "temperature_viewer.json": "f738d559db256f87d88aa69cfa3c46d33c5d580cbcf310947346f97889bba903",
    "genre_map.json": "992a136c7a9b9cf3a1ed6a14cdd1b51c4f9a2476460597b6264258aa144b08e9",
    "concepts.json": "3f6f7072897ac37c353d7757e177e009dc7094cfc5e572e397db859a92808988",
    "artists.json": "868ae150bbf1c99b0da26e86b9043e8a854e8f606de70a5bdcf34c2ee7fc6dd7",
    "artist_neighbors.json": "01feb2f0b68890e1ef38f2e72a8700ec061ab8f69f637add12f336128ddc102c",
}


class AtlasError(Exception):
    """A download failed or a file did not match its pinned hash."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def ensure_atlas(dir: Path, client: httpx.AsyncClient,
                       files: dict[str, str] = FILES) -> None:
    """Download any missing or mismatched file into ``dir``, verifying SHA-256.

    Each file is written to ``<name>.tmp`` and renamed only after its hash matches,
    so a bad download leaves nothing behind.
    """
    dir.mkdir(parents=True, exist_ok=True)
    for name, want in files.items():
        path = dir / name
        if path.is_file() and _sha256(path) == want:
            continue
        tmp = dir / f"{name}.tmp"
        try:
            resp = await client.get(BASE_URL + name)
            resp.raise_for_status()
            tmp.write_bytes(resp.content)
            got = _sha256(tmp)
            if got != want:
                raise AtlasError(f"{name}: expected sha256 {want}, got {got}")
            tmp.replace(path)
        except httpx.HTTPError as e:
            raise AtlasError(f"{name}: download failed: {e}") from e
        finally:
            tmp.unlink(missing_ok=True)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Atlas:
    """Loaded atlas data with lookups by genre name."""

    dir: Path
    _genres: dict[str, dict] = field(default_factory=dict)
    _by_mbid: dict[str, dict] = field(default_factory=dict)
    _temps: dict[str, list] = field(default_factory=dict)
    _map: dict = field(default_factory=lambda: {"families": [], "points": []})
    _point: dict[str, list] = field(default_factory=dict)
    _concepts: dict[str, dict] = field(default_factory=dict)
    _facets: dict[str, str] = field(default_factory=dict)
    _artists: dict = field(default_factory=dict)
    _by_genre: dict[str, list] | None = None
    _neighbors: dict | None = None
    _license_texts: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, dir: Path) -> "Atlas":
        a = cls(dir=dir)
        for g in _read(dir / "genres.json"):
            a._genres[g["name"]] = g
            a._by_mbid[g["mbid"]] = g
        a._temps = _read(dir / "temperature_viewer.json")
        a._map = _read(dir / "genre_map.json")
        a._point = {p[0]: p for p in a._map["points"]}
        c = _read(dir / "concepts.json")
        a._concepts = {x["id"]: x for x in c["concepts"]}
        a._facets = {f["code"]: f["label"] for f in c["facets"]}
        a._artists = _read(dir / "artists.json")
        if a._artists.get("license"):
            a._license_texts.append(a._artists["license"])
        return a

    def genre(self, name: str) -> dict | None:
        return self._genres.get(name)

    def names(self) -> list[str]:
        return sorted(self._genres)

    def is_profiled(self, name: str) -> bool:
        g = self._genres.get(name)
        return bool(g and any((g.get("profile") or {}).values()))

    def narrower(self, name: str) -> list[str]:
        """All descendants of a genre, following ``narrower`` links by MBID."""
        g = self._genres.get(name)
        if not g:
            return []
        seen = {g["mbid"]}
        out: list[str] = []
        stack = list(g.get("narrower", []))
        while stack:
            m = stack.pop()
            child = self._by_mbid.get(m)
            if child is None or m in seen:
                continue
            seen.add(m)
            out.append(child["name"])
            stack.extend(child.get("narrower", []))
        return sorted(out)

    def neighbours(self, name: str) -> list[tuple[str, float, list[str]]]:
        return [(e[0], float(e[1]), list(e[4])) for e in self._temps.get(name, [])]

    def _names_of(self, mbids: list[str]) -> list[str]:
        return [self._by_mbid[m]["name"] for m in mbids if m in self._by_mbid]

    def _concept(self, cid: str) -> dict | None:
        c = self._concepts.get(cid)
        if c is None:
            return None
        return {"id": c["id"], "label": c["label"],
                "facet": self._facets.get(c["facet"], c["facet"]),
                "definition": c["definition"]}

    def detail(self, name: str) -> dict:
        g = self._genres.get(name)
        if g is None:
            return {}
        made_of = {k: [c for c in map(self._concept, (g.get("profile") or {}).get(k, [])) if c]
                   for k in ("characteristic", "frequent", "occasional")}
        pt = self._point.get(name)
        return {
            "name": name,
            "area": list(g.get("area", [])),
            "broader": self._names_of(g.get("broader", [])),
            "influenced_by": self._names_of(g.get("influenced_by", [])),
            "made_of": made_of,
            "closest_genres": [{"genre": n, "temperature": t, "shared": s}
                               for n, t, s in self.neighbours(name)[:10]],
            "artists": [{"name": a["name"], "mbid": a["mbid"]}
                        for a in self.artists_in(name)[:8]],
            "family": self.family(name),
            "x": pt[1] if pt else None,
            "y": pt[2] if pt else None,
        }

    def points(self) -> dict:
        return {"families": list(self._map["families"]), "points": list(self._map["points"])}

    def family(self, name: str) -> str | None:
        pt = self._point.get(name)
        if pt is None:
            return None
        fams = self._map["families"]
        return fams[pt[3]] if 0 <= pt[3] < len(fams) else None

    def _index(self) -> dict[str, list]:
        """genre name -> [(artist row, votes for genre, share of artist's votes)]."""
        if self._by_genre is None:
            f = self._artists["fields"]
            ig = f.index("genres")
            names = self._artists["genres"]
            idx: dict[str, list] = {}
            for row in self._artists["artists"]:
                total = sum(v for _, v in row[ig]) or 1
                for gi, v in row[ig]:
                    idx.setdefault(names[gi], []).append((row, v, v / total))
            self._by_genre = idx
        return self._by_genre

    def artists_in(self, name: str, min_share: float = 0.3) -> list[dict]:
        """Artists whose genre votes put at least ``min_share`` on ``name``."""
        f = self._artists["fields"]
        i_id, i_name, i_sl = f.index("id"), f.index("name"), f.index("sitelinks")
        out = [{"name": r[i_name], "mbid": r[i_id], "share": round(share, 4),
                "votes": v, "sitelinks": r[i_sl]}
               for r, v, share in self._index().get(name, []) if share >= min_share]
        out.sort(key=lambda a: (-a["sitelinks"], -a["votes"]))
        return out

    def artist_neighbors(self) -> dict:
        """Similar-artist data, loaded on first use."""
        if self._neighbors is None:
            self._neighbors = _read(self.dir / "artist_neighbors.json")
        return self._neighbors

    def licenses(self) -> list[str]:
        return list(self._license_texts)

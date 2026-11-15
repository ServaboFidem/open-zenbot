"""Map Discogs styles and genres onto Soundlore genres via a hand-checked table."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.music.atlas import Atlas
from src.music.textnorm import norm

TABLE = Path(__file__).parent / "data" / "discogs_styles.tsv"

NON_MUSIC: frozenset[str] = frozenset({
    "Comedy", "Holiday", "Novelty", "Parody", "Movie Effects", "Non-Music", "Children's",
})


def read_table(path: Path = TABLE) -> list[tuple[str, str]]:
    """Rows of ``(discogs_style, soundlore_genre)``; an empty genre means drop."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        style, _, genre = line.partition("\t")
        rows.append((style.strip(), genre.strip()))
    return rows


@dataclass
class StyleMap:
    """Discogs style -> Soundlore genre, built against one atlas."""

    _atlas: Atlas
    _table: dict[str, str] = field(default_factory=dict)
    _dropped: set[str] = field(default_factory=set)
    _by_norm: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, atlas: Atlas, path: Path = TABLE) -> "StyleMap":
        """Rows whose genre the atlas does not profile are skipped."""
        m = cls(atlas)
        for style, genre in read_table(path):
            if not genre:
                m._dropped.add(norm(style))
            elif atlas.is_profiled(genre):
                m._table[norm(style)] = genre
        m._by_norm = {norm(n): n for n in atlas.names() if atlas.is_profiled(n)}
        return m

    def _one(self, tag: str) -> str | None:
        key = norm(tag)
        if key in self._dropped:
            return None
        return self._table.get(key) or self._by_norm.get(key)

    def _map(self, tags: list[str]) -> list[str]:
        out: list[str] = []
        for t in tags:
            g = self._one(t)
            if g and g not in out:
                out.append(g)
        return out

    def genres_for(self, styles: list[str], genres: list[str]) -> list[str]:
        """Soundlore genres for a release's styles; the broad genres only if no style maps."""
        return self._map(styles) or self._map(genres)

"""A small local word matcher for stored skill lists.

It scores how well a short search phrase (a need) matches a skill's name,
description and category, so the best few of thousands of stored skills can
be picked on this computer. Pure functions and one class, standard library
only, deterministic, and linear in the text it is given.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")

# Words are runs of letters and digits; "_" splits too.
SPLIT = re.compile(r"[\W_]+")
MIN_LEN = 2


def words(text: str) -> list[str]:
    """The case-folded words of text, those under two characters dropped."""
    return [w for w in SPLIT.split(text.casefold()) if len(w) >= MIN_LEN]


class Matcher:
    """IDF weights over a set of (name, description, category) documents."""

    def __init__(self, docs: Iterable[tuple[str, str, str]]) -> None:
        self._df: Counter[str] = Counter()
        self._n = 0
        for name, description, category in docs:
            self._n += 1
            self._df.update(set(words(name)) | set(words(description)) | set(words(category)))

    def idf(self, word: str) -> float:
        """log(1 + N / (1 + df)); a word no document has takes the highest weight (df 0)."""
        return math.log(1 + self._n / (1 + self._df.get(word, 0)))

    def score(self, query: str, name: str, description: str, category: str = "") -> float:
        """Sum of the IDF of each distinct query word the document has, doubled in the name."""
        in_name = set(words(name))
        in_doc = in_name | set(words(description)) | set(words(category))
        total = 0.0
        for word in set(words(query)) & in_doc:
            weight = self.idf(word)
            total += weight * 2 if word in in_name else weight
        return total


def top(
    query: str,
    items: Iterable[T],
    key: Callable[[T], tuple[str, str, str]],
    n: int,
    *,
    matcher: Matcher | None = None,
) -> list[T]:
    """The at most n items scoring above zero, best first; ties keep input order.

    A given `matcher` is used as it is; otherwise one is built over the items.
    """
    rows = list(items)
    if n <= 0 or not rows:
        return []
    keys = [key(item) for item in rows]
    matcher = matcher or Matcher(keys)
    scored = []
    for item, (name, description, category) in zip(rows, keys):
        s = matcher.score(query, name, description, category)
        if s > 0:
            scored.append((s, item))
    scored.sort(key=lambda pair: -pair[0])  # stable: ties keep input order
    return [item for _, item in scored[:n]]

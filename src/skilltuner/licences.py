"""Telling the five accepted licences apart, on this computer and conservatively: when unsure, no licence.

A licence file is text written by a stranger. It is accepted only when it is essentially the standard wording: the
standard body in full, with at most a title and copyright lines before it and nothing (or "All rights reserved")
after it. Anything modified or extended, any mention of a licence inside other text, and any other licence give "".
"""
from __future__ import annotations

import re
from functools import cache
from importlib import resources

from .convert import LICENCES

CANON = {"apache-2.0": "Apache-2.0", "mit": "MIT", "bsd-2-clause": "BSD-2-Clause",
         "bsd-3-clause": "BSD-3-Clause", "cc-by-4.0": "CC-BY-4.0"}
assert set(CANON) == set(LICENCES)

_MIT_BODY = (
    "permission is hereby granted free of charge to any person obtaining a copy of this software and associated "
    "documentation files the software to deal in the software without restriction including without limitation the "
    "rights to use copy modify merge publish distribute sublicense and or sell copies of the software and to permit "
    "persons to whom the software is furnished to do so subject to the following conditions the above copyright "
    "notice and this permission notice shall be included in all copies or substantial portions of the software the "
    "software is provided as is without warranty of any kind express or implied including but not limited to the "
    "warranties of merchantability fitness for a particular purpose and noninfringement in no event shall the "
    "authors or copyright holders be liable for any claim damages or other liability whether in an action of "
    "contract tort or otherwise arising from out of or in connection with the software or the use or other dealings "
    "in the software")
_BSD_HEAD = ("redistribution and use in source and binary forms with or without modification are permitted provided "
             "that the following conditions are met 1 redistributions of source code must retain the above copyright "
             "notice this list of conditions and the following disclaimer 2 redistributions in binary form must "
             "reproduce the above copyright notice this list of conditions and the following disclaimer in the "
             "documentation and or other materials provided with the distribution ")
_BSD_THIRD = ("3 neither the name of the copyright holder nor the names of its contributors may be used to endorse "
              "or promote products derived from this software without specific prior written permission ")
_BSD_TAIL = ("this software is provided by the copyright holders and contributors as is and any express or implied "
             "warranties including but not limited to the implied warranties of merchantability and fitness for a "
             "particular purpose are disclaimed in no event shall the copyright holder or contributors be liable for "
             "any direct indirect incidental special exemplary or consequential damages including but not limited "
             "to procurement of substitute goods or services loss of use data or profits or business interruption "
             "however caused and on any theory of liability whether in contract strict liability or tort including "
             "negligence or otherwise arising in any way out of the use of this software even if advised of the "
             "possibility of such damage")
_BSD2 = _BSD_HEAD + _BSD_TAIL
_BSD3 = _BSD_HEAD + _BSD_THIRD + _BSD_TAIL

_YEARS = r"\d{4}(?:\s*[-\u2013,]\s*\d{4})*"
_NAME = r"(?:(?:inc|llc|ltd|co|corp|jr|sr|[A-Za-z])\.|[^\W\d_][\w'\u2019-]*|&)"
_HOLDER = rf"{_NAME}(?:,?\s+{_NAME})*,?(?:\s*<(?:[^<>\s@]{{1,60}}@[^<>\s@]{{1,60}}|https?://[^<>\s]{{1,100}})>)?"
_COPYRIGHT = re.compile(rf"copyright\s*(?:\(c\)|\u00a9)?\s*{_YEARS},?\s+{_HOLDER}|all rights reserved\.?",
                        re.IGNORECASE)
_NOT_A_NAME = frozenset(
    "prohibit prohibited forbidden not no only may must shall use used commercial noncommercial without except "
    "personal permitted allowed license licensed free paid restricted unauthorized".split())
_MIT_TITLE = re.compile(r"#*\s*(the\s+)?mit\s+license(\s*\(mit\))?\s*:?", re.IGNORECASE)
_BSD_TITLE = re.compile(r"#*\s*(the\s+)?(new\s+|simplified\s+|revised\s+)?bsd[ -]?[23][ -]clause(\s+license)?"
                        r"(\s*\([^)]{0,40}\))?\s*:?", re.IGNORECASE)
_ENDINGS = ("", "all rights reserved", "end of license", "eof")


def canonical(spdx: str) -> str:
    return CANON.get(spdx.strip().casefold(), "") if isinstance(spdx, str) else ""


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def _copyright(line: str) -> bool:
    if _COPYRIGHT.fullmatch(line) is None:
        return False
    return not _NOT_A_NAME & set(re.findall(r"[a-z]+", line.casefold()))


def _framed(text: str, body: str, title: re.Pattern) -> bool:
    """True when `text` is `body` (compared as words) with only a title and copyright lines before it and
    nothing, or "all rights reserved", after it."""
    pattern = r"[^a-z0-9]+".join(re.escape(w) for w in body.split())
    m = re.search(pattern, text, re.IGNORECASE)
    if m is None or re.search(pattern, text[m.end():], re.IGNORECASE):
        return False
    lines = [ln.strip() for ln in text[:m.start()].splitlines() if ln.strip()]
    if len(lines) > 8:
        return False
    for i, line in enumerate(lines):
        if not (_copyright(line) or (i == 0 and title.fullmatch(line))):
            return False
    return _norm(text[m.end():]) in _ENDINGS


@cache
def _standard_apache() -> str:
    return _norm(resources.files("src.skilltuner").joinpath("apache-2.0.txt").read_text("utf-8"))


_APPENDIX_FIELDS = "copyright yyyy name of copyright owner"
_YEAR = re.compile(r"\d{4}")
_RESERVED = ["all", "rights", "reserved"]


def _copyright_statements(t: str) -> bool:
    """True when `t` (as `_norm` leaves it) is one to eight copyright statements in a row: "copyright", an optional
    "(c)", the years, then a holder of one to twelve words, with an optional "all rights reserved".

    Read word by word, each word one way: every four-digit word straight after "copyright" is a year, and the holder
    runs to the next "copyright". A regular expression for this backtracks for seconds on a crafted value, since a
    four-digit word could be a year or a holder's word. Words that read as terms are refused by the caller."""
    words, i, n = t.split(), 0, 0
    while i < len(words):
        if words[i] != "copyright" or n == 8:
            return False
        i, n = i + 1, n + 1
        if i < len(words) and words[i] == "c":
            i += 1
        start = i
        while i < len(words) and _YEAR.fullmatch(words[i]):
            i += 1
        if i == start:
            return False
        holder = []
        while i < len(words) and words[i] != "copyright":
            holder.append(words[i])
            i += 1
        if holder[-3:] == _RESERVED:
            holder = holder[:-3]
        if not 1 <= len(holder) <= 12:
            return False
    return n > 0


def _apache(t: str) -> bool:
    """The standard terms, then nothing or the standard appendix whole, with only its bracketed copyright line
    filled in (by copyright statements and nothing else)."""
    standard = _standard_apache()
    end = standard.index("end of terms and conditions") + len("end of terms and conditions")
    if not t.startswith(standard[:end]):
        return False
    rest = t[end:].strip()
    appendix = standard[end:].strip()
    if rest in ("", appendix):
        return True
    head, _, tail = appendix.partition(_APPENDIX_FIELDS)
    if not (rest.startswith(head) and rest.endswith(tail) and len(rest) >= len(head) + len(tail)):
        return False
    filled = rest[len(head):len(rest) - len(tail)].strip()
    return (len(filled) <= 600 and _copyright_statements(filled)
            and not _NOT_A_NAME & set(filled.split()))


@cache
def _cc_by_forms() -> frozenset[str]:
    """The standard text (`cc-by-4.0.txt`, Creative Commons' own legalcode.txt) as `_norm` leaves it, with and without
    the preamble before the licence's heading and the notice after its last section; nothing else is accepted."""
    standard = _norm(resources.files("src.skilltuner").joinpath("cc-by-4.0.txt").read_text("utf-8"))
    start = standard.index("creative commons attribution 4 0 international public license")
    end = standard.index("creative commons is not a party to its public licenses")
    preamble, legal, notice = standard[:start].strip(), standard[start:end].strip(), standard[end:].strip()
    return frozenset(" ".join(p for p in (head, legal, tail) if p)
                     for head in (preamble, "") for tail in (notice, ""))


def _cc_by(t: str) -> bool:
    return t in _cc_by_forms()


def licence_of_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    t = _norm(text)
    if _framed(text, _MIT_BODY, _MIT_TITLE):
        return "MIT"
    if _framed(text, _BSD3, _BSD_TITLE):
        return "BSD-3-Clause"
    if _framed(text, _BSD2, _BSD_TITLE):
        return "BSD-2-Clause"
    if _cc_by(t):
        return "CC-BY-4.0"
    if _apache(t):
        return "Apache-2.0"
    return ""

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
_CC_OPEN = ("attribution 4 0 international creative commons corporation creative commons is not a law firm and "
            "does not provide legal services or legal advice")

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


def _apache(t: str) -> bool:
    standard = _standard_apache()
    end = standard.index("end of terms and conditions") + len("end of terms and conditions")
    if not t.startswith(standard[:end]):
        return False
    rest = t[end:].strip()
    return rest == "" or (rest.startswith("appendix how to apply the apache license to your work")
                          and rest.endswith("limitations under the license"))


_CC_S8 = _norm(
    "a. For the avoidance of doubt, this Public License does not, and shall not be interpreted to, reduce, limit, "
    "restrict, or impose conditions on any use of the Licensed Material that could lawfully be made without "
    "permission under this Public License. b. To the extent possible, if any provision of this Public License is "
    "deemed unenforceable, it shall be automatically reformed to the minimum extent necessary to make it "
    "enforceable. If the provision cannot be reformed, it shall be severed from this Public License without "
    "affecting the enforceability of the remaining terms and conditions. c. No term or condition of this Public "
    "License will be waived and no failure to comply consented to unless expressly agreed to by the Licensor. "
    "d. Nothing in this Public License constitutes or may be interpreted as a limitation upon, or waiver of, any "
    "privileges and immunities that apply to the Licensor or You, including from the legal processes of any "
    "jurisdiction or authority.")
_CC_NOTICE = _norm(
    "Creative Commons is not a party to its public licenses. Notwithstanding, Creative Commons may elect to apply "
    "one of its public licenses to material it publishes and in those instances will be considered the Licensor. "
    "The text of the Creative Commons public licenses is dedicated to the public domain under the CC0 Public "
    "Domain Dedication. Except for the limited purpose of indicating that material is shared under a Creative "
    "Commons public license or as otherwise permitted by the Creative Commons policies published at "
    "creativecommons.org/policies, Creative Commons does not authorize the use of the trademark Creative Commons "
    "or any other trademark or logo of Creative Commons without its prior written consent including, without "
    "limitation, in connection with any unauthorized modifications to any of its public licenses or any other "
    "arrangements, understandings, or agreements concerning use of licensed material. For the avoidance of doubt, "
    "this paragraph does not form part of the public licenses. Creative Commons may be contacted at "
    "creativecommons.org.")


def _cc_by(t: str) -> bool:
    legal = "creative commons attribution 4 0 international public license"
    heading = "section 8 interpretation"
    if not (t.startswith(_CC_OPEN) and legal in t and "section 1 definitions" in t and heading in t
            and t.index(legal) < t.index("section 1 definitions")):
        return False
    tail = t[t.rindex(heading) + len(heading):].strip()
    return tail in (_CC_S8, _CC_S8 + " " + _CC_NOTICE)


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

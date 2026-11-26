"""Telling the five accepted licences apart, on this computer and conservatively: when unsure, no licence.

A licence file is text written by a stranger. It is compared with the standard wording only; anything modified or
extended, or any other licence, gives "".
"""
from __future__ import annotations

import re

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
_BSD_1 = ("redistributions of source code must retain the above copyright notice this list of conditions and the "
          "following disclaimer")
_BSD_2 = ("redistributions in binary form must reproduce the above copyright notice this list of conditions and the "
          "following disclaimer in the documentation and or other materials provided with the distribution")
_BSD_3 = "neither the name of"
_BSD_END = "this software is provided by the copyright holders and contributors"
HEADER_MAX = 300
CLOSING_MAX = 100


def canonical(spdx: str) -> str:
    return CANON.get(spdx.strip().casefold(), "") if isinstance(spdx, str) else ""


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def licence_of_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    t = _norm(text)
    start = t.find(_MIT_BODY)
    if start >= 0:
        rest = len(t) - start - len(_MIT_BODY)
        return "MIT" if start <= HEADER_MAX and rest <= CLOSING_MAX else ""
    if "creative commons attribution 4 0 international public license" in t:
        return "CC-BY-4.0"
    if "apache license" in t[:600] and "version 2 0 january 2004" in t[:600]:
        return "Apache-2.0"
    if _BSD_1 in t and _BSD_2 in t and _BSD_END in t:
        return "BSD-3-Clause" if _BSD_3 in t else "BSD-2-Clause"
    return ""

"""What a text says, and how long it may be.

Length is measured the way carriers bill it, in segments, not characters. A text made only of GSM-7 characters fits
160 in one segment and 153 a segment when split (an extension character counts two); any other character turns the
whole text into UCS-2: 70 in one segment, 67 a segment when split, counted in UTF-16 units (an emoji counts two).
Everything this module adds itself ("...", " - ", "; ") is GSM-7, so a plain-English text keeps the full 160.

Calendar text can be written by anyone who invites the owner, so it is flattened to one line and web addresses become
"[link]": a stranger's invitation never puts a clickable link on the owner's phone."""

from __future__ import annotations

import re
import unicodedata

# GSM 03.38: the default alphabet and the extension table (each extension character is sent as two).
GSM = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
       "abcdefghijklmnopqrstuvwxyzäöñüà")
GSM_EXT = "^{}\\[~]|€"
_GSM, _EXT = frozenset(GSM), frozenset(GSM_EXT)
LINK = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_BREAKS = {" ", " "}


def units(text: str) -> tuple[bool, int]:
    """(GSM-7 or not, the length a carrier counts)."""
    if all(c in _GSM or c in _EXT for c in text):
        return True, sum(2 if c in _EXT else 1 for c in text)
    return False, sum(2 if ord(c) > 0xFFFF else 1 for c in text)


def fits(text: str, segments: int) -> bool:
    gsm, n = units(text)
    one, each = (160, 153) if gsm else (70, 67)
    return n <= (one if segments == 1 else each * segments)


def clean(text: str) -> str:
    """One plain line: control and format characters become spaces, whitespace is collapsed, links become [link]."""
    flat = "".join(" " if c in _BREAKS or unicodedata.category(c) in ("Cc", "Cf") else c for c in text)
    return LINK.sub("[link]", " ".join(flat.split()))


def cut(text: str, segments: int, tail: str = "") -> str:
    """``text + tail`` when it fits in ``segments``; else the longest prefix of ``text`` that fits with "..." and
    ``tail`` after it. Fitting only gets harder as the prefix grows (a character never lowers the count or improves
    the encoding), so the longest one is found by halving."""
    if fits(text + tail, segments):
        return text + tail
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if fits(text[:mid].rstrip() + "..." + tail, segments):
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + "..." + tail


def reminder(title: str, clock: str, location: str) -> str:
    """"Reminder: <title> at <time> - <location>", in one segment."""
    where = clean(location)
    return cut(f"Reminder: {clean(title)} at {clock}" + (f" - {where}" if where else ""), 1)


def rundown(items: list[str]) -> str:
    """"Tomorrow: <item>; <item>...", in two segments, ending "; ...and N more" when some are left out."""
    if not items:
        raise ValueError("no items")
    n = len(items)
    for k in range(n, 0, -1):
        text = "Tomorrow: " + "; ".join(items[:k]) + (f"; ...and {n - k} more" if k < n else "")
        if fits(text, 2):
            return text
    return cut("Tomorrow: " + items[0], 2, tail=f"; ...and {n - 1} more" if n > 1 else "")

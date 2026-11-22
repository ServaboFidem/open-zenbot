"""The tweak knobs a design file declares, a check of a value, and the splice.

A design lists up to 8 knobs in a JSON manifest (`<script data-zen-tweaks>`) and
holds their values as CSS variables in one `:root` block of a
`<style data-zen-tweaks>`. The manifest is bot-written and untrusted: `knobs`
never raises and drops any knob that does not hold. A value is formatted here
from a checked colour or number, so no free CSS ever reaches the file.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from src.harness import design_html
from src.harness.design_parts import PartRefused

MAX_KNOBS = 8
UNITS = ("px", "rem", "em", "%", "ms", "deg", "")
LABEL_MAX = 40
MANIFEST_MAX = 20_000

_VAR = re.compile(r"--[a-z][a-z0-9-]{0,40}\Z")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_ROOT = re.compile(r":root\s*\{")


@dataclass(frozen=True)
class Knob:
    var: str
    label: str
    type: str  # "color" | "range"
    min: float
    max: float
    step: float
    unit: str
    value: str
    span: tuple[int, int]


def _real(x: object) -> bool:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(float(x))  # an int too big for a float is no number here
    except OverflowError:
        return False


def _label(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    kept = "".join(c for c in raw if unicodedata.category(c)[0] != "C"
                   and unicodedata.category(c) not in ("Zl", "Zp"))
    return kept.strip()[:LABEL_MAX].strip()


_PLACES = 12  # digits on each side of the point a written number may have


def _decimal_text(x: float) -> str | None:
    """`x` as a plain decimal (no exponent, no trailing zeros), or None.

    A float is read from its shortest repr, so 0.0000004 stays 0.0000004 and
    0.1 + 0.2 is 0.3 once rounded to 12 places; more than 12 digits before the
    point cannot be written and gives None.
    """
    d = Decimal(x) if isinstance(x, int) else Decimal(repr(float(x)))
    if not d.is_finite() or d.adjusted() >= _PLACES:
        return None
    text = format(d.quantize(Decimal(1).scaleb(-_PLACES), rounding=ROUND_HALF_EVEN), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _number_text(x: float) -> str:
    """A bound for a sentence: as written when it can be, else in short form."""
    return _decimal_text(x) or f"{float(x):g}"


def _sources(html: str) -> tuple[str, int, str] | None:
    """The one tweaks style's text and offset, and the one manifest's text."""
    styles, scripts = [], []
    for el in design_html.scan(html):
        if "data-zen-tweaks" not in el.attrs or el.inner is None:
            continue
        if el.tag == "style":
            styles.append(el)
        elif el.tag == "script":
            scripts.append(el)
    if len(styles) != 1 or len(scripts) != 1:
        return None
    s, m = styles[0].inner, scripts[0].inner
    return html[s[0]:s[1]], s[0], html[m[0]:m[1]]


_COMMENT = re.compile(r"/\*.*?(?:\*/|\Z)", re.S)


def _uncommented(css: str) -> str:
    """The style text with each CSS comment blanked (to the end when unterminated).

    Every character but a newline becomes a space, so offsets and lines stay as
    they were and a declaration inside a comment is never counted or matched.
    """
    return _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), css)


def _block(css: str) -> tuple[int, int] | None:
    opens = list(_ROOT.finditer(css))
    if len(opens) != 1:
        return None
    start = opens[0].end()
    end = css.find("}", start)
    return None if end < 0 else (start, end)


def _knob(entry: object, css: str, active: str, base: int,
          block: tuple[int, int]) -> Knob | None:
    """One knob: `active` is `css` with its comments blanked, used for every
    count and match; the value and its span are read from `css` itself."""
    if not isinstance(entry, dict):
        return None
    var, kind = entry.get("var"), entry.get("type")
    if not isinstance(var, str) or not _VAR.match(var) or kind not in ("color", "range"):
        return None
    label = _label(entry.get("label"))
    if not label:
        return None
    lo, hi, step, unit = 0, 0, 0, ""
    if kind == "range":
        lo, hi, step = entry.get("min"), entry.get("max"), entry.get("step", 1)
        unit = entry.get("unit", "")
        if not (_real(lo) and _real(hi) and _real(step)) or lo >= hi or step <= 0:
            return None
        if unit not in UNITS:
            return None
    # The variable is declared once in the whole style element, as a line in :root.
    if len(re.findall(r"(?<![\w-])" + re.escape(var) + r"\s*:", active)) != 1:
        return None
    # One greedy value group with no whitespace quantifier beside it, trimmed
    # below: an unfinished declaration on a long line fails in linear time.
    decl = re.compile(r"^[ \t]*" + re.escape(var) + r"[ \t]*:([^;\n]*);", re.M)
    m = decl.search(active, block[0], block[1])
    if not m:
        return None
    raw = m.group(1)
    start = m.start(1) + len(raw) - len(raw.lstrip(" \t"))
    end = start + len(raw.strip(" \t"))
    text = css[start:end]
    if kind == "color":
        if not _COLOR.match(text):
            return None
        value = text
    else:
        if not unit:
            num = text
        elif text.endswith(unit):
            num = text[:-len(unit)]
        else:
            return None
        if not _NUMBER.fullmatch(num) or not lo <= float(num) <= hi:
            return None
        value = num
    knob = Knob(var, label, kind, lo, hi, step, unit, value, (base + start, base + end))
    if kind == "range":
        try:  # a save compares with the value as fmt writes it, so it must be writable
            fmt(knob, float(value))
        except PartRefused:
            return None
    return knob


def knobs(html: str) -> list[Knob]:
    try:
        found = _sources(html)
        if found is None:
            return []
        css, base, raw = found
        if len(raw) > MANIFEST_MAX:
            return []
        active = _uncommented(css)
        block = _block(active)
        data = json.loads(raw)
        entries = data.get("tweaks") if isinstance(data, dict) else None
        if block is None or not isinstance(entries, list):
            return []
        out: list[Knob] = []
        for entry in entries[:MAX_KNOBS]:
            k = _knob(entry, css, active, base, block)
            if k and all(k.var != o.var for o in out):
                out.append(k)
        return out
    except Exception:
        return []


def fmt(knob: Knob, value: object) -> str:
    """The declaration's value for a knob, or PartRefused."""
    if knob.type == "color":
        if not isinstance(value, str) or not _COLOR.match(value):
            raise PartRefused("A colour is a # and six hex digits, like #c2410c.")
        return value.lower()
    refused = PartRefused(
        f"{knob.label} must be a number from {_number_text(knob.min)}"
        f" to {_number_text(knob.max)}.")
    if not _real(value) or not knob.min <= value <= knob.max:
        raise refused
    text = _decimal_text(value)
    # The text written is the number read back: it must still be in range.
    if text is None or not knob.min <= float(text) <= knob.max:
        raise refused
    return text + knob.unit


def apply(html: str, found: list[Knob], values: dict[str, object]) -> str:
    by_var = {k.var: k for k in found}
    edits = []
    for var, value in values.items():
        k = by_var.get(var)
        if k is None:
            raise PartRefused("That is not a tweak of this design.")
        edits.append((k.span, fmt(k, value)))
    for (a, b), text in sorted(edits, key=lambda e: e[0][0], reverse=True):
        html = html[:a] + text + html[b:]
    return html


def view(found: list[Knob]) -> list[dict]:
    def number(k: Knob) -> float:
        x = float(k.value)
        return int(x) if x.is_integer() else x
    return [
        {"var": k.var, "label": k.label, "type": k.type, "min": k.min, "max": k.max,
         "step": k.step, "unit": k.unit,
         "value": k.value if k.type == "color" else number(k)}
        for k in found]

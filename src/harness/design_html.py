"""An HTML scanner that reports exact source offsets (in characters).

Later code uses the offsets to change one spot in a design file and leave every
other character alone, so nothing here rewrites or normalises the source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})

_CLOSES_P = frozenset({
    "address", "article", "aside", "blockquote", "details", "div", "dl",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hgroup", "hr", "main", "menu", "nav", "ol",
    "p", "pre", "section", "table", "ul",
})
_SECTIONS = frozenset({"thead", "tbody", "tfoot"})
# A start tag of the key ends the nearest open element named in the value.
IMPLIED_END = {
    **{t: frozenset({"p"}) for t in _CLOSES_P},
    "li": frozenset({"li"}),
    "dt": frozenset({"dt", "dd"}),
    "dd": frozenset({"dt", "dd"}),
    "tr": frozenset({"tr"}),
    "td": frozenset({"td", "th"}),
    "th": frozenset({"td", "th"}),
    "thead": frozenset({"tr", "td", "th"}) | _SECTIONS,
    "tbody": frozenset({"tr", "td", "th"}) | _SECTIONS,
    "tfoot": frozenset({"tr", "td", "th"}) | _SECTIONS,
    "option": frozenset({"option"}),
    "optgroup": frozenset({"option", "optgroup"}),
}
# The search for an element to end stops at these.
_SCOPE = frozenset({"table", "ul", "ol", "select", "button", "template", "body", "html"})

_TAG_NAME = re.compile(r"<([^\s/>]+)")
_ATTR = re.compile(
    r"""([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*)))?""")


@dataclass(frozen=True)
class Element:
    tag: str
    attrs: dict[str, str]
    start: tuple[int, int]
    inner: tuple[int, int] | None
    children: int
    within: tuple[str, ...]
    spans: tuple[tuple[str, int, int], ...] = ()

    def attr_span(self, name: str) -> tuple[int, int] | None:
        """Offsets of a quoted attribute's value, quotes excluded."""
        name = name.lower()
        for n, a, b in self.spans:
            if n == name:
                return (a, b)
        return None


def _parse_attrs(raw: str, base: int):
    """Attributes of a raw start tag: (decoded values, quoted value spans).
    The first of a repeated attribute wins, as in a browser."""
    m = _TAG_NAME.match(raw)
    pos = m.end() if m else 1
    attrs: dict[str, str] = {}
    spans: list[tuple[str, int, int]] = []
    while True:
        m = _ATTR.search(raw, pos)
        if not m:
            break
        pos = max(m.end(), pos + 1)
        name = m.group(1).lower()
        if name in attrs:
            continue
        dq, sq, bare = m.group(2), m.group(3), m.group(4)
        value = dq if dq is not None else sq if sq is not None else bare
        attrs[name] = unescape(value) if value else ""
        if dq is not None:
            spans.append((name, base + m.start(2), base + m.end(2)))
        elif sq is not None:
            spans.append((name, base + m.start(3), base + m.end(3)))
    return attrs, tuple(spans)


class _Rec:
    __slots__ = ("tag", "attrs", "start", "spans", "inner", "children", "within")

    def __init__(self, tag, attrs, start, spans, within):
        self.tag, self.attrs, self.start, self.spans = tag, attrs, start, spans
        self.within = within
        self.inner = None
        self.children = 0


class _Scanner(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=False)
        starts = [0]
        for i, ch in enumerate(html):
            if ch == "\n":
                starts.append(i + 1)
        self._starts = starts
        self._recs: list[_Rec] = []
        self._stack: list[_Rec] = []

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._starts[line - 1] + col

    def _imply_end(self, tag: str) -> None:
        closes = IMPLIED_END.get(tag)
        if not closes:
            return
        for i in range(len(self._stack) - 1, -1, -1):
            name = self._stack[i].tag
            if name in closes:
                del self._stack[i:]  # left with inner None
                return
            if name in _SCOPE:
                return

    def _open(self, tag: str, void: bool) -> None:
        self._imply_end(tag)
        begin = self._offset()
        raw = self.get_starttag_text() or ""
        attrs, spans = _parse_attrs(raw, begin)
        rec = _Rec(tag, attrs, (begin, begin + len(raw)), spans,
                   tuple(r.tag for r in self._stack))
        if self._stack and tag != "br":
            self._stack[-1].children += 1
        self._recs.append(rec)
        if not void:
            self._stack.append(rec)

    def handle_starttag(self, tag, attrs):
        self._open(tag, tag in VOID)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, True)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i].tag == tag:
                rec = self._stack[i]
                rec.inner = (rec.start[1], self._offset())
                del self._stack[i:]
                return


def scan(html: str) -> list[Element]:
    """Every element in document order; nothing inside script, style or a comment."""
    p = _Scanner(html)
    p.feed(html)
    p.close()
    return [Element(r.tag, r.attrs, r.start, r.inner, r.children, r.within, r.spans)
            for r in p._recs]

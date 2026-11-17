"""A design's pictures as files: recognise them by their first bytes, embed them into a copy of the page,
and add one to the design's `images/` folder. Every read and write refuses a link."""
from __future__ import annotations

import base64
import re
import stat
from pathlib import Path

from src.harness import design_html

IMAGES = "images"
PICTURE_MAX_BYTES = 1_500_000
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_EXT = {"png": "png", "jpeg": "jpg", "webp": "webp", "gif": "gif"}
_STEM_MAX = 48  # leaves room for "-NN" and the extension inside NAME's 64 characters


class ImageRefused(Exception):
    """A picture that may not be read or written; the message says why (never a disk path)."""


def sniff(data: bytes) -> str | None:
    if data.startswith(bytes.fromhex("89504e470d0a1a0a")):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def data_uri(kind: str, data: bytes) -> str:
    return f"data:image/{kind};base64,{base64.b64encode(data).decode('ascii')}"


def _images_dir(folder: Path) -> Path:
    """The design's `images/` folder, which must be a real folder, not a link."""
    d = folder / IMAGES
    try:
        if d.is_symlink() or not d.is_dir() or d.resolve() != d.parent.resolve() / IMAGES:
            raise ImageRefused("images is a link or not a folder")
    except OSError:
        raise ImageRefused("images is not readable") from None
    return d


def load(folder: Path, name: str) -> tuple[str, bytes]:
    """`(kind, bytes)` of `folder/images/<name>`, or ImageRefused(reason)."""
    if not NAME.fullmatch(name):
        raise ImageRefused("name is not allowed")
    d = folder / IMAGES
    if not d.exists() and not d.is_symlink():
        raise ImageRefused("not found")
    d = _images_dir(folder)
    p = d / name
    try:
        st = p.lstat()
    except FileNotFoundError:
        raise ImageRefused("not found") from None
    except OSError:
        raise ImageRefused("not readable") from None
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise ImageRefused("a link or not a regular file")
    if st.st_size > PICTURE_MAX_BYTES:
        raise ImageRefused("too large")
    try:
        with open(p, "rb") as f:
            data = f.read(PICTURE_MAX_BYTES + 1)
    except OSError:
        raise ImageRefused("not readable") from None
    if len(data) > PICTURE_MAX_BYTES:
        raise ImageRefused("too large")
    kind = sniff(data)
    if kind is None:
        raise ImageRefused("not a picture")
    return kind, data


def embed(html: bytes, folder: Path) -> tuple[bytes, list[tuple[str, str]]]:
    """Put each `images/<name>` picture into its `<img src>` as a data URI. Returns the new bytes and
    `(src, reason)` for each picture left as written. Bytes that are not UTF-8 come back unchanged."""
    try:
        text = html.decode("utf-8")  # strict; a BOM stays as the first character
    except UnicodeDecodeError:
        return html, []
    notes: list[tuple[str, str]] = []
    edits: list[tuple[int, int, str]] = []
    for el in design_html.scan(text):
        if el.tag != "img":
            continue
        src = el.attrs.get("src", "")
        if not src.startswith(IMAGES + "/"):
            continue
        span = el.attr_span("src")
        if span is None:
            notes.append((src, "write the src in quotes"))
            continue
        try:
            kind, data = load(folder, src[len(IMAGES) + 1:])
        except ImageRefused as e:
            notes.append((src, str(e)))
            continue
        edits.append((span[0], span[1], data_uri(kind, data)))
    for a, b, uri in sorted(edits, reverse=True):
        text = text[:a] + uri + text[b:]
    return text.encode("utf-8"), notes


def _stem(raw: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", Path(raw).stem.lower()).strip("-")[:_STEM_MAX].strip("-")
    return stem or "picture"


def add(folder: Path, source: Path, name: str = "") -> str:
    """Copy a picture into `folder/images/` under a safe lower-case name and return `images/<name>`.
    A taken name is never overwritten: `-2`, `-3`... follow."""
    try:
        st = source.stat()
        if not stat.S_ISREG(st.st_mode):
            raise ImageRefused("not a picture")
        if st.st_size > PICTURE_MAX_BYTES:
            raise ImageRefused("too large")
        with open(source, "rb") as f:
            data = f.read(PICTURE_MAX_BYTES + 1)
    except OSError:
        raise ImageRefused("not readable") from None
    if len(data) > PICTURE_MAX_BYTES:
        raise ImageRefused("too large")
    kind = sniff(data)
    if kind is None:
        raise ImageRefused("not a picture")
    d = folder / IMAGES
    if d.is_symlink():
        raise ImageRefused("images is a link or not a folder")
    if not d.exists():
        d.mkdir()
    d = _images_dir(folder)
    stem, ext = _stem(name or source.name), _EXT[kind]
    n = 1
    while True:
        leaf = f"{stem}.{ext}" if n == 1 else f"{stem}-{n}.{ext}"
        try:
            with open(d / leaf, "xb") as out:  # exclusive: never follows or truncates what is there
                out.write(data)
        except FileExistsError:
            n += 1
            continue
        return f"{IMAGES}/{leaf}"

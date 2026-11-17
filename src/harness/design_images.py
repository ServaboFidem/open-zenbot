"""A design's pictures as files: recognise them by their first bytes, embed them into a copy of the page,
and add one to the design's `images/` folder. Every read and write refuses a link."""
from __future__ import annotations

import base64
import os
import re
import stat
from pathlib import Path
from uuid import uuid4

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
    if not hasattr(os, "O_NOFOLLOW") and p.is_symlink():  # Windows: no O_NOFOLLOW, so look first
        raise ImageRefused("a link or not a regular file")
    try:
        # O_NONBLOCK: a FIFO at the name must not hang the open; fstat below refuses it
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    except FileNotFoundError:
        raise ImageRefused("not found") from None
    except OSError:  # a link at the name is refused here (ELOOP), never followed
        raise ImageRefused("a link or not readable") from None
    with os.fdopen(fd, "rb") as f:  # every check is on the file that was opened
        try:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise ImageRefused("a link or not a regular file")
            if st.st_size > PICTURE_MAX_BYTES:
                raise ImageRefused("too large")
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
    return _put(folder, data, name or source.name)


def write_out(folder: Path, data: bytes, name: str) -> str:
    """Write picture bytes (from a `data:` URI in an older design) into `folder/images/` and return
    `images/<name>`. A file there with the same bytes is reused; other bytes are never overwritten."""
    if len(data) > PICTURE_MAX_BYTES:
        raise ImageRefused("too large")
    return _put(folder, data, name, reuse=True)


def _put(folder: Path, data: bytes, name: str, reuse: bool = False) -> str:
    kind = sniff(data)
    if kind is None:
        raise ImageRefused("not a picture")
    d = folder / IMAGES
    if d.is_symlink():
        raise ImageRefused("images is a link or not a folder")
    try:
        d.mkdir(exist_ok=True)
    except OSError:
        raise ImageRefused("images could not be made") from None
    d = _images_dir(folder)
    stem, ext = _stem(name), _EXT[kind]
    tmp = d / f".{uuid4().hex[:12]}.tmp"
    try:
        # written whole under a temporary name first, so a failed write never leaves part of a picture
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        try:
            _write_all(fd, data)
        finally:
            os.close(fd)
        return _place(folder, d, tmp, stem, ext, data, reuse)
    except OSError:
        raise ImageRefused("the picture could not be written") from None
    finally:
        tmp.unlink(missing_ok=True)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _place(folder: Path, d: Path, tmp: Path, stem: str, ext: str, data: bytes, reuse: bool) -> str:
    """Give the written temporary file the first free name; a taken name is never overwritten."""
    n = 1
    while True:
        leaf = f"{stem}.{ext}" if n == 1 else f"{stem}-{n}.{ext}"
        try:
            os.link(tmp, d / leaf)  # exclusive: fails on anything already at the name, a link included
        except FileExistsError:
            if reuse:
                try:
                    if load(folder, leaf)[1] == data:
                        return f"{IMAGES}/{leaf}"
                except ImageRefused:
                    pass
            n += 1
            continue
        return f"{IMAGES}/{leaf}"


def dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) in pixels from a PNG, GIF, WebP or JPEG header, or None when it cannot be read."""
    kind = sniff(data)
    if kind == "png" and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if kind == "gif" and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if kind == "webp":
        chunk = data[12:16]
        if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
        if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X" and len(data) >= 30:
            return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
        return None
    if kind == "jpeg":
        i = 2
        while i + 9 <= len(data):
            if data[i] != 0xFF:
                return None
            marker = data[i + 1]
            if marker == 0xFF:  # fill byte
                i += 1
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # a start-of-frame
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            if marker == 0x01 or 0xD0 <= marker <= 0xD9:  # no length
                i += 2
                continue
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None

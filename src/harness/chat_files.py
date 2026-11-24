"""Files attached in chat: picture detection and conversion to WebP."""

from __future__ import annotations

import asyncio
import io
import os
import re
import secrets
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps, UnidentifiedImageError

from src.memory.ingest import MARKITDOWN_SUFFIXES, PLAIN_SUFFIXES

REFUSED = "ZenBot can't work with this kind of file yet."
TOO_LARGE = "This picture is too large."

MAX_SIDE = 2048
MAX_PICTURE = 5_000_000
MAX_PICTURE_UPLOAD = 50_000_000
MAX_DOCUMENT = 20_000_000
LOSSY_QUALITY = 90
DOCUMENT_SUFFIXES = PLAIN_SUFFIXES | MARKITDOWN_SUFFIXES


class FileRefused(ValueError):
    """The message is the sentence shown to the user."""


def picture_kind(head: bytes) -> str | None:
    """Kind of picture by first bytes; GIF89a (animated-capable) is not one."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"GIF87a"):
        return "gif87a"
    return None


def _save(im: Image.Image, **options) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "WEBP", exif=b"", **options)
    return buf.getvalue()


def to_webp(raw: bytes) -> bytes:
    """Convert a still picture to WebP, upright and at most MAX_SIDE a side."""
    if picture_kind(raw[:16]) is None:
        raise FileRefused(REFUSED)
    try:
        with Image.open(io.BytesIO(raw)) as im:
            # an explicit check, not a warnings filter: filters are process-wide and this runs in a thread
            if im.width * im.height > (Image.MAX_IMAGE_PIXELS or float("inf")):
                raise FileRefused(REFUSED)
            if getattr(im, "n_frames", 1) > 1:
                raise FileRefused(REFUSED)
            im.load()
            im = ImageOps.exif_transpose(im)
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
            im = im.convert("RGBA" if has_alpha else "RGB")
            out = _save(im, lossless=True)
            if len(out) > MAX_PICTURE:
                out = _save(im, lossless=False, quality=LOSSY_QUALITY)
    except FileRefused:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise FileRefused(REFUSED) from None
    if len(out) > MAX_PICTURE:
        raise FileRefused(TOO_LARGE)
    return out


ID = re.compile(r"[0-9a-f]{32}")
_BAD = re.compile(r"[^A-Za-z0-9._ -]")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True)
class Upload:
    id: str
    name: str
    kind: Literal["image", "document"]
    path: Path
    size: int


def uploads_dir(vault_root: Path) -> Path:
    return vault_root / "uploads" / "chat"


def safe_name(name: str) -> str:
    """A bare file name: no folders, no leading dot, plain characters, at most 100 long."""
    bare = re.split(r"[\\/]", name)[-1]
    bare = _BAD.sub("_", bare).lstrip(". ").strip()
    if len(bare) > 100:
        stem, dot, suffix = bare.rpartition(".")
        bare = (stem[: 100 - len(suffix) - 1] + "." + suffix) if dot and len(suffix) < 20 else bare[:100]
    return bare or "file"


def _suffix(name: str) -> str:
    return Path(name).suffix.lower()


def _image_name(name: str) -> str:
    return Path(safe_name(name)).stem[:100] + ".webp"


async def save(vault_root: Path, name: str, raw: bytes) -> Upload:
    """Store one upload in its own new folder. A picture (by first bytes) becomes WebP; a document by suffix
    stays as it is; anything else is refused."""
    name = safe_name(name)
    if picture_kind(raw[:16]) is not None:
        data = await asyncio.to_thread(to_webp, raw)
        kind, stored = "image", _image_name(name)
    elif _suffix(name) in DOCUMENT_SUFFIXES:
        if len(raw) > MAX_DOCUMENT:
            raise FileRefused("This file is too large.")
        data, kind, stored = raw, "document", name
    else:
        raise FileRefused(REFUSED)
    root = uploads_dir(vault_root)
    root.mkdir(parents=True, exist_ok=True)
    folder = root / secrets.token_hex(16)
    folder.mkdir(exist_ok=False)
    path = folder / stored
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _NOFOLLOW | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return Upload(folder.name, stored, kind, path, len(data))


def _real_dir(p: Path) -> bool:
    try:
        st = p.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode)


def _head(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as f:
        return f.read(16)


def resolve(vault_root: Path, upload_id: str) -> Upload:
    """The stored upload for an id, checked again from disk; raises FileRefused when it is not what was saved."""
    if not isinstance(upload_id, str) or not ID.fullmatch(upload_id):
        raise FileRefused(REFUSED)
    root = uploads_dir(vault_root)
    folder = root / upload_id
    if not (_real_dir(root) and _real_dir(folder)):
        raise FileRefused(REFUSED)
    try:
        entries = list(folder.iterdir())
        if len(entries) != 1:
            raise FileRefused(REFUSED)
        path = entries[0]
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise FileRefused(REFUSED)
        head = _head(path)
    except OSError:
        raise FileRefused(REFUSED) from None
    if _suffix(path.name) == ".webp":
        if picture_kind(head) != "webp":
            raise FileRefused(REFUSED)
        try:
            with Image.open(path) as im:
                if getattr(im, "n_frames", 1) > 1:
                    raise FileRefused(REFUSED)
        except (UnidentifiedImageError, OSError, ValueError):
            raise FileRefused(REFUSED) from None
        kind = "image"
    elif _suffix(path.name) in DOCUMENT_SUFFIXES and picture_kind(head) is None:
        kind = "document"
    else:
        raise FileRefused(REFUSED)
    return Upload(upload_id, path.name, kind, path, st.st_size)


def remove(vault_root: Path, upload_id: str) -> bool:
    if not isinstance(upload_id, str) or not ID.fullmatch(upload_id):
        return False
    root = uploads_dir(vault_root)
    folder = root / upload_id
    if not (_real_dir(root) and _real_dir(folder)):
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


def sweep(vault_root: Path, keep: set[str], now: float, max_age_s: float = 86_400) -> int:
    """Delete upload folders older than `max_age_s` that no conversation names; returns how many."""
    root = uploads_dir(vault_root)
    if not _real_dir(root):
        return 0
    count = 0
    for folder in root.iterdir():
        if not ID.fullmatch(folder.name) or folder.name in keep or not _real_dir(folder):
            continue
        try:
            if now - folder.lstat().st_mtime < max_age_s:
                continue
        except OSError:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        count += 1
    return count

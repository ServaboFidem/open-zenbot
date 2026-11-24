"""Files attached in chat: picture detection and conversion to WebP."""

from __future__ import annotations

import asyncio
import io
import os
import re
import secrets
import shutil
import stat
from contextlib import contextmanager
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
# files a browser would run as a page are never accepted, whatever else reads them
CLOSED_SUFFIXES = (".html", ".htm", ".xhtml", ".xht", ".shtml", ".xml", ".svg", ".svgz", ".mht", ".mhtml")
DOCUMENT_SUFFIXES = (PLAIN_SUFFIXES | MARKITDOWN_SUFFIXES) - set(CLOSED_SUFFIXES)


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
_BIN = getattr(os, "O_BINARY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_DIR_FD = os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd and hasattr(os, "O_DIRECTORY")


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


def _real_dir(p: Path) -> bool:
    try:
        return stat.S_ISDIR(p.lstat().st_mode)
    except OSError:
        return False


@contextmanager
def _chat(vault_root: Path, create: bool = False):
    """The `uploads/chat` folder, checked to be a real folder at each step. Yields a directory descriptor to do
    the work relative to (None where the platform has none, then the checks are by path). Raises FileRefused
    when `uploads` or `chat` is a link or not a folder."""
    up = vault_root / "uploads"
    chat = up / "chat"
    try:
        if create:
            for d in (up, chat):
                if not os.path.lexists(d):
                    d.mkdir(exist_ok=True)
        if not (_real_dir(up) and _real_dir(chat)):
            raise FileRefused(REFUSED)
        if not _DIR_FD:
            yield None
            return
        flags = os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW
        outer = os.open(up, flags)
        try:
            fd = os.open("chat", flags, dir_fd=outer)
        finally:
            os.close(outer)
    except OSError:
        raise FileRefused(REFUSED) from None
    try:
        yield fd
    finally:
        os.close(fd)


def _rm(folder: str, fd: int | None, root: Path) -> None:
    if fd is not None and shutil.rmtree.avoids_symlink_attacks:
        shutil.rmtree(folder, ignore_errors=True, dir_fd=fd)
    else:
        shutil.rmtree(root / folder, ignore_errors=True)


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
    folder = secrets.token_hex(16)
    with _chat(vault_root, create=True) as fd:
        try:
            os.mkdir(folder if fd is not None else root / folder, 0o700, dir_fd=fd)
        except OSError:
            raise FileRefused(REFUSED) from None
        try:
            if fd is not None:
                dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=fd)
                try:
                    out = os.open(stored, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _NOFOLLOW | _BIN, 0o600, dir_fd=dfd)
                finally:
                    os.close(dfd)
            else:
                out = os.open(root / folder / stored, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _NOFOLLOW | _BIN, 0o600)
            with os.fdopen(out, "wb") as f:
                f.write(data)
        except BaseException:
            _rm(folder, fd, root)
            raise
    return Upload(folder, stored, kind, root / folder / stored, len(data))


def read(vault_root: Path, upload_id: str) -> tuple[Upload, bytes]:
    """The stored upload and its bytes, read once: the file is opened without following a link, checked on the
    open descriptor and the bytes it gave, and those same bytes are returned. Raises FileRefused when it is not
    what was saved. Use this, never the path, to serve or send an upload."""
    if not isinstance(upload_id, str) or not ID.fullmatch(upload_id):
        raise FileRefused(REFUSED)
    root = uploads_dir(vault_root)
    try:
        with _chat(vault_root) as fd:
            if fd is not None:
                dfd = os.open(upload_id, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=fd)
                try:
                    names = os.listdir(dfd)
                    if len(names) != 1:
                        raise FileRefused(REFUSED)
                    if not stat.S_ISREG(os.stat(names[0], dir_fd=dfd, follow_symlinks=False).st_mode):
                        raise FileRefused(REFUSED)  # a FIFO would block the open
                    ffd = os.open(names[0], os.O_RDONLY | _NOFOLLOW | _BIN | _NONBLOCK, dir_fd=dfd)
                finally:
                    os.close(dfd)
            else:
                if not _real_dir(root / upload_id):
                    raise FileRefused(REFUSED)
                names = os.listdir(root / upload_id)
                if len(names) != 1:
                    raise FileRefused(REFUSED)
                if not stat.S_ISREG((root / upload_id / names[0]).lstat().st_mode):
                    raise FileRefused(REFUSED)
                ffd = os.open(root / upload_id / names[0], os.O_RDONLY | _NOFOLLOW | _BIN | _NONBLOCK)
    except OSError:
        raise FileRefused(REFUSED) from None
    name = names[0]
    try:
        with os.fdopen(ffd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise FileRefused(REFUSED)
            limit = MAX_PICTURE if _suffix(name) == ".webp" else MAX_DOCUMENT
            data = f.read(limit + 1)
    except OSError:
        raise FileRefused(REFUSED) from None
    if len(data) > limit:
        raise FileRefused(REFUSED)
    head = data[:16]
    if _suffix(name) == ".webp":
        if picture_kind(head) != "webp":
            raise FileRefused(REFUSED)
        try:
            with Image.open(io.BytesIO(data)) as im:
                if getattr(im, "n_frames", 1) > 1:
                    raise FileRefused(REFUSED)
        except (UnidentifiedImageError, OSError, ValueError):
            raise FileRefused(REFUSED) from None
        kind = "image"
    elif _suffix(name) in DOCUMENT_SUFFIXES and picture_kind(head) is None:
        kind = "document"
    else:
        raise FileRefused(REFUSED)
    return Upload(upload_id, name, kind, root / upload_id / name, len(data)), data


def resolve(vault_root: Path, upload_id: str) -> Upload:
    """Like `read`, without the bytes."""
    return read(vault_root, upload_id)[0]


def remove(vault_root: Path, upload_id: str) -> bool:
    """Delete an upload's folder; False when the id is malformed, there is none, or it is still there after."""
    if not isinstance(upload_id, str) or not ID.fullmatch(upload_id):
        return False
    root = uploads_dir(vault_root)
    try:
        with _chat(vault_root) as fd:
            if not _real_dir(root / upload_id):
                return False
            _rm(upload_id, fd, root)
            return not os.path.lexists(root / upload_id)
    except (FileRefused, OSError):
        return False


def sweep(vault_root: Path, keep: set[str], now: float, max_age_s: float = 86_400) -> int:
    """Delete upload folders older than `max_age_s` that no conversation names; returns how many."""
    root = uploads_dir(vault_root)
    count = 0
    try:
        with _chat(vault_root) as fd:
            for name in os.listdir(fd if fd is not None else root):
                if not ID.fullmatch(name) or name in keep or not _real_dir(root / name):
                    continue
                try:
                    if now - (root / name).lstat().st_mtime < max_age_s:
                        continue
                except OSError:
                    continue
                _rm(name, fd, root)
                count += 1
    except (FileRefused, OSError):
        return count
    return count

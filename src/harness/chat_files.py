"""Files attached in chat: picture detection and conversion to WebP."""

from __future__ import annotations

import io
import warnings

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
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as im:
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
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError):
        raise FileRefused(REFUSED) from None
    if len(out) > MAX_PICTURE:
        raise FileRefused(TOO_LARGE)
    return out

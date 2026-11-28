"""The paddleocr server's guard: every call is checked before paddleocr-mcp sees it.

paddleocr-mcp takes `input_data` as an absolute file path, an http(s) URL, raw Base64 or a `data:` URL, and reads
any path it is given that the user's account can read. Left alone it would ignore Settings > Allowed Folders and
read the harness's own settings, ledger and calendar folders. So the server never gets a path from a bot:

- a path is read here, once: it must be absolute, resolve (links followed) inside a folder bots may read (Full
  Access or Read-Only) and outside the private folders, be a regular file with one hard link, at most
  MAX_BYTES, and an image or a PDF by its first bytes. Its bytes go to the server as a `data:` URL. The file is
  opened once, without following a link at the name, and every check after the folder check is made on what was
  opened, which must be the very file (device and inode) the resolved path named, at a path that still resolves
  the same way: a bot that swaps the file for a link between the check and the read (it can write the vault) gets
  a refusal. Swapping a parent folder for a link and back inside that window is not caught (no `dir_fd` walk, as
  in `chat_files`): a known limit.
- an http(s) URL, a `data:` URL and valid Base64 go through as they are: the server reads no file for them
  (`classify_input` tries a URL, then `data:`, then Base64, before it ever tries a path). Any input longer than
  MAX_INPUT (MAX_BYTES as Base64, plus room for a `data:` prefix) is refused before it is decoded.
- the file is read off the event loop (`asyncio.to_thread`), so a slow folder cannot stall the harness.
- anything else is refused, and so are tools and arguments the harness does not know.
- `return_images` is always off: a model gets only the text of an MCP answer (`mcp_client._text_of`).

A URL is fetched by the server itself, as `hound` and `browser` fetch any URL a bot names; this adds no reach."""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import re
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .allowed_folders import Folders

Next = Callable[[str, dict[str, Any]], Awaitable[str]]

TOOLS = {"ocr", "pp_structurev3", "paddleocr_vl"}  # one is listed, chosen by the server's model setting
ARGS = {"input_data", "output_mode", "file_type", "return_images", "runtime_params"}
MAX_BYTES = 20_000_000
MAX_INPUT = 4 * ((MAX_BYTES + 2) // 3) + 100  # MAX_BYTES as Base64, plus a data: prefix
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")  # the server's own test (paddleocr_mcp.utils.is_base64)


class Refused(RuntimeError):
    """A paddleocr call the harness will not pass on; the message is for the bot."""


def file_kind(head: bytes) -> str | None:
    """The MIME type of an image or PDF PaddleOCR reads, by its first bytes; None for anything else."""
    for sig, mime in ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
                      (b"GIF89a", "image/gif"), (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff"),
                      (b"BM", "image/bmp"), (b"%PDF-", "application/pdf")):
        if head.startswith(sig):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _real(p: Path) -> Path:
    try:
        return Path(os.path.realpath(p))
    except (OSError, ValueError):
        return p


def _inside(p: Path, roots: list[Path]) -> bool:
    return any(p == r or p.is_relative_to(r) for r in roots)


def _is_base64(v: str) -> bool:
    if not _BASE64.fullmatch(v):
        return False
    try:
        base64.b64decode(v, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


class OcrGuard:
    """The gate. `folders()` is read on every call, so a change on Settings > Allowed Folders applies at once."""

    def __init__(self, folders: Callable[[], Folders]) -> None:
        self.folders = folders

    async def __call__(self, tool: str, args: dict[str, Any], call: Next) -> str:
        if tool not in TOOLS:
            raise Refused(f"the harness does not know the paddleocr tool {tool!r}, so it does not run it")
        unknown = sorted(set(args) - ARGS)
        if unknown:
            raise Refused(f"unknown arguments: {', '.join(unknown)}")
        raw = args.get("input_data")
        if not isinstance(raw, str) or not raw.strip():
            raise Refused("input_data must be a file path, an http(s) URL, a data: URL or Base64")
        prepared = await asyncio.to_thread(self.input, raw.strip())  # a read of up to 20 MB stays off the loop
        return await call(tool, {**args, "input_data": prepared, "return_images": False})

    def input(self, v: str) -> str:
        """What the server gets for `v`: never a path."""
        if len(v) > MAX_INPUT:
            raise Refused(f"input_data is larger than {MAX_BYTES // 1_000_000} MB")
        if v.startswith(("http://", "https://", "data:")):
            return v
        if _is_base64(v):  # a JPEG's Base64 starts '/9j/', which is also an absolute POSIX path
            return v
        return self.read(v)

    def read(self, raw: str) -> str:
        if raw.startswith("~"):
            raise Refused(f"{raw}: give the full path")
        p = Path(raw)
        if not p.is_absolute():
            raise Refused(f"{raw}: give the full path of the file (relative paths are not read)")
        real = _real(p)
        f = self.folders()
        if _inside(real, [_real(Path(x)) for x in f.private]):
            raise Refused(f"{raw}: that is the harness's own folder; bots never reach it")
        if not _inside(real, [_real(Path(x)) for x in [*f.full, *f.read_only]]):
            raise Refused(f"{raw}: outside the allowed folders (Settings > Allowed Folders)")
        try:
            st = os.stat(real)
            if not stat.S_ISREG(st.st_mode):  # checked before the open, so a FIFO is never opened
                raise Refused(f"{raw}: not a file")
            if not _NOFOLLOW and os.path.islink(real):  # Windows: no O_NOFOLLOW, so look first
                raise Refused(f"{raw}: changed while it was being read")
            try:
                fd = os.open(real, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
            except FileNotFoundError:
                raise
            except OSError:  # a link put at the name since the check is refused here (ELOOP), never followed
                raise Refused(f"{raw}: changed while it was being read") from None
            with os.fdopen(fd, "rb") as fh:  # every check from here on is on the file that was opened
                got = os.fstat(fh.fileno())
                if (got.st_dev, got.st_ino) != (st.st_dev, st.st_ino) or _real(p) != real:
                    raise Refused(f"{raw}: changed while it was being read")
                if not stat.S_ISREG(got.st_mode):
                    raise Refused(f"{raw}: not a file")
                if got.st_nlink > 1:
                    raise Refused(f"{raw}: a file with more than one hard link is not read")
                if got.st_size > MAX_BYTES:
                    raise Refused(f"{raw}: larger than {MAX_BYTES // 1_000_000} MB")
                data = fh.read(MAX_BYTES + 1)
        except FileNotFoundError:
            raise Refused(f"{raw}: no such file") from None
        except OSError as e:
            raise Refused(f"{raw}: cannot be read ({type(e).__name__})") from None
        if len(data) > MAX_BYTES:
            raise Refused(f"{raw}: larger than {MAX_BYTES // 1_000_000} MB")
        mime = file_kind(data[:16])
        if mime is None:
            raise Refused(f"{raw}: not an image or a PDF (PNG, JPEG, GIF, BMP, TIFF, WebP or PDF)")
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

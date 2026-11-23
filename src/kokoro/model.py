"""The Kokoro model files: two pinned downloads kept in `<vault>-models/kokoro/`, a private folder.

The files come from the kokoro-onnx project's GitHub release, pinned here by name, size and SHA-256. GitHub answers a
release asset with a redirect, so redirects are followed by hand, a few, only to https on known hosts. A file is
written to `<name>.part` (opened new, never through a link), hashed as it arrives, and renamed into place only when
its size and hash match. Nothing here puts a path, a URL or text in a ledger entry or an error; the ledger gets the
action, the byte count, whether it worked and an error kind.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from src.vault_settings import models_dir

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str


BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
FILES: tuple[ModelFile, ModelFile] = (
    ModelFile("kokoro-v1.0.onnx", 325_505_369, "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"),
    ModelFile("voices-v1.0.bin", 28_214_398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
)
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MAX_REDIRECTS = 5
MIN_FREE_BYTES = 1_000_000_000
CHUNK = 1024 * 1024
REDIRECTS = (301, 302, 303, 307, 308)


class ModelError(Exception):
    """A download that failed. `kind`: network | refused | source_changed | disk | provider."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _free(path: Path) -> int:
    return shutil.disk_usage(path).free


def _allowed(url: httpx.URL) -> bool:
    """https, one of the named hosts, no user info and the default port (httpx gives None for 443 written out)."""
    return url.scheme == "https" and url.host in ALLOWED_HOSTS and not url.userinfo and url.port in (None, 443)


class KokoroModel:
    def __init__(self, vault_root: Path, *, transport: httpx.AsyncBaseTransport | None = None,
                 free_bytes: Callable[[Path], int] = _free, ledger: Any = None,
                 files: tuple[ModelFile, ModelFile] = FILES) -> None:
        self.dir = models_dir(vault_root) / "kokoro"
        self.files = files
        self.ledger = ledger
        self._transport = transport
        self._free_bytes = free_bytes
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._active = False  # inside download(): a second call while one works returns at once
        self._done = 0
        self._error = ""
        self._verified: dict[str, tuple[int, int]] = {}  # name: (size, mtime_ns) when its hash last matched
        self._clear_parts()  # a part file left by a crash is never trusted

    def path(self, name: str) -> Path:
        return self.dir / name

    def _part(self, f: ModelFile) -> Path:
        return self.dir / f"{f.name}.part"

    def _clear_parts(self) -> None:
        for f in self.files:
            with contextlib.suppress(OSError):
                self._part(f).unlink(missing_ok=True)  # unlinks a link itself, never its target

    def _in_place(self, f: ModelFile) -> bool:
        p = self.path(f.name)
        try:
            return p.is_file() and not p.is_symlink() and p.stat().st_size == f.size
        except OSError:
            return False

    def ready(self) -> bool:
        return all(self._in_place(f) for f in self.files)

    @property
    def total(self) -> int:
        return sum(f.size for f in self.files)

    def status(self) -> dict[str, Any]:
        if self.ready():
            return {"state": "ready", "done": self.total, "total": self.total, "error": ""}
        if self._running:
            return {"state": "downloading", "done": self._done, "total": self.total, "error": ""}
        if self._error:
            return {"state": "failed", "done": 0, "total": self.total, "error": self._error}
        return {"state": "absent", "done": 0, "total": self.total, "error": ""}

    def start_download(self) -> bool:
        if self._running or self.ready():
            return False
        self._running = True  # set here so a second call before the task first runs sees it
        self._error = ""
        self._done = 0
        self._task = asyncio.create_task(self.download())
        return True

    async def download(self) -> None:
        """Fetch what is missing. Never raises: the outcome is the state and one ledger entry."""
        if self._active:
            return
        self._active = True
        self._running = True
        self._error = ""
        self._done = 0
        error = ""
        try:
            await self._fetch_all()
        except ModelError as e:
            error = e.kind
        except OSError:
            error = "disk"
        except httpx.HTTPError:
            error = "network"
        except Exception as e:  # noqa: BLE001 - never raise; class name only, the frames may hold more
            log.warning("kokoro download failed: %s", type(e).__name__)
            error = "provider"
        finally:
            self._active = False
            self._running = False
            self._clear_parts()
        self._error = error
        self._log(action="download", bytes=self._done, ok=not error, error=error)

    async def _fetch_all(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ModelError("disk") from e
        todo = []
        for f in self.files:
            if self._in_place(f) and await self._hash_ok(f):
                self._done += f.size
            else:
                todo.append(f)
        if not todo:
            return
        try:
            free = self._free_bytes(self.dir)
        except OSError as e:
            raise ModelError("disk") from e
        if free < MIN_FREE_BYTES:
            raise ModelError("disk")
        async with httpx.AsyncClient(transport=self._transport, follow_redirects=False, trust_env=False,
                                     timeout=httpx.Timeout(30.0)) as client:
            for f in todo:
                await self._fetch(client, f)

    def _seen(self, f: ModelFile) -> tuple[int, int] | None:
        """The file's size and mtime as it stands, or None when it is not a regular file (a link is not one)."""
        try:
            st = self.path(f.name).lstat()
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns) if stat.S_ISREG(st.st_mode) else None

    def _remember(self, f: ModelFile) -> None:
        seen = self._seen(f)
        if seen is not None:
            self._verified[f.name] = seen

    async def _hash_ok(self, f: ModelFile) -> bool:
        """Whether the file's bytes match the pin. Hashed again whenever its size or mtime is not what they were when
        it last matched; remembered only when the file did not change while it was hashed."""
        seen = self._seen(f)
        if seen is not None and self._verified.get(f.name) == seen:
            return True
        self._verified.pop(f.name, None)
        if seen is None or await asyncio.to_thread(file_sha256, self.path(f.name)) != f.sha256:
            return False
        if self._seen(f) == seen:
            self._verified[f.name] = seen
        return True

    async def _fetch(self, client: httpx.AsyncClient, f: ModelFile) -> None:
        url = httpx.URL(BASE_URL + f.name)
        hops = 0
        while True:
            async with client.stream("GET", url) as r:
                if r.status_code in REDIRECTS:
                    location = r.headers.get("location")
                    if not location or hops >= MAX_REDIRECTS:
                        raise ModelError("source_changed")
                    try:
                        url = httpx.URL(urljoin(str(url), location))
                    except (httpx.InvalidURL, ValueError) as e:
                        raise ModelError("source_changed") from e
                    if not _allowed(url):
                        raise ModelError("source_changed")
                    hops += 1
                    continue
                if r.status_code in (403, 429):
                    raise ModelError("refused")
                if 300 <= r.status_code < 400:
                    raise ModelError("source_changed")
                if r.status_code != 200:
                    raise ModelError("provider")
                await self._write(r, f)
                return

    async def _write(self, r: httpx.Response, f: ModelFile) -> None:
        part = self._part(f)
        part.unlink(missing_ok=True)  # a stale one, or a link: removed itself, never written through
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        digest = hashlib.sha256()
        size = 0
        base = self._done
        with os.fdopen(os.open(part, flags, 0o644), "wb") as out:
            async for chunk in r.aiter_bytes(CHUNK):
                size += len(chunk)
                if size > f.size:
                    raise ModelError("source_changed")
                digest.update(chunk)
                out.write(chunk)
                self._done = base + size
        if size != f.size or digest.hexdigest() != f.sha256:
            raise ModelError("source_changed")
        os.replace(part, self.path(f.name))
        self._remember(f)  # hashed as it arrived

    async def verify(self) -> bool:
        """SHA-256 of both files, once per process while a file's size and mtime stay what they were when it matched.
        A file that does not match is deleted."""
        ok = True
        for f in self.files:
            if not self._in_place(f):
                self._verified.pop(f.name, None)
                ok = False
                continue
            if not await self._hash_ok(f):
                with contextlib.suppress(OSError):
                    self.path(f.name).unlink()
                ok = False
        return ok

    async def remove(self) -> bool:
        """Delete the files. True only when none remains; the ledger entry says the same."""
        for f in self.files:
            self._verified.pop(f.name, None)
            with contextlib.suppress(OSError):
                self.path(f.name).unlink(missing_ok=True)
        self._clear_parts()
        left = any(self.path(f.name).exists() for f in self.files)
        if not left:
            self._error = ""
        self._log(action="remove", bytes=0, ok=not left, error="disk" if left else "")
        return not left

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._running = False
        self._clear_parts()

    def _log(self, **data: Any) -> None:
        if self.ledger is not None:
            from src.memory.ledger import Kind  # local, as generation/speech.py does
            self.ledger.append(Kind.speech, "", "", event="model", **data)

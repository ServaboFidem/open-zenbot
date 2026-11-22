"""The Kokoro model files: download, checks, verify, remove. The network is an httpx.MockTransport."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest

from src.kokoro import model as km
from src.kokoro.model import KokoroModel, ModelFile
from src.memory.ledger import Kind

A = b"onnx-bytes-" * 50
B = b"voices-bytes-" * 20
GITHUB = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
CDN = "https://objects.githubusercontent.com/blob/"
BIG = lambda p: 10_000_000_000  # noqa: E731


def tiny_files() -> tuple[ModelFile, ModelFile]:
    return (ModelFile("a.onnx", len(A), hashlib.sha256(A).hexdigest()),
            ModelFile("b.bin", len(B), hashlib.sha256(B).hexdigest()))


class Site:
    """GitHub's release URL, optionally answering with a chain of redirects, then the file asked for."""

    def __init__(self, bodies: dict[str, bytes], hops: tuple[str, ...] = (), status: int = 200) -> None:
        self.bodies, self.hops, self.status = bodies, hops, status
        self.requests: list[httpx.Request] = []
        self.wanted = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(GITHUB):
            self.wanted = url.rsplit("/", 1)[-1]
            if self.hops:
                return httpx.Response(302, headers={"location": self.hops[0]})
        elif url in self.hops:
            i = self.hops.index(url)
            if i + 1 < len(self.hops):
                return httpx.Response(302, headers={"location": self.hops[i + 1]})
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, content=self.bodies[self.wanted])


def site(bodies=None, *, hops=(), status=200) -> Site:
    return Site(bodies or {"a.onnx": A, "b.bin": B}, hops, status)


def build(tmp_path: Path, s: Site | None = None, **kw) -> tuple[KokoroModel, Site]:
    s = s or site()
    kw.setdefault("free_bytes", BIG)
    kw.setdefault("files", tiny_files())
    return KokoroModel(tmp_path / "vault", transport=httpx.MockTransport(s), **kw), s


async def test_download_puts_both_files_in_place_and_is_ready(tmp_path: Path):
    m, s = build(tmp_path)
    assert m.status()["state"] == "absent" and not m.ready()
    await m.download()
    assert m.ready() and m.status() == {"state": "ready", "done": len(A) + len(B), "total": len(A) + len(B), "error": ""}
    assert m.path("a.onnx").read_bytes() == A and m.path("b.bin").read_bytes() == B
    assert not list(m.dir.glob("*.part"))
    assert m.dir == tmp_path / "vault-models" / "kokoro"


async def test_a_redirect_to_an_allowed_host_is_followed(tmp_path: Path):
    s = site(hops=(CDN + "x",))
    m, _ = build(tmp_path, s)
    await m.download()
    assert m.ready()
    assert any(str(r.url).startswith(CDN) for r in s.requests)


@pytest.mark.parametrize("bad", ["http://github.com/x", "https://example.com/x", "https://user:pw@github.com/x"])
async def test_a_redirect_elsewhere_is_source_changed(tmp_path: Path, bad: str):
    s = site(hops=(bad,))
    m, _ = build(tmp_path, s)
    await m.download()
    assert m.status()["state"] == "failed" and m.status()["error"] == "source_changed"
    assert len(s.requests) == 1


async def test_a_sixth_hop_is_source_changed(tmp_path: Path):
    s = site(hops=tuple(f"{CDN}h{i}" for i in range(6)))
    m, _ = build(tmp_path, s)
    await m.download()
    assert m.status()["error"] == "source_changed"
    assert len(s.requests) == 6  # the first request and five hops, never a seventh


async def test_a_wrong_hash_is_source_changed_and_leaves_no_file(tmp_path: Path):
    flipped = bytes([A[0] ^ 1]) + A[1:]
    m, _ = build(tmp_path, site({"a.onnx": flipped, "b.bin": B}))
    await m.download()
    assert m.status()["error"] == "source_changed"
    assert list(m.dir.iterdir()) == []


async def test_a_body_longer_than_the_pin_stops_at_the_pin(tmp_path: Path):
    m, _ = build(tmp_path, site({"a.onnx": A + b"extra", "b.bin": B}))
    await m.download()
    assert m.status()["error"] == "source_changed"
    assert list(m.dir.iterdir()) == []
    assert m._done <= len(A)


async def test_a_short_body_is_source_changed(tmp_path: Path):
    m, _ = build(tmp_path, site({"a.onnx": A[:-1], "b.bin": B}))
    await m.download()
    assert m.status()["error"] == "source_changed"
    assert list(m.dir.iterdir()) == []


@pytest.mark.parametrize("code", [403, 429])
async def test_403_and_429_are_refused(tmp_path: Path, code: int):
    m, _ = build(tmp_path, site(status=code))
    await m.download()
    assert m.status()["error"] == "refused"


async def test_500_is_provider(tmp_path: Path):
    m, _ = build(tmp_path, site(status=500))
    await m.download()
    assert m.status()["error"] == "provider"


async def test_a_connect_error_is_network(tmp_path: Path):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    m = KokoroModel(tmp_path / "vault", transport=httpx.MockTransport(boom), free_bytes=BIG, files=tiny_files())
    await m.download()
    assert m.status()["state"] == "failed" and m.status()["error"] == "network"


async def test_under_one_gb_free_is_disk_and_nothing_is_requested(tmp_path: Path):
    m, s = build(tmp_path, free_bytes=lambda p: 999_999_999)
    await m.download()
    assert m.status()["error"] == "disk"
    assert s.requests == []


async def test_a_failure_is_cleared_by_the_next_start(tmp_path: Path):
    m, s = build(tmp_path, free_bytes=lambda p: 1)
    await m.download()
    assert m.status()["state"] == "failed"
    m._free_bytes = BIG
    assert m.start_download()
    await m._task
    assert m.ready()


async def test_a_file_already_right_is_not_fetched_again(tmp_path: Path):
    m, s = build(tmp_path)
    m.dir.mkdir(parents=True)
    m.path("a.onnx").write_bytes(A)
    await m.download()
    assert m.ready()
    assert [r.url.path.rsplit("/", 1)[-1] for r in s.requests] == ["b.bin"]


async def test_a_part_file_that_is_a_link_is_not_written_through(tmp_path: Path):
    m, s = build(tmp_path)
    m.dir.mkdir(parents=True)
    target = tmp_path / "victim.txt"
    target.write_text("keep")
    try:
        (m.dir / "a.onnx.part").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("links cannot be made here")
    await m.download()
    assert target.read_text() == "keep"
    assert m.ready()


async def test_start_download_twice_runs_once(tmp_path: Path):
    m, s = build(tmp_path)
    assert m.start_download() is True
    assert m.start_download() is False
    assert m.status()["state"] == "downloading"
    await m._task
    assert m.ready() and len(s.requests) == 2
    assert m.start_download() is False  # ready


async def test_close_cancels_and_removes_the_part_file(tmp_path: Path):
    started = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield A[:10]
                started.set()
                await asyncio.sleep(60)

        return httpx.Response(200, stream=Body())

    m = KokoroModel(tmp_path / "vault", transport=httpx.MockTransport(slow), free_bytes=BIG, files=tiny_files())
    m.start_download()
    await asyncio.wait_for(started.wait(), 5)
    assert (m.dir / "a.onnx.part").exists()
    await m.close()
    assert not list(m.dir.glob("*.part"))
    assert m.status()["state"] == "absent"


async def test_a_stale_part_file_is_removed_when_the_model_is_built(tmp_path: Path):
    d = tmp_path / "vault-models" / "kokoro"
    d.mkdir(parents=True)
    (d / "a.onnx.part").write_bytes(b"half")
    KokoroModel(tmp_path / "vault", files=tiny_files())
    assert not (d / "a.onnx.part").exists()


async def test_status_counts_bytes_as_they_arrive(tmp_path: Path):
    seen: list[int] = []
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        body = A if request.url.path.endswith("a.onnx") else B

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                half = len(body) // 2
                yield body[:half]
                seen.append(m.status()["done"])
                gate.set()
                yield body[half:]

        return httpx.Response(200, stream=Body())

    m = KokoroModel(tmp_path / "vault", transport=httpx.MockTransport(handler), free_bytes=BIG, files=tiny_files())
    monkey = km.CHUNK
    km.CHUNK = 7
    try:
        await m.download()
    finally:
        km.CHUNK = monkey
    assert m.status()["total"] == len(A) + len(B)
    assert seen[0] > 0 and seen[0] < len(A)  # grew before the first file ended
    assert seen[1] > len(A)  # and again during the second


async def test_verify_deletes_a_file_whose_bytes_changed_and_the_state_is_absent(tmp_path: Path):
    m, _ = build(tmp_path)
    await m.download()
    m2 = KokoroModel(tmp_path / "vault", free_bytes=BIG, files=tiny_files())  # a new process
    m2.path("a.onnx").write_bytes(b"x" * len(A))  # same size, other bytes
    assert m2.ready()
    assert await m2.verify() is False
    assert not m2.path("a.onnx").exists()
    assert m2.status()["state"] == "absent"


async def test_verify_hashes_once_per_process(tmp_path: Path, monkeypatch):
    m, _ = build(tmp_path)
    await m.download()
    m2 = KokoroModel(tmp_path / "vault", files=tiny_files())
    calls = []
    real = km.file_sha256
    monkeypatch.setattr(km, "file_sha256", lambda p: calls.append(p) or real(p))
    assert await m2.verify() is True
    assert await m2.verify() is True
    assert len(calls) == 2  # once for each file, none the second time


async def test_remove_deletes_everything_and_the_state_is_absent(tmp_path: Path):
    m, _ = build(tmp_path)
    await m.download()
    (m.dir / "a.onnx.part").write_bytes(b"x")
    await m.remove()
    assert list(m.dir.iterdir()) == []
    assert m.status()["state"] == "absent"


async def test_the_ledger_entry_has_action_bytes_ok_and_error_only(tmp_path: Path):
    class Ledger:
        def __init__(self) -> None:
            self.entries: list[tuple] = []

        def append(self, kind, bot_id, session_id, **data):
            self.entries.append((kind, data))

    led = Ledger()
    m, _ = build(tmp_path, ledger=led)
    await m.download()
    assert led.entries == [(Kind.speech, {"event": "model", "action": "download", "bytes": len(A) + len(B),
                                          "ok": True, "error": ""})]
    bad, _ = build(tmp_path / "x", site(status=500), ledger=led)
    await bad.download()
    kind, data = led.entries[-1]
    assert data["ok"] is False and data["error"] == "provider"
    assert "http" not in repr(data) and str(tmp_path) not in repr(data)

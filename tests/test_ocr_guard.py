"""The paddleocr server's gate (src/harness/ocr_guard.py): paddleocr-mcp reads any path it is given, so the gate
reads a path itself against Settings > Allowed Folders and hands the server only bytes, a URL or Base64."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.harness.allowed_folders import Folders
from src.harness.ocr_guard import MAX_BYTES, OcrGuard, Refused, file_kind

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 56
PDF = b"%PDF-1.7\n" + b"\x00" * 40


class Server:
    """Stands in for paddleocr-mcp: records what reaches it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, args: dict[str, Any]) -> str:
        self.calls.append((tool, args))
        return "text"


@pytest.fixture
def w(tmp_path: Path):
    vault, ref, settings, outside = tmp_path / "vault", tmp_path / "ref", tmp_path / "vault-settings", tmp_path / "out"
    for d in (vault, ref, settings, outside):
        d.mkdir()
    (vault / "a.png").write_bytes(PNG)
    (ref / "b.pdf").write_bytes(PDF)
    (settings / "keys.png").write_bytes(PNG)
    (outside / "c.png").write_bytes(PNG)
    (vault / "notes.txt").write_text("not an image", encoding="utf-8")
    folders = Folders(full=[vault], read_only=[ref], private=[settings])
    return SimpleNamespace(guard=OcrGuard(lambda: folders), server=Server(), vault=vault, ref=ref,
                           settings=settings, outside=outside)


async def test_a_path_is_read_by_the_gate_and_sent_as_bytes(w):
    assert await w.guard("ocr", {"input_data": str(w.vault / "a.png"), "return_images": True}, w.server) == "text"
    tool, args = w.server.calls[0]
    assert tool == "ocr" and args["input_data"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert args["return_images"] is False  # a model gets only an answer's text
    await w.guard("pp_structurev3", {"input_data": f"  {w.ref / 'b.pdf'}  "}, w.server)  # Read-Only folders read
    assert w.server.calls[1][1]["input_data"].startswith("data:application/pdf;base64,")


@pytest.mark.parametrize("where", ["settings", "outside"])
async def test_a_path_outside_the_allowed_folders_or_in_a_private_one_is_refused(w, where):
    p = w.settings / "keys.png" if where == "settings" else w.outside / "c.png"
    with pytest.raises(Refused):
        await w.guard("ocr", {"input_data": str(p)}, w.server)
    assert w.server.calls == []


def test_a_link_out_of_the_allowed_folders_is_followed_and_refused(w):
    link = w.vault / "link.png"
    try:
        link.symlink_to(w.settings / "keys.png")
    except OSError:
        pytest.skip("symlinks need extra rights here")
    with pytest.raises(Refused, match="harness's own folder"):
        w.guard.input(str(link))


def test_a_hard_link_is_refused(w):
    try:
        os.link(w.vault / "a.png", w.vault / "twin.png")
    except OSError:
        pytest.skip("no hard links here")
    with pytest.raises(Refused, match="hard link"):
        w.guard.input(str(w.vault / "a.png"))


@pytest.mark.parametrize("raw", ["a.png", "~/a.png", "not a thing at all"])
def test_relative_paths_home_and_junk_are_refused(w, raw):
    with pytest.raises(Refused):
        w.guard.input(raw)


def test_only_images_and_pdfs_of_a_bounded_size_are_read(w):
    with pytest.raises(Refused, match="not an image"):
        w.guard.input(str(w.vault / "notes.txt"))
    with pytest.raises(Refused, match="no such file"):
        w.guard.input(str(w.vault / "gone.png"))
    with pytest.raises(Refused, match="not a file"):
        w.guard.input(str(w.vault))
    big = w.vault / "big.png"
    big.write_bytes(PNG + b"\x00" * MAX_BYTES)
    with pytest.raises(Refused, match="larger"):
        w.guard.input(str(big))


def test_urls_data_urls_and_base64_go_through_unchanged(w):
    jpeg64 = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 30).decode()
    assert jpeg64.startswith("/9j/")  # also an absolute POSIX path: still Base64 to the server, never read
    for v in ["https://example.com/a.png", "http://example.com/a.pdf", "data:image/png;base64,iVBORw0KGgo=", jpeg64]:
        assert w.guard.input(v) == v


async def test_unknown_tools_and_arguments_and_empty_input_are_refused(w):
    with pytest.raises(Refused, match="does not know"):
        await w.guard("shell", {"input_data": "https://example.com/a.png"}, w.server)
    with pytest.raises(Refused, match="unknown arguments"):
        await w.guard("ocr", {"input_data": "https://example.com/a.png", "path": "/etc"}, w.server)
    for bad in ["", "   ", None, 5]:
        with pytest.raises(Refused):
            await w.guard("ocr", {"input_data": bad}, w.server)
    assert w.server.calls == []


def test_file_kind_knows_the_formats_paddleocr_reads():
    assert file_kind(PNG) == "image/png" and file_kind(PDF) == "application/pdf"
    assert file_kind(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp" and file_kind(b"GIF89a") == "image/gif"
    assert file_kind(b"<svg") is None and file_kind(b"") is None


def test_a_file_swapped_between_the_check_and_the_read_is_refused(w, monkeypatch):
    # A bot can write the vault, so it can replace the checked file before the guard opens it.
    from src.harness import ocr_guard

    real_stat = os.stat
    other = w.vault / "other.png"
    other.write_bytes(PNG + b"swapped")

    def stat_then_swap(path, *a, **k):
        st = real_stat(path, *a, **k)
        if Path(path).name == "a.png":
            os.replace(other, w.vault / "a.png")  # a different file (inode) now holds the name
        return st

    monkeypatch.setattr(ocr_guard.os, "stat", stat_then_swap)
    with pytest.raises(Refused, match="changed while it was being read"):
        w.guard.input(str(w.vault / "a.png"))


def test_a_link_put_at_the_name_after_the_check_is_not_followed(w, monkeypatch):
    from src.harness import ocr_guard

    probe = w.vault / "probe.png"
    try:
        probe.symlink_to(w.settings / "keys.png")
    except OSError:
        pytest.skip("symlinks need extra rights here")
    probe.unlink()
    real_stat = os.stat

    def stat_then_link(path, *a, **k):
        st = real_stat(path, *a, **k)
        if Path(path).name == "a.png":
            (w.vault / "a.png").unlink()
            (w.vault / "a.png").symlink_to(w.settings / "keys.png")
        return st

    monkeypatch.setattr(ocr_guard.os, "stat", stat_then_link)
    with pytest.raises(Refused, match="changed while it was being read"):
        w.guard.input(str(w.vault / "a.png"))


def test_input_over_the_limit_is_refused_before_it_is_decoded(w, monkeypatch):
    from src.harness import ocr_guard

    # the real limit fits a whole MAX_BYTES file as a data: URL
    assert ocr_guard.MAX_INPUT >= len("data:image/png;base64,") + len(base64.b64encode(bytes(MAX_BYTES)))
    monkeypatch.setattr(ocr_guard, "MAX_INPUT", 64)
    monkeypatch.setattr(ocr_guard, "_is_base64", lambda v: pytest.fail("decoded an input over the limit"))
    for v in ["A" * 68, "data:image/png;base64," + "A" * 64, "https://example.com/" + "a" * 64]:
        with pytest.raises(Refused, match="larger than"):
            w.guard.input(v)


async def test_the_file_is_read_off_the_event_loop(w, monkeypatch):
    import threading

    seen: list[int] = []
    real_input = w.guard.input
    monkeypatch.setattr(w.guard, "input", lambda v: (seen.append(threading.get_ident()), real_input(v))[1])
    await w.guard("ocr", {"input_data": str(w.vault / "a.png")}, w.server)
    assert seen and seen[0] != threading.get_ident()

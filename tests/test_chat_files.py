import io
import os

import pytest
from PIL import Image

from src.harness import chat_files
from src.harness.chat_files import REFUSED, FileRefused, picture_kind, to_webp


def _save(im, fmt, **kw):
    buf = io.BytesIO()
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def _open(data):
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def test_png_jpeg_webp_gif87a_are_pictures():
    im = Image.new("RGB", (8, 8), "red")
    assert picture_kind(_save(im, "PNG")[:16]) == "png"
    assert picture_kind(_save(im, "JPEG")[:16]) == "jpeg"
    assert picture_kind(_save(im, "WEBP")[:16]) == "webp"
    assert picture_kind(b"GIF87a" + b"\0" * 10) == "gif87a"


def test_gif89a_is_not_a_picture():
    assert picture_kind(b"GIF89a" + b"\0" * 10) is None
    gif = _save(Image.new("P", (8, 8)), "GIF", comment=b"x")
    assert gif[:6] == b"GIF89a"
    with pytest.raises(FileRefused) as e:
        to_webp(gif)
    assert str(e.value) == REFUSED


def test_html_named_png_is_refused():
    with pytest.raises(FileRefused) as e:
        to_webp(b"<html><body>hi</body></html>")
    assert str(e.value) == REFUSED


def test_animated_webp_and_apng_are_refused():
    a, b = Image.new("RGB", (16, 16), "red"), Image.new("RGB", (16, 16), "blue")
    webp = _save(a, "WEBP", save_all=True, append_images=[b])
    apng = _save(a, "PNG", save_all=True, append_images=[b])
    for raw in (webp, apng):
        with pytest.raises(FileRefused):
            to_webp(raw)


def test_large_picture_fits_2048_long_side():
    out = to_webp(_save(Image.new("RGB", (4000, 1000), "green"), "PNG"))
    im = _open(out)
    assert im.format == "WEBP"
    assert im.size == (2048, 512)


def test_small_picture_is_never_scaled_up():
    out = to_webp(_save(Image.new("RGB", (300, 200), "green"), "PNG"))
    assert _open(out).size == (300, 200)


def test_exif_rotation_is_applied():
    exif = Image.Exif()
    exif[0x0112] = 6
    raw = _save(Image.new("RGB", (400, 200), "blue"), "JPEG", exif=exif)
    assert _open(to_webp(raw)).size == (200, 400)


def test_result_is_lossless_and_has_no_exif():
    src = Image.new("RGB", (64, 64), (10, 200, 30))
    out = _open(to_webp(_save(src, "PNG")))
    assert out.convert("RGB").tobytes() == src.tobytes()
    assert "exif" not in out.info


def test_noisy_picture_over_cap_goes_lossy(monkeypatch):
    monkeypatch.setattr(chat_files, "MAX_PICTURE", 200_000)
    noise = Image.frombytes("RGB", (128, 128), os.urandom(128 * 128 * 3)).resize((512, 512), Image.BILINEAR)
    calls = []
    real = Image.Image.save

    def spy(self, *a, **kw):
        calls.append(kw)
        return real(self, *a, **kw)

    monkeypatch.setattr(Image.Image, "save", spy)
    out = to_webp(_save(noise, "PNG"))
    assert _open(out).format == "WEBP"
    assert len(out) <= 200_000
    assert any(c.get("lossless") is False and c.get("quality") == 90 for c in calls)


def test_decompression_bomb_is_refused(monkeypatch):
    raw = _save(Image.new("RGB", (20, 20), "red"), "PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(FileRefused):
        to_webp(raw)


def test_too_large_after_lossy_is_refused(monkeypatch):
    import random
    rnd = random.Random(1)
    im = Image.frombytes("RGB", (64, 64), bytes(rnd.randrange(256) for _ in range(64 * 64 * 3)))
    monkeypatch.setattr(chat_files, "MAX_PICTURE", 100)
    with pytest.raises(FileRefused) as e:
        to_webp(_save(im, "PNG"))
    assert str(e.value) == chat_files.TOO_LARGE


# --- storage ---------------------------------------------------------------------------------------------------

import asyncio
import time


def _png(size=(40, 30)):
    return _save(Image.new("RGB", size, "blue"), "PNG")


def test_save_converts_a_picture_to_webp_in_its_own_folder(vault):
    up = asyncio.run(chat_files.save(vault, "photo.png", _png()))
    assert up.kind == "image" and up.name == "photo.webp"
    assert up.path == vault / "uploads" / "chat" / up.id / "photo.webp"
    assert chat_files.ID.fullmatch(up.id) and up.path.read_bytes()[8:12] == b"WEBP"


def test_save_keeps_a_pdf_as_is(vault):
    up = asyncio.run(chat_files.save(vault, "a.pdf", b"%PDF-1.4 hi"))
    assert (up.kind, up.name, up.path.read_bytes()) == ("document", "a.pdf", b"%PDF-1.4 hi")


@pytest.mark.parametrize("name,raw", [("a.mp3", b"ID3\x03"), ("a.svg", b"<svg/>")])
def test_save_refuses_an_mp3_and_an_svg(vault, name, raw):
    with pytest.raises(FileRefused) as e:
        asyncio.run(chat_files.save(vault, name, raw))
    assert str(e.value) == REFUSED


def test_safe_name_strips_paths_and_dots():
    out = chat_files.safe_name("../a/.x.png")
    assert out == "x.png" and "/" not in out and not out.startswith(".")
    assert chat_files.safe_name("..\\..\\") == "file"
    assert len(chat_files.safe_name("a" * 300 + ".txt")) <= 100


def test_resolve_refuses_a_bad_id_a_link_and_a_second_file(vault):
    up = asyncio.run(chat_files.save(vault, "a.txt", b"hello"))
    assert chat_files.resolve(vault, up.id).kind == "document"
    for bad in ("../x", "A" * 32, "nope"):
        with pytest.raises(FileRefused):
            chat_files.resolve(vault, bad)
    (up.path.parent / "b.txt").write_bytes(b"x")
    with pytest.raises(FileRefused):
        chat_files.resolve(vault, up.id)
    (up.path.parent / "b.txt").unlink()
    link = vault / "uploads" / "chat" / ("1" * 32)
    link.symlink_to(up.path.parent, target_is_directory=True)
    with pytest.raises(FileRefused):
        chat_files.resolve(vault, "1" * 32)
    other = vault / "outside.txt"
    other.write_text("x")
    up.path.unlink()
    up.path.symlink_to(other)
    with pytest.raises(FileRefused):
        chat_files.resolve(vault, up.id)


def test_resolve_refuses_a_file_swapped_for_html(vault):
    up = asyncio.run(chat_files.save(vault, "p.png", _png()))
    up.path.write_bytes(b"<html><script>alert(1)</script>")
    with pytest.raises(FileRefused):
        chat_files.resolve(vault, up.id)


def test_sweep_spares_kept_and_young_uploads(vault):
    ups = [asyncio.run(chat_files.save(vault, "a.txt", b"x")) for _ in range(3)]
    old, kept, young = ups
    now = time.time()
    for u in (old, kept):
        os.utime(u.path.parent, (now - 90_000, now - 90_000))
    assert chat_files.sweep(vault, {kept.id}, now) == 1
    assert not old.path.parent.exists() and kept.path.parent.exists() and young.path.parent.exists()


# --- fix round 1 -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["page.html", "page.htm", "data.xml"])
def test_page_types_are_never_accepted(vault, name):
    with pytest.raises(FileRefused):
        asyncio.run(chat_files.save(vault, name, b"<p>hi</p>"))
    folder = vault / "uploads" / "chat" / ("2" * 32)
    folder.mkdir(parents=True)
    (folder / name).write_bytes(b"<p>hi</p>")
    with pytest.raises(FileRefused):
        chat_files.resolve(vault, "2" * 32)


def test_save_refuses_gif89a_and_two_frame_pictures(vault):
    with pytest.raises(FileRefused):
        asyncio.run(chat_files.save(vault, "a.gif", b"GIF89a" + b"\0" * 20))
    a, b = Image.new("RGB", (8, 8), "red"), Image.new("RGB", (8, 8), "blue")
    buf = io.BytesIO()
    a.save(buf, "WEBP", save_all=True, append_images=[b], lossless=True)
    with pytest.raises(FileRefused):
        asyncio.run(chat_files.save(vault, "anim.webp", buf.getvalue()))


def test_read_returns_the_bytes_it_checked_and_refuses_a_swapped_link(vault):
    up = asyncio.run(chat_files.save(vault, "a.txt", b"hello"))
    assert chat_files.read(vault, up.id)[1] == b"hello"
    secret = vault.parent / "secret.txt"
    secret.write_text("TOP")
    up.path.unlink()
    up.path.symlink_to(secret)
    with pytest.raises(FileRefused):
        chat_files.read(vault, up.id)


def test_a_linked_uploads_folder_is_refused_and_never_swept_through(vault, tmp_path):
    outside = tmp_path / "elsewhere"
    victim = outside / "chat" / ("3" * 32)
    victim.mkdir(parents=True)
    (vault / "uploads").symlink_to(outside, target_is_directory=True)
    with pytest.raises(FileRefused):
        asyncio.run(chat_files.save(vault, "a.txt", b"x"))
    os.utime(victim, (0, 0))
    assert chat_files.sweep(vault, set(), time.time()) == 0
    assert chat_files.remove(vault, "3" * 32) is False
    assert victim.exists()
    with pytest.raises(FileRefused):
        chat_files.read(vault, "3" * 32)


def test_a_linked_chat_folder_is_refused(vault, tmp_path):
    (vault / "uploads").mkdir()
    (vault / "uploads" / "chat").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(FileRefused):
        asyncio.run(chat_files.save(vault, "a.txt", b"x"))


def test_remove_is_false_for_unknown_and_malformed_ids(vault):
    assert chat_files.remove(vault, "../x") is False
    assert chat_files.remove(vault, "4" * 32) is False


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX fifos")
def test_a_fifo_as_the_entry_is_refused_without_blocking(vault):
    folder = vault / "uploads" / "chat" / ("6" * 32)
    folder.mkdir(parents=True)
    os.mkfifo(folder / "a.txt")
    with pytest.raises(FileRefused):
        chat_files.read(vault, "6" * 32)


def test_a_vanished_upload_has_its_own_sentence(vault):
    with pytest.raises(FileRefused) as e:  # no uploads folder at all yet
        chat_files.read(vault, "5" * 32)
    assert str(e.value) == chat_files.MISSING == "That file is no longer there. Attach it again."
    up = asyncio.run(chat_files.save(vault, "a.txt", b"hello"))
    with pytest.raises(FileRefused) as e:  # the folder is there, the id's is not (swept)
        chat_files.read(vault, "6" * 32)
    assert str(e.value) == chat_files.MISSING
    up.path.unlink()
    with pytest.raises(FileRefused) as e:  # the id's folder is there, its file is gone
        chat_files.read(vault, up.id)
    assert str(e.value) == chat_files.MISSING
    with pytest.raises(FileRefused) as e:  # a malformed id is still refused as before
        chat_files.read(vault, "../x")
    assert str(e.value) == REFUSED


def test_a_16_bit_greyscale_png_keeps_its_greys():
    im = Image.new("I;16", (4, 4))
    im.putdata([30000] * 16)
    out = _open(to_webp(_save(im, "PNG")))
    r, g, b = out.convert("RGB").getpixel((0, 0))
    assert abs(r - 117) <= 2 and r == g == b


def test_document_suffixes_are_pinned():
    assert sorted(chat_files.DOCUMENT_SUFFIXES) == [
        ".csv", ".docx", ".epub", ".json", ".markdown", ".md", ".pdf", ".pptx", ".text", ".txt", ".xlsx",
    ]

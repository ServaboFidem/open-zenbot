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

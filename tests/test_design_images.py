import os

import pytest

from src.harness.design_images import (PICTURE_MAX_BYTES, ImageRefused, add, data_uri, embed, sniff)

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\0" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 16
GIF = b"GIF89a" + b"\0" * 16
WEBP = b"RIFF\0\0\0\0WEBP" + b"\0" * 16


def design(tmp_path):
    d = tmp_path / "designs" / "site"
    (d / "images").mkdir(parents=True)
    return d


def test_sniff_goes_by_first_bytes():
    assert [sniff(b) for b in (PNG, JPEG, GIF, WEBP, b"<svg/>", b"")] == ["png", "jpeg", "gif", "webp", None, None]


def test_embed_replaces_only_the_src_value(tmp_path):
    d = design(tmp_path)
    (d / "images" / "hero.jpg").write_bytes(JPEG)
    html = b'<p>\r\n<img data-zen-id="h" src="images/hero.jpg" alt="x"></p>'
    out, notes = embed(html, d)
    assert notes == [] and out == html.replace(b"images/hero.jpg", data_uri("jpeg", JPEG).encode())


def test_the_type_comes_from_the_bytes_not_the_name(tmp_path):
    d = design(tmp_path)
    (d / "images" / "a.png").write_bytes(JPEG)
    assert b"data:image/jpeg;base64," in embed(b'<img src="images/a.png">', d)[0]


def test_a_bom_and_several_pictures_survive(tmp_path):
    d = design(tmp_path)
    (d / "images" / "a.png").write_bytes(PNG)
    (d / "images" / "b.gif").write_bytes(GIF)
    html = b'\xef\xbb\xbf<img src="images/a.png"><img src="images/b.gif">'
    out, notes = embed(html, d)
    assert notes == [] and out.startswith(b"\xef\xbb\xbf<img src=\"data:image/png")
    assert b"data:image/gif;base64," in out and b"images/" not in out


def test_an_unquoted_src_gets_a_note(tmp_path):
    d = design(tmp_path)
    (d / "images" / "a.png").write_bytes(PNG)
    html = b"<img src=images/a.png>"
    out, notes = embed(html, d)
    assert out == html and len(notes) == 1 and "quotes" in notes[0][1]


@pytest.mark.parametrize("src, why", [
    ("images/missing.png", "not found"), ("images/../x.png", "name"), ("images/Sub/x.png", "name"),
    ("images/svg.png", "not a picture"), ("images/big.png", "too large"), ("https://x/y.png", None),
    ("data:image/png;base64,AA", None)])
def test_what_is_left_as_written(tmp_path, src, why):
    d = design(tmp_path)
    (d / "images" / "svg.png").write_bytes(b"<svg/>")
    (d / "images" / "big.png").write_bytes(PNG + b"\0" * PICTURE_MAX_BYTES)
    html = f'<img src="{src}">'.encode()
    out, notes = embed(html, d)
    assert out == html
    if why:
        assert len(notes) == 1 and notes[0][0] == src and why in notes[0][1]
    else:
        assert notes == []


def test_links_and_hard_links_are_refused(tmp_path):
    d = design(tmp_path)
    real = tmp_path / "outside.png"
    real.write_bytes(PNG)
    os.symlink(real, d / "images" / "ln.png")
    os.link(real, d / "images" / "hard.png")
    html = b'<img src="images/ln.png"><img src="images/hard.png">'
    out, notes = embed(html, d)
    assert out == html and [n[0] for n in notes] == ["images/ln.png", "images/hard.png"]
    # images itself a link to a real folder of pictures
    other = tmp_path / "pics"
    other.mkdir()
    (other / "a.png").write_bytes(PNG)
    e = tmp_path / "designs" / "other"
    e.mkdir()
    os.symlink(other, e / "images")
    html = b'<img src="images/a.png">'
    out, notes = embed(html, e)
    assert out == html and len(notes) == 1


def test_bytes_that_are_not_utf8_pass_through(tmp_path):
    assert embed(b"\xff\xfe<img src=\"images/a.png\">", design(tmp_path)) == (b"\xff\xfe<img src=\"images/a.png\">", [])


def test_add_copies_under_a_safe_lower_case_name(tmp_path):
    d = design(tmp_path)
    src = tmp_path / "HERO Photo.JPG"
    src.write_bytes(JPEG)
    assert add(d, src) == "images/hero-photo.jpg" and (d / "images" / "hero-photo.jpg").read_bytes() == JPEG
    assert add(d, src) == "images/hero-photo-2.jpg"
    assert add(d, src, "team") == "images/team.jpg"


@pytest.mark.parametrize("data, why", [(b"<svg/>", "picture"), (PNG + b"\0" * PICTURE_MAX_BYTES, "large")])
def test_add_refuses(tmp_path, data, why):
    d = design(tmp_path)
    src = tmp_path / "x.png"
    src.write_bytes(data)
    with pytest.raises(ImageRefused, match=why):
        add(d, src)
    assert list((d / "images").iterdir()) == []


def test_add_makes_images_but_never_through_a_link(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(PNG)
    d = tmp_path / "designs" / "new"
    d.mkdir(parents=True)
    assert add(d, src) == "images/a.png" and (d / "images" / "a.png").is_file()
    e = tmp_path / "designs" / "linked"
    e.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    os.symlink(target, e / "images")
    with pytest.raises(ImageRefused):
        add(e, src)
    assert list(target.iterdir()) == []

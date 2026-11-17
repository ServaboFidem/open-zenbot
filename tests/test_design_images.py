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


def test_a_tool_with_offered_is_listed_only_for_its_callers():
    from src.harness.tools import ToolRunner
    t = ToolRunner()
    t.register("x", "d", {"type": "object", "properties": {}}, lambda: "ok", offered=lambda b, s: b == "designer")
    names = lambda **k: [s.name for s in t.specs(**k)]  # noqa: E731
    assert "x" not in names() and "x" not in names(caller=("other", "s1")) and "x" in names(caller=("designer", "s1"))


def test_dimensions_read_from_the_header():
    from src.harness.design_images import dimensions

    png = bytes.fromhex("89504e470d0a1a0a") + b"\0\0\0\rIHDR" + (801).to_bytes(4, "big") + (603).to_bytes(4, "big")
    gif = b"GIF89a" + (801).to_bytes(2, "little") + (603).to_bytes(2, "little") + b"\0" * 6
    jpeg = (b"\xff\xd8" + b"\xff\xe0\x00\x04ab" + b"\xff\xc2\x00\x11\x08" + (603).to_bytes(2, "big")
            + (801).to_bytes(2, "big") + b"\0" * 12)
    vp8 = (b"RIFF\0\0\0\0WEBPVP8 \0\0\0\0" + b"\0\0\0\x9d\x01\x2a" + (801).to_bytes(2, "little")
           + (603).to_bytes(2, "little"))
    vp8l_bits = (801 - 1) | ((603 - 1) << 14)
    vp8l = b"RIFF\0\0\0\0WEBPVP8L\0\0\0\0\x2f" + vp8l_bits.to_bytes(4, "little")
    vp8x = b"RIFF\0\0\0\0WEBPVP8X\0\0\0\0\0\0\0\0" + (800).to_bytes(3, "little") + (602).to_bytes(3, "little")
    for data in (png, gif, jpeg, vp8, vp8l, vp8x):
        assert dimensions(data) == (801, 603), data[:16]
    assert [dimensions(b) for b in (PNG, JPEG, WEBP, b"<svg/>")] == [None, None, None, None]


def test_write_out_reuses_the_same_bytes_and_never_overwrites_others(tmp_path):
    from src.harness.design_images import write_out

    d = design(tmp_path)
    assert write_out(d, PNG, "old") == "images/old.png"
    assert write_out(d, PNG, "old") == "images/old.png"
    other = PNG + b"x"
    assert write_out(d, other, "old") == "images/old-2.png"
    assert (d / "images" / "old.png").read_bytes() == PNG and (d / "images" / "old-2.png").read_bytes() == other
    with pytest.raises(ImageRefused):
        write_out(d, b"<svg/>", "logo")
    with pytest.raises(ImageRefused, match="too large"):
        write_out(d, PNG + b"\0" * PICTURE_MAX_BYTES, "big")

"""A user's direct changes to a design (texts, pictures, tweaks) saved as one new numbered version."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.harness import design_studio, studio_edits
from src.harness.design_images import PICTURE_MAX_BYTES, data_uri, embed
from src.harness.studio_edits import (CHANGED, NOT_RESTORABLE, EditRefused, Made, Picture, catch_up, restore, save,
                                      slots_view)

PNG = (bytes.fromhex("89504e470d0a1a0a") + b"\0\0\0\rIHDR" + (800).to_bytes(4, "big") + (600).to_bytes(4, "big")
       + b"\x08\x06\0\0\0" + b"\0" * 8)
PNG2 = PNG[:-1] + b"\x01"
MANIFEST = json.dumps({"tweaks": [
    {"var": "--accent", "label": "Accent", "type": "color"},
    {"var": "--radius", "label": "Corner radius", "type": "range", "min": 0, "max": 24, "step": 1, "unit": "px"}]})
PAGE = ('<!doctype html><html><head>\n'
        '<style data-zen-tweaks>\n:root {\n  --accent: #2f6feb;\n  --radius: 8px;\n}\n</style>\n'
        f'<script type="application/json" data-zen-tweaks>{MANIFEST}</script>\n'
        '</head><body>\n'
        '<h1 data-zen-id="title">Hello</h1>\n'
        '<p data-zen-id="lede">First line<br>second line</p>\n'
        '<img data-zen-id="photo" src="images/a.png" alt="A photo">\n'
        '</body></html>\n')


@pytest.fixture
def studio(tmp_path: Path) -> SimpleNamespace:
    """A vault with design `site/index.html` attached once (version 1) in a stand-in conversation."""
    vault = tmp_path / "vault"
    folder = vault.resolve() / "designs" / "site"
    (folder / "images").mkdir(parents=True)
    (folder / "images" / "a.png").write_bytes(PNG)
    live = folder / "index.html"
    live.write_bytes(PAGE.encode("utf-8"))
    copy = design_studio.snapshot(vault, live)
    conv = SimpleNamespace(exchanges=[])
    s = SimpleNamespace(vault=vault, folder=folder, live=live, conv=conv, copy1=copy, copy1_bytes=copy.read_bytes())
    _record(s, copy)
    return s


def _record(s: SimpleNamespace, copy: Path) -> None:
    """What the routes do with a new copy: attach it in a finished exchange."""
    att = {"id": f"a{len(s.conv.exchanges)}", "title": "index.html", "path": str(copy)}
    s.conv.exchanges.append(SimpleNamespace(attachments=[att], outcome="edit"))


def _versions(s: SimpleNamespace) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in (s.folder / ".versions").iterdir()}


def _images(s: SimpleNamespace) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in (s.folder / "images").iterdir()}


def _copy(s: SimpleNamespace, n: int) -> Path:
    return s.folder / ".versions" / f"{n}.index.html"


def _save(s: SimpleNamespace, version: int = 1, texts=None, images=None, tweaks=None):
    return save(s.vault, s.conv, version, texts or {}, images or {}, tweaks or {})


def _refused(s: SimpleNamespace, status: int, **kw) -> EditRefused:
    """The save is refused with `status` and writes nothing: the working file, the copies and the pictures
    are as they were."""
    live, versions, images = s.live.read_bytes(), _versions(s), _images(s)
    with pytest.raises(EditRefused) as e:
        _save(s, **kw)
    assert e.value.status == status
    assert s.live.read_bytes() == live and _versions(s) == versions and _images(s) == images
    return e.value


def test_a_text_edit_makes_the_next_version(studio):
    made = _save(studio, texts={"title": "Ship faster"})
    assert made == Made(copy=_copy(studio, 2), version=2, texts=["title"], images=[], tweaks=[])
    want = PAGE.replace(">Hello<", ">Ship faster<").encode("utf-8")
    assert studio.live.read_bytes() == want
    assert _copy(studio, 2).read_bytes() == embed(want, studio.folder)[0]
    assert studio.copy1.read_bytes() == studio.copy1_bytes
    assert set(_versions(studio)) == {"1.index.html", "2.index.html"}


def test_markup_typed_by_the_user_shows_literally(studio):
    made = _save(studio, texts={"title": "<b>Hi</b> & co"})
    assert '<h1 data-zen-id="title">&lt;b&gt;Hi&lt;/b&gt; &amp; co</h1>' in studio.live.read_text("utf-8")
    _record(studio, made.copy)
    made = _save(studio, version=2, texts={"lede": "a\nb"})
    assert made.version == 3
    assert studio.live.read_bytes() == PAGE.replace(">Hello<", ">&lt;b&gt;Hi&lt;/b&gt; &amp; co<").replace(
        "First line<br>second line", "a<br>b").encode("utf-8")


def test_a_stale_version_writes_nothing(studio):
    _record(studio, _save(studio, texts={"title": "Ship faster"}).copy)
    e = _refused(studio, 409, version=1, texts={"lede": "Later"})
    assert e.message == CHANGED


def test_a_drifted_working_file_writes_nothing(studio):
    studio.live.write_bytes(PAGE.replace("Hello", "Drafted").encode("utf-8"))
    e = _refused(studio, 409, texts={"lede": "Later"})
    assert e.message == CHANGED


def test_a_picture_is_added_and_placed(studio):
    made = _save(studio, images={"photo": Picture(name="Team Photo.png", data=PNG2, alt="The team")})
    assert made.images == ["photo"] and made.texts == [] and made.tweaks == [] and made.version == 2
    assert _images(studio) == {"a.png": PNG, "team-photo.png": PNG2}
    assert studio.live.read_bytes() == PAGE.replace(
        'src="images/a.png" alt="A photo"', 'src="images/team-photo.png" alt="The team"').encode("utf-8")
    assert data_uri("png", PNG2) in made.copy.read_text("utf-8")


def test_a_picture_without_a_usable_name_is_named_after_its_slot(studio):
    _save(studio, images={"photo": Picture(name="фото.png", data=PNG2, alt=None)})
    assert _images(studio) == {"a.png": PNG, "photo.png": PNG2}
    assert 'src="images/photo.png" alt="A photo"' in studio.live.read_text("utf-8")


def test_a_file_that_is_no_picture_is_refused(studio):
    for data in (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', b"MZ\x90\x00" + b"\0" * 60):
        _refused(studio, 422, images={"photo": Picture(name="x.png", data=data, alt=None)})
    assert not _copy(studio, 2).exists()


def test_tweaks_are_saved_with_the_rest(studio):
    made = _save(studio, texts={"title": "Ship faster"}, tweaks={"--accent": "#C2410C", "--radius": 12})
    assert made == Made(copy=_copy(studio, 2), version=2, texts=["title"], images=[], tweaks=["--accent", "--radius"])
    assert studio.live.read_bytes() == PAGE.replace(">Hello<", ">Ship faster<").replace(
        "--accent: #2f6feb;", "--accent: #c2410c;").replace("--radius: 8px;", "--radius: 12px;").encode("utf-8")
    assert set(_versions(studio)) == {"1.index.html", "2.index.html"}


def test_a_text_before_the_tweaks_moves_them_and_both_land(studio):
    block = ('<style data-zen-tweaks>\n:root {\n  --accent: #2f6feb;\n  --radius: 8px;\n}\n</style>\n'
             f'<script type="application/json" data-zen-tweaks>{MANIFEST}</script>\n')
    late = PAGE.replace(block, "").replace("</body>", block + "</body>")
    assert late != PAGE and late.index("data-zen-tweaks") > late.index('data-zen-id="title"')
    studio.live.write_bytes(late.encode("utf-8"))
    _record(studio, catch_up(studio.vault, studio.conv))
    made = _save(studio, version=2, texts={"title": "A much longer title than before"},
                 tweaks={"--accent": "#c2410c", "--radius": 12})
    assert made.version == 3 and made.texts == ["title"] and made.tweaks == ["--accent", "--radius"]
    assert studio.live.read_bytes() == late.replace(">Hello<", ">A much longer title than before<").replace(
        "--accent: #2f6feb;", "--accent: #c2410c;").replace("--radius: 8px;", "--radius: 12px;").encode("utf-8")


def test_a_bad_tweak_value_refuses_the_whole_save(studio):
    _refused(studio, 422, texts={"title": "Ship faster"}, tweaks={"--radius": "12px; color: red"})
    _refused(studio, 422, texts={"title": "Ship faster"}, tweaks={"--radius": 25})
    _refused(studio, 422, texts={"title": "Ship faster"}, tweaks={"--accent": "red"})
    _refused(studio, 422, texts={"title": "Ship faster"}, tweaks={"--accent": "#c2410c; x"})


def test_a_bad_text_or_alt_refuses_the_whole_save(studio):
    pic = {"photo": Picture(name="b.png", data=PNG2, alt=None)}
    _refused(studio, 422, texts={"title": "   "}, images=pic)
    _refused(studio, 422, texts={"title": "tab\there"})
    _refused(studio, 422, texts={"title": "x" * 2001})
    _refused(studio, 422, texts={"title": "Fine"}, images={"photo": Picture(name="b.png", data=PNG2, alt="bad\x00")})


def test_an_unknown_slot_or_wrong_kind_is_refused(studio):
    _refused(studio, 422, texts={"photo": "Words"})
    _refused(studio, 422, images={"title": Picture(name="b.png", data=PNG2, alt=None)})
    _refused(studio, 422, texts={"nope": "Words"})
    _refused(studio, 422, images={"nope": Picture(name="b.png", data=PNG2, alt=None)})
    _refused(studio, 422, tweaks={"--nope": 3})


def test_over_the_limits_is_refused(studio):
    e = _refused(studio, 422, texts={f"t{i}": "x" for i in range(studio_edits.MAX_TEXTS + 1)})
    assert e.message == "At most 50 texts can be saved at once."
    e = _refused(studio, 422, images={f"i{i}": Picture(name="b.png", data=PNG2, alt=None)
                                      for i in range(studio_edits.MAX_IMAGES + 1)})
    assert e.message == "At most 10 pictures can be saved at once."
    e = _refused(studio, 422, tweaks={f"--k{i}": 1 for i in range(9)})
    assert e.message == "At most 8 tweaks can be saved at once."
    big = PNG + b"\0" * (PICTURE_MAX_BYTES + 1 - len(PNG))
    e = _refused(studio, 422, images={"photo": Picture(name="big.png", data=big, alt=None)})
    assert e.message == "A picture can be at most 1,500,000 bytes."


def test_nothing_changed_makes_no_version(studio):
    assert _save(studio, texts={"title": "Hello", "lede": "First line\nsecond line"}) is None
    assert _save(studio, tweaks={"--accent": "#2F6FEB", "--radius": 8}) is None
    assert _save(studio, images={"photo": Picture(name="a.png", data=PNG, alt="A photo")}) is None
    assert studio.live.read_bytes() == PAGE.encode("utf-8")
    assert set(_versions(studio)) == {"1.index.html"} and _images(studio) == {"a.png": PNG}


def test_a_copy_that_cannot_be_taken_puts_the_file_back(studio, monkeypatch):
    def refuse(*_a, **_k):
        raise design_studio.SnapshotRefused("designs/site/.versions/2.index.html is already there")

    monkeypatch.setattr(studio_edits.design_studio, "snapshot", refuse)
    with pytest.raises(EditRefused) as e:
        _save(studio, texts={"title": "Ship faster"})
    assert e.value.status == 409 and ".versions" not in e.value.message
    assert studio.live.read_bytes() == PAGE.encode("utf-8")
    assert set(_versions(studio)) == {"1.index.html"}


def test_a_design_that_would_be_too_large_writes_nothing(studio, monkeypatch):
    monkeypatch.setattr(studio_edits, "_too_large", lambda html, folder: True)
    e = _refused(studio, 422, texts={"title": "Ship faster"})
    assert e.message == "the design would be too large"


def test_catch_up_copies_a_file_the_designer_did_not_show(studio):
    drafted = PAGE.replace("Hello", "Drafted").encode("utf-8")
    studio.live.write_bytes(drafted)
    copy = catch_up(studio.vault, studio.conv)
    assert copy == _copy(studio, 2)
    assert copy.read_bytes() == embed(drafted, studio.folder)[0]
    assert studio.live.read_bytes() == drafted and studio.copy1.read_bytes() == studio.copy1_bytes
    _record(studio, copy)
    assert catch_up(studio.vault, studio.conv) is None
    assert set(_versions(studio)) == {"1.index.html", "2.index.html"}


def test_catch_up_without_a_design_or_a_copy_is_refused(studio, monkeypatch):
    with pytest.raises(EditRefused) as e:
        catch_up(studio.vault, SimpleNamespace(exchanges=[]))
    assert (e.value.status, e.value.message) == (409, "Ask the Designer to show the design again.")
    studio.live.write_bytes(b"<p>drafted</p>")

    def refuse(*_a, **_k):
        raise design_studio.SnapshotRefused("no")

    monkeypatch.setattr(studio_edits.design_studio, "snapshot", refuse)
    with pytest.raises(EditRefused) as e:
        catch_up(studio.vault, studio.conv)
    assert (e.value.status, e.value.message) == (409, "Ask the Designer to show the design again.")


def test_slots_view_lists_what_can_be_edited(studio):
    view = slots_view(studio.vault, studio.conv)
    assert view == {
        "version": 1,
        "texts": [{"id": "title", "text": "Hello"}, {"id": "lede", "text": "First line\nsecond line"}],
        "images": [{"id": "photo", "name": "a.png", "alt": "A photo"}],
        "tweaks": [
            {"var": "--accent", "label": "Accent", "type": "color", "min": 0, "max": 0, "step": 0, "unit": "",
             "value": "#2f6feb"},
            {"var": "--radius", "label": "Corner radius", "type": "range", "min": 0, "max": 24, "step": 1,
             "unit": "px", "value": 8}],
    }
    assert str(studio.vault) not in json.dumps(view) and "designs" not in json.dumps(view)


def test_slots_view_names_no_file_for_an_embedded_picture(studio):
    page = PAGE.replace('src="images/a.png"', f'src="{data_uri("png", PNG)}"').replace("Hello", "Embedded")
    studio.live.write_bytes(page.encode("utf-8"))
    _record(studio, catch_up(studio.vault, studio.conv))
    assert slots_view(studio.vault, studio.conv)["images"] == [{"id": "photo", "name": "", "alt": "A photo"}]


def test_slots_view_of_a_drifted_file_is_refused(studio):
    studio.live.write_bytes(PAGE.replace("Hello", "Drafted").encode("utf-8"))
    with pytest.raises(EditRefused) as e:
        slots_view(studio.vault, studio.conv)
    assert (e.value.status, e.value.message) == (409, CHANGED)


def _drafted(s: SimpleNamespace, page: str) -> Path:
    """The working file rewritten and caught up as the next version, recorded."""
    s.live.write_bytes(page.encode("utf-8"))
    copy = catch_up(s.vault, s.conv)
    _record(s, copy)
    return copy


def _restore_refused(s: SimpleNamespace, status: int, version: int, newest: int) -> EditRefused:
    """The restore is refused with `status` and writes nothing."""
    live, versions, images = s.live.read_bytes(), _versions(s), _images(s)
    with pytest.raises(EditRefused) as e:
        restore(s.vault, s.conv, version, newest)
    assert e.value.status == status
    assert s.live.read_bytes() == live and _versions(s) == versions and _images(s) == images
    return e.value


def test_restore_makes_a_new_newest_version_equal_to_the_old_one(studio):
    _record(studio, _save(studio, texts={"title": "Ship faster"}).copy)
    history = _versions(studio)
    made = restore(studio.vault, studio.conv, 1, 2)
    assert made == Made(copy=_copy(studio, 3), version=3, texts=[], images=[], tweaks=[])
    assert made.copy.read_bytes() == studio.copy1_bytes
    assert {k: v for k, v in _versions(studio).items() if k != "3.index.html"} == history  # history only moves forward
    assert b"data:image" not in studio.live.read_bytes()
    assert studio.live.read_bytes() == PAGE.encode("utf-8")  # images/a.png again: the same file, no duplicate
    assert _images(studio) == {"a.png": PNG}
    _record(studio, made.copy)
    assert _save(studio, version=3, texts={"lede": "Editable again"}).version == 4


def test_restore_after_a_picture_was_overwritten_in_place(studio):
    (studio.folder / "images" / "a.png").write_bytes(PNG2)  # the same name, other bytes
    _drafted(studio, PAGE.replace("Hello", "Later"))
    assert data_uri("png", PNG2) in _copy(studio, 2).read_text("utf-8")
    history = _versions(studio)
    made = restore(studio.vault, studio.conv, 1, 2)
    assert made.version == 3 and made.copy.read_bytes() == studio.copy1_bytes
    assert data_uri("png", PNG2) not in made.copy.read_text("utf-8")  # the old picture, never the new one
    assert _images(studio) == {"a.png": PNG2, "photo.png": PNG}  # the old bytes written out under a new name
    assert studio.live.read_bytes() == PAGE.replace("images/a.png", "images/photo.png").encode("utf-8")
    assert {k: v for k, v in _versions(studio).items() if k != "3.index.html"} == history


def test_restoring_the_newest_version_or_an_unknown_one_is_refused(studio):
    _restore_refused(studio, 422, 1, 1)
    _record(studio, _save(studio, texts={"title": "Ship faster"}).copy)
    for version in (2, 3, 0, -1):
        e = _restore_refused(studio, 422, version, 2)
        assert ".versions" not in e.message and str(studio.vault) not in e.message
    _copy(studio, 1).unlink()  # a copy gone from the disk (as a test only: history is never removed)
    _restore_refused(studio, 422, 1, 2)
    outside = studio.vault.parent / "outside.html"
    outside.write_bytes(studio.copy1_bytes)
    _copy(studio, 1).symlink_to(outside)  # a link at the copy's name is never read
    _restore_refused(studio, 422, 1, 2)


def test_restore_with_a_stale_newest_is_refused(studio):
    _record(studio, _save(studio, texts={"title": "Ship faster"}).copy)
    assert _restore_refused(studio, 409, 1, 1).message == CHANGED
    studio.live.write_bytes(PAGE.replace("Hello", "Drafted").encode("utf-8"))  # the working file ahead of its copy
    assert _restore_refused(studio, 409, 1, 2).message == CHANGED


def test_a_version_that_cannot_round_trip_is_refused(studio):
    # version 2 shows a picture that was missing then; it is there now, so the page would no longer render as it did
    _drafted(studio, PAGE.replace("images/a.png", "images/later.png"))
    _drafted(studio, PAGE.replace("Hello", "Third"))
    (studio.folder / "images" / "later.png").write_bytes(PNG2)
    assert _restore_refused(studio, 409, 2, 3).message == NOT_RESTORABLE == "This version cannot be restored."


def test_a_version_whose_rebuilt_page_differs_is_refused(studio, monkeypatch):
    _record(studio, _save(studio, texts={"title": "Ship faster"}).copy)
    real = studio_edits.design_images.embed

    def drifted(html, folder, budget=studio_edits.design_images.PAGE_MAX_BYTES):
        out = real(html, folder, budget)
        return (out[0] + b"<!-- drift -->", out[1]) if b">Hello<" in html else out

    monkeypatch.setattr(studio_edits.design_images, "embed", drifted)
    assert _restore_refused(studio, 409, 1, 2).message == NOT_RESTORABLE


def test_an_embedded_picture_that_would_not_embed_the_same_is_left_as_written(studio):
    import base64

    odd = f'data:image/png;base64, {base64.b64encode(PNG).decode("ascii")}'  # a space: embed would not write it so
    page = PAGE.replace('src="images/a.png"', f'src="{odd}"')
    _drafted(studio, page)
    _drafted(studio, PAGE.replace("Hello", "Third"))
    made = restore(studio.vault, studio.conv, 2, 3)
    assert made.copy.read_bytes() == _copy(studio, 2).read_bytes()
    assert studio.live.read_bytes() == page.encode("utf-8") and _images(studio) == {"a.png": PNG}


def test_restoring_a_version_the_newest_already_shows_makes_none(studio):
    _record(studio, _save(studio, texts={"title": "Ship faster"}).copy)
    _record(studio, restore(studio.vault, studio.conv, 1, 2).copy)
    versions, live = _versions(studio), studio.live.read_bytes()
    assert restore(studio.vault, studio.conv, 1, 3) is None
    assert _versions(studio) == versions and studio.live.read_bytes() == live


def test_a_restore_whose_copy_cannot_be_taken_puts_the_file_back(studio, monkeypatch):
    _record(studio, _save(studio, texts={"title": "Ship faster"}).copy)
    live = studio.live.read_bytes()

    def refuse(*_a, **_k):
        raise design_studio.SnapshotRefused("designs/site/.versions/3.index.html is already there")

    monkeypatch.setattr(studio_edits.design_studio, "snapshot", refuse)
    e = _restore_refused(studio, 409, 1, 2)
    assert ".versions" not in e.message and studio.live.read_bytes() == live


def _zipped(s: SimpleNamespace) -> dict[str, bytes]:
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(studio_edits.zip_bytes(s.vault, s.conv))) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_zip_bytes_holds_the_working_file_and_its_pictures_only(studio):
    (studio.folder / "images" / "b.gif").write_bytes(b"GIF89a" + b"\0" * 8)
    (studio.folder / "images" / "sub").mkdir()
    (studio.folder / "images" / "sub" / "c.png").write_bytes(PNG)
    (studio.folder / "notes.txt").write_text("not the page", encoding="utf-8")
    assert _zipped(studio) == {"index.html": PAGE.encode("utf-8"), "images/a.png": PNG,
                               "images/b.gif": b"GIF89a" + b"\0" * 8}


def test_zip_bytes_follows_no_link(studio, tmp_path):
    outside = tmp_path / "secret.png"
    outside.write_bytes(PNG2)
    try:
        (studio.folder / "images" / "linked.png").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks cannot be created here")
    hard = studio.folder / "images" / "hard.png"
    try:
        hard.hardlink_to(outside)
    except OSError:
        pass
    assert set(_zipped(studio)) == {"index.html", "images/a.png"}


def test_zip_bytes_with_images_as_a_link_holds_the_page_alone(studio, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (studio.folder / "images").rename(elsewhere)
    try:
        (studio.folder / "images").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks cannot be created here")
    assert set(_zipped(studio)) == {"index.html"}


def test_zip_bytes_without_a_design_is_refused(studio):
    studio.conv.exchanges.clear()
    with pytest.raises(EditRefused) as e:
        studio_edits.zip_bytes(studio.vault, studio.conv)
    assert e.value.status == 409

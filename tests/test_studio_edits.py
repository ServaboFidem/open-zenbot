"""A user's direct changes to a design (texts, pictures, tweaks) saved as one new numbered version."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.harness import design_studio, studio_edits
from src.harness.design_images import PICTURE_MAX_BYTES, data_uri, embed
from src.harness.studio_edits import CHANGED, EditRefused, Made, Picture, catch_up, save, slots_view

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

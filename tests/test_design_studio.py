import pytest

from src.harness.design_studio import revision_of, snapshot, with_bridge


def test_snapshot_numbers_copies_and_skips_an_unchanged_file(tmp_path):
    f = tmp_path / "designs" / "landing" / "index.html"; f.parent.mkdir(parents=True)
    f.write_text("<p>one</p>", encoding="utf-8")
    a = snapshot(tmp_path, f)
    assert a == tmp_path / "designs" / "landing" / ".versions" / "1.index.html"
    assert snapshot(tmp_path, f) == a and len(list(a.parent.iterdir())) == 1
    f.write_text("<p>two</p>", encoding="utf-8")
    b = snapshot(tmp_path, f)
    assert b.name == "2.index.html" and a.read_text(encoding="utf-8") == "<p>one</p>"
    assert revision_of(tmp_path, b) == ("landing", 2, "index.html")


@pytest.mark.parametrize("rel", ["designs/x.html", "designs/a/b/index.html", "designs/a/notes.md",
                                 "designs/a/.versions/1.index.html", "other/a/index.html"])
def test_snapshot_leaves_other_paths_alone(tmp_path, rel):
    f = tmp_path / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text("x", encoding="utf-8")
    assert snapshot(tmp_path, f) is None


def test_snapshot_compares_resolved_paths(tmp_path):
    (tmp_path / "designs" / "a").mkdir(parents=True)
    out = tmp_path / "outside.html"; out.write_text("x", encoding="utf-8")
    assert snapshot(tmp_path, tmp_path / "designs" / "a" / ".." / ".." / "outside.html") is None
    assert snapshot(tmp_path, tmp_path / "designs" / "a" / ".." / "a" / "missing.html") is None


def test_revision_of_ignores_non_copies(tmp_path):
    assert revision_of(tmp_path, tmp_path / "designs" / "a" / "index.html") is None
    assert revision_of(tmp_path, tmp_path / "designs" / "a" / ".versions" / "x.index.html") is None


def test_snapshot_refuses_a_symlinked_versions_folder(tmp_path):
    f = tmp_path / "designs" / "a" / "index.html"; f.parent.mkdir(parents=True)
    f.write_text("<p>x</p>", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"; elsewhere.mkdir()
    try:
        (f.parent / ".versions").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks cannot be created here")
    assert snapshot(tmp_path, f) is None
    assert list(elsewhere.iterdir()) == []


MARK = b"<script data-zen-bridge>"


@pytest.mark.parametrize("html, tail", [
    (b"<html><body><p>x</p></body></html>", b"</body></html>"),
    (b"<BODY><p>x</p></BODY>", b"</BODY>"),
    (b"<body><script>var s='</body>';</script></body>", b"</body>"),
])
def test_bridge_goes_once_before_the_last_body_close(html, tail):
    out = with_bridge(html)
    assert out.count(MARK) == 1 and out.endswith(tail) and out.rindex(MARK) < out.rindex(tail)


def test_bridge_is_appended_when_there_is_no_body_close():
    assert with_bridge(b"<p>x</p>").startswith(b"<p>x</p>" + MARK)

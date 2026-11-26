import json
import secrets
from pathlib import Path

import pytest

from src.skilltuner.convert import BuiltSkill
from src.skilltuner.suggestions import Suggestion, SuggestionStore


def make(i=None, at="2026-11-25T10:00:00Z", **kw):
    base = dict(id=i or secrets.token_hex(16), slug="pdf-tool", source_slug="pdf", name="PDF tool",
                use="Work with PDFs", topic="pdf", score=0.8, license="MIT", source_url="https://x.example/s",
                original="orig", converted="conv", at=at)
    base.update(kw)
    return Suggestion(**base)


def built(**files):
    return BuiltSkill(slug="pdf-tool", files=files or {"SKILL.md": "---\nname: x\n---\n", "LICENSE.txt": "MIT"})


def link(target: Path, at: Path, directory=True):
    try:
        at.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("this system will not make links")


def test_round_trip(tmp_path):
    st = SuggestionStore(tmp_path / "t")
    a, b = make(at="2026-11-25T10:00:00Z"), make(at="2026-11-25T11:00:00Z")
    st.add(a, built())
    st.add(b, built())
    assert [s.id for s in st.list()] == [b.id, a.id]
    assert st.get(a.id) == a and st.count() == 2
    assert st.files(a.id)["SKILL.md"].startswith("---")
    assert st.remove(a.id) is True and st.remove(a.id) is False
    assert st.get(a.id) is None and st.count() == 1
    assert not (tmp_path / "t" / a.id).exists()


def test_malformed_ids_are_none(tmp_path):
    st = SuggestionStore(tmp_path / "t")
    for bad in ("", "..", "../x", "A" * 32, "abc", "g" * 32):
        assert st.get(bad) is None and st.files(bad) == {} and st.remove(bad) is False


def test_add_refuses_bad_file_names_and_ids(tmp_path):
    st = SuggestionStore(tmp_path / "t")
    for name in ("../x", "a/b", "a\b", ".hidden", "", ".."):
        with pytest.raises(ValueError):
            st.add(make(), built(**{name: "x"}))
    with pytest.raises(ValueError):
        st.add(make(i="nothex"), built())
    assert st.list() == []
    assert not [p for p in (tmp_path / "t").iterdir()] if (tmp_path / "t").exists() else True


def test_a_linked_suggestion_folder_is_skipped(tmp_path):
    st = SuggestionStore(tmp_path / "t")
    a = make()
    st.add(a, built())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil_id = secrets.token_hex(16)
    (elsewhere / "record.json").write_text(json.dumps(make(i=shutil_id).__dict__))
    link(elsewhere, tmp_path / "t" / shutil_id)
    assert [s.id for s in st.list()] == [a.id]
    assert st.get(shutil_id) is None and st.files(shutil_id) == {} and st.remove(shutil_id) is False
    assert (elsewhere / "record.json").exists()


def test_a_linked_root_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link(real, tmp_path / "t")
    st = SuggestionStore(tmp_path / "t")
    assert st.list() == [] and st.count() == 0
    with pytest.raises(OSError):
        st.add(make(), built())
    assert list(real.iterdir()) == []


def test_a_linked_file_is_not_read(tmp_path):
    st = SuggestionStore(tmp_path / "t")
    a = make()
    st.add(a, built())
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    f = tmp_path / "t" / a.id / "skill" / "SKILL.md"
    f.unlink()
    link(secret, f, directory=False)
    assert "SKILL.md" not in st.files(a.id)


def test_junk_never_breaks_list(tmp_path):
    root = tmp_path / "t"
    st = SuggestionStore(root)
    a = make()
    st.add(a, built())
    (root / ".tmp-abc").mkdir()
    (root / "notes").mkdir()
    (root / ("a" * 32)).mkdir()                      # no record
    bad = root / ("b" * 32)
    bad.mkdir()
    (bad / "record.json").write_text("{not json")
    odd = root / ("c" * 32)
    odd.mkdir()
    (odd / "record.json").write_text(json.dumps({"id": "c" * 32}))   # missing fields
    (root / "file.txt").write_text("x")
    assert [s.id for s in st.list()] == [a.id]


def test_a_record_with_another_id_than_its_folder_is_skipped(tmp_path):
    root = tmp_path / "t"
    st = SuggestionStore(root)
    a = make()
    st.add(a, built())
    other = root / ("d" * 32)
    (root).joinpath("d" * 32).mkdir()
    (other / "record.json").write_text(json.dumps(a.__dict__))
    assert [s.id for s in st.list()] == [a.id]
    assert st.get("d" * 32) is None


def test_nothing_is_replaced_by_a_second_add_of_the_same_id(tmp_path):
    st = SuggestionStore(tmp_path / "t")
    a = make()
    st.add(a, built())
    with pytest.raises(FileExistsError):
        st.add(a, built())

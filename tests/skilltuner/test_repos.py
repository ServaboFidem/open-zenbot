import pytest

from src.skilltuner.repos import BAD_ADDRESS, RepoEntry, parse_address, parse_index_path


def test_address_forms():
    assert parse_address("https://github.com/sickn33/agentic-awesome-skills/tree/main/skills") == (
        "sickn33", "agentic-awesome-skills", "main", "skills")
    assert parse_address("https://github.com/anthropics/skills") == ("anthropics", "skills", "", "")
    assert parse_address("https://github.com/a/b/") == ("a", "b", "", "")
    assert parse_address("https://github.com/a/b.git") == ("a", "b", "", "")
    assert parse_address("https://github.com/a/b/tree/dev") == ("a", "b", "dev", "")
    assert parse_address("https://github.com/a/b/tree/main/skills/deep/") == ("a", "b", "main", "skills/deep")


@pytest.mark.parametrize("text", [
    "http://github.com/a/b",
    "https://gitlab.com/a/b",
    "https://user@github.com/a/b",
    "https://github.com:444/a/b",
    "https://github.com/a",
    "https://github.com/a/b/tree/main/../x",
    "https://github.com/a/b/tree/main/a%2Fb",
    "https://github.com/a/b/tree/main/a\\b",
    "https://github.com/a/b/blob/main/x",
    "https://github.com/" + "o" * 40 + "/b",
    "https://github.com/a/b/tree/main/" + "x" * 500,
    "https://github.com/a/b?x=1",
    "https://github.com/a/b#frag",
    "https://www.github.com/a/b",
    "https://github.com/a/b/tree/ma\tin/x",
    "https://github.com/a/b/tree/ma\rin/x",
    "https://github.com/a/b/tree/ma\nin/x",
])
def test_addresses_refused(text):
    with pytest.raises(ValueError) as e:
        parse_address(text)
    assert str(e.value) == BAD_ADDRESS


def test_entry_address_and_label():
    e = RepoEntry("i", "o", "r", "main", "skills", "", "MIT")
    assert e.address() == "https://github.com/o/r/tree/main/skills"
    assert e.label() == "github.com/o/r"
    assert RepoEntry("i", "o", "r", "", "", "", "").address() == "https://github.com/o/r"


def test_index_paths():
    assert parse_index_path("data/skills_index.json") == "data/skills_index.json"
    assert parse_index_path("") == ""
    for bad in ("../x.json", "/x.json", "a\\b.json", "https://x", "a\tb.json", "a\nb.json"):
        with pytest.raises(ValueError):
            parse_index_path(bad)

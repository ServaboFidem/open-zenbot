"""Friend check slice tests: known close, known-but-distant, unknown, alias.

Read-only proof: every test snapshots the registry file and the people list
and asserts the check changed neither.
"""

from __future__ import annotations

import pytest

from src.memory.friend_check import is_friend
from src.memory.registry import PeopleRegistry


@pytest.fixture
def people(tmp_path):
    reg = PeopleRegistry(tmp_path / "people.json")
    reg.set_owner("Alex Rivera", [])
    reg.add("Dana Reyes", relation="friend", tier="close")
    reg.add("Lee Morgan", relation="colleague", tier="known")
    reg.add_alias("Dana Reyes", "Dana R")
    reg.add_alias("Lee Morgan", "Lee M")
    return reg


def snapshot(reg: PeopleRegistry) -> tuple[list[tuple[str, str, tuple[str, ...]]], str]:
    people_path = reg.path
    before_entries = sorted((e.name, e.tier, tuple(e.aliases)) for e in reg.all())
    text = people_path.read_text(encoding="utf-8") if people_path.exists() else ""
    return before_entries, text


def assert_unchanged(reg: PeopleRegistry, before) -> None:
    before_entries, before_text = before
    after = sorted((e.name, e.tier, tuple(e.aliases)) for e in reg.all())
    assert after == before_entries
    assert reg.path.read_text(encoding="utf-8") == before_text


def test_close_friend_is_a_friend(people: PeopleRegistry) -> None:
    before = snapshot(people)
    assert is_friend(people, "Dana Reyes") is True
    assert_unchanged(people, before)


def test_known_but_not_close_is_not_a_friend(people: PeopleRegistry) -> None:
    before = snapshot(people)
    assert is_friend(people, "Lee Morgan") is False
    assert_unchanged(people, before)


def test_unknown_name_is_not_a_friend(people: PeopleRegistry) -> None:
    before = snapshot(people)
    assert is_friend(people, "Sam Stranger") is False
    assert_unchanged(people, before)


def test_alias_of_close_friend_is_a_friend(people: PeopleRegistry) -> None:
    before = snapshot(people)
    assert is_friend(people, "Dana R") is True
    assert_unchanged(people, before)


def test_alias_of_known_is_not_a_friend(people: PeopleRegistry) -> None:
    before = snapshot(people)
    assert is_friend(people, "Lee M") is False
    assert_unchanged(people, before)


def test_match_is_case_insensitive_and_trims(people: PeopleRegistry) -> None:
    before = snapshot(people)
    assert is_friend(people, "  dana reyes ") is True
    assert is_friend(people, "LEE MORGAN") is False
    assert_unchanged(people, before)


def test_empty_name_is_not_a_friend(people: PeopleRegistry) -> None:
    before = snapshot(people)
    assert is_friend(people, "") is False
    assert is_friend(people, "   ") is False
    assert_unchanged(people, before)


def test_owner_is_not_a_friend(people: PeopleRegistry) -> None:
    before = snapshot(people)
    assert is_friend(people, "Alex Rivera") is False
    assert_unchanged(people, before)

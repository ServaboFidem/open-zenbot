"""The harness reads the repo's .env at start, so a key kept there (PEXELS_API_KEY for the pexels MCP
server, OPENROUTER_API_KEY, ZEN_* settings) reaches the harness however it is launched. A variable
already set in the environment wins over the file."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.harness import main as harness_main

NAMES = ("ZEN_TEST_FROM_FILE", "ZEN_TEST_ALREADY_SET", "ZEN_TEST_QUOTED")


@pytest.fixture(autouse=True)
def clean_env():
    saved = {n: os.environ.get(n) for n in NAMES}
    for n in NAMES:
        os.environ.pop(n, None)
    yield
    for n, v in saved.items():
        os.environ.pop(n, None)
        if v is not None:
            os.environ[n] = v


def test_env_file_sets_what_is_unset_and_never_overrides(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("# a comment\nZEN_TEST_FROM_FILE=from-file\nZEN_TEST_ALREADY_SET=from-file\n"
                   'ZEN_TEST_QUOTED="two words"\n# ZEN_TEST_COMMENTED=no\n', encoding="utf-8")
    os.environ["ZEN_TEST_ALREADY_SET"] = "from-shell"
    loaded = harness_main.load_env_file(env)
    assert os.environ["ZEN_TEST_FROM_FILE"] == "from-file"
    assert os.environ["ZEN_TEST_ALREADY_SET"] == "from-shell"
    assert os.environ["ZEN_TEST_QUOTED"] == "two words"
    assert "ZEN_TEST_COMMENTED" not in os.environ
    assert sorted(loaded) == ["ZEN_TEST_FROM_FILE", "ZEN_TEST_QUOTED"]


def test_a_missing_env_file_is_fine(tmp_path: Path):
    assert harness_main.load_env_file(tmp_path / "nope.env") == []


def test_main_loads_the_env_file_before_it_builds_the_harness(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ZEN_TEST_FROM_FILE=seen\n", encoding="utf-8")
    monkeypatch.setattr(harness_main, "ENV_FILE", env)
    seen = {}

    class Stop(Exception):
        pass

    def build_harness(config=None):
        seen["value"] = os.environ.get("ZEN_TEST_FROM_FILE")
        raise Stop

    monkeypatch.setattr(harness_main, "build_harness", build_harness)
    with pytest.raises(Stop):
        harness_main.main()
    assert seen["value"] == "seen"


def test_env_file_is_the_repo_root_one():
    assert harness_main.ENV_FILE == Path(__file__).resolve().parents[1] / ".env"

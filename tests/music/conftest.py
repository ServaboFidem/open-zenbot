from pathlib import Path

import pytest

from src.music.atlas import Atlas

FIXTURE_ATLAS = Path(__file__).parent.parent / "fixtures" / "music" / "atlas"


@pytest.fixture(scope="session")
def atlas() -> Atlas:
    return Atlas.load(FIXTURE_ATLAS)

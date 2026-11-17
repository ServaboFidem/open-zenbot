import pytest

from src.events.store import CalendarStore


@pytest.fixture
def store(tmp_path):
    s = CalendarStore(tmp_path / "calendar.db")
    yield s
    s.close()

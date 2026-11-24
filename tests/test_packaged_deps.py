"""The installed app's CPU-only project must track the root project."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_packaged_dependencies_match_the_root_project():
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    packed = tomllib.loads((ROOT / "packaging/harness/pyproject.toml").read_text())
    extra = [d for d in packed["project"]["dependencies"] if d not in root["dependencies"]]
    assert sorted(set(packed["project"]["dependencies"]) - set(extra)) == sorted(root["dependencies"]), (
        "packaging/harness/pyproject.toml must list exactly the root project's dependencies "
        "(plus torch); update it and run `uv lock --project packaging/harness`"
    )
    assert len(extra) == 1 and extra[0].startswith("torch")
    assert packed["project"]["requires-python"] == root["requires-python"]
    assert packed["project"]["version"] == root["version"]


def test_fastapi_floor_keeps_strict_content_types():
    """Every POST route takes a required JSON body so a cross-site page cannot send one without a preflight. Before
    FastAPI 0.132 a body with no Content-Type was still read as JSON, which a page can send with no preflight."""
    for path in ("pyproject.toml", "packaging/harness/pyproject.toml"):
        deps = tomllib.loads((ROOT / path).read_text())["project"]["dependencies"]
        floor = next(d for d in deps if d.startswith("fastapi")).split(">=")[1]
        assert tuple(int(p) for p in floor.split(".")[:2]) >= (0, 132), path


def test_the_packaged_lock_has_no_nvidia_packages():
    lock = (ROOT / "packaging/harness/uv.lock").read_text()
    assert 'name = "torch"' in lock and 'name = "nvidia-' not in lock

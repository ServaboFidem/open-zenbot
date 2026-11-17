def test_pyproject_and_package_json_agree():
    import json, tomllib
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    py = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    js = json.loads((root / "package.json").read_text())["version"]
    assert py == js

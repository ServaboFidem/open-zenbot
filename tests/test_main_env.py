def test_env_file_follows_zen_env_file(tmp_path):
    from src.harness.main import ENV_FILE, env_file
    assert env_file({}) == ENV_FILE
    assert env_file({"ZEN_ENV_FILE": ""}) == ENV_FILE
    assert env_file({"ZEN_ENV_FILE": str(tmp_path / "x.env")}) == tmp_path / "x.env"

from pathlib import Path

from needledrop.config import Settings, load_settings


def test_defaults():
    settings = Settings()
    assert settings.db_path == Path("./library.duckdb")
    assert settings.auth_port == 8787
    assert settings.fuzzy_threshold == 0.87


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("NEEDLEDROP_DB_PATH", "/tmp/custom.duckdb")
    monkeypatch.setenv("NEEDLEDROP_FUZZY_THRESHOLD", "0.95")
    settings = load_settings()
    assert settings.db_path == Path("/tmp/custom.duckdb")
    assert settings.fuzzy_threshold == 0.95


def test_threshold_is_bounded():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(fuzzy_threshold=1.5)

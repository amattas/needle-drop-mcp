from needledrop.config import Settings


def test_mb_defaults():
    s = Settings()
    assert s.mb_dump_base_url.startswith("https://data.metabrainz.org/")
    assert s.mb_server_raw_base.startswith("https://raw.githubusercontent.com/metabrainz/")
    assert s.mb_postgres_image == "postgres:18"
    assert s.mb_postgres_container == "needledrop-mb-import"
    assert s.mb_postgres_port == 55432
    assert s.mb_postgres_db == "musicbrainz"
    assert s.mb_postgres_user == "musicbrainz"
    assert s.mb_postgres_password  # non-empty throwaway default


def test_mb_env_override(monkeypatch):
    monkeypatch.setenv("NEEDLEDROP_MB_POSTGRES_PORT", "5599")
    assert Settings().mb_postgres_port == 5599

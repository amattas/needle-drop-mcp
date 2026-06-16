from needledrop.db.duckdb_store import connect, init_schema, open_db, table_exists

EXPECTED_TABLES = {
    "artists",
    "albums",
    "tracks",
    "library_items",
    "match_candidates",
    "playlists",
    "sync_runs",
    "cleanup_findings",
}


def test_init_schema_creates_all_tables(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert EXPECTED_TABLES.issubset(names)


def test_init_schema_is_idempotent(tmp_path):
    db = tmp_path / "library.duckdb"
    con = connect(db)
    init_schema(con)
    init_schema(con)  # must not raise
    count = con.execute("SELECT count(*) FROM artists").fetchone()[0]
    assert count == 0


def test_albums_sequence_autoassigns_ids(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    con.execute("INSERT INTO artists (canonical_name) VALUES ('Green Day')")
    artist_id = con.execute("SELECT id FROM artists").fetchone()[0]
    con.execute(
        "INSERT INTO albums (artist_id, title) VALUES (?, 'Dookie')", [artist_id]
    )
    con.execute(
        "INSERT INTO albums (artist_id, title) VALUES (?, 'Insomniac')", [artist_id]
    )
    ids = [r[0] for r in con.execute("SELECT id FROM albums ORDER BY id").fetchall()]
    assert ids == [1, 2]


def test_library_items_unique_constraint(tmp_path):
    import duckdb
    import pytest

    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    con.execute(
        "INSERT INTO library_items (service, service_item_id, item_type) "
        "VALUES ('apple_music', 'l.1', 'album')"
    )
    with pytest.raises(duckdb.ConstraintException):
        con.execute(
            "INSERT INTO library_items (service, service_item_id, item_type) "
            "VALUES ('apple_music', 'l.1', 'album')"
        )


def test_table_exists_detects_base_tables(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    assert table_exists(con, "artists") is True
    assert table_exists(con, "mb_release_group") is False


def test_open_db_bootstraps_schema_on_fresh_db(tmp_path):
    # open_db must create the canonical schema so CLI commands work on a clean DB.
    con = open_db(tmp_path / "fresh.duckdb")
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert EXPECTED_TABLES.issubset(names)
    # Idempotent: opening again must not raise.
    open_db(tmp_path / "fresh.duckdb")


def test_open_db_albums_has_total_tracks(tmp_path):
    con = open_db(tmp_path / "fresh.duckdb")
    cols = [r[1] for r in con.execute("PRAGMA table_info('albums')").fetchall()]
    assert "total_tracks" in cols
    open_db(tmp_path / "fresh.duckdb")  # opening again must not raise (idempotent)


def test_total_tracks_migration_upgrades_legacy_albums(tmp_path):
    from importlib import resources

    from needledrop.db.duckdb_store import apply_migrations

    con = connect(tmp_path / "legacy.duckdb")
    con.execute("CREATE TABLE albums (id INTEGER, title VARCHAR)")  # pre-migration shape
    migrations = resources.files("needledrop.db").joinpath("migrations")
    applied = apply_migrations(con, migrations)
    assert "0001_add_albums_total_tracks" in applied
    cols = [r[1] for r in con.execute("PRAGMA table_info('albums')").fetchall()]
    assert "total_tracks" in cols

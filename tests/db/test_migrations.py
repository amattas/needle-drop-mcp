from needledrop.db.duckdb_store import apply_migrations, connect, init_schema

MIGRATION_SQL = "ALTER TABLE artists ADD COLUMN country VARCHAR;"


def _write_migration(migrations_dir):
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "0001_add_artist_country.sql").write_text(MIGRATION_SQL)


def test_migration_applied_once_and_alters_schema(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    migrations_dir = tmp_path / "migrations"
    _write_migration(migrations_dir)

    applied = apply_migrations(con, migrations_dir)
    assert applied == ["0001_add_artist_country"]

    con.execute("INSERT INTO artists (canonical_name, country) VALUES ('Muse', 'GB')")
    row = con.execute("SELECT canonical_name, country FROM artists").fetchone()
    assert row == ("Muse", "GB")


def test_migration_is_idempotent(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    migrations_dir = tmp_path / "migrations"
    _write_migration(migrations_dir)

    apply_migrations(con, migrations_dir)
    second = apply_migrations(con, migrations_dir)
    assert second == []

    recorded = con.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    assert recorded == 1


def test_no_migrations_dir_entries_is_noop(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    empty_dir = tmp_path / "migrations"
    empty_dir.mkdir()
    assert apply_migrations(con, empty_dir) == []


def test_failed_migration_rolls_back(tmp_path):
    import duckdb
    import pytest

    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    # The second statement re-adds an existing column -> error mid-migration.
    (migrations_dir / "0001_bad.sql").write_text(
        "ALTER TABLE artists ADD COLUMN note VARCHAR;\n"
        "ALTER TABLE artists ADD COLUMN note VARCHAR;"
    )

    with pytest.raises(duckdb.Error):
        apply_migrations(con, migrations_dir)

    # Nothing recorded and the first statement was rolled back (no `note` column).
    recorded = con.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    assert recorded == 0
    columns = [row[1] for row in con.execute("PRAGMA table_info('artists')").fetchall()]
    assert "note" not in columns

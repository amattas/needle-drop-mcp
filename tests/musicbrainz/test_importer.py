import pytest

from needledrop.musicbrainz.importer import SCHEMA_BOOTSTRAP_SQL, run_import


class SpyPostgres:
    def __init__(self):
        self.events = []

    def start(self):
        self.events.append("start")

    def wait_ready(self):
        self.events.append("wait_ready")

    def run_sql(self, sql):
        self.events.append(("run_sql", sql))

    def copy_table(self, table, path):
        self.events.append(("copy", table, path))

    def teardown(self):
        self.events.append("teardown")


class FakeDuck:
    pass


def test_run_import_sequences_and_materializes():
    pg = SpyPostgres()
    materialized = []

    def fake_materializer(con):
        materialized.append(con)
        return ["artist", "release_group"]

    tables = run_import(
        pg=pg,
        duckdb_con=FakeDuck(),
        ddl_sql_texts=["-- extensions", "-- tables"],
        table_files=[("artist", "/dump/mbdump/artist")],
        attach=lambda con: pg.events.append("attach"),
        materializer=fake_materializer,
    )

    assert tables == ["artist", "release_group"]
    assert pg.events[0] == "start"
    assert pg.events[1] == "wait_ready"
    assert pg.events[2] == ("run_sql", SCHEMA_BOOTSTRAP_SQL)
    # DDLs are prefixed with the search-path line, so match on substring.
    assert any(
        isinstance(e, tuple) and e[0] == "run_sql" and "-- extensions" in e[1]
        for e in pg.events
    )
    assert ("copy", "artist", "/dump/mbdump/artist") in pg.events
    assert "attach" in pg.events
    assert pg.events[-1] == "teardown"


def test_run_import_tears_down_on_failure():
    pg = SpyPostgres()

    def boom(con):
        raise RuntimeError("materialize failed")

    with pytest.raises(RuntimeError):
        run_import(
            pg=pg,
            duckdb_con=FakeDuck(),
            ddl_sql_texts=[],
            table_files=[],
            attach=lambda con: None,
            materializer=boom,
        )
    assert pg.events[-1] == "teardown"

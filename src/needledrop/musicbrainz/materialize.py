"""Materialize an attached MusicBrainz Postgres into local DuckDB mb_* tables."""

from __future__ import annotations

import duckdb

# Lists tables in the attached Postgres `musicbrainz` schema via DuckDB's catalog.
LIST_TABLES_SQL = (
    "SELECT table_name FROM duckdb_tables() "
    "WHERE database_name = 'pg' AND schema_name = 'musicbrainz' "
    "ORDER BY table_name"
)


def attach_sql(*, host: str, port: int, db: str, user: str, password: str) -> str:
    """ATTACH statement for the running Postgres (read-only)."""
    conn = f"host={host} port={port} dbname={db} user={user} password={password}"
    return f"ATTACH '{conn}' AS pg (TYPE postgres, READ_ONLY)"


def materialize_sql(table: str) -> str:
    """CTAS that copies one Postgres musicbrainz table into a local `mb_<table>`."""
    return (
        f'CREATE OR REPLACE TABLE "mb_{table}" AS '
        f'SELECT * FROM pg.musicbrainz."{table}"'
    )


def attach(con: duckdb.DuckDBPyConnection, *, host: str, port: int, db: str,
           user: str, password: str) -> None:
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    con.execute(attach_sql(host=host, port=port, db=db, user=user, password=password))


def list_pg_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [row[0] for row in con.execute(LIST_TABLES_SQL).fetchall()]


def materialize_all(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Materialize every attached musicbrainz-schema table as mb_<table>. Returns the names."""
    tables = list_pg_tables(con)
    for table in tables:
        con.execute(materialize_sql(table))
    return tables

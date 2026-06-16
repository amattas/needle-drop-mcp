"""DuckDB connection management and schema lifecycle (baseline + migrations)."""

from __future__ import annotations

import time
from importlib import resources
from pathlib import Path

import duckdb

# DuckDB is single-writer and raises immediately on a conflicting lock (no
# busy-timeout). open_db retries with exponential backoff to ride out transient
# overlaps; a long-held lock still eventually raises a clear error.
_LOCK_RETRIES = 8
_LOCK_RETRY_BASE_DELAY = 0.2  # seconds; doubles each attempt, capped at 2s


def connect(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) a DuckDB database at db_path."""
    return duckdb.connect(str(db_path))


def _connect_with_lock_retry(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Like connect(), but sleep-and-retry while another process holds the write lock.

    Only lock conflicts are retried; other IO errors (e.g. a disk problem) raise at
    once. After the retry budget is exhausted, raise a clear error instead of the
    raw DuckDB lock exception.
    """
    delay = _LOCK_RETRY_BASE_DELAY
    for attempt in range(_LOCK_RETRIES):
        try:
            return connect(db_path)
        except duckdb.IOException as exc:
            if "lock" not in str(exc).lower():
                raise
            if attempt == _LOCK_RETRIES - 1:
                raise RuntimeError(
                    f"Could not open {db_path}: it is locked by another process "
                    "(another needledrop client or a running `sync`?). "
                    "Close the other one and retry."
                ) from exc
            time.sleep(delay)
            delay = min(delay * 2, 2.0)


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Apply the idempotent baseline schema."""
    sql = resources.files("needledrop.db").joinpath("schema.sql").read_text(encoding="utf-8")
    for statement in _split_statements(sql):
        con.execute(statement)


def apply_migrations(con: duckdb.DuckDBPyConnection, migrations_dir: str | Path) -> list[str]:
    """Apply pending *.sql migrations in lexical order; return versions applied."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version VARCHAR PRIMARY KEY, applied_at TIMESTAMP DEFAULT now())"
    )
    applied = {
        row[0] for row in con.execute("SELECT version FROM schema_migrations").fetchall()
    }
    newly_applied: list[str] = []
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        # Apply each migration atomically: its DDL and the bookkeeping row commit
        # together, so a mid-migration failure leaves no partial schema to re-attempt.
        con.execute("BEGIN TRANSACTION")
        try:
            for statement in _split_statements(path.read_text(encoding="utf-8")):
                con.execute(statement)
            con.execute("INSERT INTO schema_migrations (version) VALUES (?)", [version])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        newly_applied.append(version)
    return newly_applied


def table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """True if a table with this name exists in the database's main schema."""
    count = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name = ? AND table_schema = 'main' AND table_type = 'BASE TABLE'",
        [table_name],
    ).fetchone()[0]
    return count > 0


def open_db(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Connect AND ensure the canonical schema exists (baseline + pending migrations).

    This is the entry point CLI commands should use: `connect` alone opens the
    file but does not create any tables, so commands that touch the canonical
    schema (sync, etc.) must bootstrap it first.
    """
    con = _connect_with_lock_retry(db_path)
    init_schema(con)
    apply_migrations(con, resources.files("needledrop.db").joinpath("migrations"))
    return con


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into statements, dropping comment-only lines.

    Naive by design: it removes whole-line ``--`` comments then splits on ``;``.
    It does NOT handle semicolons inside string literals or trailing inline
    comments, so project-authored schema/migration SQL must keep one statement
    per ``;`` and must not embed ``;`` in string defaults.
    """
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    cleaned = "\n".join(lines)
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]

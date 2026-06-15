"""DuckDB connection management and schema lifecycle (baseline + migrations)."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import duckdb


def connect(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) a DuckDB database at db_path."""
    return duckdb.connect(str(db_path))


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
        for statement in _split_statements(path.read_text(encoding="utf-8")):
            con.execute(statement)
        con.execute("INSERT INTO schema_migrations (version) VALUES (?)", [version])
        newly_applied.append(version)
    return newly_applied


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into statements, dropping comment-only lines."""
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    cleaned = "\n".join(lines)
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]

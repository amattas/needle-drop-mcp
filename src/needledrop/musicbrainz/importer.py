"""Orchestrates `needledrop mb import` with guaranteed Postgres teardown."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from needledrop.config import Settings
from needledrop.db.duckdb_store import connect
from needledrop.musicbrainz import dumps, schema_sql
from needledrop.musicbrainz.materialize import attach as _duck_attach
from needledrop.musicbrainz.materialize import materialize_all
from needledrop.musicbrainz.postgres import EphemeralPostgres, PostgresSpec

# All schemas MusicBrainz's InitDb creates before applying CreateTables.sql.
_MB_SCHEMAS: tuple[str, ...] = (
    "musicbrainz", "cover_art_archive", "documentation", "event_art_archive",
    "json_dump", "report", "sitemaps", "statistics", "wikidocs", "dbmirror2",
)
SCHEMA_BOOTSTRAP_SQL = "\n".join(f"CREATE SCHEMA IF NOT EXISTS {s};" for s in _MB_SCHEMAS)
_SEARCH_PATH = "SET search_path = musicbrainz, public;\n"


def run_import(
    *,
    pg,
    duckdb_con,
    ddl_sql_texts: Sequence[str],
    table_files: Iterable[tuple[str, str]],
    attach: Callable[[object], None],
    materializer: Callable[[object], list[str]] = materialize_all,
) -> list[str]:
    """Load DDL + data into `pg`, then materialize into DuckDB. Always tears down.

    `attach` connects `duckdb_con` to the running Postgres; `materializer` returns
    the materialized table names. Both are injected so this is unit-testable.
    """
    try:
        pg.start()
        pg.wait_ready()
        pg.run_sql(SCHEMA_BOOTSTRAP_SQL)
        for ddl in ddl_sql_texts:
            pg.run_sql(_SEARCH_PATH + ddl)
        for table, container_path in table_files:
            pg.copy_table(table, container_path)
        attach(duckdb_con)
        return materializer(duckdb_con)
    finally:
        pg.teardown()


def import_musicbrainz(settings: Settings, *, http=None) -> dict:
    """Full entry point: acquire the export, load it, materialize into DuckDB.

    Heavy I/O path — exercised by the documented manual run, not CI. Returns a
    summary dict {schema_sequence, tag, tables}.
    """
    import httpx

    owns_http = http is None
    http = http or httpx.Client(timeout=None, follow_redirects=True)
    data_dir = Path(settings.mb_data_dir)
    try:
        latest = dumps.resolve_latest(
            http.get(f"{settings.mb_dump_base_url.rstrip('/')}/LATEST").raise_for_status().text
        )
        seq = int(
            http.get(dumps.fullexport_url(settings.mb_dump_base_url, latest, "SCHEMA_SEQUENCE"))
            .raise_for_status()
            .text.strip()
        )
        tag = schema_sql.tag_for_schema_sequence(seq)  # fail-loud before the big download
        ddl_texts = [
            http.get(url).raise_for_status().text
            for url in schema_sql.ddl_file_urls(settings.mb_server_raw_base, tag)
        ]
        sums = dumps.parse_sha256sums(
            http.get(dumps.fullexport_url(settings.mb_dump_base_url, latest, "SHA256SUMS"))
            .raise_for_status()
            .text
        )
        tarball = dumps.download_file(
            dumps.fullexport_url(settings.mb_dump_base_url, latest, "mbdump.tar.bz2"),
            data_dir / latest / "mbdump.tar.bz2",
            client=http,
        )
        dumps.verify_sha256(tarball, sums, "mbdump.tar.bz2")
        mbdump_dir = dumps.extract_tarball(tarball, data_dir / latest / "extracted")
        table_files = [
            (name, f"/dump/mbdump/{path.name}")
            for name, path in dumps.list_table_files(mbdump_dir)
        ]

        spec = PostgresSpec(
            image=settings.mb_postgres_image,
            container=settings.mb_postgres_container,
            port=settings.mb_postgres_port,
            db=settings.mb_postgres_db,
            user=settings.mb_postgres_user,
            password=settings.mb_postgres_password,
        )
        pg = EphemeralPostgres(spec, mbdump_dir.parent)
        con = connect(settings.db_path)

        def _attach(c):
            _duck_attach(
                c, host="127.0.0.1", port=spec.port, db=spec.db,
                user=spec.user, password=spec.password,
            )

        tables = run_import(
            pg=pg,
            duckdb_con=con,
            ddl_sql_texts=ddl_texts,
            table_files=table_files,
            attach=_attach,
        )
        return {"schema_sequence": seq, "tag": tag, "tables": tables}
    finally:
        if owns_http:
            http.close()

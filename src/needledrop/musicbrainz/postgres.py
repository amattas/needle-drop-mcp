"""Ephemeral MusicBrainz Postgres lifecycle via the `docker` CLI."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PostgresSpec:
    image: str
    container: str
    port: int
    db: str
    user: str
    password: str


def docker_run_args(spec: PostgresSpec, dump_dir: str | Path) -> list[str]:
    """argv to start the ephemeral container with the dump dir mounted read-only."""
    return [
        "docker", "run", "-d", "--rm",
        "--name", spec.container,
        "-e", f"POSTGRES_DB={spec.db}",
        "-e", f"POSTGRES_USER={spec.user}",
        "-e", f"POSTGRES_PASSWORD={spec.password}",
        "-p", f"{spec.port}:5432",
        "-v", f"{Path(dump_dir).resolve()}:/dump:ro",
        spec.image,
    ]


def pg_isready_args(spec: PostgresSpec) -> list[str]:
    return ["docker", "exec", spec.container, "pg_isready", "-U", spec.user, "-d", spec.db]


def psql_args(spec: PostgresSpec) -> list[str]:
    return [
        "docker", "exec", "-i", spec.container,
        "psql", "-v", "ON_ERROR_STOP=1", "-U", spec.user, "-d", spec.db,
    ]


def teardown_args(spec: PostgresSpec) -> list[str]:
    return ["docker", "rm", "-f", spec.container]


def copy_table_sql(table: str, container_path: str) -> str:
    """Server-side COPY of a headerless tab/`\\N` dump file into a musicbrainz table."""
    return f"COPY musicbrainz.\"{table}\" FROM '{container_path}' WITH (FORMAT text);"


class EphemeralPostgres:
    """Manages a throwaway postgres:18 container for one import run."""

    def __init__(self, spec: PostgresSpec, dump_dir: str | Path, *, runner=subprocess.run):
        self._spec = spec
        self._dump_dir = Path(dump_dir)
        self._run = runner

    def start(self) -> None:
        self._run(docker_run_args(self._spec, self._dump_dir), check=True)

    def wait_ready(self, *, attempts: int = 60, sleep: float = 2.0, sleeper=time.sleep) -> None:
        for _ in range(attempts):
            result = self._run(pg_isready_args(self._spec), capture_output=True)
            if result.returncode == 0:
                return
            sleeper(sleep)
        raise TimeoutError(
            f"Postgres container '{self._spec.container}' not ready after {attempts} attempts"
        )

    def run_sql(self, sql: str) -> None:
        self._run(psql_args(self._spec), input=sql.encode("utf-8"), check=True)

    def copy_table(self, table: str, container_path: str) -> None:
        self.run_sql(copy_table_sql(table, container_path))

    def teardown(self) -> None:
        # Best-effort: never raise from teardown (it runs in `finally`).
        self._run(teardown_args(self._spec), check=False)

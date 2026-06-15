from pathlib import Path

from needledrop.musicbrainz.postgres import (
    PostgresSpec,
    copy_table_sql,
    docker_run_args,
    pg_isready_args,
    psql_args,
    teardown_args,
)

SPEC = PostgresSpec(
    image="postgres:18",
    container="needledrop-mb-import",
    port=55432,
    db="musicbrainz",
    user="musicbrainz",
    password="pw",
)


def test_docker_run_args(tmp_path):
    dump = tmp_path / "out"
    dump.mkdir()
    args = docker_run_args(SPEC, dump)
    assert args[:3] == ["docker", "run", "-d"]
    assert "--rm" in args
    assert "--name" in args and "needledrop-mb-import" in args
    assert "-p" in args and "55432:5432" in args
    assert f"{dump.resolve()}:/dump:ro" in args
    assert args[-1] == "postgres:18"
    assert "POSTGRES_PASSWORD=pw" in args


def test_pg_isready_args():
    assert pg_isready_args(SPEC) == [
        "docker", "exec", "needledrop-mb-import",
        "pg_isready", "-U", "musicbrainz", "-d", "musicbrainz",
    ]


def test_psql_args():
    assert psql_args(SPEC) == [
        "docker", "exec", "-i", "needledrop-mb-import",
        "psql", "-v", "ON_ERROR_STOP=1", "-U", "musicbrainz", "-d", "musicbrainz",
    ]


def test_teardown_args():
    assert teardown_args(SPEC) == ["docker", "rm", "-f", "needledrop-mb-import"]


def test_copy_table_sql():
    sql = copy_table_sql("release_group", "/dump/mbdump/release_group")
    assert sql == (
        'COPY musicbrainz."release_group" FROM '
        "'/dump/mbdump/release_group' WITH (FORMAT text);"
    )

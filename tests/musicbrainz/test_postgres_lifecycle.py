import subprocess

import pytest

from needledrop.musicbrainz.postgres import EphemeralPostgres, PostgresSpec

SPEC = PostgresSpec("postgres:18", "c", 55432, "musicbrainz", "musicbrainz", "pw")


class FakeRunner:
    """Records calls; returns queued returncodes for pg_isready."""

    def __init__(self, isready_codes=(0,)):
        self.calls = []
        self._isready = list(isready_codes)

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        rc = 0
        if "pg_isready" in args:
            rc = self._isready.pop(0) if self._isready else 0
        return subprocess.CompletedProcess(args, rc)


def test_start_runs_docker_run(tmp_path):
    runner = FakeRunner()
    pg = EphemeralPostgres(SPEC, tmp_path, runner=runner)
    pg.start()
    assert runner.calls[0][0][:3] == ["docker", "run", "-d"]
    assert runner.calls[0][1].get("check") is True


def test_wait_ready_polls_until_zero(tmp_path):
    runner = FakeRunner(isready_codes=(1, 1, 0))
    slept = []
    pg = EphemeralPostgres(SPEC, tmp_path, runner=runner)
    pg.wait_ready(attempts=5, sleep=0.01, sleeper=slept.append)
    assert sum(1 for c, _ in runner.calls if "pg_isready" in c) == 3
    assert len(slept) == 2  # slept after the two failures, not after success


def test_wait_ready_times_out(tmp_path):
    runner = FakeRunner(isready_codes=(1, 1, 1))
    pg = EphemeralPostgres(SPEC, tmp_path, runner=runner)
    with pytest.raises(TimeoutError):
        pg.wait_ready(attempts=3, sleep=0.0, sleeper=lambda _s: None)


def test_run_sql_pipes_input(tmp_path):
    runner = FakeRunner()
    pg = EphemeralPostgres(SPEC, tmp_path, runner=runner)
    pg.run_sql("SELECT 1;")
    args, kwargs = runner.calls[-1]
    assert args[:4] == ["docker", "exec", "-i", "c"]
    assert kwargs["input"] == b"SELECT 1;"
    assert kwargs.get("check") is True


def test_teardown_does_not_check(tmp_path):
    runner = FakeRunner()
    pg = EphemeralPostgres(SPEC, tmp_path, runner=runner)
    pg.teardown()
    args, kwargs = runner.calls[-1]
    assert args == ["docker", "rm", "-f", "c"]
    assert kwargs.get("check") is False

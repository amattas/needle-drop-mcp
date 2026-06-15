import shutil
import time

import duckdb
import pytest

from needledrop.musicbrainz.materialize import attach, materialize_all
from needledrop.musicbrainz.postgres import EphemeralPostgres, PostgresSpec

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker")

SPEC = PostgresSpec(
    image="postgres:18",
    container="needledrop-mb-import-test",
    port=55439,
    db="musicbrainz",
    user="musicbrainz",
    password="testpw",
)


@pytest.mark.skipif(not DOCKER, reason="docker not available")
def test_bridge_end_to_end(tmp_path):
    # Synthetic dump: mbdump/<table> tab-separated, \N nulls, no header.
    mbdump = tmp_path / "extracted" / "mbdump"
    mbdump.mkdir(parents=True)
    (mbdump / "artist").write_text("1\tNine Inch Nails\n2\tAphex Twin\n")

    pg = EphemeralPostgres(SPEC, tmp_path / "extracted")
    try:
        pg.start()
        pg.wait_ready(attempts=60, sleep=1.0)
        pg.run_sql("CREATE SCHEMA IF NOT EXISTS musicbrainz;")
        pg.run_sql(
            "SET search_path = musicbrainz, public;\n"
            "CREATE TABLE musicbrainz.artist (id INTEGER, name TEXT);"
        )
        pg.copy_table("artist", "/dump/mbdump/artist")

        con = duckdb.connect(str(tmp_path / "library.duckdb"))
        attach(con, host="127.0.0.1", port=SPEC.port, db=SPEC.db,
               user=SPEC.user, password=SPEC.password)
        tables = materialize_all(con)

        assert "artist" in tables
        rows = con.execute('SELECT id, name FROM "mb_artist" ORDER BY id').fetchall()
        assert rows == [(1, "Nine Inch Nails"), (2, "Aphex Twin")]
    finally:
        pg.teardown()
        time.sleep(0.1)  # brief settle for socket close before tmp_path teardown

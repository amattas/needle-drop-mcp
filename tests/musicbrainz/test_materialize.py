from needledrop.musicbrainz.materialize import attach_sql, materialize_sql


def test_attach_sql():
    sql = attach_sql(host="127.0.0.1", port=55432, db="musicbrainz", user="mb", password="pw")
    assert sql == (
        "ATTACH 'host=127.0.0.1 port=55432 dbname=musicbrainz user=mb password=pw' "
        "AS pg (TYPE postgres, READ_ONLY)"
    )


def test_materialize_sql_prefixes_and_quotes():
    assert materialize_sql("release_group") == (
        'CREATE OR REPLACE TABLE "mb_release_group" AS '
        'SELECT * FROM pg.musicbrainz."release_group"'
    )

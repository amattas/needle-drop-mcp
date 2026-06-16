from datetime import datetime

from needledrop.analysis.missing_albums import find_missing_core_albums
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import record_library_item, upsert_album
from needledrop.models.enums import FindingType


def _db():
    con = connect(":memory:")
    init_schema(con)
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit_name (artist_credit INTEGER, position INTEGER, "
                "artist INTEGER, name VARCHAR, join_phrase VARCHAR)")
    con.execute("CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR, "
                "artist_credit INTEGER, type INTEGER)")
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type_join "
                "(release_group INTEGER, secondary_type INTEGER)")
    con.execute("INSERT INTO mb_release_group_primary_type VALUES (1, 'Album'), (2, 'Single')")
    con.execute(
        "INSERT INTO mb_release_group_secondary_type VALUES (5, 'Live'), (6, 'Compilation')"
    )
    con.execute("INSERT INTO mb_artist VALUES (1, 'gid-lp', 'Linkin Park', 'Linkin Park')")
    con.execute("INSERT INTO mb_artist_credit_name VALUES (10, 0, 1, 'Linkin Park', '')")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'rg-ht', 'Hybrid Theory', 10, 1)")
    con.execute("INSERT INTO mb_release_group VALUES (101, 'rg-met', 'Meteora', 10, 1)")
    con.execute("INSERT INTO mb_release_group VALUES (102, 'rg-live', 'Live in Texas', 10, 1)")
    con.execute("INSERT INTO mb_release_group_secondary_type_join VALUES (102, 5)")
    return con


def _own_album(con, *, apple_id, title, rg_mbid):
    album_id = upsert_album(con, title=title, release_group_mbid=rg_mbid,
                            external_ids={"apple": apple_id})
    record_library_item(
        con, service="apple_music", service_item_id=apple_id, item_type="album",
        canonical_id=album_id, match_method="upc", seen_at=datetime(2026, 6, 15, 12, 0, 0),
    )


def test_finds_unowned_studio_album_by_owned_artist():
    con = _db()
    _own_album(con, apple_id="l.1", title="Hybrid Theory", rg_mbid="rg-ht")
    findings = find_missing_core_albums(con)
    assert [f.finding_type for f in findings] == [FindingType.MISSING_CORE_ALBUM]
    assert "Meteora" in findings[0].description
    assert findings[0].recommendation.payload["release_group_mbid"] == "rg-met"


def test_owning_all_studio_albums_yields_nothing():
    con = _db()
    _own_album(con, apple_id="l.1", title="Hybrid Theory", rg_mbid="rg-ht")
    _own_album(con, apple_id="l.2", title="Meteora", rg_mbid="rg-met")
    assert find_missing_core_albums(con) == []


def test_handles_uuid_gid_columns():
    # Real MusicBrainz materializes mb_*.gid as UUID. The owned-vs-catalog comparison
    # uses `NOT IN`, which (unlike `=`) won't auto-cast UUID vs our VARCHAR mbids — so
    # gid is CAST to VARCHAR. Regression for the BinderException this raised on a real dump.
    con = connect(":memory:")
    init_schema(con)
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid UUID, name VARCHAR, sort_name VARCHAR)")
    con.execute(
        "CREATE TABLE mb_artist_credit_name "
        "(artist_credit INTEGER, position INTEGER, artist INTEGER, "
        "name VARCHAR, join_phrase VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid UUID, name VARCHAR, artist_credit INTEGER, type INTEGER)"
    )
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type (id INTEGER, name VARCHAR)")
    con.execute(
        "CREATE TABLE mb_release_group_secondary_type_join "
        "(release_group INTEGER, secondary_type INTEGER)"
    )
    con.execute("INSERT INTO mb_release_group_primary_type VALUES (1, 'Album')")
    con.execute(
        "INSERT INTO mb_artist VALUES (1, '11111111-1111-1111-1111-111111111111', 'Muse', 'Muse')"
    )
    con.execute("INSERT INTO mb_artist_credit_name VALUES (10, 0, 1, 'Muse', '')")
    con.execute(
        "INSERT INTO mb_release_group VALUES "
        "(100, '22222222-2222-2222-2222-222222222222', 'Absolution', 10, 1)"
    )
    con.execute(
        "INSERT INTO mb_release_group VALUES "
        "(101, '33333333-3333-3333-3333-333333333333', 'Black Holes and Revelations', 10, 1)"
    )
    _own_album(con, apple_id="l.1", title="Absolution",
               rg_mbid="22222222-2222-2222-2222-222222222222")
    findings = find_missing_core_albums(con)
    assert [f.recommendation.payload["release_group_mbid"] for f in findings] == [
        "33333333-3333-3333-3333-333333333333"
    ]

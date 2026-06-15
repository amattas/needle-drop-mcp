from datetime import datetime

from needledrop.analysis.compilation_pollution import find_compilation_pollution
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import record_library_item, upsert_album
from needledrop.models.enums import FindingType

VARIOUS_ARTISTS_GID = "89ad4ac3-39f7-470e-963a-56509c546377"


def _db():
    con = connect(":memory:")
    init_schema(con)
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit_name (artist_credit INTEGER, position INTEGER, "
                "artist INTEGER, name VARCHAR, join_phrase VARCHAR)")
    con.execute("CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR, "
                "artist_credit INTEGER, type INTEGER)")
    con.execute("CREATE TABLE mb_release_group_secondary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type_join "
                "(release_group INTEGER, secondary_type INTEGER)")
    con.execute("INSERT INTO mb_release_group_secondary_type VALUES (1, 'Compilation'), (2, 'Soundtrack')")
    return con


def _own_album(con, *, apple_id, title, rg_mbid):
    album_id = upsert_album(con, title=title, release_group_mbid=rg_mbid,
                            external_ids={"apple": apple_id})
    record_library_item(con, service="apple_music", service_item_id=apple_id, item_type="album",
                        canonical_id=album_id, match_method="upc", seen_at=datetime(2026, 6, 15, 12, 0, 0))


def test_flags_compilation_secondary_type():
    con = _db()
    con.execute("INSERT INTO mb_release_group VALUES (10, 'rg-comp', 'Now 100', 50, 1)")
    con.execute("INSERT INTO mb_release_group_secondary_type_join VALUES (10, 1)")
    _own_album(con, apple_id="l.1", title="Now 100", rg_mbid="rg-comp")
    findings = find_compilation_pollution(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.COMPILATION_POLLUTION


def test_flags_various_artists_credit():
    con = _db()
    con.execute("INSERT INTO mb_artist VALUES (99, '%s', 'Various Artists', 'Various Artists')"
                % VARIOUS_ARTISTS_GID)
    con.execute("INSERT INTO mb_artist_credit_name VALUES (60, 0, 99, 'Various Artists', '')")
    con.execute("INSERT INTO mb_release_group VALUES (11, 'rg-va', 'Movie OST', 60, 1)")
    _own_album(con, apple_id="l.2", title="Movie OST", rg_mbid="rg-va")
    assert len(find_compilation_pollution(con)) == 1


def test_regular_album_not_flagged():
    con = _db()
    con.execute("INSERT INTO mb_release_group VALUES (12, 'rg-ok', 'OK Computer', 70, 1)")
    _own_album(con, apple_id="l.3", title="OK Computer", rg_mbid="rg-ok")
    assert find_compilation_pollution(con) == []

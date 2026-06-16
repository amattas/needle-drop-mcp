from needledrop.analysis.single_replaced import find_single_replaced
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.models.enums import FindingType


def _own_album_item(con, *, title, apple_id):
    con.execute(
        "INSERT INTO albums (title, external_ids_json) VALUES (?, json_object('apple', ?))",
        [title, apple_id],
    )
    album_id = con.execute("SELECT id FROM albums WHERE title = ?", [title]).fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', ?, 'album', ?, 'present')",
        [apple_id, album_id],
    )
    return album_id


def _own_track(con, *, service_item_id, title, recording_mbid, album_id=None):
    con.execute(
        "INSERT INTO tracks (title, recording_mbid, album_id) VALUES (?, ?, ?)",
        [title, recording_mbid, album_id],
    )
    track_id = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', ?, 'track', ?, 'present')",
        [service_item_id, track_id],
    )
    return track_id


def test_find_single_replaced_flags_standalone_also_on_owned_album():
    con = connect(":memory:")
    init_schema(con)
    album_id = _own_album_item(con, title="Dookie", apple_id="a.dookie")
    _own_track(con, service_item_id="s.album", title="Basket Case",
               recording_mbid="rec-bc", album_id=album_id)
    single = _own_track(con, service_item_id="s.single", title="Basket Case",
                        recording_mbid="rec-bc", album_id=None)
    findings = find_single_replaced(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.SINGLE_REPLACED_BY_ALBUM
    assert findings[0].entity_id == single


def test_find_single_replaced_ignores_when_no_owned_album_copy():
    con = connect(":memory:")
    init_schema(con)
    _own_track(con, service_item_id="s.single", title="Basket Case",
               recording_mbid="rec-bc", album_id=None)
    assert find_single_replaced(con) == []

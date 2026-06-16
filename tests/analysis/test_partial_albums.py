from needledrop.analysis.partial_albums import find_partial_albums
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.models.enums import FindingType


def _own_album(con, *, title, total_tracks, apple_id):
    con.execute(
        "INSERT INTO albums (title, total_tracks, external_ids_json) "
        "VALUES (?, ?, json_object('apple', ?))",
        [title, total_tracks, apple_id],
    )
    album_id = con.execute("SELECT id FROM albums WHERE title = ?", [title]).fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', ?, 'album', ?, 'present')",
        [apple_id, album_id],
    )
    return album_id


def _own_track_on(con, *, album_id, title, service_item_id):
    con.execute("INSERT INTO tracks (title, album_id) VALUES (?, ?)", [title, album_id])
    track_id = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', ?, 'track', ?, 'present')",
        [service_item_id, track_id],
    )


def test_find_partial_albums_flags_incomplete_album():
    con = connect(":memory:")
    init_schema(con)
    album_id = _own_album(con, title="Dookie", total_tracks=3, apple_id="a.dookie")
    _own_track_on(con, album_id=album_id, title="Burnout", service_item_id="s.1")
    _own_track_on(con, album_id=album_id, title="Having a Blast", service_item_id="s.2")
    findings = find_partial_albums(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.PARTIAL_ALBUM
    assert "2 of 3" in findings[0].description


def test_find_partial_albums_ignores_complete_and_empty():
    con = connect(":memory:")
    init_schema(con)
    complete = _own_album(con, title="EP", total_tracks=2, apple_id="a.ep")
    _own_track_on(con, album_id=complete, title="A", service_item_id="s.1")
    _own_track_on(con, album_id=complete, title="B", service_item_id="s.2")
    _own_album(con, title="Empty", total_tracks=5, apple_id="a.empty")  # owned==0 -> not partial
    unknown = _own_album(con, title="Mystery", total_tracks=None, apple_id="a.mystery")
    _own_track_on(con, album_id=unknown, title="X", service_item_id="s.3")  # unknown total -> skip
    assert find_partial_albums(con) == []

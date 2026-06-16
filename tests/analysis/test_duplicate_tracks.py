from needledrop.analysis.duplicate_tracks import find_duplicate_tracks
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.models.enums import FindingType


def _add_track(con, *, service_item_id, title, recording_mbid=None, isrc=None):
    if recording_mbid is not None:
        con.execute(
            "INSERT INTO tracks (title, recording_mbid) VALUES (?, ?)", [title, recording_mbid]
        )
    else:
        con.execute("INSERT INTO tracks (title, isrc) VALUES (?, ?)", [title, isrc])
    track_id = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', ?, 'track', ?, 'present')",
        [service_item_id, track_id],
    )


def test_find_duplicate_tracks_groups_by_recording_mbid():
    con = connect(":memory:")
    init_schema(con)
    _add_track(con, service_item_id="s.1", title="Creep", recording_mbid="rec-creep")
    _add_track(con, service_item_id="s.2", title="Creep", recording_mbid="rec-creep")
    _add_track(con, service_item_id="s.3", title="No Surprises", recording_mbid="rec-ns")
    findings = find_duplicate_tracks(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.DUPLICATE_TRACK
    assert "2 copies" in findings[0].description


def test_find_duplicate_tracks_groups_unmatched_by_isrc():
    con = connect(":memory:")
    init_schema(con)
    _add_track(con, service_item_id="s.1", title="Creep", isrc="GBAYE9900001")
    _add_track(con, service_item_id="s.2", title="Creep", isrc="GBAYE9900001")
    findings = find_duplicate_tracks(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.DUPLICATE_TRACK


def test_find_duplicate_tracks_ignores_singletons_and_unidentified():
    con = connect(":memory:")
    init_schema(con)
    _add_track(con, service_item_id="s.1", title="Creep", recording_mbid="rec-creep")
    _add_track(con, service_item_id="s.2", title="Unknown")  # no mbid/isrc -> not grouped
    assert find_duplicate_tracks(con) == []

from datetime import datetime

from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import record_library_item, upsert_album, upsert_artist, upsert_track


def _con():
    con = connect(":memory:")
    init_schema(con)
    return con


def test_upsert_artist_inserts_and_returns_id():
    con = _con()
    artist_id = upsert_artist(
        con, canonical_name="Radiohead", mbid="mbid-r", sort_name="Radiohead"
    )
    assert isinstance(artist_id, int)
    row = con.execute(
        "SELECT canonical_name, mbid FROM artists WHERE id = ?", [artist_id]
    ).fetchone()
    assert row == ("Radiohead", "mbid-r")


def test_upsert_artist_dedupes_by_mbid():
    con = _con()
    first = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    again = upsert_artist(con, canonical_name="Radiohead (updated)", mbid="mbid-r")
    assert again == first
    assert con.execute("SELECT count(*) FROM artists").fetchone()[0] == 1
    assert con.execute("SELECT canonical_name FROM artists").fetchone()[0] == "Radiohead (updated)"


def test_upsert_artist_dedupes_by_apple_id_when_no_mbid():
    con = _con()
    first = upsert_artist(con, canonical_name="Radiohead", external_ids={"apple": "A1"})
    again = upsert_artist(
        con, canonical_name="Radiohead", external_ids={"apple": "A1"}, mbid="mbid-r"
    )
    assert again == first
    assert con.execute("SELECT count(*) FROM artists").fetchone()[0] == 1
    assert con.execute("SELECT mbid FROM artists").fetchone()[0] == "mbid-r"


def test_upsert_album_dedupes_by_apple_id_and_backfills_mbid():
    con = _con()
    artist_id = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    first = upsert_album(
        con, title="OK Computer", artist_id=artist_id, external_ids={"apple": "alb1"}
    )
    again = upsert_album(
        con,
        title="OK Computer",
        artist_id=artist_id,
        release_group_mbid="rg-okc",
        version_class="standard",
        external_ids={"apple": "alb1"},
    )
    assert again == first
    assert con.execute("SELECT count(*) FROM albums").fetchone()[0] == 1
    row = con.execute(
        "SELECT release_group_mbid, version_class FROM albums WHERE id = ?", [first]
    ).fetchone()
    assert row == ("rg-okc", "standard")


def test_upsert_album_dedupes_by_release_group_mbid():
    con = _con()
    artist_id = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    a = upsert_album(con, title="OK Computer", artist_id=artist_id, release_group_mbid="rg-okc")
    b = upsert_album(con, title="OK Computer", artist_id=artist_id, release_group_mbid="rg-okc")
    assert a == b
    assert con.execute("SELECT count(*) FROM albums").fetchone()[0] == 1


def test_upsert_track_inserts_with_recording_mbid():
    con = _con()
    artist_id = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    album_id = upsert_album(con, title="OK Computer", artist_id=artist_id)
    track_id = upsert_track(
        con,
        title="Karma Police",
        album_id=album_id,
        artist_id=artist_id,
        recording_mbid="rec-karma",
        isrc="GBAYE9700116",
        external_ids={"apple": "trk1"},
    )
    row = con.execute(
        "SELECT recording_mbid, isrc FROM tracks WHERE id = ?", [track_id]
    ).fetchone()
    assert row == ("rec-karma", "GBAYE9700116")


def test_record_library_item_inserts_present():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    item_id = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album",
        canonical_id=None, match_confidence=None, match_method="none", seen_at=t,
    )
    row = con.execute(
        "SELECT status, added_at, last_seen_at, match_method FROM library_items WHERE id = ?",
        [item_id],
    ).fetchone()
    assert row[0] == "present"
    assert row[1] == t and row[2] == t
    assert row[3] == "none"


def test_record_library_item_upserts_preserving_added_at():
    con = _con()
    t1 = datetime(2026, 6, 1, 10, 0, 0)
    t2 = datetime(2026, 6, 15, 12, 0, 0)
    first = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album", seen_at=t1,
    )
    again = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album",
        canonical_id=42, match_confidence=1.0, match_method="upc", seen_at=t2,
    )
    assert again == first
    row = con.execute(
        "SELECT added_at, last_seen_at, canonical_id, match_confidence, match_method "
        "FROM library_items WHERE id = ?",
        [first],
    ).fetchone()
    assert row[0] == t1
    assert row[1] == t2
    assert row[2] == 42 and row[3] == 1.0 and row[4] == "upc"


from needledrop.db.repository import save_match_candidates


def test_save_match_candidates_replaces_pending():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    item_id = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album", seen_at=t,
    )
    save_match_candidates(con, library_item_id=item_id, candidates=[
        {"candidate_mbid": "rg-1", "candidate_kind": "release_group", "score": 0.8, "method": "fuzzy"},
        {"candidate_mbid": "rg-2", "candidate_kind": "release_group", "score": 0.6, "method": "fuzzy"},
    ])
    assert con.execute(
        "SELECT count(*) FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchone()[0] == 2

    save_match_candidates(con, library_item_id=item_id, candidates=[
        {"candidate_mbid": "rg-3", "candidate_kind": "release_group", "score": 0.9, "method": "fuzzy"},
    ])
    rows = con.execute(
        "SELECT candidate_mbid, status FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchall()
    assert rows == [("rg-3", "pending")]


def test_save_match_candidates_empty_is_noop():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    item_id = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album", seen_at=t,
    )
    save_match_candidates(con, library_item_id=item_id, candidates=[])
    assert con.execute(
        "SELECT count(*) FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchone()[0] == 0

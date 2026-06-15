from datetime import datetime

import duckdb

from needledrop.connectors.apple_models import LibraryAlbum, LibrarySong
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.services.sync import diff_sync, sync_library


def _seed_mb(con):
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit (id INTEGER, name VARCHAR)")
    con.execute(
        "CREATE TABLE mb_artist_credit_name "
        "(artist_credit INTEGER, position INTEGER, artist INTEGER, name VARCHAR, join_phrase VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, type INTEGER)"
    )
    con.execute(
        "CREATE TABLE mb_release (id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, "
        "release_group INTEGER, barcode VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mb_recording (id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, "
        "length INTEGER)"
    )
    con.execute("CREATE TABLE mb_isrc (id INTEGER, recording INTEGER, isrc VARCHAR)")
    con.execute("INSERT INTO mb_artist VALUES (1, 'gid-radiohead', 'Radiohead', 'Radiohead')")
    con.execute("INSERT INTO mb_artist_credit VALUES (10, 'Radiohead')")
    con.execute("INSERT INTO mb_artist_credit_name VALUES (10, 0, 1, 'Radiohead', '')")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'gid-okc', 'OK Computer', 10, 1)")
    con.execute(
        "INSERT INTO mb_release VALUES (1000, 'gid-rel', 'OK Computer', 10, 100, '0724385522123')"
    )


class FakeConnector:
    def __init__(self, albums=(), songs=(), playlists=()):
        self._albums, self._songs, self._playlists = albums, songs, playlists

    def iter_library_albums(self):
        return iter(self._albums)

    def iter_library_songs(self):
        return iter(self._songs)

    def iter_library_playlists(self):
        return iter(self._playlists)


def _db():
    con = connect(":memory:")
    init_schema(con)
    _seed_mb(con)
    return con


def test_sync_matches_album_by_upc_and_records_item():
    con = _db()
    now = datetime(2026, 6, 15, 12, 0, 0)
    connector = FakeConnector(albums=[
        LibraryAlbum(id="l.a1", name="OK Computer", artist_name="Radiohead", upc="0724385522123")
    ])
    summary = sync_library(connector, con, now=now)
    assert summary == {"added": 1, "removed": 0, "present": 1}
    row = con.execute(
        "SELECT li.match_method, a.release_group_mbid "
        "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "WHERE li.service_item_id = 'l.a1'"
    ).fetchone()
    assert row == ("upc", "gid-okc")


def test_sync_unmatched_album_saves_candidates():
    con = _db()
    now = datetime(2026, 6, 15, 12, 0, 0)
    connector = FakeConnector(albums=[
        LibraryAlbum(id="l.b1", name="In Rainbows", artist_name="Radiohead")
    ])
    sync_library(connector, con, now=now)
    item_id = con.execute(
        "SELECT id FROM library_items WHERE service_item_id = 'l.b1'"
    ).fetchone()[0]
    method = con.execute(
        "SELECT match_method FROM library_items WHERE id = ?", [item_id]
    ).fetchone()[0]
    assert method == "none"
    candidates = con.execute(
        "SELECT candidate_mbid FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchall()
    assert ("gid-okc",) in candidates


def test_sync_marks_unseen_items_removed_across_runs():
    con = _db()
    run1 = datetime(2026, 6, 1, 10, 0, 0)
    sync_library(connector=FakeConnector(albums=[
        LibraryAlbum(id="l.gone", name="OK Computer", artist_name="Radiohead", upc="0724385522123")
    ]), con=con, now=run1)
    run2 = datetime(2026, 6, 15, 12, 0, 0)
    summary = sync_library(connector=FakeConnector(albums=[]), con=con, now=run2)
    assert summary["removed"] == 1
    status = con.execute(
        "SELECT status FROM library_items WHERE service_item_id = 'l.gone'"
    ).fetchone()[0]
    assert status == "removed"


def test_sync_matches_track_by_isrc():
    con = _db()
    con.execute("INSERT INTO mb_recording VALUES (5000, 'gid-karma', 'Karma Police', 10, 261000)")
    con.execute("INSERT INTO mb_isrc VALUES (1, 5000, 'GBAYE9700116')")
    now = datetime(2026, 6, 15, 12, 0, 0)
    connector = FakeConnector(songs=[
        LibrarySong(id="l.s1", name="Karma Police", artist_name="Radiohead", isrc="GBAYE9700116")
    ])
    sync_library(connector, con, now=now)
    row = con.execute(
        "SELECT li.match_method, t.recording_mbid "
        "FROM library_items li JOIN tracks t ON li.canonical_id = t.id "
        "WHERE li.service_item_id = 'l.s1'"
    ).fetchone()
    assert row == ("isrc", "gid-karma")


def test_diff_sync_returns_latest_completed_run_summary():
    con = _db()
    sync_library(FakeConnector(albums=[
        LibraryAlbum(id="l.a1", name="OK Computer", artist_name="Radiohead", upc="0724385522123")
    ]), con, now=datetime(2026, 6, 1, 10, 0, 0))
    sync_library(FakeConnector(albums=[
        LibraryAlbum(id="l.a1", name="OK Computer", artist_name="Radiohead", upc="0724385522123"),
        LibraryAlbum(id="l.a2", name="Kid A", artist_name="Radiohead"),
    ]), con, now=datetime(2026, 6, 15, 12, 0, 0))
    diff = diff_sync(con)
    assert diff == {"added": 1, "removed": 0, "present": 2}


def test_diff_sync_no_runs_returns_empty():
    con = _db()
    assert diff_sync(con) == {}

from datetime import datetime

from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import get_findings, record_library_item, upsert_album
from needledrop.services.cleanup import ignore_finding, mark_finding_resolved, run_cleanup_scan


def _db():
    con = connect(":memory:")
    init_schema(con)
    # Minimal mb_* so the compilation/missing analyses can run (empty is fine).
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit_name (artist_credit INTEGER, position INTEGER, "
                "artist INTEGER, name VARCHAR, join_phrase VARCHAR)")
    con.execute("CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR, "
                "artist_credit INTEGER, type INTEGER)")
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type_join "
                "(release_group INTEGER, secondary_type INTEGER)")
    return con


def _own_album(con, *, apple_id, title, rg_mbid):
    album_id = upsert_album(con, title=title, release_group_mbid=rg_mbid,
                            external_ids={"apple": apple_id})
    record_library_item(
        con, service="apple_music", service_item_id=apple_id, item_type="album",
        canonical_id=album_id, match_method="upc", seen_at=datetime(2026, 6, 15, 12, 0, 0),
    )


def test_run_cleanup_scan_persists_duplicate_finding():
    con = _db()
    _own_album(con, apple_id="l.1", title="Dookie", rg_mbid="rg-d")
    _own_album(con, apple_id="l.2", title="Dookie (Deluxe)", rg_mbid="rg-d")
    counts = run_cleanup_scan(con, now=datetime(2026, 6, 15, 12, 0, 0))
    assert counts == {"duplicate_album": 1}
    assert len(get_findings(con)) == 1


def test_mark_finding_resolved_removes_from_open():
    con = _db()
    _own_album(con, apple_id="l.1", title="Dookie", rg_mbid="rg-d")
    _own_album(con, apple_id="l.2", title="Dookie (Deluxe)", rg_mbid="rg-d")
    run_cleanup_scan(con, now=datetime(2026, 6, 15, 12, 0, 0))
    fid = get_findings(con)[0].id
    mark_finding_resolved(con, fid, now=datetime(2026, 6, 16, 9, 0, 0))
    assert get_findings(con) == []
    assert len(get_findings(con, include_closed=True)) == 1


def test_ignore_finding_removes_from_open():
    con = _db()
    _own_album(con, apple_id="l.1", title="Dookie", rg_mbid="rg-d")
    _own_album(con, apple_id="l.2", title="Dookie (Deluxe)", rg_mbid="rg-d")
    run_cleanup_scan(con, now=datetime(2026, 6, 15, 12, 0, 0))
    fid = get_findings(con)[0].id
    ignore_finding(con, fid, now=datetime(2026, 6, 16, 9, 0, 0))
    assert get_findings(con) == []


def test_run_cleanup_scan_includes_duplicate_tracks(tmp_path):
    from needledrop.db.duckdb_store import connect, init_schema
    from needledrop.services.cleanup import run_cleanup_scan
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    for sid in ("s.1", "s.2"):
        con.execute("INSERT INTO tracks (title, recording_mbid) VALUES ('Creep', 'rec-creep')")
        track_id = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
        con.execute(
            "INSERT INTO library_items "
            "(service, service_item_id, item_type, canonical_id, status) "
            "VALUES ('apple_music', ?, 'track', ?, 'present')",
            [sid, track_id],
        )
    counts = run_cleanup_scan(con, now=datetime(2026, 6, 15))
    assert counts.get("duplicate_track") == 1

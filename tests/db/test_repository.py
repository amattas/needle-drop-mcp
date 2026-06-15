import json as _json
from datetime import datetime

from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import (
    complete_sync_run,
    get_library_albums,
    get_library_summary,
    mark_unseen_removed,
    record_library_item,
    save_match_candidates,
    start_sync_run,
    upsert_album,
    upsert_artist,
    upsert_track,
)


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


def test_upsert_album_dedupes_by_release_mbid():
    con = _con()
    artist_id = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    a = upsert_album(con, title="OK Computer", artist_id=artist_id, release_mbid="rel-okc")
    b = upsert_album(con, title="OK Computer", artist_id=artist_id, release_mbid="rel-okc")
    assert a == b
    assert con.execute("SELECT count(*) FROM albums").fetchone()[0] == 1


def test_upsert_album_keeps_distinct_editions_of_one_release_group():
    # Standard + Deluxe share a release-group but are separate owned editions —
    # they must stay distinct canonical rows so each keeps its version_class.
    con = _con()
    artist_id = upsert_artist(con, canonical_name="Green Day", mbid="mbid-gd")
    standard = upsert_album(
        con, title="Dookie", artist_id=artist_id, release_group_mbid="rg-dookie",
        version_class="standard", external_ids={"apple": "alb-std"},
    )
    deluxe = upsert_album(
        con, title="Dookie (Deluxe)", artist_id=artist_id, release_group_mbid="rg-dookie",
        version_class="deluxe", external_ids={"apple": "alb-dlx"},
    )
    assert standard != deluxe
    assert con.execute("SELECT count(*) FROM albums").fetchone()[0] == 2
    # Both share the release-group (the grouping key the dedup analysis uses).
    rgs = con.execute("SELECT DISTINCT release_group_mbid FROM albums").fetchall()
    assert rgs == [("rg-dookie",)]
    classes = {r[0] for r in con.execute("SELECT version_class FROM albums").fetchall()}
    assert classes == {"standard", "deluxe"}


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


def test_save_match_candidates_replaces_pending():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    item_id = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album", seen_at=t,
    )
    save_match_candidates(con, library_item_id=item_id, candidates=[
        {"candidate_mbid": "rg-1", "candidate_kind": "release_group", "score": 0.8,
         "method": "fuzzy"},
        {"candidate_mbid": "rg-2", "candidate_kind": "release_group", "score": 0.6,
         "method": "fuzzy"},
    ])
    assert con.execute(
        "SELECT count(*) FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchone()[0] == 2

    save_match_candidates(con, library_item_id=item_id, candidates=[
        {"candidate_mbid": "rg-3", "candidate_kind": "release_group", "score": 0.9,
         "method": "fuzzy"},
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


def test_sync_run_lifecycle():
    con = _con()
    started = datetime(2026, 6, 15, 12, 0, 0)
    run_id = start_sync_run(con, service="apple_music", started_at=started)
    assert con.execute(
        "SELECT status FROM sync_runs WHERE id = ?", [run_id]
    ).fetchone()[0] == "running"

    completed = datetime(2026, 6, 15, 12, 5, 0)
    complete_sync_run(con, run_id=run_id, completed_at=completed, summary={"albums": 3})
    row = con.execute(
        "SELECT status, completed_at, summary_json FROM sync_runs WHERE id = ?", [run_id]
    ).fetchone()
    assert row[0] == "completed"
    assert row[1] == completed
    assert _json.loads(row[2]) == {"albums": 3}


def test_mark_unseen_removed():
    con = _con()
    old = datetime(2026, 6, 1, 10, 0, 0)
    now = datetime(2026, 6, 15, 12, 0, 0)
    stale = record_library_item(
        con, service="apple_music", service_item_id="l.gone", item_type="album", seen_at=old,
    )
    fresh = record_library_item(
        con, service="apple_music", service_item_id="l.here", item_type="album", seen_at=now,
    )
    removed_count = mark_unseen_removed(con, service="apple_music", run_started_at=now)
    assert removed_count == 1
    statuses = dict(con.execute("SELECT id, status FROM library_items").fetchall())
    assert statuses[stale] == "removed"
    assert statuses[fresh] == "present"


def _seed_album(con, *, apple_id, title, rg_mbid, method, seen_at):
    artist_id = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    album_id = upsert_album(
        con, title=title, artist_id=artist_id, release_group_mbid=rg_mbid,
        external_ids={"apple": apple_id},
    )
    record_library_item(
        con, service="apple_music", service_item_id=apple_id, item_type="album",
        canonical_id=album_id, match_confidence=1.0, match_method=method, seen_at=seen_at,
    )


def test_get_library_summary_counts_present_by_type_and_match():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    _seed_album(
        con, apple_id="l.a1", title="OK Computer", rg_mbid="rg1", method="upc", seen_at=t
    )
    _seed_album(con, apple_id="l.a2", title="Kid A", rg_mbid="rg2", method="none", seen_at=t)
    summary = get_library_summary(con)
    assert summary["album"] == 2
    assert summary["matched"] == 1
    assert summary["unmatched"] == 1


def test_get_library_albums_returns_present_albums():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    _seed_album(
        con, apple_id="l.a1", title="OK Computer", rg_mbid="rg1", method="upc", seen_at=t
    )
    albums = get_library_albums(con)
    assert len(albums) == 1
    assert albums[0]["title"] == "OK Computer"
    assert albums[0]["release_group_mbid"] == "rg1"


def test_upsert_artist_dedupes_by_name_when_no_ids():
    con = _con()
    first = upsert_artist(con, canonical_name="Radiohead")
    again = upsert_artist(con, canonical_name="Radiohead")
    assert again == first
    assert con.execute("SELECT count(*) FROM artists").fetchone()[0] == 1


def test_upsert_artist_name_dedup_does_not_collide_with_id_matched():
    con = _con()
    with_mbid = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    name_only = upsert_artist(con, canonical_name="Radiohead")
    assert name_only == with_mbid
    assert con.execute("SELECT count(*) FROM artists").fetchone()[0] == 1


from needledrop.db.repository import get_findings, save_cleanup_findings
from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def test_save_and_get_findings_roundtrip():
    con = _con()
    finding = CleanupFinding(
        finding_type=FindingType.DUPLICATE_ALBUM,
        severity=FindingSeverity.LOW,
        entity_id=7,
        description="You own 2 versions of 'Dookie'.",
        recommendation=Recommendation(action="review_duplicates", payload={"n": 2}),
    )
    save_cleanup_findings(con, [finding])
    got = get_findings(con)
    assert len(got) == 1
    assert got[0].finding_type == FindingType.DUPLICATE_ALBUM
    assert got[0].description == "You own 2 versions of 'Dookie'."
    assert got[0].recommendation.action == "review_duplicates"
    assert got[0].recommendation.payload == {"n": 2}


def test_save_replaces_open_findings():
    con = _con()
    save_cleanup_findings(con, [CleanupFinding(
        finding_type=FindingType.DUPLICATE_ALBUM, severity=FindingSeverity.LOW, description="old"
    )])
    save_cleanup_findings(con, [CleanupFinding(
        finding_type=FindingType.DUPLICATE_ALBUM, severity=FindingSeverity.LOW, description="new"
    )])
    descriptions = [f.description for f in get_findings(con)]
    assert descriptions == ["new"]


def test_save_respects_ignored_finding_across_scans():
    con = _con()
    save_cleanup_findings(con, [CleanupFinding(
        finding_type=FindingType.COMPILATION_POLLUTION, severity=FindingSeverity.INFO,
        description="'Now 100' is a compilation.",
    )])
    fid = get_findings(con)[0].id
    con.execute("UPDATE cleanup_findings SET ignored_at = now() WHERE id = ?", [fid])
    save_cleanup_findings(con, [CleanupFinding(
        finding_type=FindingType.COMPILATION_POLLUTION, severity=FindingSeverity.INFO,
        description="'Now 100' is a compilation.",
    )])
    assert get_findings(con) == []

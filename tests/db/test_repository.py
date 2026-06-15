import json as _json
from datetime import datetime

from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import (
    complete_sync_run,
    get_findings,
    get_library_albums,
    get_library_summary,
    get_review_queue,
    list_unmatched,
    mark_unseen_removed,
    record_library_item,
    reject_match,
    resolve_match,
    save_cleanup_findings,
    save_match_candidates,
    search_library,
    start_sync_run,
    upsert_album,
    upsert_artist,
    upsert_track,
)
from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


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


def _seed_titled_items(con):
    """Two albums + one track as present library items with mixed match state."""
    con.execute("INSERT INTO artists (canonical_name) VALUES ('Green Day')")
    artist_id = con.execute("SELECT id FROM artists").fetchone()[0]
    con.execute(
        "INSERT INTO albums (artist_id, title, release_group_mbid) "
        "VALUES (?, 'Dookie', 'rg-dookie')",
        [artist_id],
    )
    con.execute(
        "INSERT INTO albums (artist_id, title) VALUES (?, 'Untagged Bootleg')",
        [artist_id],
    )
    con.execute(
        "INSERT INTO tracks (artist_id, title) VALUES (?, 'Basket Case')",
        [artist_id],
    )
    dookie_id = con.execute("SELECT id FROM albums WHERE title = 'Dookie'").fetchone()[0]
    bootleg_id = con.execute(
        "SELECT id FROM albums WHERE title = 'Untagged Bootleg'"
    ).fetchone()[0]
    track_id = con.execute("SELECT id FROM tracks WHERE title = 'Basket Case'").fetchone()[0]
    # Matched album, unmatched album, matched track.
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_method, status) "
        "VALUES ('apple_music', 'l.dookie', 'album', ?, 'upc', 'present')",
        [dookie_id],
    )
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_method, status) "
        "VALUES ('apple_music', 'l.bootleg', 'album', ?, 'none', 'present')",
        [bootleg_id],
    )
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_method, status) "
        "VALUES ('apple_music', 'l.basket', 'track', ?, 'fuzzy', 'present')",
        [track_id],
    )


def test_list_unmatched_returns_only_unmatched_present_items(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_titled_items(con)
    rows = list_unmatched(con)
    assert [r["title"] for r in rows] == ["Untagged Bootleg"]
    assert rows[0]["item_type"] == "album"
    assert rows[0]["service_item_id"] == "l.bootleg"


def test_search_library_matches_titles_case_insensitively(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_titled_items(con)
    rows = search_library(con, "case")  # lowercase query matches 'Basket Case'
    assert [r["title"] for r in rows] == ["Basket Case"]
    assert rows[0]["item_type"] == "track"
    assert rows[0]["match_method"] == "fuzzy"


def test_search_library_spans_albums_and_tracks(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_titled_items(con)
    titles = {r["title"] for r in search_library(con, "")}  # empty query matches all three titles
    assert titles == {"Dookie", "Untagged Bootleg", "Basket Case"}


def _seed_review_item(con, *, item_type="album", canonical_title="Kid A",
                      service_item_id="l.kida"):
    """A present, unmatched library item with two pending candidates."""
    if item_type == "album":
        con.execute("INSERT INTO albums (title) VALUES (?)", [canonical_title])
        canonical_id = con.execute(
            "SELECT id FROM albums WHERE title = ?", [canonical_title]
        ).fetchone()[0]
        kind = "release_group"
    else:
        con.execute("INSERT INTO tracks (title) VALUES (?)", [canonical_title])
        canonical_id = con.execute(
            "SELECT id FROM tracks WHERE title = ?", [canonical_title]
        ).fetchone()[0]
        kind = "recording"
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_method, status) "
        "VALUES ('apple_music', ?, ?, ?, 'none', 'present')",
        [service_item_id, item_type, canonical_id],
    )
    item_id = con.execute(
        "SELECT id FROM library_items WHERE service_item_id = ?", [service_item_id]
    ).fetchone()[0]
    for mbid, score in [("rg-good", 0.81), ("rg-meh", 0.74)]:
        con.execute(
            "INSERT INTO match_candidates "
            "(library_item_id, candidate_mbid, candidate_kind, score, method, status) "
            "VALUES (?, ?, ?, ?, 'fuzzy', 'pending')",
            [item_id, mbid, kind, score],
        )
    return item_id, canonical_id


def test_get_review_queue_lists_items_with_pending_candidates(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    item_id, _ = _seed_review_item(con)
    queue = get_review_queue(con)
    assert len(queue) == 1
    entry = queue[0]
    assert entry["library_item_id"] == item_id
    assert entry["item_type"] == "album"
    assert entry["title"] == "Kid A"
    assert [c["candidate_mbid"] for c in entry["candidates"]] == ["rg-good", "rg-meh"]
    assert entry["candidates"][0]["candidate_kind"] == "release_group"
    assert entry["candidates"][0]["name"] is None


def test_get_review_queue_enriches_names_when_mb_present(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_review_item(con)
    con.execute("CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR)")
    con.execute("INSERT INTO mb_release_group VALUES (1, 'rg-good', 'Kid A (MB)')")
    queue = get_review_queue(con)
    names = {c["candidate_mbid"]: c["name"] for c in queue[0]["candidates"]}
    assert names["rg-good"] == "Kid A (MB)"
    assert names["rg-meh"] is None


def test_resolve_match_links_canonical_and_flips_statuses(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    item_id, canonical_id = _seed_review_item(con)
    chosen = con.execute(
        "SELECT id FROM match_candidates WHERE candidate_mbid = 'rg-good'"
    ).fetchone()[0]
    result = resolve_match(con, candidate_id=chosen)
    assert result == {
        "library_item_id": item_id,
        "item_type": "album",
        "candidate_mbid": "rg-good",
    }
    assert con.execute(
        "SELECT release_group_mbid FROM albums WHERE id = ?", [canonical_id]
    ).fetchone()[0] == "rg-good"
    method, conf = con.execute(
        "SELECT match_method, match_confidence FROM library_items WHERE id = ?", [item_id]
    ).fetchone()
    assert method == "manual"
    assert conf == 1.0
    statuses = dict(con.execute(
        "SELECT candidate_mbid, status FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchall())
    assert statuses == {"rg-good": "confirmed", "rg-meh": "rejected"}
    assert get_review_queue(con) == []


def test_resolve_match_links_recording_for_track(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    item_id, canonical_id = _seed_review_item(
        con, item_type="track", canonical_title="Idioteque", service_item_id="l.idio"
    )
    chosen = con.execute(
        "SELECT id FROM match_candidates WHERE candidate_mbid = 'rg-good'"
    ).fetchone()[0]
    resolve_match(con, candidate_id=chosen)
    assert con.execute(
        "SELECT recording_mbid FROM tracks WHERE id = ?", [canonical_id]
    ).fetchone()[0] == "rg-good"


def test_get_review_queue_enriches_recording_names_when_mb_present(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_review_item(
        con, item_type="track", canonical_title="Idioteque", service_item_id="l.idio"
    )
    con.execute("CREATE TABLE mb_recording (id INTEGER, gid VARCHAR, name VARCHAR)")
    con.execute("INSERT INTO mb_recording VALUES (1, 'rg-good', 'Idioteque (MB)')")
    queue = get_review_queue(con)
    names = {c["candidate_mbid"]: c["name"] for c in queue[0]["candidates"]}
    assert names["rg-good"] == "Idioteque (MB)"
    assert names["rg-meh"] is None


def test_resolve_match_rejects_candidate_kind_mismatch(tmp_path):
    import pytest

    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    # Album item, but with a 'recording'-kind candidate -> kind mismatch.
    con.execute("INSERT INTO albums (title) VALUES ('Amnesiac')")
    canonical_id = con.execute(
        "SELECT id FROM albums WHERE title = 'Amnesiac'"
    ).fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_method, status) "
        "VALUES ('apple_music', 'l.amne', 'album', ?, 'none', 'present')",
        [canonical_id],
    )
    item_id = con.execute(
        "SELECT id FROM library_items WHERE service_item_id = 'l.amne'"
    ).fetchone()[0]
    con.execute(
        "INSERT INTO match_candidates "
        "(library_item_id, candidate_mbid, candidate_kind, score, method, status) "
        "VALUES (?, 'rec-x', 'recording', 0.9, 'fuzzy', 'pending')",
        [item_id],
    )
    bad = con.execute(
        "SELECT id FROM match_candidates WHERE candidate_mbid = 'rec-x'"
    ).fetchone()[0]
    with pytest.raises(ValueError):
        resolve_match(con, candidate_id=bad)
    # Mismatch raises before the transaction -> nothing changed.
    assert con.execute(
        "SELECT release_group_mbid FROM albums WHERE id = ?", [canonical_id]
    ).fetchone()[0] is None
    assert con.execute(
        "SELECT status FROM match_candidates WHERE id = ?", [bad]
    ).fetchone()[0] == "pending"


def test_resolve_match_rejects_unknown_or_nonpending(tmp_path):
    import pytest

    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    item_id, _ = _seed_review_item(con)
    with pytest.raises(ValueError):
        resolve_match(con, candidate_id=99999)
    chosen = con.execute(
        "SELECT id FROM match_candidates WHERE candidate_mbid = 'rg-good'"
    ).fetchone()[0]
    resolve_match(con, candidate_id=chosen)
    with pytest.raises(ValueError):
        resolve_match(con, candidate_id=chosen)


def test_reject_match_rejects_all_pending(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    item_id, _ = _seed_review_item(con)
    rejected = reject_match(con, library_item_id=item_id)
    assert rejected == 2
    pending = con.execute(
        "SELECT count(*) FROM match_candidates "
        "WHERE library_item_id = ? AND status = 'pending'", [item_id]
    ).fetchone()[0]
    assert pending == 0
    assert con.execute(
        "SELECT match_method FROM library_items WHERE id = ?", [item_id]
    ).fetchone()[0] == "none"
    assert get_review_queue(con) == []

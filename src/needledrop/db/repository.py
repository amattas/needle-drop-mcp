"""Persistence for canonical entities, library items, and sync runs (DuckDB).

Separate from db/duckdb_store.py (connection + schema lifecycle): this module is
the entity/CRUD layer the sync service drives.
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb

from needledrop.db.duckdb_store import table_exists
from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def _dump_external_ids(external_ids: dict[str, str] | None) -> str:
    return json.dumps(external_ids or {}, sort_keys=True)


def upsert_artist(
    con: duckdb.DuckDBPyConnection,
    *,
    canonical_name: str,
    mbid: str | None = None,
    sort_name: str | None = None,
    external_ids: dict[str, str] | None = None,
) -> int:
    """Insert or update an artist, deduping by MBID, then Apple external id, then exact canonical
    name. Returns its id."""
    external_ids = external_ids or {}
    ext_json = _dump_external_ids(external_ids)

    if mbid:
        row = con.execute("SELECT id FROM artists WHERE mbid = ?", [mbid]).fetchone()
        if row:
            con.execute(
                "UPDATE artists SET canonical_name = ?, sort_name = ?, external_ids_json = ? "
                "WHERE id = ?",
                [canonical_name, sort_name, ext_json, row[0]],
            )
            return row[0]

    apple_id = external_ids.get("apple")
    if apple_id:
        row = con.execute(
            "SELECT id FROM artists WHERE json_extract_string(external_ids_json, '$.apple') = ?",
            [apple_id],
        ).fetchone()
        if row:
            con.execute(
                "UPDATE artists SET canonical_name = ?, sort_name = ?, "
                "mbid = COALESCE(?, mbid), external_ids_json = ? WHERE id = ?",
                [canonical_name, sort_name, mbid, ext_json, row[0]],
            )
            return row[0]

    # Last-resort dedup: same display name (name collisions are accepted for an
    # Apple-only library; MBID disambiguates when present).
    row = con.execute(
        "SELECT id FROM artists WHERE canonical_name = ?", [canonical_name]
    ).fetchone()
    if row:
        con.execute(
            "UPDATE artists SET sort_name = COALESCE(?, sort_name), mbid = COALESCE(?, mbid), "
            "external_ids_json = ? WHERE id = ?",
            [sort_name, mbid, ext_json, row[0]],
        )
        return row[0]

    return con.execute(
        "INSERT INTO artists (mbid, canonical_name, sort_name, external_ids_json) "
        "VALUES (?, ?, ?, ?) RETURNING id",
        [mbid, canonical_name, sort_name, ext_json],
    ).fetchone()[0]


def upsert_album(
    con: duckdb.DuckDBPyConnection,
    *,
    title: str,
    artist_id: int | None = None,
    release_group_mbid: str | None = None,
    release_mbid: str | None = None,
    version_class: str | None = None,
    external_ids: dict[str, str] | None = None,
) -> int:
    """Insert or update an album, deduping by release (edition) MBID then Apple external id.

    `release_group_mbid` is the version-cluster grouping attribute, NOT a dedup key:
    distinct editions (standard/deluxe/remaster) share a release-group but must stay
    separate canonical rows so each keeps its own version_class — the duplicate-album
    analysis groups these rows by release_group_mbid.
    """
    external_ids = external_ids or {}
    ext_json = _dump_external_ids(external_ids)

    if release_mbid:
        row = con.execute(
            "SELECT id FROM albums WHERE release_mbid = ?", [release_mbid]
        ).fetchone()
        if row:
            con.execute(
                "UPDATE albums SET title = ?, artist_id = COALESCE(?, artist_id), "
                "release_group_mbid = COALESCE(?, release_group_mbid), "
                "version_class = COALESCE(?, version_class), external_ids_json = ? WHERE id = ?",
                [title, artist_id, release_group_mbid, version_class, ext_json, row[0]],
            )
            return row[0]

    apple_id = external_ids.get("apple")
    if apple_id:
        row = con.execute(
            "SELECT id FROM albums WHERE json_extract_string(external_ids_json, '$.apple') = ?",
            [apple_id],
        ).fetchone()
        if row:
            con.execute(
                "UPDATE albums SET title = ?, artist_id = COALESCE(?, artist_id), "
                "release_group_mbid = COALESCE(?, release_group_mbid), "
                "release_mbid = COALESCE(?, release_mbid), "
                "version_class = COALESCE(?, version_class), external_ids_json = ? WHERE id = ?",
                [title, artist_id, release_group_mbid, release_mbid, version_class,
                 ext_json, row[0]],
            )
            return row[0]

    return con.execute(
        "INSERT INTO albums "
        "(release_group_mbid, release_mbid, artist_id, title, version_class, external_ids_json) "
        "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
        [release_group_mbid, release_mbid, artist_id, title, version_class, ext_json],
    ).fetchone()[0]


def upsert_track(
    con: duckdb.DuckDBPyConnection,
    *,
    title: str,
    album_id: int | None = None,
    artist_id: int | None = None,
    recording_mbid: str | None = None,
    isrc: str | None = None,
    disc_number: int | None = None,
    track_number: int | None = None,
    duration_ms: int | None = None,
    external_ids: dict[str, str] | None = None,
) -> int:
    """Insert or update a track, deduping by recording MBID then Apple external id."""
    external_ids = external_ids or {}
    ext_json = _dump_external_ids(external_ids)

    if recording_mbid:
        row = con.execute(
            "SELECT id FROM tracks WHERE recording_mbid = ?", [recording_mbid]
        ).fetchone()
        if row:
            con.execute(
                "UPDATE tracks SET title = ?, album_id = COALESCE(?, album_id), "
                "artist_id = COALESCE(?, artist_id), isrc = COALESCE(?, isrc), "
                "external_ids_json = ? WHERE id = ?",
                [title, album_id, artist_id, isrc, ext_json, row[0]],
            )
            return row[0]

    apple_id = external_ids.get("apple")
    if apple_id:
        row = con.execute(
            "SELECT id FROM tracks WHERE json_extract_string(external_ids_json, '$.apple') = ?",
            [apple_id],
        ).fetchone()
        if row:
            con.execute(
                "UPDATE tracks SET title = ?, album_id = COALESCE(?, album_id), "
                "artist_id = COALESCE(?, artist_id), recording_mbid = COALESCE(?, recording_mbid), "
                "isrc = COALESCE(?, isrc), external_ids_json = ? WHERE id = ?",
                [title, album_id, artist_id, recording_mbid, isrc, ext_json, row[0]],
            )
            return row[0]

    return con.execute(
        "INSERT INTO tracks "
        "(recording_mbid, album_id, artist_id, title, isrc, disc_number, track_number, "
        "duration_ms, external_ids_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        [recording_mbid, album_id, artist_id, title, isrc, disc_number, track_number,
         duration_ms, ext_json],
    ).fetchone()[0]


def record_library_item(
    con: duckdb.DuckDBPyConnection,
    *,
    service: str,
    service_item_id: str,
    item_type: str,
    seen_at: datetime,
    canonical_id: int | None = None,
    match_confidence: float | None = None,
    match_method: str = "none",
) -> int:
    """Insert or update a library item (present), preserving added_at across runs."""
    return con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_confidence, match_method, "
        "added_at, last_seen_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'present') "
        "ON CONFLICT (service, service_item_id, item_type) DO UPDATE SET "
        "canonical_id = excluded.canonical_id, match_confidence = excluded.match_confidence, "
        "match_method = excluded.match_method, last_seen_at = excluded.last_seen_at, "
        "status = 'present' "
        "RETURNING id",
        [service, service_item_id, item_type, canonical_id, match_confidence, match_method,
         seen_at, seen_at],
    ).fetchone()[0]


def save_match_candidates(
    con: duckdb.DuckDBPyConnection,
    *,
    library_item_id: int,
    candidates: list[dict],
) -> None:
    """Replace the pending review-queue candidates for a library item.

    Each candidate dict: candidate_mbid, candidate_kind, score, method.
    """
    con.execute(
        "DELETE FROM match_candidates WHERE library_item_id = ? AND status = 'pending'",
        [library_item_id],
    )
    for c in candidates:
        con.execute(
            "INSERT INTO match_candidates "
            "(library_item_id, candidate_mbid, candidate_kind, score, method, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            [library_item_id, c["candidate_mbid"], c["candidate_kind"], c["score"], c["method"]],
        )


def start_sync_run(con: duckdb.DuckDBPyConnection, *, service: str, started_at: datetime) -> int:
    """Open a sync run (status 'running'); returns its id."""
    return con.execute(
        "INSERT INTO sync_runs (service, started_at, status, summary_json) "
        "VALUES (?, ?, 'running', '{}') RETURNING id",
        [service, started_at],
    ).fetchone()[0]


def complete_sync_run(
    con: duckdb.DuckDBPyConnection, *, run_id: int, completed_at: datetime, summary: dict
) -> None:
    """Mark a sync run completed with a summary."""
    con.execute(
        "UPDATE sync_runs "
        "SET completed_at = ?, status = 'completed', summary_json = ? WHERE id = ?",
        [completed_at, json.dumps(summary, sort_keys=True), run_id],
    )


def mark_unseen_removed(
    con: duckdb.DuckDBPyConnection, *, service: str, run_started_at: datetime
) -> int:
    """Mark still-present items not seen during this run as removed; returns the count."""
    rows = con.execute(
        "UPDATE library_items SET status = 'removed' "
        "WHERE service = ? AND status = 'present' "
        "AND (last_seen_at IS NULL OR last_seen_at < ?) "
        "RETURNING id",
        [service, run_started_at],
    ).fetchall()
    return len(rows)


def get_library_summary(con: duckdb.DuckDBPyConnection) -> dict:
    """Counts of present library items by type, plus matched/unmatched totals."""
    summary: dict[str, int] = {}
    for item_type, count in con.execute(
        "SELECT item_type, count(*) FROM library_items WHERE status = 'present' GROUP BY item_type"
    ).fetchall():
        summary[item_type] = count
    matched, unmatched = con.execute(
        "SELECT "
        "count(*) FILTER (WHERE match_method <> 'none'), "
        "count(*) FILTER (WHERE match_method = 'none') "
        "FROM library_items WHERE status = 'present'"
    ).fetchone()
    summary["matched"] = matched
    summary["unmatched"] = unmatched
    return summary


def get_library_albums(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Present library albums joined to their canonical album rows."""
    rows = con.execute(
        "SELECT a.id, a.title, a.release_group_mbid, a.version_class, "
        "li.match_method, li.match_confidence "
        "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "WHERE li.status = 'present' AND li.item_type = 'album' "
        "ORDER BY a.title"
    ).fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "release_group_mbid": r[2],
            "version_class": r[3],
            "match_method": r[4],
            "match_confidence": r[5],
        }
        for r in rows
    ]


def list_unmatched(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Present library items with no MusicBrainz match, with a resolved display title.

    `canonical_id` is polymorphic (album or track by item_type), so the title is
    pulled from whichever canonical table the item points at.
    """
    rows = con.execute(
        "SELECT li.id, li.item_type, li.service_item_id, "
        "COALESCE(al.title, tr.title) AS title "
        "FROM library_items li "
        "LEFT JOIN albums al ON li.item_type = 'album' AND li.canonical_id = al.id "
        "LEFT JOIN tracks tr ON li.item_type = 'track' AND li.canonical_id = tr.id "
        "WHERE li.status = 'present' AND li.match_method = 'none' "
        "ORDER BY title"
    ).fetchall()
    return [
        {"id": r[0], "item_type": r[1], "service_item_id": r[2], "title": r[3]}
        for r in rows
    ]


def search_library(con: duckdb.DuckDBPyConnection, query: str) -> list[dict]:
    """Case-insensitive substring search over present album & track titles.

    `query` is interpolated into a SQL LIKE pattern, so ``%`` and ``_`` act as
    wildcards rather than literals and an empty string matches every present
    item. This permissive matching is intentional for a read-only search tool.
    """
    rows = con.execute(
        "SELECT li.id, li.item_type, COALESCE(al.title, tr.title) AS title, "
        "li.match_method "
        "FROM library_items li "
        "LEFT JOIN albums al ON li.item_type = 'album' AND li.canonical_id = al.id "
        "LEFT JOIN tracks tr ON li.item_type = 'track' AND li.canonical_id = tr.id "
        "WHERE li.status = 'present' "
        "AND lower(COALESCE(al.title, tr.title)) LIKE '%' || lower(?) || '%' "
        "ORDER BY title",
        [query],
    ).fetchall()
    return [
        {"id": r[0], "item_type": r[1], "title": r[2], "match_method": r[3]}
        for r in rows
    ]


def save_cleanup_findings(con: duckdb.DuckDBPyConnection, findings: list[CleanupFinding]) -> None:
    """Replace the open (unresolved, unignored) findings with a fresh scan's results.

    Findings whose (type, description) the user already resolved or ignored are
    NOT re-inserted, so prior decisions survive a re-scan.
    """
    con.execute(
        "DELETE FROM cleanup_findings WHERE resolved_at IS NULL AND ignored_at IS NULL"
    )
    suppressed = {
        (row[0], row[1])
        for row in con.execute(
            "SELECT finding_type, description FROM cleanup_findings "
            "WHERE resolved_at IS NOT NULL OR ignored_at IS NOT NULL"
        ).fetchall()
    }
    for finding in findings:
        if (finding.finding_type.value, finding.description) in suppressed:
            continue
        recommendation_json = json.dumps(
            finding.recommendation.model_dump() if finding.recommendation else None,
            sort_keys=True,
            default=str,
        )
        con.execute(
            "INSERT INTO cleanup_findings "
            "(finding_type, severity, entity_id, description, recommendation_json) "
            "VALUES (?, ?, ?, ?, ?)",
            [finding.finding_type.value, finding.severity.value, finding.entity_id,
             finding.description, recommendation_json],
        )


def get_review_queue(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Present library items that still have pending match candidates.

    Each entry: library_item_id, item_type, resolved canonical title, current
    match_method, and the pending candidates (score-desc). Candidate `name` is
    filled from the mb_* authority tables when present, else None.
    """
    rows = con.execute(
        "SELECT li.id, li.item_type, COALESCE(al.title, tr.title) AS title, li.match_method "
        "FROM library_items li "
        "LEFT JOIN albums al ON li.item_type = 'album' AND li.canonical_id = al.id "
        "LEFT JOIN tracks tr ON li.item_type = 'track' AND li.canonical_id = tr.id "
        "WHERE li.status = 'present' AND EXISTS ("
        "  SELECT 1 FROM match_candidates mc "
        "  WHERE mc.library_item_id = li.id AND mc.status = 'pending') "
        "ORDER BY title"
    ).fetchall()
    queue: list[dict] = []
    for item_id, item_type, title, match_method in rows:
        cand_rows = con.execute(
            "SELECT id, candidate_mbid, candidate_kind, score, method "
            "FROM match_candidates WHERE library_item_id = ? AND status = 'pending' "
            "ORDER BY score DESC, id",
            [item_id],
        ).fetchall()
        candidates = [
            {
                "candidate_id": c[0],
                "candidate_mbid": c[1],
                "candidate_kind": c[2],
                "score": c[3],
                "method": c[4],
                "name": None,
            }
            for c in cand_rows
        ]
        queue.append(
            {
                "library_item_id": item_id,
                "item_type": item_type,
                "title": title,
                "match_method": match_method,
                "candidates": candidates,
            }
        )
    _enrich_candidate_names(con, queue)
    return queue


def _enrich_candidate_names(con: duckdb.DuckDBPyConnection, queue: list[dict]) -> None:
    """Fill candidate `name` from mb_* tables (release_group / recording), if present."""
    by_kind = {"release_group": "mb_release_group", "recording": "mb_recording"}
    names: dict[str, str] = {}
    for kind, table in by_kind.items():
        gids = {
            c["candidate_mbid"]
            for entry in queue
            for c in entry["candidates"]
            if c["candidate_kind"] == kind
        }
        if not gids or not table_exists(con, table):
            continue
        placeholders = ", ".join("?" * len(gids))
        for gid, name in con.execute(
            f"SELECT gid, name FROM {table} WHERE gid IN ({placeholders})", list(gids)
        ).fetchall():
            names[gid] = name
    for entry in queue:
        for c in entry["candidates"]:
            c["name"] = names.get(c["candidate_mbid"])


def resolve_match(con: duckdb.DuckDBPyConnection, *, candidate_id: int) -> dict:
    """Confirm one pending candidate: link the canonical row to its MBID, mark the
    item manually matched, confirm the choice, reject its siblings — atomically.

    Returns {library_item_id, item_type, candidate_mbid}. Raises ValueError if the
    candidate is unknown / not pending, the item is missing, the candidate kind does
    not match the item type, or the item has no canonical row to link.
    """
    cand = con.execute(
        "SELECT library_item_id, candidate_mbid, candidate_kind, status "
        "FROM match_candidates WHERE id = ?",
        [candidate_id],
    ).fetchone()
    if cand is None:
        raise ValueError(f"No match candidate with id {candidate_id}.")
    library_item_id, candidate_mbid, candidate_kind, status = cand
    if status != "pending":
        raise ValueError(f"Candidate {candidate_id} is not pending (status={status}).")

    item = con.execute(
        "SELECT item_type, canonical_id FROM library_items WHERE id = ?",
        [library_item_id],
    ).fetchone()
    if item is None:
        raise ValueError(f"Library item {library_item_id} not found.")
    item_type, canonical_id = item
    expected_kind = {"album": "release_group", "track": "recording"}.get(item_type)
    if candidate_kind != expected_kind:
        raise ValueError(
            f"Candidate kind '{candidate_kind}' cannot resolve a '{item_type}' item."
        )
    if canonical_id is None:
        raise ValueError(f"Library item {library_item_id} has no canonical row to link.")

    con.execute("BEGIN TRANSACTION")
    try:
        if item_type == "album":
            con.execute(
                "UPDATE albums SET release_group_mbid = ? WHERE id = ?",
                [candidate_mbid, canonical_id],
            )
        else:
            con.execute(
                "UPDATE tracks SET recording_mbid = ? WHERE id = ?",
                [candidate_mbid, canonical_id],
            )
        con.execute(
            "UPDATE library_items SET match_method = 'manual', match_confidence = 1.0 "
            "WHERE id = ?",
            [library_item_id],
        )
        con.execute(
            "UPDATE match_candidates SET status = 'confirmed' WHERE id = ?", [candidate_id]
        )
        con.execute(
            "UPDATE match_candidates SET status = 'rejected' "
            "WHERE library_item_id = ? AND id <> ? AND status = 'pending'",
            [library_item_id, candidate_id],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {
        "library_item_id": library_item_id,
        "item_type": item_type,
        "candidate_mbid": candidate_mbid,
    }


def reject_match(con: duckdb.DuckDBPyConnection, *, library_item_id: int) -> int:
    """Reject every pending candidate for an item (user declined them); returns the count.

    The item is left unmatched (match_method stays whatever it was, typically 'none').
    """
    rows = con.execute(
        "UPDATE match_candidates SET status = 'rejected' "
        "WHERE library_item_id = ? AND status = 'pending' RETURNING id",
        [library_item_id],
    ).fetchall()
    return len(rows)


def get_findings(
    con: duckdb.DuckDBPyConnection, *, include_closed: bool = False
) -> list[CleanupFinding]:
    """Read findings as CleanupFinding objects (open ones only, unless include_closed)."""
    sql = (
        "SELECT id, finding_type, severity, entity_id, description, recommendation_json, "
        "resolved_at, ignored_at FROM cleanup_findings"
    )
    if not include_closed:
        sql += " WHERE resolved_at IS NULL AND ignored_at IS NULL"
    sql += " ORDER BY id"
    findings: list[CleanupFinding] = []
    for row in con.execute(sql).fetchall():
        recommendation = None
        if row[5]:
            data = json.loads(row[5])
            if data:
                recommendation = Recommendation(**data)
        findings.append(
            CleanupFinding(
                id=row[0],
                finding_type=FindingType(row[1]),
                severity=FindingSeverity(row[2]),
                entity_id=row[3],
                description=row[4],
                recommendation=recommendation,
                resolved_at=row[6],
                ignored_at=row[7],
            )
        )
    return findings

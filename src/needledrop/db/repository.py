"""Persistence for canonical entities, library items, and sync runs (DuckDB).

Separate from db/duckdb_store.py (connection + schema lifecycle): this module is
the entity/CRUD layer the sync service drives.
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb


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
    """Insert or update an artist, deduping by MBID then Apple external id. Returns its id."""
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
    """Insert or update an album, deduping by release-group MBID then Apple external id."""
    external_ids = external_ids or {}
    ext_json = _dump_external_ids(external_ids)

    if release_group_mbid:
        row = con.execute(
            "SELECT id FROM albums WHERE release_group_mbid = ?", [release_group_mbid]
        ).fetchone()
        if row:
            con.execute(
                "UPDATE albums SET title = ?, artist_id = COALESCE(?, artist_id), "
                "release_mbid = COALESCE(?, release_mbid), "
                "version_class = COALESCE(?, version_class), external_ids_json = ? WHERE id = ?",
                [title, artist_id, release_mbid, version_class, ext_json, row[0]],
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
        "UPDATE sync_runs SET completed_at = ?, status = 'completed', summary_json = ? WHERE id = ?",
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

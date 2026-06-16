"""Library synchronization: pull → match → persist → snapshot."""

from __future__ import annotations

import json
from datetime import datetime

import duckdb

from needledrop.connectors.base import MusicConnector
from needledrop.db.repository import (
    complete_sync_run,
    find_or_create_song_album,
    mark_unseen_removed,
    record_library_item,
    save_match_candidates,
    start_sync_run,
    upsert_album,
    upsert_artist,
    upsert_track,
)
from needledrop.matching.matcher import AlbumQuery, TrackQuery, match_album, match_track
from needledrop.models.match import MatchCandidate
from needledrop.normalize.album_versions import classify_album_version


def _candidate_dict(candidate: MatchCandidate) -> dict:
    return {
        "candidate_mbid": candidate.candidate_mbid,
        "candidate_kind": candidate.candidate_kind.value,
        "score": candidate.score,
        "method": candidate.method.value,
    }


def sync_library(
    connector: MusicConnector,
    con: duckdb.DuckDBPyConnection,
    *,
    now: datetime,
    service: str = "apple_music",
) -> dict:
    """Pull the connector's library, match + persist it, reconcile the snapshot.

    Returns the run summary {added, removed, present}.
    """
    run_id = start_sync_run(con, service=service, started_at=now)

    for album in connector.iter_library_albums():
        _sync_album(con, album, now=now, service=service)
    for song in connector.iter_library_songs():
        _sync_track(con, song, now=now, service=service)
    for playlist in connector.iter_library_playlists():
        record_library_item(
            con, service=service, service_item_id=playlist.id, item_type="playlist", seen_at=now,
        )

    removed = mark_unseen_removed(con, service=service, run_started_at=now)
    added = con.execute(
        "SELECT count(*) FROM library_items WHERE added_at = ?", [now]
    ).fetchone()[0]
    present = con.execute(
        "SELECT count(*) FROM library_items WHERE status = 'present'"
    ).fetchone()[0]
    summary = {"added": added, "removed": removed, "present": present}
    complete_sync_run(con, run_id=run_id, completed_at=now, summary=summary)
    return summary


def _sync_album(con, album, *, now, service) -> None:
    artist_id = (
        upsert_artist(con, canonical_name=album.artist_name) if album.artist_name else None
    )
    result = match_album(
        con, AlbumQuery(title=album.name, artist_name=album.artist_name, upc=album.upc)
    )
    canonical_id = upsert_album(
        con,
        title=album.name,
        artist_id=artist_id,
        release_group_mbid=result.mbid,
        version_class=classify_album_version(album.name).value,
        total_tracks=album.track_count,
        external_ids={"apple": album.id},
    )
    item_id = record_library_item(
        con, service=service, service_item_id=album.id, item_type="album",
        canonical_id=canonical_id, match_confidence=result.confidence,
        match_method=result.method.value, seen_at=now,
    )
    save_match_candidates(
        con, library_item_id=item_id, candidates=[_candidate_dict(c) for c in result.candidates]
    )


def _sync_track(con, song, *, now, service) -> None:
    artist_id = (
        upsert_artist(con, canonical_name=song.artist_name) if song.artist_name else None
    )
    album_id = (
        find_or_create_song_album(con, artist_id=artist_id, title=song.album_name)
        if song.album_name
        else None
    )
    result = match_track(
        con, TrackQuery(title=song.name, artist_name=song.artist_name, isrc=song.isrc)
    )
    canonical_id = upsert_track(
        con,
        title=song.name,
        artist_id=artist_id,
        album_id=album_id,
        recording_mbid=result.mbid,
        isrc=song.isrc,
        disc_number=song.disc_number,
        track_number=song.track_number,
        duration_ms=song.duration_ms,
        external_ids={"apple": song.id},
    )
    item_id = record_library_item(
        con, service=service, service_item_id=song.id, item_type="track",
        canonical_id=canonical_id, match_confidence=result.confidence,
        match_method=result.method.value, seen_at=now,
    )
    save_match_candidates(
        con, library_item_id=item_id, candidates=[_candidate_dict(c) for c in result.candidates]
    )


def diff_sync(con: duckdb.DuckDBPyConnection) -> dict:
    """Return the most recent completed sync run's summary (its added/removed/present diff)."""
    row = con.execute(
        "SELECT summary_json FROM sync_runs WHERE status = 'completed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return json.loads(row[0]) if row else {}

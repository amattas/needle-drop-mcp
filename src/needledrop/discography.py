"""MusicBrainz-backed discography browse: an artist's release-groups and an album's editions.

Read-only over the materialized mb_* authority tables joined to the local library, so
each result is flagged with whether you already own it. Returns [] when mb_* is absent.
"""

from __future__ import annotations

import duckdb

from needledrop.db.duckdb_store import table_exists


def _owned_release_group_mbids(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0]
        for row in con.execute(
            "SELECT DISTINCT a.release_group_mbid "
            "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
            "WHERE li.status = 'present' AND li.item_type = 'album' "
            "AND a.release_group_mbid IS NOT NULL"
        ).fetchall()
    }


def _owned_release_mbids(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0]
        for row in con.execute(
            "SELECT DISTINCT a.release_mbid "
            "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
            "WHERE li.status = 'present' AND li.item_type = 'album' "
            "AND a.release_mbid IS NOT NULL"
        ).fetchall()
    }


def get_artist_collection(con: duckdb.DuckDBPyConnection, artist_mbid: str) -> list[dict]:
    """An artist's release-groups (full discography), flagged by ownership.

    Each entry: release_group_mbid, title, primary_type, owned. [] if mb_* is absent.
    """
    if not table_exists(con, "mb_release_group"):
        return []
    rows = con.execute(
        "SELECT DISTINCT CAST(rg.gid AS VARCHAR), rg.name, "
        "COALESCE(pt.name, 'Unknown') AS primary_type "
        "FROM mb_artist ar "
        "JOIN mb_artist_credit_name acn ON acn.artist = ar.id "
        "JOIN mb_release_group rg ON rg.artist_credit = acn.artist_credit "
        "LEFT JOIN mb_release_group_primary_type pt ON rg.type = pt.id "
        "WHERE ar.gid = ? "
        "ORDER BY rg.name",
        [artist_mbid],
    ).fetchall()
    owned = _owned_release_group_mbids(con)
    return [
        {
            "release_group_mbid": gid,
            "title": name,
            "primary_type": primary_type,
            "owned": gid in owned,
        }
        for gid, name, primary_type in rows
    ]


def get_album_versions(
    con: duckdb.DuckDBPyConnection, release_group_mbid: str
) -> list[dict]:
    """All release editions of a release-group, with track counts, flagged by ownership.

    Each entry: release_mbid, title, barcode, track_count, owned. [] if mb_* is absent.
    """
    if not table_exists(con, "mb_release_group"):
        return []
    rows = con.execute(
        "SELECT CAST(r.gid AS VARCHAR), r.name, r.barcode, "
        "  (SELECT sum(m.track_count) FROM mb_medium m WHERE m.release = r.id) AS track_count "
        "FROM mb_release_group rg JOIN mb_release r ON r.release_group = rg.id "
        "WHERE rg.gid = ? "
        "ORDER BY r.name",
        [release_group_mbid],
    ).fetchall()
    owned = _owned_release_mbids(con)
    return [
        {
            "release_mbid": gid,
            "title": name,
            "barcode": barcode,
            "track_count": int(track_count) if track_count is not None else None,
            "owned": gid in owned,
        }
        for gid, name, barcode, track_count in rows
    ]


def get_song_detail(con: duckdb.DuckDBPyConnection, recording_mbid: str) -> dict:
    """Where a recording lives: owned library albums containing it, plus the
    release-groups it appears on (MusicBrainz), each ownership-flagged.

    Returns {recording_mbid, library_albums, appears_on}. `appears_on` is [] without mb_*.
    """
    library_albums = [
        {
            "album_id": r[0],
            "title": r[1],
            "release_group_mbid": r[2],
            "version_class": r[3],
        }
        for r in con.execute(
            "SELECT DISTINCT al.id, al.title, al.release_group_mbid, al.version_class "
            "FROM tracks tr "
            "JOIN albums al ON tr.album_id = al.id "
            "JOIN library_items li ON li.canonical_id = al.id "
            "  AND li.item_type = 'album' AND li.status = 'present' "
            "WHERE tr.recording_mbid = ? "
            "ORDER BY al.title",
            [recording_mbid],
        ).fetchall()
    ]
    appears_on: list[dict] = []
    if table_exists(con, "mb_recording"):
        owned = _owned_release_group_mbids(con)
        appears_on = [
            {
                "release_group_mbid": gid,
                "title": name,
                "primary_type": primary_type,
                "owned": gid in owned,
            }
            for gid, name, primary_type in con.execute(
                "SELECT DISTINCT CAST(rg.gid AS VARCHAR), rg.name, COALESCE(pt.name, 'Unknown') "
                "FROM mb_recording rec "
                "JOIN mb_track t ON t.recording = rec.id "
                "JOIN mb_medium m ON t.medium = m.id "
                "JOIN mb_release r ON m.release = r.id "
                "JOIN mb_release_group rg ON r.release_group = rg.id "
                "LEFT JOIN mb_release_group_primary_type pt ON rg.type = pt.id "
                "WHERE rec.gid = ? "
                "ORDER BY rg.name",
                [recording_mbid],
            ).fetchall()
        ]
    return {
        "recording_mbid": recording_mbid,
        "library_albums": library_albums,
        "appears_on": appears_on,
    }


def get_album_detail(con: duckdb.DuckDBPyConnection, release_group_mbid: str) -> dict:
    """Consolidation view of a release-group: the owned editions you hold (the duplicate
    set) with each one's Apple library id + completeness, plus all available editions.

    Returns {release_group_mbid, owned_editions, available_versions}. owned_editions each:
    {album_id, apple_album_id, title, version_class, total_tracks, owned_track_count}.
    `available_versions` reuses get_album_versions (MusicBrainz; [] without mb_*).
    """
    owned_editions = [
        {
            "album_id": r[0],
            "apple_album_id": r[1],
            "title": r[2],
            "version_class": r[3],
            "total_tracks": r[4],
            "owned_track_count": r[5],
        }
        for r in con.execute(
            "SELECT a.id, json_extract_string(a.external_ids_json, '$.apple') AS apple_id, "
            "a.title, a.version_class, a.total_tracks, ("
            "  SELECT count(*) FROM library_items lit JOIN tracks t ON lit.canonical_id = t.id "
            "  WHERE lit.status = 'present' AND lit.item_type = 'track' AND t.album_id = a.id"
            ") AS owned_tracks "
            "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
            "WHERE li.status = 'present' AND li.item_type = 'album' "
            "AND a.release_group_mbid = ? "
            "ORDER BY a.title",
            [release_group_mbid],
        ).fetchall()
    ]
    return {
        "release_group_mbid": release_group_mbid,
        "owned_editions": owned_editions,
        "available_versions": get_album_versions(con, release_group_mbid),
    }

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
        "SELECT DISTINCT rg.gid, rg.name, COALESCE(pt.name, 'Unknown') AS primary_type "
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
        "SELECT r.gid, r.name, r.barcode, "
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

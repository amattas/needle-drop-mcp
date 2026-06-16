"""Partial-album detection: you own only some tracks of an album you added."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def find_partial_albums(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Owned album items where the owned linked-track count is below total_tracks."""
    rows = con.execute(
        "SELECT a.id, a.title, a.total_tracks, ("
        "  SELECT count(*) FROM library_items lit JOIN tracks t ON lit.canonical_id = t.id "
        "  WHERE lit.status = 'present' AND lit.item_type = 'track' AND t.album_id = a.id"
        ") AS owned "
        "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "WHERE li.status = 'present' AND li.item_type = 'album' "
        "AND a.total_tracks IS NOT NULL AND a.total_tracks > 0 "
        "ORDER BY a.title"
    ).fetchall()
    findings: list[CleanupFinding] = []
    for album_id, title, total, owned in rows:
        if 0 < owned < total:
            findings.append(
                CleanupFinding(
                    finding_type=FindingType.PARTIAL_ALBUM,
                    severity=FindingSeverity.MEDIUM,
                    entity_id=album_id,
                    description=f"You own {owned} of {total} tracks from '{title}'.",
                    recommendation=Recommendation(
                        action="complete_album",
                        detail=f"{total - owned} track(s) missing.",
                        payload={"album_id": album_id, "owned": owned, "total_tracks": total},
                    ),
                )
            )
    return findings

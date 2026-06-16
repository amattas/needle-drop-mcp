"""Single-replaced-by-album detection: a standalone single you also own on an album."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def find_single_replaced(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Owned standalone tracks whose recording is also owned on an album you have."""
    rows = con.execute(
        "WITH owned_albums AS ("
        "  SELECT a.id FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "  WHERE li.status = 'present' AND li.item_type = 'album'), "
        "owned_tracks AS ("
        "  SELECT tr.id AS track_id, tr.title AS title, tr.recording_mbid AS rec, "
        "    tr.album_id AS album_id "
        "  FROM library_items li JOIN tracks tr ON li.canonical_id = tr.id "
        "  WHERE li.status = 'present' AND li.item_type = 'track' "
        "    AND tr.recording_mbid IS NOT NULL) "
        "SELECT ot.track_id, ot.title, ot.rec FROM owned_tracks ot "
        "WHERE (ot.album_id IS NULL OR ot.album_id NOT IN (SELECT id FROM owned_albums)) "
        "AND EXISTS ("
        "  SELECT 1 FROM owned_tracks alb "
        "  WHERE alb.rec = ot.rec AND alb.album_id IN (SELECT id FROM owned_albums)) "
        "ORDER BY ot.track_id"
    ).fetchall()
    findings: list[CleanupFinding] = []
    for track_id, title, rec in rows:
        findings.append(
            CleanupFinding(
                finding_type=FindingType.SINGLE_REPLACED_BY_ALBUM,
                severity=FindingSeverity.LOW,
                entity_id=track_id,
                description=(
                    f"You own a standalone copy of '{title}' that's also on an album you own."
                ),
                recommendation=Recommendation(
                    action="remove_redundant_single",
                    detail="The album already includes this track.",
                    payload={"track_id": track_id, "recording_mbid": rec},
                ),
            )
        )
    return findings

"""Duplicate-track detection: the same recording owned more than once."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def find_duplicate_tracks(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Owned tracks sharing a recording identity (recording MBID, else ISRC)."""
    rows = con.execute(
        "WITH owned AS ("
        "  SELECT tr.id AS track_id, tr.title AS title, "
        "    CASE WHEN tr.recording_mbid IS NOT NULL THEN 'rec:' || tr.recording_mbid "
        "         WHEN tr.isrc IS NOT NULL THEN 'isrc:' || tr.isrc END AS key "
        "  FROM library_items li JOIN tracks tr ON li.canonical_id = tr.id "
        "  WHERE li.status = 'present' AND li.item_type = 'track') "
        "SELECT key, count(*) AS n, min(title) AS title, list(track_id) AS track_ids "
        "FROM owned WHERE key IS NOT NULL "
        "GROUP BY key HAVING count(*) > 1 "
        "ORDER BY key"
    ).fetchall()
    findings: list[CleanupFinding] = []
    for key, n, title, track_ids in rows:
        findings.append(
            CleanupFinding(
                finding_type=FindingType.DUPLICATE_TRACK,
                severity=FindingSeverity.LOW,
                entity_id=track_ids[0],
                description=f"You own {n} copies of the track '{title}'.",
                recommendation=Recommendation(
                    action="review_duplicate_tracks",
                    detail=f"{n} copies share identity {key}.",
                    payload={"identity": key, "track_ids": track_ids},
                ),
            )
        )
    return findings

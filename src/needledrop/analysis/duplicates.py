"""Duplicate-album detection: multiple owned editions of one release-group."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def find_duplicate_albums(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Owned album editions sharing a release-group (you own more than one version)."""
    rows = con.execute(
        "SELECT a.release_group_mbid, count(*) AS n, min(a.title) AS title, "
        "list(a.id) AS album_ids, list(a.version_class) AS versions "
        "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "WHERE li.status = 'present' AND li.item_type = 'album' "
        "AND a.release_group_mbid IS NOT NULL "
        "GROUP BY a.release_group_mbid HAVING count(*) > 1"
    ).fetchall()
    findings: list[CleanupFinding] = []
    for rg_mbid, n, title, album_ids, versions in rows:
        labels = ", ".join(str(v) for v in versions)
        findings.append(
            CleanupFinding(
                finding_type=FindingType.DUPLICATE_ALBUM,
                severity=FindingSeverity.LOW,
                entity_id=album_ids[0],
                description=f"You own {n} versions of '{title}' ({labels}).",
                recommendation=Recommendation(
                    action="review_duplicates",
                    detail=f"Editions: {labels}",
                    payload={"release_group_mbid": rg_mbid, "album_ids": album_ids},
                ),
            )
        )
    return findings

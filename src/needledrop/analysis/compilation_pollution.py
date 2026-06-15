"""Compilation-pollution detection: soundtracks, compilations, Various-Artists records."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation

_VARIOUS_ARTISTS_GID = "89ad4ac3-39f7-470e-963a-56509c546377"


def find_compilation_pollution(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Owned albums whose release-group is a compilation/soundtrack or Various-Artists."""
    try:
        rows = con.execute(
            "SELECT DISTINCT a.id, a.title "
            "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
            "JOIN mb_release_group rg ON a.release_group_mbid = rg.gid "
            "WHERE li.status = 'present' AND li.item_type = 'album' AND ("
            "  EXISTS (SELECT 1 FROM mb_release_group_secondary_type_join j "
            "          JOIN mb_release_group_secondary_type st ON j.secondary_type = st.id "
            "          WHERE j.release_group = rg.id AND st.name IN ('Compilation', 'Soundtrack')) "
            "  OR EXISTS (SELECT 1 FROM mb_artist_credit_name acn "
            "             JOIN mb_artist ar ON acn.artist = ar.id "
            "             WHERE acn.artist_credit = rg.artist_credit AND ar.gid = ?)) "
            "ORDER BY a.title",
            [_VARIOUS_ARTISTS_GID],
        ).fetchall()
    except duckdb.CatalogException:
        return []
    return [
        CleanupFinding(
            finding_type=FindingType.COMPILATION_POLLUTION,
            severity=FindingSeverity.INFO,
            entity_id=album_id,
            description=f"'{title}' is a compilation, soundtrack, or Various-Artists release.",
            recommendation=Recommendation(action="review_compilation"),
        )
        for album_id, title in rows
    ]

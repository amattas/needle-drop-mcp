"""Missing-core-album detection: studio albums by owned artists that aren't owned."""

from __future__ import annotations

import duckdb

from needledrop.db.duckdb_store import table_exists
from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation

_VARIOUS_ARTISTS_GID = "89ad4ac3-39f7-470e-963a-56509c546377"

_QUERY = """
WITH owned AS (
    SELECT DISTINCT a.release_group_mbid AS gid
    FROM library_items li JOIN albums a ON li.canonical_id = a.id
    WHERE li.status = 'present' AND li.item_type = 'album' AND a.release_group_mbid IS NOT NULL
),
owned_artists AS (
    SELECT DISTINCT acn.artist AS artist_id
    FROM owned o
    JOIN mb_release_group rg ON o.gid = rg.gid
    JOIN mb_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
    JOIN mb_artist ar ON acn.artist = ar.id
    WHERE ar.gid <> ?
)
SELECT DISTINCT rg.gid, rg.name, ar.name
FROM owned_artists oa
JOIN mb_artist ar ON ar.id = oa.artist_id
JOIN mb_artist_credit_name acn ON acn.artist = oa.artist_id
JOIN mb_release_group rg ON rg.artist_credit = acn.artist_credit
JOIN mb_release_group_primary_type pt ON rg.type = pt.id
WHERE pt.name = 'Album'
  AND rg.gid NOT IN (SELECT gid FROM owned)
  AND NOT EXISTS (
      SELECT 1 FROM mb_release_group_secondary_type_join j
      JOIN mb_release_group_secondary_type st ON j.secondary_type = st.id
      WHERE j.release_group = rg.id AND st.name IN ('Compilation', 'Live', 'Soundtrack')
  )
ORDER BY ar.name, rg.name
"""


def find_missing_core_albums(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Studio albums (Album primary type, non-compilation/live) by owned artists, not owned."""
    if not table_exists(con, "mb_release_group"):
        return []
    rows = con.execute(_QUERY, [_VARIOUS_ARTISTS_GID]).fetchall()
    return [
        CleanupFinding(
            finding_type=FindingType.MISSING_CORE_ALBUM,
            severity=FindingSeverity.INFO,
            entity_id=None,
            description=f"Missing: {artist} — {title}",
            recommendation=Recommendation(
                action="add_album",
                payload={"release_group_mbid": gid, "artist": artist, "title": title},
            ),
        )
        for gid, title, artist in rows
    ]

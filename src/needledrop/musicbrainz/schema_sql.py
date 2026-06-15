"""Map a MusicBrainz SCHEMA_SEQUENCE to the matching musicbrainz-server git tag,
and build URLs for the schema DDL files needed to create the (empty) tables.
"""

from __future__ import annotations

# SCHEMA_SEQUENCE -> musicbrainz-server git tag carrying that schema's DDL.
# Add an entry when MusicBrainz ships a schema change (the "-schema-change" tags
# at https://github.com/metabrainz/musicbrainz-server/tags).
SCHEMA_SEQUENCE_TAGS: dict[int, str] = {
    30: "v-2025-05-23.0-schema-change",
    31: "v-2026-05-11.0-schema-change",
}

# DDL files in the exact order they must be applied to an empty database.
# PKs/FKs/indexes/functions/triggers/views are intentionally omitted — the data
# is read-only and transient (we SELECT it into DuckDB and drop Postgres).
DDL_FILES: tuple[str, ...] = (
    "Extensions.sql",
    "CreateCollations.sql",
    "CreateTypes.sql",
    "CreateTables.sql",
)


def tag_for_schema_sequence(seq: int) -> str:
    """Return the musicbrainz-server tag for a schema sequence, or fail loudly."""
    try:
        return SCHEMA_SEQUENCE_TAGS[seq]
    except KeyError:
        known = ", ".join(str(k) for k in sorted(SCHEMA_SEQUENCE_TAGS))
        raise ValueError(
            f"Unknown MusicBrainz SCHEMA_SEQUENCE {seq} (known: {known}). "
            "Add a mapping to SCHEMA_SEQUENCE_TAGS in "
            "needledrop.musicbrainz.schema_sql — find the matching '-schema-change' "
            "tag at https://github.com/metabrainz/musicbrainz-server/tags."
        ) from None


def ddl_file_urls(raw_base: str, tag: str) -> list[str]:
    """Raw-GitHub URLs for the ordered DDL files at a given tag."""
    base = raw_base.rstrip("/")
    return [f"{base}/{tag}/admin/sql/{name}" for name in DDL_FILES]

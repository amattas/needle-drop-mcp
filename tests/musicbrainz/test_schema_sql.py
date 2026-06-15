import pytest

from needledrop.musicbrainz.schema_sql import (
    DDL_FILES,
    ddl_file_urls,
    tag_for_schema_sequence,
)


def test_tag_for_known_sequence():
    assert tag_for_schema_sequence(31) == "v-2026-05-11.0-schema-change"


def test_tag_for_unknown_sequence_raises():
    with pytest.raises(ValueError) as exc:
        tag_for_schema_sequence(999)
    assert "999" in str(exc.value)
    assert "SCHEMA_SEQUENCE_TAGS" in str(exc.value)


def test_ddl_file_order():
    assert DDL_FILES == (
        "Extensions.sql",
        "CreateCollations.sql",
        "CreateTypes.sql",
        "CreateTables.sql",
    )


def test_ddl_file_urls():
    urls = ddl_file_urls(
        "https://raw.githubusercontent.com/metabrainz/musicbrainz-server",
        "v-2026-05-11.0-schema-change",
    )
    assert urls[0] == (
        "https://raw.githubusercontent.com/metabrainz/musicbrainz-server/"
        "v-2026-05-11.0-schema-change/admin/sql/Extensions.sql"
    )
    assert len(urls) == 4
    assert urls[-1].endswith("/admin/sql/CreateTables.sql")

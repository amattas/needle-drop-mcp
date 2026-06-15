from datetime import datetime

from needledrop.analysis.duplicates import find_duplicate_albums
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import record_library_item, upsert_album
from needledrop.models.enums import FindingType


def _db():
    con = connect(":memory:")
    init_schema(con)
    return con


def _own_album(con, *, apple_id, title, rg_mbid, version_class):
    album_id = upsert_album(
        con, title=title, release_group_mbid=rg_mbid, version_class=version_class,
        external_ids={"apple": apple_id},
    )
    record_library_item(
        con, service="apple_music", service_item_id=apple_id, item_type="album",
        canonical_id=album_id, match_method="upc", seen_at=datetime(2026, 6, 15, 12, 0, 0),
    )


def test_finds_two_editions_of_one_release_group():
    con = _db()
    _own_album(con, apple_id="l.1", title="Dookie", rg_mbid="rg-dookie", version_class="standard")
    _own_album(
        con, apple_id="l.2", title="Dookie (Deluxe)", rg_mbid="rg-dookie", version_class="deluxe"
    )
    findings = find_duplicate_albums(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.DUPLICATE_ALBUM
    assert "2 versions" in findings[0].description
    assert findings[0].recommendation.payload["release_group_mbid"] == "rg-dookie"


def test_single_edition_is_not_a_duplicate():
    con = _db()
    _own_album(con, apple_id="l.1", title="Dookie", rg_mbid="rg-dookie", version_class="standard")
    assert find_duplicate_albums(con) == []


def test_unmatched_albums_are_ignored():
    con = _db()
    _own_album(con, apple_id="l.1", title="Unknown A", rg_mbid=None, version_class="standard")
    _own_album(con, apple_id="l.2", title="Unknown B", rg_mbid=None, version_class="standard")
    assert find_duplicate_albums(con) == []

from needledrop.musicbrainz.dumps import (
    fullexport_url,
    list_table_files,
    parse_sha256sums,
    read_schema_sequence,
    resolve_latest,
    sha256_file,
)


def test_resolve_latest_strips():
    assert resolve_latest("20260613-002047\n") == "20260613-002047"


def test_resolve_latest_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        resolve_latest("   \n")


def test_fullexport_url_joins():
    url = fullexport_url(
        "https://data.metabrainz.org/pub/musicbrainz/data/fullexport/",
        "20260613-002047",
        "mbdump.tar.bz2",
    )
    assert url == (
        "https://data.metabrainz.org/pub/musicbrainz/data/fullexport/"
        "20260613-002047/mbdump.tar.bz2"
    )


def test_read_schema_sequence(tmp_path):
    p = tmp_path / "SCHEMA_SEQUENCE"
    p.write_text("31\n")
    assert read_schema_sequence(p) == 31


def test_parse_sha256sums():
    body = "abc123  mbdump.tar.bz2\ndef456 *other.tar.bz2\n\n"
    assert parse_sha256sums(body) == {
        "mbdump.tar.bz2": "abc123",
        "other.tar.bz2": "def456",
    }


def test_sha256_file(tmp_path):
    import hashlib

    p = tmp_path / "f.bin"
    p.write_bytes(b"needledrop")
    assert sha256_file(p) == hashlib.sha256(b"needledrop").hexdigest()


def test_list_table_files_skips_metadata(tmp_path):
    mbdump = tmp_path / "mbdump"
    mbdump.mkdir()
    (mbdump / "artist").write_text("")
    (mbdump / "release_group").write_text("")
    (mbdump / "SCHEMA_SEQUENCE").write_text("31")
    (mbdump / "TIMESTAMP").write_text("x")
    names = [name for name, _ in list_table_files(mbdump)]
    assert names == ["artist", "release_group"]

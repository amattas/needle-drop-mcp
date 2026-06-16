import duckdb
import pytest

from needledrop.matching.matcher import AlbumQuery, match_album
from needledrop.models.enums import MatchMethod


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    c.execute("CREATE TABLE mb_artist_credit (id INTEGER, name VARCHAR)")
    c.execute(
        "CREATE TABLE mb_artist_credit_name "
        "(artist_credit INTEGER, position INTEGER, artist INTEGER, "
        "name VARCHAR, join_phrase VARCHAR)"
    )
    c.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, type INTEGER)"
    )
    c.execute(
        "CREATE TABLE mb_release "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, "
        "release_group INTEGER, barcode VARCHAR)"
    )
    c.execute(
        "CREATE TABLE mb_recording "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, length INTEGER)"
    )
    c.execute("CREATE TABLE mb_isrc (id INTEGER, recording INTEGER, isrc VARCHAR)")
    c.execute("INSERT INTO mb_artist VALUES (1, 'gid-radiohead', 'Radiohead', 'Radiohead')")
    c.execute("INSERT INTO mb_artist_credit VALUES (10, 'Radiohead')")
    c.execute("INSERT INTO mb_artist_credit_name VALUES (10, 0, 1, 'Radiohead', '')")
    c.execute("INSERT INTO mb_release_group VALUES (100, 'gid-okc', 'OK Computer', 10, 1)")
    c.execute("INSERT INTO mb_release_group VALUES (101, 'gid-kida', 'Kid A', 10, 1)")
    c.execute(
        "INSERT INTO mb_release VALUES "
        "(1000, 'gid-okc-rel', 'OK Computer', 10, 100, '0724385522123')"
    )
    c.execute("INSERT INTO mb_recording VALUES (5000, 'gid-karma', 'Karma Police', 10, 261000)")
    c.execute("INSERT INTO mb_isrc VALUES (1, 5000, 'GBAYE9700116')")
    # An artist whose MB name carries diacritics, for accent-stripping coverage.
    c.execute("INSERT INTO mb_artist VALUES (2, 'gid-sigurros', 'Sigur Rós', 'Sigur Ros')")
    c.execute("INSERT INTO mb_artist_credit VALUES (11, 'Sigur Rós')")
    c.execute("INSERT INTO mb_artist_credit_name VALUES (11, 0, 2, 'Sigur Rós', '')")
    c.execute("INSERT INTO mb_release_group VALUES (102, 'gid-takk', 'Takk...', 11, 1)")
    return c


def test_match_album_by_upc(con):
    result = match_album(
        con, AlbumQuery(title="OK Computer", artist_name="Radiohead", upc="0724385522123")
    )
    assert result.method == MatchMethod.UPC
    assert result.mbid == "gid-okc"
    assert result.confidence == 1.0


def test_match_album_fuzzy_ignores_edition_noise(con):
    result = match_album(con, AlbumQuery(title="OK Computer (Remastered)", artist_name="Radiohead"))
    assert result.method == MatchMethod.FUZZY
    assert result.mbid == "gid-okc"
    assert result.confidence >= 0.87


def test_match_album_case_folded_artist(con):
    # Lowercase library artist still resolves against MB's title-cased name.
    result = match_album(con, AlbumQuery(title="Kid A", artist_name="radiohead"))
    assert result.mbid == "gid-kida"


def test_match_album_accent_stripped_artist(con):
    # MB stores "Sigur Rós"; an accent-free library query must still resolve
    # (fold_accents in Python must agree with strip_accents in SQL).
    result = match_album(con, AlbumQuery(title="Takk...", artist_name="sigur ros"))
    assert result.mbid == "gid-takk"


def test_match_album_no_match_returns_candidates(con):
    result = match_album(con, AlbumQuery(title="In Rainbows", artist_name="Radiohead"))
    assert result.mbid is None
    assert result.method == MatchMethod.NONE
    assert {c.candidate_mbid for c in result.candidates} == {"gid-okc", "gid-kida"}


def test_match_album_unknown_artist_no_candidates(con):
    result = match_album(con, AlbumQuery(title="Whatever", artist_name="Nonexistent Band"))
    assert result.mbid is None
    assert result.candidates == []


def test_match_track_by_isrc(con):
    from needledrop.matching.matcher import TrackQuery, match_track

    result = match_track(
        con, TrackQuery(title="Karma Police", artist_name="Radiohead", isrc="GBAYE9700116")
    )
    assert result.method == MatchMethod.ISRC
    assert result.mbid == "gid-karma"
    assert result.confidence == 1.0


def test_match_track_fuzzy(con):
    from needledrop.matching.matcher import TrackQuery, match_track

    result = match_track(con, TrackQuery(title="Karma Police", artist_name="Radiohead"))
    assert result.method == MatchMethod.FUZZY
    assert result.mbid == "gid-karma"


def test_match_track_no_match_returns_candidates(con):
    from needledrop.matching.matcher import TrackQuery, match_track

    result = match_track(con, TrackQuery(title="Paranoid Android", artist_name="Radiohead"))
    assert result.mbid is None
    assert result.method == MatchMethod.NONE
    assert {c.candidate_mbid for c in result.candidates} == {"gid-karma"}


def test_match_album_without_musicbrainz_degrades_to_no_match():
    # No mb_* tables (MusicBrainz not imported) — must not raise; degrade gracefully
    # so sync can run before `mb import`.
    bare = duckdb.connect(":memory:")
    result = match_album(
        bare, AlbumQuery(title="OK Computer", artist_name="Radiohead", upc="0724385522123")
    )
    assert result.mbid is None
    assert result.method == MatchMethod.NONE
    assert result.candidates == []


def test_match_track_without_musicbrainz_degrades_to_no_match():
    from needledrop.matching.matcher import TrackQuery, match_track

    bare = duckdb.connect(":memory:")
    result = match_track(
        bare, TrackQuery(title="Karma Police", artist_name="Radiohead", isrc="GBAYE9700116")
    )
    assert result.mbid is None
    assert result.method == MatchMethod.NONE
    assert result.candidates == []

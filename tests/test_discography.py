from needledrop.db.duckdb_store import connect, init_schema
from needledrop.discography import (
    get_album_detail,
    get_album_versions,
    get_artist_collection,
    get_song_detail,
)


def _seed_artist_discography(con):
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit_name (artist INTEGER, artist_credit INTEGER)")
    con.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, type INTEGER)"
    )
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("INSERT INTO mb_artist VALUES (1, 'artist-radiohead', 'Radiohead')")
    con.execute("INSERT INTO mb_artist_credit_name VALUES (1, 10)")
    con.execute("INSERT INTO mb_release_group_primary_type VALUES (1, 'Album')")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'rg-okc', 'OK Computer', 10, 1)")
    con.execute("INSERT INTO mb_release_group VALUES (101, 'rg-kida', 'Kid A', 10, 1)")
    con.execute(
        "INSERT INTO albums (title, release_group_mbid) VALUES ('OK Computer', 'rg-okc')"
    )
    album_id = con.execute("SELECT id FROM albums WHERE title = 'OK Computer'").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', 'l.okc', 'album', ?, 'present')",
        [album_id],
    )


def test_get_artist_collection_lists_release_groups_with_ownership(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_artist_discography(con)
    collection = get_artist_collection(con, "artist-radiohead")
    by_title = {c["title"]: c for c in collection}
    assert set(by_title) == {"OK Computer", "Kid A"}
    assert by_title["OK Computer"]["owned"] is True
    assert by_title["OK Computer"]["primary_type"] == "Album"
    assert by_title["Kid A"]["owned"] is False
    assert by_title["OK Computer"]["release_group_mbid"] == "rg-okc"


def test_get_artist_collection_empty_without_mb(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    assert get_artist_collection(con, "artist-radiohead") == []


def _seed_release_versions(con):
    con.execute(
        "CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR, "
        "artist_credit INTEGER, type INTEGER)"
    )
    con.execute(
        "CREATE TABLE mb_release "
        "(id INTEGER, gid VARCHAR, name VARCHAR, barcode VARCHAR, release_group INTEGER)"
    )
    con.execute("CREATE TABLE mb_medium (id INTEGER, release INTEGER, track_count INTEGER)")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'rg-okc', 'OK Computer', 10, 1)")
    con.execute("INSERT INTO mb_release VALUES (200, 'rel-std', 'OK Computer', '111', 100)")
    con.execute(
        "INSERT INTO mb_release VALUES (201, 'rel-oknotok', 'OKNOTOK 1997 2017', '222', 100)"
    )
    con.execute("INSERT INTO mb_medium VALUES (300, 200, 12)")
    con.execute("INSERT INTO mb_medium VALUES (301, 201, 23)")
    con.execute(
        "INSERT INTO albums (title, release_group_mbid, release_mbid) "
        "VALUES ('OK Computer', 'rg-okc', 'rel-std')"
    )
    album_id = con.execute("SELECT id FROM albums WHERE title = 'OK Computer'").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', 'l.okc', 'album', ?, 'present')",
        [album_id],
    )


def test_get_album_versions_lists_editions_with_ownership_and_counts(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_release_versions(con)
    versions = get_album_versions(con, "rg-okc")
    by_title = {v["title"]: v for v in versions}
    assert set(by_title) == {"OK Computer", "OKNOTOK 1997 2017"}
    assert by_title["OK Computer"]["owned"] is True
    assert by_title["OK Computer"]["track_count"] == 12
    assert by_title["OKNOTOK 1997 2017"]["owned"] is False
    assert by_title["OKNOTOK 1997 2017"]["track_count"] == 23


def test_get_album_versions_empty_without_mb(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    assert get_album_versions(con, "rg-okc") == []


def _seed_song_detail(con):
    con.execute(
        "INSERT INTO albums (title, release_group_mbid, external_ids_json) "
        "VALUES ('OK Computer', 'rg-okc', json_object('apple', 'la.okc'))"
    )
    album_id = con.execute("SELECT id FROM albums WHERE title = 'OK Computer'").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', 'la.okc', 'album', ?, 'present')",
        [album_id],
    )
    con.execute(
        "INSERT INTO tracks (title, recording_mbid, album_id) VALUES ('Lucky', 'rec-lucky', ?)",
        [album_id],
    )
    con.execute("CREATE TABLE mb_recording (id INTEGER, gid VARCHAR)")
    con.execute("CREATE TABLE mb_track (id INTEGER, recording INTEGER, medium INTEGER)")
    con.execute("CREATE TABLE mb_medium (id INTEGER, release INTEGER, track_count INTEGER)")
    con.execute("CREATE TABLE mb_release (id INTEGER, gid VARCHAR, release_group INTEGER)")
    con.execute(
        "CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR, type INTEGER)"
    )
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("INSERT INTO mb_recording VALUES (1, 'rec-lucky')")
    con.execute("INSERT INTO mb_release_group_primary_type VALUES (1, 'Album')")
    con.execute("INSERT INTO mb_release_group VALUES (10, 'rg-okc', 'OK Computer', 1)")
    con.execute("INSERT INTO mb_release_group VALUES (11, 'rg-comp', 'Best Of', 1)")
    con.execute("INSERT INTO mb_release VALUES (20, 'rel-okc', 10)")
    con.execute("INSERT INTO mb_release VALUES (21, 'rel-comp', 11)")
    con.execute("INSERT INTO mb_medium VALUES (30, 20, 12)")
    con.execute("INSERT INTO mb_medium VALUES (31, 21, 20)")
    con.execute("INSERT INTO mb_track VALUES (40, 1, 30)")
    con.execute("INSERT INTO mb_track VALUES (41, 1, 31)")


def test_get_song_detail_reports_library_albums_and_mb_placements(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_song_detail(con)
    detail = get_song_detail(con, "rec-lucky")
    assert [a["title"] for a in detail["library_albums"]] == ["OK Computer"]
    appears = {a["release_group_mbid"]: a for a in detail["appears_on"]}
    assert set(appears) == {"rg-okc", "rg-comp"}
    assert appears["rg-okc"]["owned"] is True
    assert appears["rg-comp"]["owned"] is False


def test_get_song_detail_without_mb_still_lists_library_albums(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    con.execute(
        "INSERT INTO albums (title, external_ids_json) "
        "VALUES ('OK Computer', json_object('apple', 'la.okc'))"
    )
    album_id = con.execute("SELECT id FROM albums").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', 'la.okc', 'album', ?, 'present')",
        [album_id],
    )
    con.execute(
        "INSERT INTO tracks (title, recording_mbid, album_id) VALUES ('Lucky', 'rec-lucky', ?)",
        [album_id],
    )
    detail = get_song_detail(con, "rec-lucky")
    assert [a["title"] for a in detail["library_albums"]] == ["OK Computer"]
    assert detail["appears_on"] == []


def _seed_album_detail(con):
    for title, version, total, apple, owned_tracks in [
        ("OK Computer", "standard", 12, "la.std", 2),
        ("OK Computer (Deluxe)", "deluxe", 23, "la.dlx", 23),
    ]:
        con.execute(
            "INSERT INTO albums (title, release_group_mbid, version_class, total_tracks, "
            "external_ids_json) VALUES (?, 'rg-okc', ?, ?, json_object('apple', ?))",
            [title, version, total, apple],
        )
        album_id = con.execute("SELECT id FROM albums WHERE title = ?", [title]).fetchone()[0]
        con.execute(
            "INSERT INTO library_items "
            "(service, service_item_id, item_type, canonical_id, status) "
            "VALUES ('apple_music', ?, 'album', ?, 'present')",
            [apple, album_id],
        )
        for i in range(owned_tracks):
            con.execute(
                "INSERT INTO tracks (title, album_id) VALUES (?, ?)", [f"{title}-{i}", album_id]
            )
            tid = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
            con.execute(
                "INSERT INTO library_items "
                "(service, service_item_id, item_type, canonical_id, status) "
                "VALUES ('apple_music', ?, 'track', ?, 'present')",
                [f"s.{apple}.{i}", tid],
            )


def test_get_album_detail_shows_owned_editions_for_consolidation(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_album_detail(con)
    detail = get_album_detail(con, "rg-okc")
    editions = {e["title"]: e for e in detail["owned_editions"]}
    assert set(editions) == {"OK Computer", "OK Computer (Deluxe)"}
    assert editions["OK Computer"]["apple_album_id"] == "la.std"
    assert editions["OK Computer"]["total_tracks"] == 12
    assert editions["OK Computer"]["owned_track_count"] == 2
    assert editions["OK Computer (Deluxe)"]["owned_track_count"] == 23
    assert detail["available_versions"] == []

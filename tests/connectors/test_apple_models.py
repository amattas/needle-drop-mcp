from needledrop.connectors.apple_models import (
    CatalogAlbum,
    CatalogSong,
    LibraryAlbum,
    LibraryPlaylist,
    LibrarySong,
)


def test_library_album_from_api():
    resource = {
        "id": "l.abc",
        "type": "library-albums",
        "attributes": {
            "name": "OK Computer",
            "artistName": "Radiohead",
            "trackCount": 12,
            "releaseDate": "1997-05-21",
            "dateAdded": "2023-11-01T10:00:00Z",
        },
    }
    album = LibraryAlbum.from_api(resource)
    assert album.id == "l.abc"
    assert album.name == "OK Computer"
    assert album.artist_name == "Radiohead"
    assert album.track_count == 12
    assert album.release_date == "1997-05-21"
    assert album.date_added == "2023-11-01T10:00:00Z"


def test_library_song_maps_duration():
    resource = {
        "id": "l.s1",
        "type": "library-songs",
        "attributes": {
            "name": "Karma Police",
            "artistName": "Radiohead",
            "albumName": "OK Computer",
            "durationInMillis": 261000,
            "trackNumber": 6,
            "discNumber": 1,
        },
    }
    song = LibrarySong.from_api(resource)
    assert song.duration_ms == 261000
    assert song.track_number == 6
    assert song.disc_number == 1
    assert song.album_name == "OK Computer"


def test_library_playlist_flattens_description():
    resource = {
        "id": "p.1",
        "type": "library-playlists",
        "attributes": {"name": "Faves", "description": {"standard": "my favourites"}},
    }
    pl = LibraryPlaylist.from_api(resource)
    assert pl.name == "Faves"
    assert pl.description == "my favourites"


def test_library_playlist_missing_description():
    pl = LibraryPlaylist.from_api({"id": "p.2", "attributes": {"name": "X"}})
    assert pl.description is None


def test_catalog_album_carries_upc():
    resource = {
        "id": "1109714933",
        "type": "albums",
        "attributes": {
            "name": "OK Computer",
            "artistName": "Radiohead",
            "upc": "634904032463",
            "trackCount": 12,
            "releaseDate": "1997-05-21",
        },
    }
    album = CatalogAlbum.from_api(resource)
    assert album.id == "1109714933"
    assert album.upc == "634904032463"
    assert album.track_count == 12


def test_catalog_song_carries_isrc():
    resource = {
        "id": "1109714945",
        "type": "songs",
        "attributes": {
            "name": "Karma Police",
            "artistName": "Radiohead",
            "isrc": "GBAYE9700116",
            "albumName": "OK Computer",
        },
    }
    song = CatalogSong.from_api(resource)
    assert song.isrc == "GBAYE9700116"
    assert song.album_name == "OK Computer"


def test_library_album_extracts_embedded_catalog_upc():
    resource = {
        "id": "l.abc",
        "type": "library-albums",
        "attributes": {"name": "OK Computer", "artistName": "Radiohead"},
        "relationships": {
            "catalog": {"data": [{"id": "123", "type": "albums",
                                  "attributes": {"upc": "634904032463"}}]}
        },
    }
    album = LibraryAlbum.from_api(resource)
    assert album.upc == "634904032463"


def test_library_album_empty_catalog_relationship_is_none():
    resource = {"id": "l.x", "attributes": {"name": "X"},
                "relationships": {"catalog": {"data": []}}}
    assert LibraryAlbum.from_api(resource).upc is None


def test_library_song_extracts_embedded_catalog_isrc():
    resource = {
        "id": "l.s1",
        "type": "library-songs",
        "attributes": {"name": "Karma Police", "artistName": "Radiohead"},
        "relationships": {
            "catalog": {"data": [{"id": "456", "type": "songs",
                                  "attributes": {"isrc": "GBAYE9700116"}}]}
        },
    }
    song = LibrarySong.from_api(resource)
    assert song.isrc == "GBAYE9700116"

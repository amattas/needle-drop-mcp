from needledrop.models.canonical import (
    CanonicalAlbum,
    CanonicalArtist,
    CanonicalTrack,
    LibraryItem,
    Playlist,
)
from needledrop.models.enums import ItemType, MatchMethod, Service, VersionClass


def test_artist_defaults_external_ids_empty():
    artist = CanonicalArtist(canonical_name="Linkin Park")
    assert artist.id is None
    assert artist.mbid is None
    assert artist.external_ids == {}


def test_album_carries_mbids_and_version():
    album = CanonicalAlbum(
        title="Meteora",
        release_group_mbid="rg-123",
        release_mbid="rel-456",
        version_class=VersionClass.STANDARD,
        external_ids={"apple": "1234", "upc": "093624867", },
    )
    assert album.release_group_mbid == "rg-123"
    assert album.version_class is VersionClass.STANDARD
    assert album.external_ids["apple"] == "1234"


def test_track_optional_fields():
    track = CanonicalTrack(title="Numb", isrc="USWB10300001", track_number=8)
    assert track.recording_mbid is None
    assert track.isrc == "USWB10300001"
    assert track.track_number == 8


def test_library_item_match_defaults():
    item = LibraryItem(
        service=Service.APPLE_MUSIC,
        service_item_id="l.abc",
        item_type=ItemType.ALBUM,
    )
    assert item.match_method is MatchMethod.NONE
    assert item.match_confidence is None
    assert item.status.value == "present"


def test_playlist_minimal():
    pl = Playlist(service=Service.APPLE_MUSIC, service_playlist_id="p.1", name="Faves")
    assert pl.description is None

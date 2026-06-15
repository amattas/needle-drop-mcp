from needledrop.models.enums import (
    CandidateKind,
    FindingSeverity,
    FindingType,
    ItemType,
    LibraryStatus,
    MatchMethod,
    MatchStatus,
    Service,
    VersionClass,
)


def test_enum_values_are_stable_strings():
    assert Service.APPLE_MUSIC.value == "apple_music"
    assert ItemType.ALBUM.value == "album"
    assert LibraryStatus.REMOVED.value == "removed"
    assert VersionClass.DELUXE.value == "deluxe"
    assert MatchMethod.ISRC.value == "isrc"
    assert MatchStatus.PENDING.value == "pending"
    assert CandidateKind.RELEASE_GROUP.value == "release_group"
    assert FindingType.UNMATCHED_ITEM.value == "unmatched_item"
    assert FindingSeverity.HIGH.value == "high"


def test_enums_are_str_subclasses():
    assert isinstance(Service.APPLE_MUSIC, str)
    assert MatchMethod("fuzzy") is MatchMethod.FUZZY

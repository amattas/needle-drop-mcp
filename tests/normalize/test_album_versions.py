from needledrop.models.enums import VersionClass
from needledrop.normalize.album_versions import classify_album_version, get_album_base_title


def test_get_album_base_title_strips_parenthetical_editions():
    assert get_album_base_title("American Idiot (20th Anniversary Deluxe Edition)") == (
        "American Idiot"
    )
    assert get_album_base_title("Meteora (Bonus Track Version)") == "Meteora"
    assert get_album_base_title("Abbey Road [Remastered]") == "Abbey Road"


def test_get_album_base_title_strips_trailing_dash_edition():
    assert get_album_base_title("Dookie - Deluxe") == "Dookie"


def test_get_album_base_title_leaves_plain_titles():
    assert get_album_base_title("OK Computer") == "OK Computer"


def test_classify_album_version():
    assert classify_album_version("Dookie (30th Anniversary Edition)") == VersionClass.ANNIVERSARY
    assert classify_album_version("Abbey Road (Remastered)") == VersionClass.REMASTER
    assert classify_album_version("Nevermind (Deluxe Edition)") == VersionClass.DELUXE
    assert classify_album_version("MTV Unplugged (Live)") == VersionClass.LIVE
    assert classify_album_version("Dookie") == VersionClass.STANDARD


def test_classify_live_uses_word_boundary():
    assert classify_album_version("Deliverance") == VersionClass.STANDARD


def test_classify_clean_uses_trailing_word_boundary():
    # "Cleaning" must not classify as CLEAN (prefix-only match would be a bug).
    assert classify_album_version("Spring Cleaning") == VersionClass.STANDARD


def test_classify_remastered_still_matches_with_boundaries():
    # The trailing word boundary must not break remaster/remastered matching.
    assert classify_album_version("The Wall (Remaster)") == VersionClass.REMASTER
    assert classify_album_version("The Wall (Remastered)") == VersionClass.REMASTER

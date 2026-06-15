from needledrop.normalize.artists import is_various_artists, split_artist_credit


def test_split_artist_credit_separators():
    assert split_artist_credit("Jay-Z & Kanye West") == ["Jay-Z", "Kanye West"]
    assert split_artist_credit("blink-182 feat. Robert Smith") == ["blink-182", "Robert Smith"]
    assert split_artist_credit("Calvin Harris featuring Rihanna") == ["Calvin Harris", "Rihanna"]


def test_split_artist_credit_single():
    assert split_artist_credit("Radiohead") == ["Radiohead"]


def test_is_various_artists():
    assert is_various_artists("Various Artists") is True
    assert is_various_artists("VA") is True
    assert is_various_artists("various") is True
    assert is_various_artists("Green Day") is False

from needledrop.matching.scoring import title_score


def test_identical_titles_score_one():
    assert title_score("ok computer", "ok computer") == 1.0


def test_word_order_insensitive():
    assert title_score("computer ok", "ok computer") == 1.0


def test_close_titles_score_high():
    assert title_score("the bends", "bends") >= 0.7


def test_different_titles_score_low():
    assert title_score("kid a", "ok computer") < 0.5


def test_both_empty_score_one():
    assert title_score("", "") == 1.0

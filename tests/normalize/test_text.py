from needledrop.normalize.text import fold_accents, normalize_name


def test_fold_accents_lowercases_and_strips_diacritics():
    assert fold_accents("Beyoncé") == "beyonce"
    assert fold_accents("Sigur Rós") == "sigur ros"
    assert fold_accents("Jay-Z") == "jay-z"  # punctuation preserved by fold


def test_normalize_name_strips_punctuation_and_collapses():
    assert normalize_name("Beyoncé!") == "beyonce"
    assert normalize_name("Jay-Z") == "jay z"
    assert normalize_name("  AC/DC  ") == "ac dc"
    assert normalize_name("OK Computer") == "ok computer"


def test_normalize_name_empty():
    assert normalize_name("   ") == ""

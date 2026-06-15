"""General text normalization used for matching and display."""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")


def fold_accents(value: str) -> str:
    """Lowercase and strip diacritics, preserving punctuation/structure.

    Mirrors DuckDB `lower(strip_accents(...))` for exact artist-name matching.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.lower().strip()


def normalize_name(value: str) -> str:
    """Fold accents, drop punctuation, and collapse whitespace (for fuzzy keys)."""
    folded = fold_accents(value)
    no_punct = _NON_ALNUM.sub(" ", folded)
    return _WHITESPACE.sub(" ", no_punct).strip()

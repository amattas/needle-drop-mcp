"""Artist-credit normalization."""

from __future__ import annotations

import re

from needledrop.normalize.text import normalize_name

# Splits collaboration separators. Conservative: only well-signalled joiners
# (`feat.`/`featuring`, `&`, `/`, comma) — not `and`/`x`/`vs`, which over-split
# legitimate single names. Note: "Earth, Wind & Fire" still over-splits — a known
# heuristic limitation; matching primarily uses the first (primary) credit.
_SPLIT = re.compile(r"\s*(?:,|&|/|\bfeat\.?(?=\s)|\bfeaturing\b)\s*", re.IGNORECASE)

_VARIOUS = {"various artists", "various", "va"}


def split_artist_credit(credit: str) -> list[str]:
    """Split a combined artist credit into individual artist names."""
    return [part.strip() for part in _SPLIT.split(credit) if part.strip()]


def is_various_artists(name: str) -> bool:
    """True if the name denotes a Various-Artists compilation credit."""
    return normalize_name(name) in _VARIOUS

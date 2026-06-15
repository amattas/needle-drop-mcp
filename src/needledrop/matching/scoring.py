"""Fuzzy similarity scoring for matching."""

from __future__ import annotations

from rapidfuzz import fuzz


def title_score(a: str, b: str) -> float:
    """Word-order-insensitive similarity of two normalized titles, in [0, 1]."""
    if not a and not b:
        return 1.0
    return fuzz.token_sort_ratio(a, b) / 100.0

"""Album title version intelligence: base-title extraction + version classing."""

from __future__ import annotations

import re

from needledrop.models.enums import VersionClass

_EDITION_WORDS = (
    r"deluxe|expanded|remaster(?:ed)?|anniversary|edition|version|bonus|"
    r"special|reissue|explicit|clean|mono|stereo"
)
# A parenthetical/bracketed suffix containing an edition word.
_BRACKET_EDITION = re.compile(
    rf"\s*[\(\[][^\)\]]*\b(?:{_EDITION_WORDS})\b[^\)\]]*[\)\]]", re.IGNORECASE
)
# A trailing " - <edition>" suffix.
_DASH_EDITION = re.compile(rf"\s*-\s*(?:{_EDITION_WORDS}|single|ep)\b.*$", re.IGNORECASE)

# Ordered: first match wins (anniversary before deluxe for "Anniversary Deluxe").
_VERSION_CHECKS: tuple[tuple[str, VersionClass], ...] = (
    ("anniversary", VersionClass.ANNIVERSARY),
    ("deluxe", VersionClass.DELUXE),
    ("expanded", VersionClass.EXPANDED),
    ("remaster(?:ed)?", VersionClass.REMASTER),
    ("live", VersionClass.LIVE),
    ("explicit", VersionClass.EXPLICIT),
    ("clean", VersionClass.CLEAN),
)


def get_album_base_title(title: str) -> str:
    """Strip edition/version noise to the core album title."""
    stripped = _BRACKET_EDITION.sub("", title)
    stripped = _DASH_EDITION.sub("", stripped)
    return stripped.strip()


def classify_album_version(title: str) -> VersionClass:
    """Classify an album title's version from keyword cues (word-boundary matched)."""
    lowered = title.lower()
    for keyword, version in _VERSION_CHECKS:
        if re.search(rf"\b{keyword}\b", lowered):
            return version
    return VersionClass.STANDARD

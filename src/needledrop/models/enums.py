"""Single source of truth for domain enums shared across models and the DB layer."""

from __future__ import annotations

from enum import StrEnum


class Service(StrEnum):
    APPLE_MUSIC = "apple_music"


class ItemType(StrEnum):
    ALBUM = "album"
    TRACK = "track"
    PLAYLIST = "playlist"


class LibraryStatus(StrEnum):
    PRESENT = "present"
    REMOVED = "removed"


class VersionClass(StrEnum):
    STANDARD = "standard"
    DELUXE = "deluxe"
    EXPANDED = "expanded"
    REMASTER = "remaster"
    ANNIVERSARY = "anniversary"
    LIVE = "live"
    COMPILATION = "compilation"
    CLEAN = "clean"
    EXPLICIT = "explicit"
    UNKNOWN = "unknown"


class MatchMethod(StrEnum):
    ISRC = "isrc"
    UPC = "upc"
    FUZZY = "fuzzy"
    MANUAL = "manual"
    NONE = "none"


class MatchStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class CandidateKind(StrEnum):
    RELEASE_GROUP = "release_group"
    RECORDING = "recording"
    ARTIST = "artist"


class FindingType(StrEnum):
    DUPLICATE_ALBUM = "duplicate_album"
    DUPLICATE_TRACK = "duplicate_track"
    PARTIAL_ALBUM = "partial_album"
    SINGLE_REPLACED_BY_ALBUM = "single_replaced_by_album"
    MISSING_CORE_ALBUM = "missing_core_album"
    COMPILATION_POLLUTION = "compilation_pollution"
    METADATA_PROBLEM = "metadata_problem"
    UNMATCHED_ITEM = "unmatched_item"


class FindingSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

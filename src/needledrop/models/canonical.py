"""Provider-independent canonical music entities and library presence records."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from needledrop.models.enums import (
    ItemType,
    LibraryStatus,
    MatchMethod,
    Service,
    VersionClass,
)


class CanonicalArtist(BaseModel):
    id: int | None = None
    mbid: str | None = None
    canonical_name: str
    sort_name: str | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)


class CanonicalAlbum(BaseModel):
    id: int | None = None
    release_group_mbid: str | None = None
    release_mbid: str | None = None
    artist_id: int | None = None
    title: str
    version_class: VersionClass | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)


class CanonicalTrack(BaseModel):
    id: int | None = None
    recording_mbid: str | None = None
    album_id: int | None = None
    artist_id: int | None = None
    title: str
    isrc: str | None = None
    disc_number: int | None = None
    track_number: int | None = None
    duration_ms: int | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)


class LibraryItem(BaseModel):
    id: int | None = None
    service: Service
    service_item_id: str
    item_type: ItemType
    canonical_id: int | None = None
    match_confidence: float | None = None
    match_method: MatchMethod = MatchMethod.NONE
    added_at: datetime | None = None
    last_seen_at: datetime | None = None
    status: LibraryStatus = LibraryStatus.PRESENT


class Playlist(BaseModel):
    id: int | None = None
    service: Service
    service_playlist_id: str
    name: str
    description: str | None = None

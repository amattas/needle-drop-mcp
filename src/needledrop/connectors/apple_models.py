"""Provider-shaped Pydantic models parsed from Apple Music API JSON.

These mirror what Apple returns (library + catalog resources). Mapping them into
the canonical store + matching happens in a later plan; the connector only fetches
and parses.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LibraryAlbum(BaseModel):
    id: str
    name: str
    artist_name: str | None = None
    track_count: int | None = None
    release_date: str | None = None
    date_added: str | None = None

    @classmethod
    def from_api(cls, resource: dict[str, Any]) -> LibraryAlbum:
        a = resource.get("attributes", {})
        return cls(
            id=resource["id"],
            name=a.get("name", ""),
            artist_name=a.get("artistName"),
            track_count=a.get("trackCount"),
            release_date=a.get("releaseDate"),
            date_added=a.get("dateAdded"),
        )


class LibrarySong(BaseModel):
    id: str
    name: str
    artist_name: str | None = None
    album_name: str | None = None
    duration_ms: int | None = None
    track_number: int | None = None
    disc_number: int | None = None
    release_date: str | None = None

    @classmethod
    def from_api(cls, resource: dict[str, Any]) -> LibrarySong:
        a = resource.get("attributes", {})
        return cls(
            id=resource["id"],
            name=a.get("name", ""),
            artist_name=a.get("artistName"),
            album_name=a.get("albumName"),
            duration_ms=a.get("durationInMillis"),
            track_number=a.get("trackNumber"),
            disc_number=a.get("discNumber"),
            release_date=a.get("releaseDate"),
        )


class LibraryPlaylist(BaseModel):
    id: str
    name: str
    description: str | None = None

    @classmethod
    def from_api(cls, resource: dict[str, Any]) -> LibraryPlaylist:
        a = resource.get("attributes", {})
        description = a.get("description", {})
        return cls(
            id=resource["id"],
            name=a.get("name", ""),
            description=description.get("standard") if isinstance(description, dict) else None,
        )


class CatalogAlbum(BaseModel):
    id: str
    name: str
    artist_name: str | None = None
    upc: str | None = None
    track_count: int | None = None
    release_date: str | None = None

    @classmethod
    def from_api(cls, resource: dict[str, Any]) -> CatalogAlbum:
        a = resource.get("attributes", {})
        return cls(
            id=resource["id"],
            name=a.get("name", ""),
            artist_name=a.get("artistName"),
            upc=a.get("upc"),
            track_count=a.get("trackCount"),
            release_date=a.get("releaseDate"),
        )


class CatalogSong(BaseModel):
    id: str
    name: str
    artist_name: str | None = None
    album_name: str | None = None
    isrc: str | None = None
    duration_ms: int | None = None

    @classmethod
    def from_api(cls, resource: dict[str, Any]) -> CatalogSong:
        a = resource.get("attributes", {})
        return cls(
            id=resource["id"],
            name=a.get("name", ""),
            artist_name=a.get("artistName"),
            album_name=a.get("albumName"),
            isrc=a.get("isrc"),
            duration_ms=a.get("durationInMillis"),
        )


class CatalogSearchResult(BaseModel):
    albums: list[CatalogAlbum] = Field(default_factory=list)
    songs: list[CatalogSong] = Field(default_factory=list)

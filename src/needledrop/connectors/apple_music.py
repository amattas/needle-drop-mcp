"""Read-only Apple Music API client."""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx

from needledrop.connectors.apple_models import (
    CatalogAlbum,
    CatalogSearchResult,
    CatalogSong,
    LibraryAlbum,
    LibraryPlaylist,
    LibrarySong,
)
from needledrop.connectors.apple_token import (
    AppleCredentials,
    load_credentials,
    make_developer_token,
)
from needledrop.connectors.base import MusicConnector

# Apple's library endpoints intermittently return 429/5xx on deep pagination
# (more so with include=catalog on large libraries); these are retried with backoff.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class AppleMusicConnector(MusicConnector):
    """Reads the user's Apple Music library and searches the catalog.

    Mutating operations are intentionally absent (added in a later plan).
    """

    BASE_URL = "https://api.music.apple.com"
    LIBRARY_PAGE_LIMIT = 100
    MAX_PAGE_RETRIES = 5

    def __init__(
        self,
        credentials: AppleCredentials,
        *,
        client: httpx.Client | None = None,
        developer_token: str | None = None,
    ) -> None:
        self._creds = credentials
        self._developer_token = developer_token or make_developer_token(
            credentials.p8_pem, team_id=credentials.team_id, key_id=credentials.key_id
        )
        self._client = client or httpx.Client(base_url=self.BASE_URL, timeout=30.0)

    @classmethod
    def from_keystore(cls) -> AppleMusicConnector:
        return cls(load_credentials())

    def _headers(self, *, user: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._developer_token}"}
        if user:
            if not self._creds.user_token:
                raise RuntimeError(
                    "Music User Token missing — run `needledrop auth apple login`."
                )
            headers["Music-User-Token"] = self._creds.user_token
        return headers

    def get_storefront(self) -> str:
        return self._get_json("/v1/me/storefront")["data"][0]["id"]

    def _get_json(self, url: str) -> dict:
        """GET a user-authorized URL, retrying transient 429/5xx with exponential backoff.

        Non-retryable responses (and the final attempt) go through raise_for_status,
        so a genuine error still surfaces — we just don't let one flaky page abort a
        whole-library sync.
        """
        backoff = 1.0
        for attempt in range(self.MAX_PAGE_RETRIES):
            response = self._client.get(url, headers=self._headers(user=True))
            final = attempt == self.MAX_PAGE_RETRIES - 1
            if final or response.status_code not in _RETRYABLE_STATUS:
                response.raise_for_status()
                return response.json()
            retry_after = response.headers.get("Retry-After", "")
            time.sleep(float(retry_after) if retry_after.isdigit() else backoff)
            backoff = min(backoff * 2, 30.0)
        raise RuntimeError("unreachable")  # pragma: no cover

    def _paginate(self, path: str, *, include: str | None = None) -> Iterator[dict]:
        query = f"?limit={self.LIBRARY_PAGE_LIMIT}"
        if include:
            query += f"&include={include}"
        next_url: str | None = path + query
        while next_url:
            body = self._get_json(next_url)
            yield from body.get("data", [])
            next_url = body.get("next")

    def iter_library_albums(self) -> Iterator[LibraryAlbum]:
        for resource in self._paginate("/v1/me/library/albums", include="catalog"):
            yield LibraryAlbum.from_api(resource)

    def iter_library_songs(self) -> Iterator[LibrarySong]:
        for resource in self._paginate("/v1/me/library/songs", include="catalog"):
            yield LibrarySong.from_api(resource)

    def iter_library_playlists(self) -> Iterator[LibraryPlaylist]:
        for resource in self._paginate("/v1/me/library/playlists"):
            yield LibraryPlaylist.from_api(resource)

    def search_catalog(
        self,
        storefront: str,
        term: str,
        types: tuple[str, ...] = ("albums", "songs"),
        limit: int = 25,
    ) -> CatalogSearchResult:
        response = self._client.get(
            f"/v1/catalog/{storefront}/search",
            params={"term": term, "types": ",".join(types), "limit": limit},
            headers=self._headers(user=False),
        )
        response.raise_for_status()
        results = response.json().get("results", {})
        albums = [CatalogAlbum.from_api(x) for x in results.get("albums", {}).get("data", [])]
        songs = [CatalogSong.from_api(x) for x in results.get("songs", {}).get("data", [])]
        return CatalogSearchResult(albums=albums, songs=songs)

    def add_albums_to_library(self, catalog_album_ids: list[str]) -> None:
        """Add catalog albums (by catalog id) to the user's library."""
        response = self._client.post(
            "/v1/me/library",
            params={"ids[albums]": ",".join(catalog_album_ids)},
            headers=self._headers(user=True),
        )
        response.raise_for_status()

    def remove_album_from_library(self, library_album_id: str) -> None:
        """Remove a library album (by library id) from the user's library."""
        response = self._client.delete(
            f"/v1/me/library/albums/{library_album_id}",
            headers=self._headers(user=True),
        )
        response.raise_for_status()

    def create_playlist(
        self,
        name: str,
        *,
        description: str | None = None,
        track_ids: list[str] | None = None,
    ) -> LibraryPlaylist:
        """Create a library playlist, optionally seeded with song ids."""
        attributes: dict[str, str] = {"name": name}
        if description is not None:
            attributes["description"] = description
        body: dict = {"attributes": attributes}
        if track_ids:
            body["relationships"] = {
                "tracks": {"data": [{"id": tid, "type": "songs"} for tid in track_ids]}
            }
        response = self._client.post(
            "/v1/me/library/playlists", json=body, headers=self._headers(user=True)
        )
        response.raise_for_status()
        return LibraryPlaylist.from_api(response.json()["data"][0])

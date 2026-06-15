"""Read-only Apple Music API client."""

from __future__ import annotations

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


class AppleMusicConnector(MusicConnector):
    """Reads the user's Apple Music library and searches the catalog.

    Mutating operations are intentionally absent (added in a later plan).
    """

    BASE_URL = "https://api.music.apple.com"
    LIBRARY_PAGE_LIMIT = 100

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
        response = self._client.get("/v1/me/storefront", headers=self._headers(user=True))
        response.raise_for_status()
        return response.json()["data"][0]["id"]

    def _paginate(self, path: str, *, include: str | None = None) -> Iterator[dict]:
        query = f"?limit={self.LIBRARY_PAGE_LIMIT}"
        if include:
            query += f"&include={include}"
        next_url: str | None = path + query
        while next_url:
            response = self._client.get(next_url, headers=self._headers(user=True))
            response.raise_for_status()
            body = response.json()
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

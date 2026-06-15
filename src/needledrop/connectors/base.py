"""Abstract connector interface every music-service connector implements.

Read-only for now; mutating operations (add/remove album, playlists) are added
in a later plan.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any


class MusicConnector(ABC):
    @abstractmethod
    def get_storefront(self) -> str:
        """Return the user's storefront code (e.g. 'us')."""

    @abstractmethod
    def iter_library_albums(self) -> Iterator[Any]:
        """Yield the user's saved library albums."""

    @abstractmethod
    def iter_library_songs(self) -> Iterator[Any]:
        """Yield the user's saved library songs."""

    @abstractmethod
    def iter_library_playlists(self) -> Iterator[Any]:
        """Yield the user's library playlists."""

    @abstractmethod
    def search_catalog(
        self, storefront: str, term: str, types: tuple[str, ...] = ("albums", "songs"), limit: int = 25
    ) -> Any:
        """Search the provider catalog."""

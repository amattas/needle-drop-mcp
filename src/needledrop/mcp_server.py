"""MCP server exposing NeedleDrop's library intelligence.

`create_server(connect)` builds a FastMCP instance whose DB-backed tools open a
fresh DuckDB connection per call (via the injected `connect` factory) and close
it when the call finishes. That keeps the server lock-free while idle: DuckDB is
single-writer, so holding a connection open for the whole process lifetime would
block the CLI and other clients (and make the connect/health-check handshake
collide on the lock). Per-call open/close means the lock is held only for the
duration of an actual query.

Catalog browse (`search_catalog`) reads the Apple Music catalog via an injected
callable. The Apple-library-mutating tools (`add_album`, `remove_album`,
`create_playlist`) default to a dry-run preview and only apply when called with
`dry_run=false` and a `mutator` is injected.

stdio transport speaks MCP over stdout — never print() to stdout from here.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from datetime import datetime

import duckdb
from fastmcp import FastMCP

from needledrop.analysis.compilation_pollution import (
    find_compilation_pollution as _find_compilation_pollution,
)
from needledrop.analysis.duplicate_tracks import find_duplicate_tracks as _find_duplicate_tracks
from needledrop.analysis.duplicates import find_duplicate_albums as _find_duplicate_albums
from needledrop.analysis.missing_albums import (
    find_missing_core_albums as _find_missing_core_albums,
)
from needledrop.analysis.partial_albums import find_partial_albums as _find_partial_albums
from needledrop.analysis.single_replaced import find_single_replaced as _find_single_replaced
from needledrop.db.repository import (
    get_findings as _get_findings,
)
from needledrop.db.repository import (
    get_library_albums as _get_library_albums,
)
from needledrop.db.repository import (
    get_library_summary as _get_library_summary,
)
from needledrop.db.repository import (
    get_review_queue as _get_review_queue,
)
from needledrop.db.repository import (
    list_unmatched as _list_unmatched,
)
from needledrop.db.repository import (
    reject_match as _reject_match,
)
from needledrop.db.repository import (
    resolve_match as _resolve_match,
)
from needledrop.db.repository import (
    search_library as _search_library,
)
from needledrop.discography import get_album_detail as _get_album_detail
from needledrop.discography import get_album_versions as _get_album_versions
from needledrop.discography import get_artist_collection as _get_artist_collection
from needledrop.discography import get_song_detail as _get_song_detail
from needledrop.services.cleanup import run_cleanup_scan as _run_cleanup_scan


def create_server(
    connect: Callable[[], duckdb.DuckDBPyConnection],
    *,
    sync_runner: Callable[[], dict] | None = None,
    catalog_search: Callable[[str, tuple[str, ...], int], dict] | None = None,
    mutator: object | None = None,
) -> FastMCP:
    """Build the NeedleDrop MCP server.

    `connect` is a zero-arg factory that opens a DuckDB connection. DB-backed tools
    open one per call and close it when done (see module docstring), so the server
    never holds the single-writer lock while idle.

    `sync_runner` is an injected zero-arg callable that performs a library sync and
    returns its summary dict (injected so the server stays decoupled from
    credentials/network; if None, `trigger_sync` raises).

    `catalog_search` is an injected callable ``(term, types, limit) -> dict`` that
    searches the Apple Music catalog (if None, the `search_catalog` tool raises).

    `mutator` exposes ``add_albums_to_library(ids)``,
    ``remove_album_from_library(library_album_id)``, and
    ``create_playlist(name, *, description, track_ids) -> LibraryPlaylist``. The
    corresponding tools default to a dry-run preview and only apply when called with
    ``dry_run=False``; a real apply with no mutator injected raises.
    """
    mcp = FastMCP(name="needledrop")

    def _q(fn):
        """Run `fn(con)` against a fresh connection, closing it when done."""
        with closing(connect()) as con:
            return fn(con)

    @mcp.tool
    def get_library_summary() -> dict:
        """Counts of present library items by type, plus matched/unmatched totals."""
        return _q(_get_library_summary)

    @mcp.tool
    def list_albums() -> list[dict]:
        """Present library albums joined to their canonical album metadata."""
        return _q(_get_library_albums)

    @mcp.tool
    def find_duplicate_albums() -> list[dict]:
        """Owned albums where you hold more than one edition of a release-group."""
        return _q(lambda con: [f.model_dump(mode="json") for f in _find_duplicate_albums(con)])

    @mcp.tool
    def find_compilation_pollution() -> list[dict]:
        """Owned albums that are compilations, soundtracks, or Various-Artists records."""
        return _q(lambda con: [f.model_dump(mode="json") for f in _find_compilation_pollution(con)])

    @mcp.tool
    def find_missing_core_albums() -> list[dict]:
        """Studio albums by artists you own that are missing from your library."""
        return _q(lambda con: [f.model_dump(mode="json") for f in _find_missing_core_albums(con)])

    @mcp.tool
    def find_duplicate_tracks() -> list[dict]:
        """Tracks you own more than one copy of (same recording identity)."""
        return _q(lambda con: [f.model_dump(mode="json") for f in _find_duplicate_tracks(con)])

    @mcp.tool
    def find_partial_albums() -> list[dict]:
        """Albums you added but own only some of the tracks from."""
        return _q(lambda con: [f.model_dump(mode="json") for f in _find_partial_albums(con)])

    @mcp.tool
    def find_single_replaced() -> list[dict]:
        """Standalone singles you also own on a full album (redundant)."""
        return _q(lambda con: [f.model_dump(mode="json") for f in _find_single_replaced(con)])

    @mcp.tool
    def generate_cleanup_report() -> dict:
        """Run every analysis, persist findings, and return counts plus open findings."""
        def _run(con):
            counts = _run_cleanup_scan(con, now=datetime.now())
            findings = [f.model_dump(mode="json") for f in _get_findings(con)]
            return {"counts": counts, "findings": findings}

        return _q(_run)

    @mcp.tool
    def list_unmatched() -> list[dict]:
        """Present library items with no MusicBrainz match (need review)."""
        return _q(_list_unmatched)

    @mcp.tool
    def search_library(query: str) -> list[dict]:
        """Case-insensitive substring search over present album & track titles."""
        return _q(lambda con: _search_library(con, query))

    @mcp.tool
    def list_review_queue() -> list[dict]:
        """Present library items with pending match candidates awaiting a decision."""
        return _q(_get_review_queue)

    @mcp.tool
    def resolve_match(candidate_id: int) -> dict:
        """Confirm a pending candidate (by its candidate_id) as the item's match."""
        return _q(lambda con: _resolve_match(con, candidate_id=candidate_id))

    @mcp.tool
    def reject_match(library_item_id: int) -> dict:
        """Reject all pending candidates for a library item; returns the count rejected."""
        return _q(lambda con: {"rejected": _reject_match(con, library_item_id=library_item_id)})

    @mcp.tool
    def get_artist_collection(artist_mbid: str) -> list[dict]:
        """An artist's full release-group discography (MusicBrainz), flagged by ownership."""
        return _q(lambda con: _get_artist_collection(con, artist_mbid))

    @mcp.tool
    def get_album_versions(release_group_mbid: str) -> list[dict]:
        """All release editions of a release-group (MusicBrainz), flagged by ownership."""
        return _q(lambda con: _get_album_versions(con, release_group_mbid))

    @mcp.tool
    def get_song_detail(recording_mbid: str) -> dict:
        """Where a recording lives: owned library albums + release-groups it appears on."""
        return _q(lambda con: _get_song_detail(con, recording_mbid))

    @mcp.tool
    def get_album_detail(release_group_mbid: str) -> dict:
        """Consolidation view: owned editions of a release-group (with Apple ids +
        completeness) and all available editions, to decide what to keep/remove/add."""
        return _q(lambda con: _get_album_detail(con, release_group_mbid))

    @mcp.tool
    def search_catalog(term: str, types: list[str] | None = None, limit: int = 25) -> dict:
        """Search the Apple Music catalog (albums/songs) by text."""
        if catalog_search is None:
            raise RuntimeError(
                "Catalog search is not available: no catalog_search configured for this server."
            )
        return catalog_search(term, tuple(types) if types else ("albums", "songs"), limit)

    @mcp.tool
    def trigger_sync() -> dict:
        """Re-pull the Apple Music library into the local database; returns the summary."""
        if sync_runner is None:
            raise RuntimeError(
                "Sync is not available: no sync_runner configured for this server."
            )
        return sync_runner()

    @mcp.tool
    def add_album(catalog_album_id: str, dry_run: bool = True) -> dict:
        """Add a catalog album to your Apple Music library.

        Defaults to a dry-run preview. Pass dry_run=false to APPLY the change to your
        real library.
        """
        if dry_run:
            return {"dry_run": True, "action": "add_album", "catalog_album_id": catalog_album_id}
        if mutator is None:
            raise RuntimeError("Mutations are not available: no mutator configured.")
        mutator.add_albums_to_library([catalog_album_id])
        return {"dry_run": False, "added_album": catalog_album_id}

    @mcp.tool
    def remove_album(library_album_id: str, dry_run: bool = True) -> dict:
        """Remove an album from your Apple Music library.

        Defaults to a dry-run preview. Pass dry_run=false to APPLY the removal to your
        real library.
        """
        if dry_run:
            return {
                "dry_run": True,
                "action": "remove_album",
                "library_album_id": library_album_id,
            }
        if mutator is None:
            raise RuntimeError("Mutations are not available: no mutator configured.")
        mutator.remove_album_from_library(library_album_id)
        return {"dry_run": False, "removed_album": library_album_id}

    @mcp.tool
    def create_playlist(
        name: str,
        description: str | None = None,
        track_ids: list[str] | None = None,
        dry_run: bool = True,
    ) -> dict:
        """Create a playlist in your Apple Music library.

        Defaults to a dry-run preview. Pass dry_run=false to actually create it.
        """
        if dry_run:
            return {
                "dry_run": True,
                "action": "create_playlist",
                "name": name,
                "track_count": len(track_ids or []),
            }
        if mutator is None:
            raise RuntimeError("Mutations are not available: no mutator configured.")
        playlist = mutator.create_playlist(name, description=description, track_ids=track_ids)
        return {"dry_run": False, "created_playlist": playlist.model_dump(mode="json")}

    return mcp

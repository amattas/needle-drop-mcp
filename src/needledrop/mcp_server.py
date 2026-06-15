"""Read-only MCP server exposing NeedleDrop's library intelligence.

`create_server(con)` builds a FastMCP instance whose tools are closures over a
single open DuckDB connection. The tools are read-only with respect to Apple
Music; the only writes performed are to the LOCAL DuckDB (cleanup findings, and
the opt-in `trigger_sync` re-pull). Catalog-facing and Apple-mutating tools are
deferred to a later plan.

stdio transport speaks MCP over stdout — never print() to stdout from here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import duckdb
from fastmcp import FastMCP

from needledrop.analysis.compilation_pollution import (
    find_compilation_pollution as _find_compilation_pollution,
)
from needledrop.analysis.duplicates import find_duplicate_albums as _find_duplicate_albums
from needledrop.analysis.missing_albums import (
    find_missing_core_albums as _find_missing_core_albums,
)
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
from needledrop.services.cleanup import run_cleanup_scan as _run_cleanup_scan


def create_server(
    con: duckdb.DuckDBPyConnection,
    *,
    sync_runner: Callable[[], dict] | None = None,
) -> FastMCP:
    """Build the read-only NeedleDrop MCP server over an open DuckDB connection.

    `sync_runner` is an injected zero-arg callable that performs a library sync
    and returns its summary dict. It is injected (rather than built here) so the
    server stays decoupled from credentials and the network, and so tests can
    stub it. If it is None, `trigger_sync` raises.

    All tools (and `sync_runner`) share this single `con`. That is safe only
    under the default stdio transport, which handles requests sequentially: a
    DuckDB Python connection is not safe for concurrent use, so switching to a
    concurrent transport (HTTP/SSE) would require a per-request connection.
    """
    mcp = FastMCP(name="needledrop")

    @mcp.tool
    def get_library_summary() -> dict:
        """Counts of present library items by type, plus matched/unmatched totals."""
        return _get_library_summary(con)

    @mcp.tool
    def list_albums() -> list[dict]:
        """Present library albums joined to their canonical album metadata."""
        return _get_library_albums(con)

    @mcp.tool
    def find_duplicate_albums() -> list[dict]:
        """Owned albums where you hold more than one edition of a release-group."""
        return [f.model_dump(mode="json") for f in _find_duplicate_albums(con)]

    @mcp.tool
    def find_compilation_pollution() -> list[dict]:
        """Owned albums that are compilations, soundtracks, or Various-Artists records."""
        return [f.model_dump(mode="json") for f in _find_compilation_pollution(con)]

    @mcp.tool
    def find_missing_core_albums() -> list[dict]:
        """Studio albums by artists you own that are missing from your library."""
        return [f.model_dump(mode="json") for f in _find_missing_core_albums(con)]

    @mcp.tool
    def generate_cleanup_report() -> dict:
        """Run every analysis, persist findings, and return counts plus open findings."""
        counts = _run_cleanup_scan(con, now=datetime.now())
        findings = [f.model_dump(mode="json") for f in _get_findings(con)]
        return {"counts": counts, "findings": findings}

    @mcp.tool
    def list_unmatched() -> list[dict]:
        """Present library items with no MusicBrainz match (need review)."""
        return _list_unmatched(con)

    @mcp.tool
    def search_library(query: str) -> list[dict]:
        """Case-insensitive substring search over present album & track titles."""
        return _search_library(con, query)

    @mcp.tool
    def list_review_queue() -> list[dict]:
        """Present library items with pending match candidates awaiting a decision."""
        return _get_review_queue(con)

    @mcp.tool
    def resolve_match(candidate_id: int) -> dict:
        """Confirm a pending candidate (by its candidate_id) as the item's match."""
        return _resolve_match(con, candidate_id=candidate_id)

    @mcp.tool
    def reject_match(library_item_id: int) -> dict:
        """Reject all pending candidates for a library item; returns the count rejected."""
        return {"rejected": _reject_match(con, library_item_id=library_item_id)}

    @mcp.tool
    def trigger_sync() -> dict:
        """Re-pull the Apple Music library into the local database; returns the summary."""
        if sync_runner is None:
            raise RuntimeError(
                "Sync is not available: no sync_runner configured for this server."
            )
        return sync_runner()

    return mcp

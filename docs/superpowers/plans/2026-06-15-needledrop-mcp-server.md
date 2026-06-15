# NeedleDrop Read-Only MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose NeedleDrop's library intelligence over the Model Context Protocol as a read-only FastMCP server, launchable via `needledrop serve`, so an MCP client (Claude Desktop, etc.) can inspect the library and run cleanup analyses.

**Architecture:** A `create_server(con, *, sync_runner=None)` factory builds a `FastMCP` instance whose tools are closures over a single open DuckDB connection. Tools delegate to the existing repository/analysis/cleanup functions — no new business logic. The server is read-only over Apple Music: the only writes it performs are to the *local* DuckDB (cleanup findings persistence, and an opt-in `trigger_sync` that re-pulls the library). The CLI `serve` command opens the canonical DB with `open_db`, wires a keystore-backed `sync_runner`, and runs the server over stdio. Two new repository read helpers (`list_unmatched`, `search_library`) back the corresponding tools.

**Tech Stack:** FastMCP 3.4.2 (`from fastmcp import FastMCP, Client`), DuckDB, Pydantic v2 (`model_dump(mode="json")` for wire-safe output), typer, pytest (no pytest-asyncio — tests wrap async client calls in `asyncio.run`).

---

## Background & Key Facts (read before starting)

**Environment:** Python interpreter is `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python`. Use it directly for all commands (do NOT use `mamba run`). No Docker or network needed for any task in this plan.

**Run the suite / lint with:**
- `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest -q`
- `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`

Ruff config: line-length 100, select E/F/I/UP/B, B008 ignored (typer Option defaults). Keep every line ≤ 100 chars.

**FastMCP 3.4.2 facts (verified in this environment):**
- `from fastmcp import FastMCP, Client`
- `mcp = FastMCP(name="needledrop")`
- `@mcp.tool` (bare, no parens) registers the function as a tool AND returns the *original function unchanged* — it stays directly callable. The tool name is the function name.
- `mcp.run(show_banner=False)` runs synchronously; default transport is stdio.
- In-memory testing: `async with Client(mcp) as client: result = await client.call_tool("tool_name", {args})` then read `result.data` (the deserialized return value). `await client.list_tools()` returns objects with `.name`.
- pytest-asyncio is NOT installed. Wrap async client interactions in a plain function and call `asyncio.run(...)` from the synchronous test.
- **stdio gotcha:** the server speaks MCP over stdout. NEVER `print()` to stdout from server code; tool *return values* are fine (they go through the protocol). If logging is needed, log to stderr.

**Existing code this plan builds on (all merged on `main`, 156 tests green):**

`src/needledrop/db/duckdb_store.py`:
- `open_db(db_path) -> duckdb.DuckDBPyConnection` — connect + init_schema + apply_migrations. CLI entry point.

`src/needledrop/db/repository.py` (you will ADD two functions here):
- `get_library_summary(con) -> dict` — counts by item_type + `matched`/`unmatched` totals.
- `get_library_albums(con) -> list[dict]` — present album rows joined to canonical albums; each dict has `id, title, release_group_mbid, version_class, match_method, match_confidence`.
- `get_findings(con, *, include_closed=False) -> list[CleanupFinding]`.

`src/needledrop/analysis/{duplicates,compilation_pollution,missing_albums}.py`:
- `find_duplicate_albums(con) -> list[CleanupFinding]`
- `find_compilation_pollution(con) -> list[CleanupFinding]`
- `find_missing_core_albums(con) -> list[CleanupFinding]`

`src/needledrop/services/cleanup.py`:
- `run_cleanup_scan(con, *, now: datetime) -> dict[str, int]` — runs all three analyses, persists findings, returns counts by type.

`src/needledrop/services/sync.py`:
- `sync_library(connector, con, *, now, service="apple_music") -> dict` with keys `added, removed, present`.

`src/needledrop/connectors/apple_music.py`:
- `AppleMusicConnector.from_keystore() -> AppleMusicConnector` — builds a connector from stored credentials.

`src/needledrop/config.py`:
- `load_settings() -> Settings`; `Settings.db_path: Path`.

`src/needledrop/cli.py`:
- `app = typer.Typer(...)`; existing commands `mb import`, `auth apple set-credentials`, `auth apple login`, `sync`. You will ADD `serve`.

**Schema facts (`src/needledrop/db/schema.sql`):**
- `library_items(id, service, service_item_id, item_type, canonical_id, match_confidence, match_method, added_at, last_seen_at, status, ...)`. `match_method = 'none'` means unmatched. `status = 'present'` means currently in the library. `canonical_id` is a polymorphic soft reference: it points at `albums.id` when `item_type='album'`, `tracks.id` when `item_type='track'` (sync always creates a canonical row, even for unmatched items).
- `albums(id, ..., title, ...)`, `tracks(id, ..., title, ...)`.

**CleanupFinding** (`src/needledrop/models/findings.py`) is a Pydantic model with `finding_type` (StrEnum), `severity` (StrEnum), `entity_id`, `description`, `recommendation`, `resolved_at`, `ignored_at`. Use `.model_dump(mode="json")` to get a JSON-safe dict (enums → strings, datetimes → ISO strings) for MCP tool returns.

**Scope (this plan):** read-only tools mapping to existing functionality, plus a local-only `trigger_sync`. **Deferred to Plan 9:** Apple-mutating tools (add/remove album, playlists), `resolve_match`, and catalog-facing tools (`search_catalog`, `get_artist_collection`, `get_album_versions`), plus the not-yet-built analyses (partial-album / single-track / duplicate-track detection).

---

## File Structure

- **Modify** `src/needledrop/db/repository.py` — add `list_unmatched(con)` and `search_library(con, query)`.
- **Modify** `tests/db/test_repository.py` — tests for the two new helpers.
- **Create** `src/needledrop/mcp_server.py` — `create_server(con, *, sync_runner=None) -> FastMCP` with the read-only tool set.
- **Create** `tests/test_mcp_server.py` — direct-call unit tests + in-memory `Client(mcp)` integration tests.
- **Modify** `src/needledrop/cli.py` — add the `serve` command.
- **Modify** `tests/test_cli.py` (or create `tests/test_cli_serve.py`) — test that `serve` builds and runs the server.

---

## Task 1: Repository read helpers (`list_unmatched`, `search_library`)

**Files:**
- Modify: `src/needledrop/db/repository.py` (append two functions after `get_library_albums`)
- Test: `tests/db/test_repository.py`

These two functions back the `list_unmatched` and `search_library` MCP tools. Both read present library items and resolve a display title by joining to the canonical album OR track depending on `item_type` (polymorphic `canonical_id`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/db/test_repository.py`. First check the existing imports at the top of that file and add `list_unmatched, search_library` to the `from needledrop.db.repository import (...)` line (or add a new import line if the file imports them individually). Then append these tests:

```python
def _seed_titled_items(con):
    """Two albums + one track as present library items with mixed match state."""
    con.execute("INSERT INTO artists (canonical_name) VALUES ('Green Day')")
    artist_id = con.execute("SELECT id FROM artists").fetchone()[0]
    con.execute(
        "INSERT INTO albums (artist_id, title, release_group_mbid) "
        "VALUES (?, 'Dookie', 'rg-dookie')",
        [artist_id],
    )
    con.execute(
        "INSERT INTO albums (artist_id, title) VALUES (?, 'Untagged Bootleg')",
        [artist_id],
    )
    con.execute(
        "INSERT INTO tracks (artist_id, title) VALUES (?, 'Basket Case')",
        [artist_id],
    )
    dookie_id = con.execute("SELECT id FROM albums WHERE title = 'Dookie'").fetchone()[0]
    bootleg_id = con.execute(
        "SELECT id FROM albums WHERE title = 'Untagged Bootleg'"
    ).fetchone()[0]
    track_id = con.execute("SELECT id FROM tracks WHERE title = 'Basket Case'").fetchone()[0]
    # Matched album, unmatched album, matched track.
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_method, status) "
        "VALUES ('apple_music', 'l.dookie', 'album', ?, 'upc', 'present')",
        [dookie_id],
    )
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_method, status) "
        "VALUES ('apple_music', 'l.bootleg', 'album', ?, 'none', 'present')",
        [bootleg_id],
    )
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_method, status) "
        "VALUES ('apple_music', 'l.basket', 'track', ?, 'fuzzy', 'present')",
        [track_id],
    )


def test_list_unmatched_returns_only_unmatched_present_items(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_titled_items(con)
    rows = list_unmatched(con)
    assert [r["title"] for r in rows] == ["Untagged Bootleg"]
    assert rows[0]["item_type"] == "album"
    assert rows[0]["service_item_id"] == "l.bootleg"


def test_search_library_matches_titles_case_insensitively(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_titled_items(con)
    rows = search_library(con, "case")  # lowercase query matches 'Basket Case'
    assert [r["title"] for r in rows] == ["Basket Case"]
    assert rows[0]["item_type"] == "track"
    assert rows[0]["match_method"] == "fuzzy"


def test_search_library_spans_albums_and_tracks(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_titled_items(con)
    titles = {r["title"] for r in search_library(con, "a")}  # matches all three titles
    assert titles == {"Dookie", "Untagged Bootleg", "Basket Case"}
```

Ensure `connect` and `init_schema` are imported in the test file (the existing tests use them — confirm the import line includes both).

- [ ] **Step 2: Run tests to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -q -k "unmatched or search_library"`
Expected: FAIL with `ImportError: cannot import name 'list_unmatched'` (or `NameError`).

- [ ] **Step 3: Implement the helpers**

Append to `src/needledrop/db/repository.py` (after `get_library_albums`):

```python
def list_unmatched(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Present library items with no MusicBrainz match, with a resolved display title.

    `canonical_id` is polymorphic (album or track by item_type), so the title is
    pulled from whichever canonical table the item points at.
    """
    rows = con.execute(
        "SELECT li.id, li.item_type, li.service_item_id, "
        "COALESCE(al.title, tr.title) AS title "
        "FROM library_items li "
        "LEFT JOIN albums al ON li.item_type = 'album' AND li.canonical_id = al.id "
        "LEFT JOIN tracks tr ON li.item_type = 'track' AND li.canonical_id = tr.id "
        "WHERE li.status = 'present' AND li.match_method = 'none' "
        "ORDER BY title"
    ).fetchall()
    return [
        {"id": r[0], "item_type": r[1], "service_item_id": r[2], "title": r[3]}
        for r in rows
    ]


def search_library(con: duckdb.DuckDBPyConnection, query: str) -> list[dict]:
    """Case-insensitive substring search over present album & track titles."""
    rows = con.execute(
        "SELECT li.id, li.item_type, COALESCE(al.title, tr.title) AS title, "
        "li.match_method "
        "FROM library_items li "
        "LEFT JOIN albums al ON li.item_type = 'album' AND li.canonical_id = al.id "
        "LEFT JOIN tracks tr ON li.item_type = 'track' AND li.canonical_id = tr.id "
        "WHERE li.status = 'present' "
        "AND lower(COALESCE(al.title, tr.title)) LIKE '%' || lower(?) || '%' "
        "ORDER BY title",
        [query],
    ).fetchall()
    return [
        {"id": r[0], "item_type": r[1], "title": r[2], "match_method": r[3]}
        for r in rows
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -q`
Expected: PASS (all repository tests, including the three new ones).

- [ ] **Step 5: Lint**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check src/needledrop/db/repository.py tests/db/test_repository.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/db/repository.py tests/db/test_repository.py
git commit -m "feat: add list_unmatched and search_library repository helpers"
```

---

## Task 2: FastMCP server module (`create_server`)

**Files:**
- Create: `src/needledrop/mcp_server.py`
- Test: `tests/test_mcp_server.py`

`create_server(con, *, sync_runner=None)` returns a `FastMCP` whose tools close over `con`. Read-only tools wrap repository/analysis functions; `generate_cleanup_report` and `trigger_sync` write only to the local DB. `sync_runner` is an injected zero-arg callable returning a summary dict (so tests stub it and the server stays decoupled from credentials/network); if it's `None` and `trigger_sync` is invoked, raise a clear error.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_server.py`:

```python
import asyncio
from datetime import datetime

from fastmcp import Client

from needledrop.db.duckdb_store import connect, init_schema
from needledrop.mcp_server import create_server


def _seed(con):
    con.execute("INSERT INTO artists (canonical_name) VALUES ('Green Day')")
    artist_id = con.execute("SELECT id FROM artists").fetchone()[0]
    con.execute(
        "INSERT INTO albums (artist_id, title, release_group_mbid, version_class) "
        "VALUES (?, 'Dookie', 'rg-dookie', 'standard')",
        [artist_id],
    )
    con.execute(
        "INSERT INTO albums (artist_id, title, release_group_mbid, version_class) "
        "VALUES (?, 'Dookie (Deluxe)', 'rg-dookie', 'deluxe')",
        [artist_id],
    )
    con.execute(
        "INSERT INTO albums (artist_id, title) VALUES (?, 'Untagged Bootleg')",
        [artist_id],
    )
    standard = con.execute("SELECT id FROM albums WHERE title = 'Dookie'").fetchone()[0]
    deluxe = con.execute(
        "SELECT id FROM albums WHERE title = 'Dookie (Deluxe)'"
    ).fetchone()[0]
    bootleg = con.execute(
        "SELECT id FROM albums WHERE title = 'Untagged Bootleg'"
    ).fetchone()[0]
    for sid, cid, method in [
        ("l.std", standard, "upc"),
        ("l.dlx", deluxe, "upc"),
        ("l.boot", bootleg, "none"),
    ]:
        con.execute(
            "INSERT INTO library_items "
            "(service, service_item_id, item_type, canonical_id, match_method, status) "
            "VALUES ('apple_music', ?, 'album', ?, ?, 'present')",
            [sid, cid, method],
        )


def _fresh_con():
    con = connect(":memory:")
    init_schema(con)
    return con


def _call(server, tool, args=None):
    """Invoke a tool through an in-memory MCP client; return result.data."""
    async def go():
        async with Client(server) as client:
            result = await client.call_tool(tool, args or {})
            return result.data

    return asyncio.run(go())


def test_server_exposes_expected_tools():
    con = _fresh_con()
    server = create_server(con)

    async def list_names():
        async with Client(server) as client:
            return {t.name for t in await client.list_tools()}

    names = asyncio.run(list_names())
    assert {
        "get_library_summary",
        "list_albums",
        "find_duplicate_albums",
        "find_compilation_pollution",
        "find_missing_core_albums",
        "generate_cleanup_report",
        "list_unmatched",
        "search_library",
        "trigger_sync",
    }.issubset(names)


def test_get_library_summary_tool_counts_items():
    con = _fresh_con()
    _seed(con)
    summary = _call(create_server(con), "get_library_summary")
    assert summary["album"] == 3
    assert summary["matched"] == 2
    assert summary["unmatched"] == 1


def test_list_albums_tool_returns_present_albums():
    con = _fresh_con()
    _seed(con)
    albums = _call(create_server(con), "list_albums")
    titles = {a["title"] for a in albums}
    assert {"Dookie", "Dookie (Deluxe)", "Untagged Bootleg"}.issubset(titles)


def test_find_duplicate_albums_tool_reports_release_group_dupes():
    con = _fresh_con()
    _seed(con)
    findings = _call(create_server(con), "find_duplicate_albums")
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "duplicate_album"


def test_list_unmatched_tool_returns_unmatched_only():
    con = _fresh_con()
    _seed(con)
    rows = _call(create_server(con), "list_unmatched")
    assert [r["title"] for r in rows] == ["Untagged Bootleg"]


def test_search_library_tool_filters_by_title():
    con = _fresh_con()
    _seed(con)
    rows = _call(create_server(con), "search_library", {"query": "deluxe"})
    assert [r["title"] for r in rows] == ["Dookie (Deluxe)"]


def test_generate_cleanup_report_tool_runs_scan_and_returns_findings():
    con = _fresh_con()
    _seed(con)
    report = _call(create_server(con), "generate_cleanup_report")
    assert report["counts"]["duplicate_album"] == 1
    descriptions = {f["description"] for f in report["findings"]}
    assert any("versions of 'Dookie'" in d for d in descriptions)


def test_trigger_sync_tool_invokes_injected_runner():
    con = _fresh_con()
    calls = []

    def runner():
        calls.append(True)
        return {"added": 5, "removed": 1, "present": 42}

    summary = _call(create_server(con, sync_runner=runner), "trigger_sync")
    assert calls == [True]
    assert summary == {"added": 5, "removed": 1, "present": 42}


def test_trigger_sync_without_runner_reports_error():
    con = _fresh_con()
    # No sync_runner injected: calling the tool must surface a clear error,
    # not crash silently. Direct-call the underlying function for a clean assertion.
    server = create_server(con)
    # The bare @mcp.tool decorator leaves the function importable via the module
    # only through the client; assert via client that the call errors.
    import pytest
    from fastmcp.exceptions import ToolError

    async def go():
        async with Client(server) as client:
            await client.call_tool("trigger_sync", {})

    with pytest.raises(ToolError):
        asyncio.run(go())
```

Note on the last test: FastMCP wraps exceptions raised inside a tool as `fastmcp.exceptions.ToolError` when surfaced through the client. If that import path is wrong in 3.4.2, adjust to the actual exception type FastMCP raises (verify with a quick REPL check); the behavioral assertion — calling `trigger_sync` with no runner raises — must hold.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_mcp_server.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'needledrop.mcp_server'`.

- [ ] **Step 3: Implement the server module**

Create `src/needledrop/mcp_server.py`:

```python
"""Read-only MCP server exposing NeedleDrop's library intelligence.

`create_server(con)` builds a FastMCP instance whose tools are closures over a
single open DuckDB connection. The tools are read-only with respect to Apple
Music; the only writes performed are to the LOCAL DuckDB (cleanup findings, and
the opt-in `trigger_sync` re-pull). Catalog-facing and Apple-mutating tools are
deferred to a later plan.

stdio transport speaks MCP over stdout — never print() to stdout from here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import duckdb
from fastmcp import FastMCP

from needledrop.analysis.compilation_pollution import find_compilation_pollution
from needledrop.analysis.duplicates import find_duplicate_albums
from needledrop.analysis.missing_albums import find_missing_core_albums
from needledrop.db.repository import (
    get_library_albums,
    get_library_summary,
    get_findings,
    list_unmatched,
    search_library,
)
from needledrop.services.cleanup import run_cleanup_scan


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
    """
    mcp = FastMCP(name="needledrop")

    @mcp.tool
    def get_library_summary() -> dict:
        """Counts of present library items by type, plus matched/unmatched totals."""
        return get_library_summary(con)

    @mcp.tool
    def list_albums() -> list[dict]:
        """Present library albums joined to their canonical album metadata."""
        return get_library_albums(con)

    @mcp.tool
    def find_duplicate_albums() -> list[dict]:
        """Owned albums where you hold more than one edition of a release-group."""
        return [f.model_dump(mode="json") for f in find_duplicate_albums(con)]

    @mcp.tool
    def find_compilation_pollution() -> list[dict]:
        """Owned albums that are compilations, soundtracks, or Various-Artists records."""
        return [f.model_dump(mode="json") for f in find_compilation_pollution(con)]

    @mcp.tool
    def find_missing_core_albums() -> list[dict]:
        """Studio albums by artists you own that are missing from your library."""
        return [f.model_dump(mode="json") for f in find_missing_core_albums(con)]

    @mcp.tool
    def generate_cleanup_report() -> dict:
        """Run every analysis, persist findings, and return counts plus open findings."""
        counts = run_cleanup_scan(con, now=datetime.now())
        findings = [f.model_dump(mode="json") for f in get_findings(con)]
        return {"counts": counts, "findings": findings}

    @mcp.tool
    def list_unmatched() -> list[dict]:
        """Present library items with no MusicBrainz match (need review)."""
        return list_unmatched(con)

    @mcp.tool
    def search_library(query: str) -> list[dict]:
        """Case-insensitive substring search over present album & track titles."""
        return search_library(con, query)

    @mcp.tool
    def trigger_sync() -> dict:
        """Re-pull the Apple Music library into the local database; returns the summary."""
        if sync_runner is None:
            raise RuntimeError(
                "Sync is not available: no sync_runner configured for this server."
            )
        return sync_runner()

    return mcp
```

**IMPORTANT naming caveat:** the inner tool functions shadow the imported function names (`get_library_summary`, `find_duplicate_albums`, `list_unmatched`, `search_library`). Inside each tool the call to the imported function would then recurse into itself. To avoid this, alias the imports. Replace the import block and the affected tool bodies so the module-level imports use distinct names:

```python
from needledrop.analysis.compilation_pollution import (
    find_compilation_pollution as _find_compilation_pollution,
)
from needledrop.analysis.duplicates import find_duplicate_albums as _find_duplicate_albums
from needledrop.analysis.missing_albums import (
    find_missing_core_albums as _find_missing_core_albums,
)
from needledrop.db.repository import (
    get_findings as _get_findings,
    get_library_albums as _get_library_albums,
    get_library_summary as _get_library_summary,
    list_unmatched as _list_unmatched,
    search_library as _search_library,
)
from needledrop.services.cleanup import run_cleanup_scan as _run_cleanup_scan
```

Then the tool bodies call the underscore-aliased functions, e.g.:

```python
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
```

Drop the unused `Any` import if ruff flags it (F401). Keep `Callable` and `datetime`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_mcp_server.py -q`
Expected: PASS. If `test_trigger_sync_without_runner_reports_error` fails on the `ToolError` import/type, open a REPL to find the real exception type FastMCP raises through the client and update the test import to match (the behavior — it raises — is what matters).

- [ ] **Step 5: Lint**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check src/needledrop/mcp_server.py tests/test_mcp_server.py`
Expected: no errors (resolve any F401 for unused imports).

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add read-only FastMCP server"
```

---

## Task 3: `needledrop serve` CLI command

**Files:**
- Modify: `src/needledrop/cli.py`
- Test: `tests/test_cli_serve.py` (create)

Wire the server into the CLI. `serve` opens the canonical DB, builds a keystore-backed `sync_runner` closing over that connection, and runs the server over stdio.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_serve.py` (mirrors the mocking style of `tests/test_cli_sync.py`):

```python
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from needledrop.cli import app

runner = CliRunner()


def test_serve_builds_and_runs_server():
    with patch("needledrop.cli.load_settings") as load_settings_mock, \
         patch("needledrop.cli.open_db") as open_db_mock, \
         patch("needledrop.cli.create_server") as create_server_mock:
        load_settings_mock.return_value = MagicMock(db_path=":memory:")
        server = MagicMock()
        create_server_mock.return_value = server
        result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert open_db_mock.called
    assert create_server_mock.called
    # The server must be run over stdio with the banner suppressed.
    server.run.assert_called_once_with(show_banner=False)
    # A sync_runner must be wired in so trigger_sync works at runtime.
    assert "sync_runner" in create_server_mock.call_args.kwargs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_cli_serve.py -q`
Expected: FAIL — `serve` command does not exist (typer exits non-zero / `create_server` not importable in `needledrop.cli`).

- [ ] **Step 3: Implement the command**

In `src/needledrop/cli.py`, add the import near the other `needledrop` imports:

```python
from needledrop.mcp_server import create_server
```

Then add the command (place it after the `sync` command, before `def main`):

```python
@app.command("serve")
def serve() -> None:
    """Run the read-only MCP server over stdio."""
    settings = load_settings()
    con = open_db(settings.db_path)

    def sync_runner() -> dict:
        connector = AppleMusicConnector.from_keystore()
        return sync_library(connector, con, now=datetime.now())

    server = create_server(con, sync_runner=sync_runner)
    server.run(show_banner=False)
```

`AppleMusicConnector`, `sync_library`, `open_db`, `load_settings`, and `datetime` are already imported in `cli.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_cli_serve.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + lint (CI-parity gate)**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest -q`
Expected: PASS (all prior tests + the new ones).

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/cli.py tests/test_cli_serve.py
git commit -m "feat: add needledrop serve MCP command"
```

---

## Self-Review (completed)

**Spec coverage:** All in-scope tools from the design map to a task — `get_library_summary`, `list_albums`, the three `find_*` analyses, `generate_cleanup_report`, `list_unmatched`, `search_library`, `trigger_sync` (Task 2), backed by the two new repository helpers (Task 1), exposed via `needledrop serve` (Task 3). Deferred tools (`search_catalog`, `get_artist_collection`, `get_album_versions`, Apple-mutating tools, `resolve_match`) are explicitly out of scope for Plan 9.

**Placeholder scan:** No TBD/TODO; every code step shows complete code.

**Type consistency:** Tool returns are JSON-safe (`dict`, `list[dict]`); CleanupFinding serialized via `model_dump(mode="json")`. Repository helpers return `list[dict]` matching the tool signatures. The shadowing pitfall (inner tool names vs imported names) is called out explicitly with the underscore-alias fix. `sync_runner` signature `Callable[[], dict]` matches the CLI's wired closure and the test stub.

**Known verification point:** the exact exception type FastMCP surfaces for an in-tool error (`ToolError`) must be confirmed against 3.4.2 at implementation time; the test asserts behavior (raises), with a noted fallback.

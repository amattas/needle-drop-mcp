# NeedleDrop Catalog & Discography Browse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add browse tools — `get_artist_collection` and `get_album_versions` (an artist's discography and an album's editions, from the local MusicBrainz authority) and `search_catalog` (Apple Music catalog text search) — exposed as read-only MCP tools.

**Architecture:** The two discography browses are MusicBrainz-backed DB queries over the materialized `mb_*` tables joined to the local library (so they degrade to `[]` when MB isn't imported and flag which items you already own) — this matches the design's "MusicBrainz is the discography authority" decision and keeps them fully testable with seeded tables. `search_catalog` reaches the Apple catalog through the connector, injected into the MCP server as a `catalog_search` callable (mirroring the existing `sync_runner` injection) so the server stays decoupled from credentials/network and tests can stub it.

**Tech Stack:** DuckDB, FastMCP 3.4.2, Pydantic v2, httpx (existing connector), pytest.

---

## Background & Key Facts (read before starting)

**Environment:** Python interpreter `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python` (use directly; NOT `mamba run`). No Docker/network needed (catalog search is stubbed in tests).

**Gates:** `... -m pytest -q` and `... -m ruff check .`. Ruff: line-length 100 (≤100 chars/line), select E/F/I/UP/B, B008 ignored.

**MusicBrainz schema (verified column/join names from `src/needledrop/analysis/missing_albums.py`):**
- `mb_release_group(id, gid, name, artist_credit, type)` — `type` → `mb_release_group_primary_type.id`.
- `mb_artist(id, gid, name)`; `mb_artist_credit_name(artist, artist_credit)`.
- `mb_release_group_primary_type(id, name)` (e.g. 'Album', 'Single', 'EP').
- `mb_release(id, gid, name, barcode, release_group)` — the editions of a release-group.
- `mb_medium(id, release, track_count)` — discs of a release; sum `track_count` for an edition's total.
- mb_* tables exist only after `needledrop mb import`. Guard access with `table_exists(con, "mb_release_group")` (in `src/needledrop/db/duckdb_store.py`) and return `[]` when absent — same pattern as `find_missing_core_albums`.

**Local library facts:** owned albums are `library_items` rows with `item_type='album'`, `status='present'`, joined to `albums` via `canonical_id`. `albums.release_group_mbid` is the version-cluster id; `albums.release_mbid` is the specific edition id (often NULL unless matched by UPC). Ownership flags below use these.

**MCP server (`src/needledrop/mcp_server.py`):** `create_server(con, *, sync_runner=None) -> FastMCP`. Tools are inner `@mcp.tool` functions over `con`; imports are aliased with a leading `_` (one `from ... import (X as _X)` per symbol). `sync_runner` is an injected callable so the server needs no credentials to start; if `None` and the tool is called, it raises (surfaces as `fastmcp.exceptions.ToolError`). The `serve` CLI (`src/needledrop/cli.py`) builds the connector lazily and wires `sync_runner`. Finding/dict lists are returned directly (JSON-safe) or via `model_dump(mode="json")`.

**Connector (`src/needledrop/connectors/apple_music.py`) — already implemented:**
- `AppleMusicConnector.from_keystore()`, `.get_storefront() -> str`, and
- `.search_catalog(storefront, term, types=("albums","songs"), limit=25) -> CatalogSearchResult`.
- `CatalogSearchResult` / `CatalogAlbum` / `CatalogSong` are Pydantic models (see `apple_models.py`) — `model_dump(mode="json")` yields JSON-safe dicts. (Confirm they are Pydantic `BaseModel` when wiring; they are constructed via `.from_api` like `LibraryAlbum`.)

**Scope:** the two MB discography browses + the catalog-search plumbing/tool. **Out of scope:** Apple-library mutations (next/final plan) — `search_catalog` here only reads, but it's what a later "add album" flow will use to resolve an Apple catalog id.

---

## File Structure

- **Create** `src/needledrop/discography.py` — `get_artist_collection(con, artist_mbid)`, `get_album_versions(con, release_group_mbid)`.
- **Create** `tests/test_discography.py` — seeded-mb_* tests for both.
- **Modify** `src/needledrop/mcp_server.py` — add `catalog_search` injection + three tools.
- **Modify** `src/needledrop/cli.py` — wire a `catalog_search` closure in `serve`.
- **Modify** `tests/test_mcp_server.py`, `tests/test_cli_serve.py` — tool + wiring tests.

---

## Task 1: MusicBrainz discography browse functions

**Files:**
- Create: `src/needledrop/discography.py`
- Test: `tests/test_discography.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_discography.py`:

```python
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.discography import get_album_versions, get_artist_collection


def _seed_artist_discography(con):
    """Artist 'Radiohead' with two release-groups; the library owns one of them."""
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit_name (artist INTEGER, artist_credit INTEGER)")
    con.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, type INTEGER)"
    )
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("INSERT INTO mb_artist VALUES (1, 'artist-radiohead', 'Radiohead')")
    con.execute("INSERT INTO mb_artist_credit_name VALUES (1, 10)")
    con.execute("INSERT INTO mb_release_group_primary_type VALUES (1, 'Album')")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'rg-okc', 'OK Computer', 10, 1)")
    con.execute("INSERT INTO mb_release_group VALUES (101, 'rg-kida', 'Kid A', 10, 1)")
    # The library owns OK Computer (by release_group_mbid).
    con.execute(
        "INSERT INTO albums (title, release_group_mbid) VALUES ('OK Computer', 'rg-okc')"
    )
    album_id = con.execute("SELECT id FROM albums WHERE title = 'OK Computer'").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', 'l.okc', 'album', ?, 'present')",
        [album_id],
    )


def test_get_artist_collection_lists_release_groups_with_ownership(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_artist_discography(con)
    collection = get_artist_collection(con, "artist-radiohead")
    by_title = {c["title"]: c for c in collection}
    assert set(by_title) == {"OK Computer", "Kid A"}
    assert by_title["OK Computer"]["owned"] is True
    assert by_title["OK Computer"]["primary_type"] == "Album"
    assert by_title["Kid A"]["owned"] is False
    assert by_title["OK Computer"]["release_group_mbid"] == "rg-okc"


def test_get_artist_collection_empty_without_mb(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    assert get_artist_collection(con, "artist-radiohead") == []


def _seed_release_versions(con):
    """Release-group 'rg-okc' with two editions; the library owns one (by release_mbid)."""
    con.execute(
        "CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR, "
        "artist_credit INTEGER, type INTEGER)"
    )
    con.execute(
        "CREATE TABLE mb_release "
        "(id INTEGER, gid VARCHAR, name VARCHAR, barcode VARCHAR, release_group INTEGER)"
    )
    con.execute("CREATE TABLE mb_medium (id INTEGER, release INTEGER, track_count INTEGER)")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'rg-okc', 'OK Computer', 10, 1)")
    con.execute("INSERT INTO mb_release VALUES (200, 'rel-std', 'OK Computer', '111', 100)")
    con.execute(
        "INSERT INTO mb_release VALUES (201, 'rel-oknotok', 'OKNOTOK 1997 2017', '222', 100)"
    )
    con.execute("INSERT INTO mb_medium VALUES (300, 200, 12)")
    con.execute("INSERT INTO mb_medium VALUES (301, 201, 23)")
    # The library owns the standard edition (by release_mbid).
    con.execute(
        "INSERT INTO albums (title, release_group_mbid, release_mbid) "
        "VALUES ('OK Computer', 'rg-okc', 'rel-std')"
    )
    album_id = con.execute("SELECT id FROM albums WHERE title = 'OK Computer'").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', 'l.okc', 'album', ?, 'present')",
        [album_id],
    )


def test_get_album_versions_lists_editions_with_ownership_and_counts(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_release_versions(con)
    versions = get_album_versions(con, "rg-okc")
    by_title = {v["title"]: v for v in versions}
    assert set(by_title) == {"OK Computer", "OKNOTOK 1997 2017"}
    assert by_title["OK Computer"]["owned"] is True
    assert by_title["OK Computer"]["track_count"] == 12
    assert by_title["OKNOTOK 1997 2017"]["owned"] is False
    assert by_title["OKNOTOK 1997 2017"]["track_count"] == 23


def test_get_album_versions_empty_without_mb(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    assert get_album_versions(con, "rg-okc") == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_discography.py -q`
Expected: FAIL (`ModuleNotFoundError: needledrop.discography`).

- [ ] **Step 3: Implement**

Create `src/needledrop/discography.py`:

```python
"""MusicBrainz-backed discography browse: an artist's release-groups and an album's editions.

Read-only over the materialized mb_* authority tables joined to the local library, so
each result is flagged with whether you already own it. Returns [] when mb_* is absent.
"""

from __future__ import annotations

import duckdb

from needledrop.db.duckdb_store import table_exists


def _owned_release_group_mbids(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0]
        for row in con.execute(
            "SELECT DISTINCT a.release_group_mbid "
            "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
            "WHERE li.status = 'present' AND li.item_type = 'album' "
            "AND a.release_group_mbid IS NOT NULL"
        ).fetchall()
    }


def _owned_release_mbids(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0]
        for row in con.execute(
            "SELECT DISTINCT a.release_mbid "
            "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
            "WHERE li.status = 'present' AND li.item_type = 'album' "
            "AND a.release_mbid IS NOT NULL"
        ).fetchall()
    }


def get_artist_collection(con: duckdb.DuckDBPyConnection, artist_mbid: str) -> list[dict]:
    """An artist's release-groups (full discography), flagged by ownership.

    Each entry: release_group_mbid, title, primary_type, owned. [] if mb_* is absent.
    """
    if not table_exists(con, "mb_release_group"):
        return []
    rows = con.execute(
        "SELECT DISTINCT rg.gid, rg.name, COALESCE(pt.name, 'Unknown') AS primary_type "
        "FROM mb_artist ar "
        "JOIN mb_artist_credit_name acn ON acn.artist = ar.id "
        "JOIN mb_release_group rg ON rg.artist_credit = acn.artist_credit "
        "LEFT JOIN mb_release_group_primary_type pt ON rg.type = pt.id "
        "WHERE ar.gid = ? "
        "ORDER BY rg.name",
        [artist_mbid],
    ).fetchall()
    owned = _owned_release_group_mbids(con)
    return [
        {
            "release_group_mbid": gid,
            "title": name,
            "primary_type": primary_type,
            "owned": gid in owned,
        }
        for gid, name, primary_type in rows
    ]


def get_album_versions(
    con: duckdb.DuckDBPyConnection, release_group_mbid: str
) -> list[dict]:
    """All release editions of a release-group, with track counts, flagged by ownership.

    Each entry: release_mbid, title, barcode, track_count, owned. [] if mb_* is absent.
    """
    if not table_exists(con, "mb_release_group"):
        return []
    rows = con.execute(
        "SELECT r.gid, r.name, r.barcode, "
        "  (SELECT sum(m.track_count) FROM mb_medium m WHERE m.release = r.id) AS track_count "
        "FROM mb_release_group rg JOIN mb_release r ON r.release_group = rg.id "
        "WHERE rg.gid = ? "
        "ORDER BY r.name",
        [release_group_mbid],
    ).fetchall()
    owned = _owned_release_mbids(con)
    return [
        {
            "release_mbid": gid,
            "title": name,
            "barcode": barcode,
            "track_count": int(track_count) if track_count is not None else None,
            "owned": gid in owned,
        }
        for gid, name, barcode, track_count in rows
    ]
```

- [ ] **Step 4: Run to verify pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_discography.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check src/needledrop/discography.py tests/test_discography.py`
Expected: clean.

```bash
git add src/needledrop/discography.py tests/test_discography.py
git commit -m "feat: add MusicBrainz discography browse (artist collection, album versions)"
```

---

## Task 2: MCP tools + catalog-search plumbing

**Files:**
- Modify: `src/needledrop/mcp_server.py`
- Modify: `src/needledrop/cli.py`
- Test: `tests/test_mcp_server.py`, `tests/test_cli_serve.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mcp_server.py`:

```python
def test_get_artist_collection_tool_returns_discography():
    con = _fresh_con()
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit_name (artist INTEGER, artist_credit INTEGER)")
    con.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, type INTEGER)"
    )
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("INSERT INTO mb_artist VALUES (1, 'artist-rh', 'Radiohead')")
    con.execute("INSERT INTO mb_artist_credit_name VALUES (1, 10)")
    con.execute("INSERT INTO mb_release_group_primary_type VALUES (1, 'Album')")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'rg-kida', 'Kid A', 10, 1)")
    result = _call(create_server(con), "get_artist_collection", {"artist_mbid": "artist-rh"})
    assert [r["title"] for r in result] == ["Kid A"]
    assert result[0]["owned"] is False


def test_search_catalog_tool_uses_injected_callable():
    con = _fresh_con()
    calls = []

    def catalog_search(term, types, limit):
        calls.append((term, types, limit))
        return {"albums": [{"id": "a.1", "name": "Dookie"}], "songs": []}

    server = create_server(con, catalog_search=catalog_search)
    result = _call(server, "search_catalog", {"term": "dookie"})
    assert calls == [("dookie", ("albums", "songs"), 25)]
    assert result["albums"][0]["name"] == "Dookie"


def test_search_catalog_tool_without_callable_errors():
    con = _fresh_con()
    from fastmcp.exceptions import ToolError

    async def go():
        async with Client(create_server(con)) as client:
            await client.call_tool("search_catalog", {"term": "x"})

    with pytest.raises(ToolError):
        asyncio.run(go())
```

Also extend `test_server_exposes_expected_tools` to add `"get_artist_collection"`, `"get_album_versions"`, `"search_catalog"`.

Add to `tests/test_cli_serve.py` (the existing `test_serve_builds_and_runs_server` asserts `sync_runner` in kwargs; add an assertion that `catalog_search` is also wired):

```python
def test_serve_wires_catalog_search():
    with patch("needledrop.cli.load_settings") as load_settings_mock, \
         patch("needledrop.cli.open_db"), \
         patch("needledrop.cli.create_server") as create_server_mock:
        load_settings_mock.return_value = MagicMock(db_path=":memory:")
        create_server_mock.return_value = MagicMock()
        result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert "catalog_search" in create_server_mock.call_args.kwargs
```

- [ ] **Step 2: Run to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_mcp_server.py tests/test_cli_serve.py -q -k "artist_collection or search_catalog or catalog_search or expected_tools"`
Expected: FAIL.

- [ ] **Step 3: Implement the MCP tools**

In `src/needledrop/mcp_server.py`:

Add aliased imports (one statement per symbol, matching the file's style):

```python
from needledrop.discography import get_album_versions as _get_album_versions
from needledrop.discography import get_artist_collection as _get_artist_collection
```

Add a `catalog_search` parameter to `create_server` (alongside `sync_runner`):

```python
def create_server(
    con: duckdb.DuckDBPyConnection,
    *,
    sync_runner: Callable[[], dict] | None = None,
    catalog_search: Callable[[str, tuple[str, ...], int], dict] | None = None,
) -> FastMCP:
```

Update the docstring to note `catalog_search` is an injected callable (Apple catalog search) so the server stays decoupled from credentials; if `None`, the `search_catalog` tool raises.

Register three tools inside `create_server` (place after `list_review_queue`/`resolve_match`/`reject_match`, before `trigger_sync`):

```python
    @mcp.tool
    def get_artist_collection(artist_mbid: str) -> list[dict]:
        """An artist's full release-group discography (MusicBrainz), flagged by ownership."""
        return _get_artist_collection(con, artist_mbid)

    @mcp.tool
    def get_album_versions(release_group_mbid: str) -> list[dict]:
        """All release editions of a release-group (MusicBrainz), flagged by ownership."""
        return _get_album_versions(con, release_group_mbid)

    @mcp.tool
    def search_catalog(term: str, types: list[str] | None = None, limit: int = 25) -> dict:
        """Search the Apple Music catalog (albums/songs) by text."""
        if catalog_search is None:
            raise RuntimeError(
                "Catalog search is not available: no catalog_search configured for this server."
            )
        return catalog_search(term, tuple(types) if types else ("albums", "songs"), limit)
```

- [ ] **Step 4: Wire `catalog_search` in the CLI**

In `src/needledrop/cli.py`, update `serve` to build a lazy connector shared by both `sync_runner` and `catalog_search`, resolving the storefront once:

```python
@app.command("serve")
def serve() -> None:
    """Run the read-only MCP server over stdio."""
    settings = load_settings()
    con = open_db(settings.db_path)
    state: dict = {}

    def _connector() -> AppleMusicConnector:
        if "connector" not in state:
            state["connector"] = AppleMusicConnector.from_keystore()
        return state["connector"]

    def sync_runner() -> dict:
        return sync_library(_connector(), con, now=datetime.now())

    def catalog_search(term: str, types: tuple[str, ...], limit: int) -> dict:
        connector = _connector()
        if "storefront" not in state:
            state["storefront"] = connector.get_storefront()
        result = connector.search_catalog(state["storefront"], term, types, limit)
        return result.model_dump(mode="json")

    server = create_server(con, sync_runner=sync_runner, catalog_search=catalog_search)
    server.run(show_banner=False)
```

(`AppleMusicConnector`, `sync_library`, `open_db`, `load_settings`, `datetime`, `create_server` are already imported in cli.py.)

- [ ] **Step 5: Run tests + full suite + lint (CI-parity gate)**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest -q`
Expected: PASS (all tests).

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/mcp_server.py src/needledrop/cli.py tests/test_mcp_server.py tests/test_cli_serve.py
git commit -m "feat: add discography + catalog-search MCP tools and serve wiring"
```

---

## Self-Review (completed)

**Spec coverage:** All three browse tools delivered — `get_artist_collection`, `get_album_versions` (MB-backed, ownership-flagged, Task 1) and `search_catalog` (Apple connector via injected callable, Task 2) — exposed as MCP tools, with the `serve` CLI wiring the catalog search lazily.

**Architecture fit:** discography comes from MusicBrainz (the design's discography authority), reusing the verified `mb_*` join/column names from `find_missing_core_albums`, guarded by `table_exists` so it degrades to `[]` pre-import. `search_catalog` reuses the already-implemented connector method; the server is decoupled via a `catalog_search` callable mirroring `sync_runner` (no credentials needed to start; tests stub it; missing → `ToolError`).

**Placeholder scan:** No TBD/TODO; complete code in every step.

**Type consistency:** `get_artist_collection(con, artist_mbid) -> list[dict]` ({release_group_mbid, title, primary_type, owned}); `get_album_versions(con, release_group_mbid) -> list[dict]` ({release_mbid, title, barcode, track_count, owned}); the MCP tools delegate with matching signatures. `catalog_search: Callable[[str, tuple[str,...], int], dict]` matches both the tool's call (`catalog_search(term, types, limit)`) and the CLI closure. The `search_catalog` tool maps `types: list[str] | None` → tuple default `("albums","songs")`, matching the connector signature.

**Edge cases:** both discography functions return `[]` without mb_* (tested); ownership flags use release_group_mbid (collection) vs release_mbid (versions) correctly; `track_count` coalesces NULL; `search_catalog` without an injected callable raises (→ ToolError, tested). One note: `get_album_versions` uses `mb_release.barcode`, `mb_release.release_group`, and `mb_medium.{release,track_count}` — standard MusicBrainz columns; tests seed them explicitly, and they should be validated against a real dump during integration.

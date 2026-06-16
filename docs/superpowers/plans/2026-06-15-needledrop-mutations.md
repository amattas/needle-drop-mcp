# NeedleDrop Apple Music Mutations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **SAFETY GATE:** This plan adds the only operations in NeedleDrop that **write to the user's real Apple Music library** (add album, remove album, create playlist). Every mutating MCP tool defaults to a **dry-run preview** and only performs a real change when called explicitly with `dry_run=false`. Do not weaken that default.

**Goal:** Add Apple Music library mutations — add a catalog album, remove a library album, create a playlist — as connector methods and dry-run-by-default MCP tools, with the mutating connector injected into the server the same way `sync_runner`/`catalog_search` are; plus two read-only consolidation lookups (`get_song_detail`, `get_album_detail`) that give the LLM the context to decide what to keep, remove, or add.

**Architecture:** Write methods live on `AppleMusicConnector` (the read-only `MusicConnector` base stays read-only so existing fake connectors keep working). The MCP server gains an injected `mutator` (duck-typed object exposing the three write methods); the `serve` CLI wires a lazy proxy over the real connector. Each mutating tool takes `dry_run: bool = True`: with `dry_run` it returns a preview and never touches Apple or the mutator; with `dry_run=false` it calls the mutator. No `mutator` injected → a real (non-dry-run) call raises. Mutations act on Apple only; the local DuckDB reconciles on the next `sync`.

**Tech Stack:** httpx (existing connector + `httpx.MockTransport` in tests), FastMCP 3.4.2, Pydantic v2, pytest.

---

## Background & Key Facts (read before starting)

**Environment:** Python interpreter `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python` (use directly; NOT `mamba run`). No live network/credentials — Apple calls are mocked with `httpx.MockTransport`.

**Gates:** `... -m pytest -q` and `... -m ruff check .`. Ruff: line-length 100 (≤100 chars/line), select E/F/I/UP/B, B008 ignored.

**Connector (`src/needledrop/connectors/apple_music.py`):**
- Constructor: `AppleMusicConnector(credentials, *, client: httpx.Client | None = None, developer_token: str | None = None)`. Tests pass a `client=httpx.Client(transport=httpx.MockTransport(handler), base_url=AppleMusicConnector.BASE_URL)` and `developer_token="t"` so no real token is minted.
- `_headers(*, user: bool)` returns `Authorization: Bearer <dev token>` and, when `user=True`, `Music-User-Token` (raises if the user token is missing). All mutations are `user=True`.
- Existing methods use `self._client.get(...)`. You will add `post`/`delete` write methods.

**Apple Music write API (endpoints to implement):**
- **Add to library:** `POST /v1/me/library?ids[albums]=<catalogId>[,<catalogId>...]` (catalog ids — i.e. `CatalogAlbum.id`). Returns `202 Accepted`, empty body.
- **Remove from library:** `DELETE /v1/me/library/albums/{libraryAlbumId}` (library id — i.e. `LibraryAlbum.id`). Returns `204 No Content`.
- **Create playlist:** `POST /v1/me/library/playlists` with JSON body `{"attributes": {"name": ..., "description": ...?}, "relationships"?: {"tracks": {"data": [{"id": <songId>, "type": "songs"}, ...]}}}`. Returns `201` with `{"data": [ <library playlist resource> ]}` — parse via `LibraryPlaylist.from_api`.

**Models (`src/needledrop/connectors/apple_models.py`):** `CatalogAlbum.id` is the catalog id (used to ADD). `LibraryAlbum.id` is the library id (used to REMOVE). `LibraryPlaylist.from_api(resource)` parses a returned playlist (`{id, name, description}`).

**Base connector (`src/needledrop/connectors/base.py`):** read-only abstract interface. **Do NOT add abstract mutating methods here** — existing fake connectors in `tests/services/test_sync.py` implement only the read interface and would break. Mutations are concrete on `AppleMusicConnector`; the server's `mutator` is duck-typed.

**MCP server (`src/needledrop/mcp_server.py`):** `create_server(con, *, sync_runner=None, catalog_search=None) -> FastMCP`. You add `mutator=None`. Tools are inner `@mcp.tool` functions; injected dependencies are closed over; a missing dependency raises (→ `fastmcp.exceptions.ToolError`). Tool-surface test is `test_server_exposes_expected_tools` in `tests/test_mcp_server.py`.

**Serve CLI (`src/needledrop/cli.py`):** `serve` builds a lazy connector in a `state` dict shared by `sync_runner` and `catalog_search`. You add a lazy `mutator` proxy over the same `_connector()`.

**Scope:** add-album, remove-album, create-playlist — connector methods + dry-run MCP tools. **Out of scope:** editing/reordering playlists, batch remove, undo. The local DB is reconciled by the next `sync`, not by these tools.

---

## File Structure

- **Modify** `src/needledrop/connectors/apple_music.py` — `add_albums_to_library`, `remove_album_from_library`, `create_playlist`.
- **Modify** `tests/connectors/test_apple_music.py` (or the existing connector test file) — `MockTransport` request-assertion tests.
- **Modify** `src/needledrop/mcp_server.py` — `mutator` param + three dry-run tools.
- **Modify** `src/needledrop/cli.py` — wire a lazy `mutator` proxy in `serve`.
- **Modify** `tests/test_mcp_server.py`, `tests/test_cli_serve.py` — tool dry-run/apply tests + wiring.

---

## Task 1: Connector write methods

**Files:**
- Modify: `src/needledrop/connectors/apple_music.py`
- Test: the existing Apple connector test module (find it under `tests/connectors/`; READ it first to match the `httpx.MockTransport` setup style)

- [ ] **Step 1: Write the failing tests**

First READ the existing connector test file to copy its exact `MockTransport`/client construction and credentials fixture. Then add tests asserting the three write requests. Pattern (adapt to the file's existing fixtures/helpers):

```python
import httpx

from needledrop.connectors.apple_models import AppleCredentials  # or the file's existing fixture
from needledrop.connectors.apple_music import AppleMusicConnector


def _connector(handler):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=AppleMusicConnector.BASE_URL
    )
    creds = AppleCredentials(team_id="T", key_id="K", p8_pem="pem", user_token="u")
    return AppleMusicConnector(creds, client=client, developer_token="devtok")


def test_add_albums_to_library_posts_catalog_ids():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["muth"] = request.headers.get("Music-User-Token")
        return httpx.Response(202)

    _connector(handler).add_albums_to_library(["1440857781", "1440857782"])
    assert seen["method"] == "POST"
    assert "/v1/me/library" in seen["url"]
    assert "ids%5Balbums%5D=1440857781,1440857782" in seen["url"] or \
           "ids[albums]=1440857781,1440857782" in seen["url"]
    assert seen["muth"] == "u"


def test_remove_album_from_library_deletes_by_library_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(204)

    _connector(handler).remove_album_from_library("l.123")
    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/v1/me/library/albums/l.123")


def test_create_playlist_posts_attributes_and_tracks():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={"data": [{"id": "p.1", "attributes": {"name": "Cleanup",
                  "description": {"standard": "auto"}}}]},
        )

    playlist = _connector(handler).create_playlist(
        "Cleanup", description="auto", track_ids=["s.1", "s.2"]
    )
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/v1/me/library/playlists")
    assert seen["body"]["attributes"]["name"] == "Cleanup"
    assert seen["body"]["attributes"]["description"] == "auto"
    assert [t["id"] for t in seen["body"]["relationships"]["tracks"]["data"]] == ["s.1", "s.2"]
    assert playlist.id == "p.1"
    assert playlist.name == "Cleanup"
```

Note: `AppleCredentials` import path / fields — use whatever the existing connector tests use (it may live in `apple_token`). Match the existing fixture exactly.

- [ ] **Step 2: Run to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/ -q -k "add_albums or remove_album or create_playlist"`
Expected: FAIL (`AttributeError` — methods not defined).

- [ ] **Step 3: Implement**

Add to `AppleMusicConnector` (after `search_catalog`). Add `LibraryPlaylist` to the existing `from needledrop.connectors.apple_models import (...)` block if not already imported.

```python
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
        """Create a library playlist, optionally seeded with catalog/library song ids."""
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
```

- [ ] **Step 4: Run to verify pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/ -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check src/needledrop/connectors/apple_music.py tests/connectors/`
Expected: clean.

```bash
git add src/needledrop/connectors/apple_music.py tests/connectors/
git commit -m "feat: add Apple Music library write methods (add/remove album, create playlist)"
```

---

## Task 2: Dry-run mutating MCP tools + serve wiring

**Files:**
- Modify: `src/needledrop/mcp_server.py`
- Modify: `src/needledrop/cli.py`
- Test: `tests/test_mcp_server.py`, `tests/test_cli_serve.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mcp_server.py`:

```python
class _FakeMutator:
    def __init__(self):
        self.added = []
        self.removed = []
        self.created = []

    def add_albums_to_library(self, ids):
        self.added.append(ids)

    def remove_album_from_library(self, library_album_id):
        self.removed.append(library_album_id)

    def create_playlist(self, name, *, description=None, track_ids=None):
        self.created.append((name, description, tuple(track_ids or ())))
        from needledrop.connectors.apple_models import LibraryPlaylist
        return LibraryPlaylist(id="p.1", name=name, description=description)


def test_add_album_tool_dry_run_does_not_mutate():
    con = _fresh_con()
    mut = _FakeMutator()
    result = _call(create_server(con, mutator=mut), "add_album", {"catalog_album_id": "c.1"})
    assert result["dry_run"] is True
    assert mut.added == []  # nothing applied


def test_add_album_tool_applies_when_not_dry_run():
    con = _fresh_con()
    mut = _FakeMutator()
    result = _call(
        create_server(con, mutator=mut),
        "add_album",
        {"catalog_album_id": "c.1", "dry_run": False},
    )
    assert result["dry_run"] is False
    assert mut.added == [["c.1"]]


def test_remove_album_tool_applies_when_not_dry_run():
    con = _fresh_con()
    mut = _FakeMutator()
    _call(create_server(con, mutator=mut), "remove_album",
          {"library_album_id": "l.9", "dry_run": False})
    assert mut.removed == ["l.9"]


def test_create_playlist_tool_applies_when_not_dry_run():
    con = _fresh_con()
    mut = _FakeMutator()
    result = _call(
        create_server(con, mutator=mut),
        "create_playlist",
        {"name": "Cleanup", "track_ids": ["s.1"], "dry_run": False},
    )
    assert mut.created == [("Cleanup", None, ("s.1",))]
    assert result["created_playlist"]["id"] == "p.1"


def test_mutating_tool_without_mutator_errors_when_applying():
    con = _fresh_con()
    from fastmcp.exceptions import ToolError

    async def go():
        async with Client(create_server(con)) as client:
            await client.call_tool("add_album", {"catalog_album_id": "c.1", "dry_run": False})

    with pytest.raises(ToolError):
        asyncio.run(go())


def test_mutating_tool_dry_run_works_without_mutator():
    con = _fresh_con()
    # Dry-run must preview even with no mutator configured (it never touches Apple).
    result = _call(create_server(con), "remove_album", {"library_album_id": "l.9"})
    assert result["dry_run"] is True
```

Also extend `test_server_exposes_expected_tools` to add `"add_album"`, `"remove_album"`, `"create_playlist"`.

Add to `tests/test_cli_serve.py`:

```python
def test_serve_wires_mutator():
    with patch("needledrop.cli.load_settings") as load_settings_mock, \
         patch("needledrop.cli.open_db"), \
         patch("needledrop.cli.create_server") as create_server_mock:
        load_settings_mock.return_value = MagicMock(db_path=":memory:")
        create_server_mock.return_value = MagicMock()
        result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert "mutator" in create_server_mock.call_args.kwargs
```

- [ ] **Step 2: Run to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_mcp_server.py tests/test_cli_serve.py -q -k "album or playlist or mutator or expected_tools"`
Expected: FAIL.

- [ ] **Step 3: Add the `mutator` param + tools to `mcp_server.py`**

Add `mutator` to `create_server` (alongside the other injected deps):

```python
def create_server(
    con: duckdb.DuckDBPyConnection,
    *,
    sync_runner: Callable[[], dict] | None = None,
    catalog_search: Callable[[str, tuple[str, ...], int], dict] | None = None,
    mutator: object | None = None,
) -> FastMCP:
```

Update the docstring: `mutator` is an injected object exposing `add_albums_to_library(ids)`, `remove_album_from_library(library_album_id)`, and `create_playlist(name, *, description, track_ids)`; mutating tools default to a dry-run preview and only call it when `dry_run=false`; with no mutator a real (non-dry-run) call raises.

Register three tools inside `create_server` (place after `trigger_sync`):

```python
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
```

- [ ] **Step 4: Wire a lazy `mutator` in `serve` (`cli.py`)**

In `serve`, after `catalog_search` is defined, add a lazy proxy that forwards to `_connector()` and pass it to `create_server`:

```python
    class _LazyMutator:
        def add_albums_to_library(self, ids: list[str]) -> None:
            _connector().add_albums_to_library(ids)

        def remove_album_from_library(self, library_album_id: str) -> None:
            _connector().remove_album_from_library(library_album_id)

        def create_playlist(self, name, *, description=None, track_ids=None):
            return _connector().create_playlist(
                name, description=description, track_ids=track_ids
            )

    server = create_server(
        con, sync_runner=sync_runner, catalog_search=catalog_search, mutator=_LazyMutator()
    )
    server.run(show_banner=False)
```

(Replace the existing `create_server(...)`/`server.run(...)` lines with the above. `_LazyMutator` keeps the connector lazy so `serve` still starts without credentials.)

- [ ] **Step 5: Run tests + full suite + lint (CI-parity gate)**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest -q`
Expected: PASS (all tests).

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/mcp_server.py src/needledrop/cli.py tests/test_mcp_server.py tests/test_cli_serve.py
git commit -m "feat: add dry-run-by-default mutating MCP tools (add/remove album, create playlist)"
```

---

## Task 3: Consolidation lookups — `get_song_detail`, `get_album_detail`

**Files:**
- Modify: `src/needledrop/discography.py` (append; reuses `get_album_versions`, `_owned_release_group_mbids`, `table_exists` already in the module)
- Test: `tests/test_discography.py`

These read-only lookups give the LLM the context to make consolidation decisions. `get_song_detail` answers "where is this recording" (owned albums containing it + the release-groups it appears on per MusicBrainz). `get_album_detail` answers "show me this album's duplicate set" (every owned edition of the release-group, with the Apple library id needed to remove it + completeness, plus all available editions).

Verified mb_* columns (reuse from `missing_albums.py`/`discography.py`, plus): `mb_recording(id, gid)`, `mb_track(id, recording, medium)`, `mb_medium(id, release, track_count)`, `mb_release(id, gid, release_group)`, `mb_release_group(id, gid, name, type)`, `mb_release_group_primary_type(id, name)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_discography.py`:

```python
from needledrop.discography import get_album_detail, get_song_detail  # extend existing import


def _seed_song_detail(con):
    """A recording owned on an owned album, plus MB placements on two release-groups."""
    con.execute(
        "INSERT INTO albums (title, release_group_mbid, external_ids_json) "
        "VALUES ('OK Computer', 'rg-okc', json_object('apple', 'la.okc'))"
    )
    album_id = con.execute("SELECT id FROM albums WHERE title = 'OK Computer'").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', 'la.okc', 'album', ?, 'present')",
        [album_id],
    )
    con.execute(
        "INSERT INTO tracks (title, recording_mbid, album_id) VALUES ('Lucky', 'rec-lucky', ?)",
        [album_id],
    )
    # MB: the recording appears on two release-groups (the album + a compilation).
    con.execute("CREATE TABLE mb_recording (id INTEGER, gid VARCHAR)")
    con.execute("CREATE TABLE mb_track (id INTEGER, recording INTEGER, medium INTEGER)")
    con.execute("CREATE TABLE mb_medium (id INTEGER, release INTEGER, track_count INTEGER)")
    con.execute("CREATE TABLE mb_release (id INTEGER, gid VARCHAR, release_group INTEGER)")
    con.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid VARCHAR, name VARCHAR, type INTEGER)"
    )
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("INSERT INTO mb_recording VALUES (1, 'rec-lucky')")
    con.execute("INSERT INTO mb_release_group_primary_type VALUES (1, 'Album')")
    con.execute("INSERT INTO mb_release_group VALUES (10, 'rg-okc', 'OK Computer', 1)")
    con.execute("INSERT INTO mb_release_group VALUES (11, 'rg-comp', 'Best Of', 1)")
    con.execute("INSERT INTO mb_release VALUES (20, 'rel-okc', 10)")
    con.execute("INSERT INTO mb_release VALUES (21, 'rel-comp', 11)")
    con.execute("INSERT INTO mb_medium VALUES (30, 20, 12)")
    con.execute("INSERT INTO mb_medium VALUES (31, 21, 20)")
    con.execute("INSERT INTO mb_track VALUES (40, 1, 30)")
    con.execute("INSERT INTO mb_track VALUES (41, 1, 31)")


def test_get_song_detail_reports_library_albums_and_mb_placements(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_song_detail(con)
    detail = get_song_detail(con, "rec-lucky")
    assert [a["title"] for a in detail["library_albums"]] == ["OK Computer"]
    appears = {a["release_group_mbid"]: a for a in detail["appears_on"]}
    assert set(appears) == {"rg-okc", "rg-comp"}
    assert appears["rg-okc"]["owned"] is True   # owned via the library album
    assert appears["rg-comp"]["owned"] is False


def test_get_song_detail_without_mb_still_lists_library_albums(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    con.execute(
        "INSERT INTO albums (title, external_ids_json) "
        "VALUES ('OK Computer', json_object('apple', 'la.okc'))"
    )
    album_id = con.execute("SELECT id FROM albums").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', 'la.okc', 'album', ?, 'present')",
        [album_id],
    )
    con.execute(
        "INSERT INTO tracks (title, recording_mbid, album_id) VALUES ('Lucky', 'rec-lucky', ?)",
        [album_id],
    )
    detail = get_song_detail(con, "rec-lucky")
    assert [a["title"] for a in detail["library_albums"]] == ["OK Computer"]
    assert detail["appears_on"] == []  # no mb_* tables


def _seed_album_detail(con):
    """Two owned editions of one release-group (the duplicate set) + MB editions."""
    for title, version, total, apple, owned_tracks in [
        ("OK Computer", "standard", 12, "la.std", 2),
        ("OK Computer (Deluxe)", "deluxe", 23, "la.dlx", 23),
    ]:
        con.execute(
            "INSERT INTO albums (title, release_group_mbid, version_class, total_tracks, "
            "external_ids_json) VALUES (?, 'rg-okc', ?, ?, json_object('apple', ?))",
            [title, version, total, apple],
        )
        album_id = con.execute("SELECT id FROM albums WHERE title = ?", [title]).fetchone()[0]
        con.execute(
            "INSERT INTO library_items "
            "(service, service_item_id, item_type, canonical_id, status) "
            "VALUES ('apple_music', ?, 'album', ?, 'present')",
            [apple, album_id],
        )
        for i in range(owned_tracks):
            con.execute(
                "INSERT INTO tracks (title, album_id) VALUES (?, ?)", [f"{title}-{i}", album_id]
            )
            tid = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
            con.execute(
                "INSERT INTO library_items "
                "(service, service_item_id, item_type, canonical_id, status) "
                "VALUES ('apple_music', ?, 'track', ?, 'present')",
                [f"s.{apple}.{i}", tid],
            )


def test_get_album_detail_shows_owned_editions_for_consolidation(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_album_detail(con)
    detail = get_album_detail(con, "rg-okc")
    editions = {e["title"]: e for e in detail["owned_editions"]}
    assert set(editions) == {"OK Computer", "OK Computer (Deluxe)"}
    assert editions["OK Computer"]["apple_album_id"] == "la.std"
    assert editions["OK Computer"]["total_tracks"] == 12
    assert editions["OK Computer"]["owned_track_count"] == 2   # incomplete edition
    assert editions["OK Computer (Deluxe)"]["owned_track_count"] == 23  # complete
    # available_versions reuses get_album_versions (empty here without mb_release).
    assert detail["available_versions"] == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_discography.py -q -k "song_detail or album_detail"`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement (append to `src/needledrop/discography.py`)**

```python
def get_song_detail(con: duckdb.DuckDBPyConnection, recording_mbid: str) -> dict:
    """Where a recording lives: owned library albums containing it, plus the
    release-groups it appears on (MusicBrainz), each ownership-flagged.

    Returns {recording_mbid, library_albums, appears_on}. `appears_on` is [] without mb_*.
    """
    library_albums = [
        {
            "album_id": r[0],
            "title": r[1],
            "release_group_mbid": r[2],
            "version_class": r[3],
        }
        for r in con.execute(
            "SELECT DISTINCT al.id, al.title, al.release_group_mbid, al.version_class "
            "FROM tracks tr "
            "JOIN albums al ON tr.album_id = al.id "
            "JOIN library_items li ON li.canonical_id = al.id "
            "  AND li.item_type = 'album' AND li.status = 'present' "
            "WHERE tr.recording_mbid = ? "
            "ORDER BY al.title",
            [recording_mbid],
        ).fetchall()
    ]
    appears_on: list[dict] = []
    if table_exists(con, "mb_recording"):
        owned = _owned_release_group_mbids(con)
        appears_on = [
            {
                "release_group_mbid": gid,
                "title": name,
                "primary_type": primary_type,
                "owned": gid in owned,
            }
            for gid, name, primary_type in con.execute(
                "SELECT DISTINCT rg.gid, rg.name, COALESCE(pt.name, 'Unknown') "
                "FROM mb_recording rec "
                "JOIN mb_track t ON t.recording = rec.id "
                "JOIN mb_medium m ON t.medium = m.id "
                "JOIN mb_release r ON m.release = r.id "
                "JOIN mb_release_group rg ON r.release_group = rg.id "
                "LEFT JOIN mb_release_group_primary_type pt ON rg.type = pt.id "
                "WHERE rec.gid = ? "
                "ORDER BY rg.name",
                [recording_mbid],
            ).fetchall()
        ]
    return {
        "recording_mbid": recording_mbid,
        "library_albums": library_albums,
        "appears_on": appears_on,
    }


def get_album_detail(con: duckdb.DuckDBPyConnection, release_group_mbid: str) -> dict:
    """Consolidation view of a release-group: the owned editions you hold (the duplicate
    set) with each one's Apple library id + completeness, plus all available editions.

    Returns {release_group_mbid, owned_editions, available_versions}. owned_editions each:
    {album_id, apple_album_id, title, version_class, total_tracks, owned_track_count}.
    `available_versions` reuses get_album_versions (MusicBrainz; [] without mb_*).
    """
    owned_editions = [
        {
            "album_id": r[0],
            "apple_album_id": r[1],
            "title": r[2],
            "version_class": r[3],
            "total_tracks": r[4],
            "owned_track_count": r[5],
        }
        for r in con.execute(
            "SELECT a.id, json_extract_string(a.external_ids_json, '$.apple') AS apple_id, "
            "a.title, a.version_class, a.total_tracks, ("
            "  SELECT count(*) FROM library_items lit JOIN tracks t ON lit.canonical_id = t.id "
            "  WHERE lit.status = 'present' AND lit.item_type = 'track' AND t.album_id = a.id"
            ") AS owned_tracks "
            "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
            "WHERE li.status = 'present' AND li.item_type = 'album' "
            "AND a.release_group_mbid = ? "
            "ORDER BY a.title",
            [release_group_mbid],
        ).fetchall()
    ]
    return {
        "release_group_mbid": release_group_mbid,
        "owned_editions": owned_editions,
        "available_versions": get_album_versions(con, release_group_mbid),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_discography.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
git add src/needledrop/discography.py tests/test_discography.py
git commit -m "feat: add get_song_detail and get_album_detail consolidation lookups"
```

---

## Task 4: Consolidation-lookup MCP tools

**Files:**
- Modify: `src/needledrop/mcp_server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mcp_server.py`:

```python
def test_get_album_detail_tool_returns_owned_editions():
    con = _fresh_con()
    for title, apple in [("OK Computer", "la.std"), ("OK Computer (Deluxe)", "la.dlx")]:
        con.execute(
            "INSERT INTO albums (title, release_group_mbid, external_ids_json) "
            "VALUES (?, 'rg-okc', json_object('apple', ?))",
            [title, apple],
        )
        album_id = con.execute("SELECT id FROM albums WHERE title = ?", [title]).fetchone()[0]
        con.execute(
            "INSERT INTO library_items "
            "(service, service_item_id, item_type, canonical_id, status) "
            "VALUES ('apple_music', ?, 'album', ?, 'present')",
            [apple, album_id],
        )
    detail = _call(create_server(con), "get_album_detail", {"release_group_mbid": "rg-okc"})
    titles = {e["title"] for e in detail["owned_editions"]}
    assert titles == {"OK Computer", "OK Computer (Deluxe)"}
    assert {e["apple_album_id"] for e in detail["owned_editions"]} == {"la.std", "la.dlx"}


def test_get_song_detail_tool_returns_library_albums():
    con = _fresh_con()
    con.execute(
        "INSERT INTO albums (title, external_ids_json) "
        "VALUES ('OK Computer', json_object('apple', 'la.okc'))"
    )
    album_id = con.execute("SELECT id FROM albums").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', 'la.okc', 'album', ?, 'present')",
        [album_id],
    )
    con.execute(
        "INSERT INTO tracks (title, recording_mbid, album_id) VALUES ('Lucky', 'rec-lucky', ?)",
        [album_id],
    )
    detail = _call(create_server(con), "get_song_detail", {"recording_mbid": "rec-lucky"})
    assert [a["title"] for a in detail["library_albums"]] == ["OK Computer"]
    assert detail["appears_on"] == []
```

Also extend `test_server_exposes_expected_tools` to add `"get_song_detail"`, `"get_album_detail"`.

- [ ] **Step 2: Run to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_mcp_server.py -q -k "song_detail or album_detail or expected_tools"`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/needledrop/mcp_server.py`, add aliased imports:

```python
from needledrop.discography import get_album_detail as _get_album_detail
from needledrop.discography import get_song_detail as _get_song_detail
```

Register two tools inside `create_server` (place after `get_album_versions`):

```python
    @mcp.tool
    def get_song_detail(recording_mbid: str) -> dict:
        """Where a recording lives: owned library albums + release-groups it appears on."""
        return _get_song_detail(con, recording_mbid)

    @mcp.tool
    def get_album_detail(release_group_mbid: str) -> dict:
        """Consolidation view: owned editions of a release-group (with Apple ids +
        completeness) and all available editions, to decide what to keep/remove/add."""
        return _get_album_detail(con, release_group_mbid)
```

- [ ] **Step 4: Run tests + full suite + lint (CI-parity gate)**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest -q`
Expected: PASS.

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add consolidation-lookup MCP tools (get_song_detail, get_album_detail)"
```

---

## Self-Review (completed)

**Spec coverage:** The three mutations — add catalog album, remove library album, create playlist — are implemented as connector methods (Task 1) and dry-run-by-default MCP tools (Task 2), with the mutator injected and the `serve` CLI wiring a lazy proxy.

**Safety:** Every mutating tool defaults to `dry_run=True` and returns a preview without touching Apple or the mutator; only an explicit `dry_run=false` applies a change, and then only if a mutator is injected (else it raises → `ToolError`). Dry-run previews work even with no mutator (tested). The read-only `MusicConnector` base is unchanged, so existing fake connectors and the read-only guarantees elsewhere are unaffected.

**Placeholder scan:** No TBD/TODO; complete code in every step. The one adapt-to-existing point (the connector test fixture / `AppleCredentials` import) names exactly what to match.

**Type consistency:** `add_albums_to_library(list[str])`, `remove_album_from_library(str)`, `create_playlist(name, *, description=None, track_ids=None) -> LibraryPlaylist` match the `_FakeMutator`, the `_LazyMutator`, and the tool call sites. Tool returns are JSON-safe dicts (`created_playlist` via `model_dump(mode="json")`). `add_album` adds by catalog id; `remove_album` removes by library id — matching `CatalogAlbum.id` / `LibraryAlbum.id`.

**Edge cases:** missing mutator on apply → raises; dry-run without mutator → preview; `create_playlist` omits the `relationships` block when no `track_ids`; `description` omitted from the body when None. Mutations change Apple only; the local DB reconciles on the next `sync` (documented).

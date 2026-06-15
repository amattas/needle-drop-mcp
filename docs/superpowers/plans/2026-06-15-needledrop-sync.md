# NeedleDrop Sync Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `needledrop sync` end to end — pull the Apple library (with inline catalog enrichment for UPC/ISRC), match each item against MusicBrainz, persist canonical entities + library items + review candidates, reconcile a full snapshot, record the run, and expose `diff_sync`.

**Architecture:** `services/sync.py` orchestrates the five prior layers: connector (Plan 3, now requesting `include=catalog` so albums/songs carry their catalog `upc`/`isrc`) → matcher (Plan 4) → repository (Plan 5). One canonical row per owned edition; `library_items` carry the match result; unseen items are marked removed; each run's added/removed/present counts land in `sync_runs.summary_json` (which `diff_sync` reads). A small `upsert_artist` name-dedup fallback is added because Apple library albums give only an artist *name* (no id) and the matcher yields a release-group MBID (not an artist MBID).

**Tech Stack:** Python 3.13, DuckDB, httpx, typer. Builds on merged Plans 1–5.

**Plan series:** Plan 6 of 8. This is the keystone that turns the prior layers into a working `needledrop sync`. The remaining plans: analysis + read-only MCP (7), mutations + discography (8). Design spec: `docs/superpowers/specs/2026-06-15-needledrop-mcp-design.md` (§3, §6.7).

---

## Environment notes for implementers

- Python via the project env interpreter: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python` (NOT `mamba run`). Tests e.g.: `... -m pytest tests/services/test_sync.py -v`.
- CI-parity gate before "done": `... -m pytest` green AND `... -m ruff check .` clean. ruff line-length 100 — wrap long lines; don't quote forward-ref annotations (UP037).
- No new dependencies. No network/Docker in tests (fake connector + in-memory DuckDB).

## Verified facts this plan relies on (from prior research)

- `include=catalog` IS supported on the library list endpoints; each library album/song comes back with `relationships.catalog.data[0].attributes.upc` / `.isrc` populated inline (empty for a minority of items → those fall back to fuzzy matching). One paginated pass; no per-item fetches.
- The matcher (`match_album`/`match_track`) returns a release-group / recording **MBID** (`gid`) — not a release or artist MBID. So canonical albums dedup by Apple id (release_mbid is unknown), and artists need a name-based dedup fallback.

---

## File Structure

```text
src/needledrop/connectors/apple_models.py   # MODIFY: LibraryAlbum.upc, LibrarySong.isrc
src/needledrop/connectors/apple_music.py     # MODIFY: _paginate include=, iter_* use include=catalog
src/needledrop/db/repository.py              # MODIFY: upsert_artist name-dedup fallback
src/needledrop/services/__init__.py          # NEW (empty)
src/needledrop/services/sync.py              # NEW: sync_library, diff_sync
src/needledrop/cli.py                        # MODIFY: `needledrop sync`

tests/connectors/test_apple_models.py        # MODIFY: catalog-embed cases
tests/connectors/test_apple_music.py         # MODIFY: include=catalog request
tests/db/test_repository.py                  # MODIFY: artist name-dedup
tests/services/test_sync.py                  # NEW
tests/test_cli_sync.py                        # NEW
```

---

### Task 1: Catalog enrichment in provider models

**Files:**
- Modify: `src/needledrop/connectors/apple_models.py`
- Test: `tests/connectors/test_apple_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/connectors/test_apple_models.py`:

```python
def test_library_album_extracts_embedded_catalog_upc():
    resource = {
        "id": "l.abc",
        "type": "library-albums",
        "attributes": {"name": "OK Computer", "artistName": "Radiohead"},
        "relationships": {
            "catalog": {"data": [{"id": "123", "type": "albums",
                                  "attributes": {"upc": "634904032463"}}]}
        },
    }
    album = LibraryAlbum.from_api(resource)
    assert album.upc == "634904032463"


def test_library_album_empty_catalog_relationship_is_none():
    resource = {"id": "l.x", "attributes": {"name": "X"},
                "relationships": {"catalog": {"data": []}}}
    assert LibraryAlbum.from_api(resource).upc is None


def test_library_song_extracts_embedded_catalog_isrc():
    resource = {
        "id": "l.s1",
        "type": "library-songs",
        "attributes": {"name": "Karma Police", "artistName": "Radiohead"},
        "relationships": {
            "catalog": {"data": [{"id": "456", "type": "songs",
                                  "attributes": {"isrc": "GBAYE9700116"}}]}
        },
    }
    song = LibrarySong.from_api(resource)
    assert song.isrc == "GBAYE9700116"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_models.py -v`
Expected: FAIL — `AttributeError: 'LibraryAlbum' object has no attribute 'upc'`.

- [ ] **Step 3: Implement**

In `src/needledrop/connectors/apple_models.py`, add a module-level helper (after the imports) and the new fields.

Helper:

```python
def _embedded_catalog_attr(resource: dict[str, Any], attr: str) -> str | None:
    """Read an attribute from an embedded `include=catalog` relationship, if present."""
    data = resource.get("relationships", {}).get("catalog", {}).get("data") or []
    if data:
        return data[0].get("attributes", {}).get(attr)
    return None
```

Add `upc: str | None = None` to `LibraryAlbum` and set it in `LibraryAlbum.from_api`:

```python
            date_added=a.get("dateAdded"),
            upc=_embedded_catalog_attr(resource, "upc"),
```

Add `isrc: str | None = None` to `LibrarySong` and set it in `LibrarySong.from_api`:

```python
            release_date=a.get("releaseDate"),
            isrc=_embedded_catalog_attr(resource, "isrc"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_models.py -v`
Expected: PASS (existing model tests + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/connectors/apple_models.py tests/connectors/test_apple_models.py
git commit -m "feat: extract catalog UPC/ISRC from embedded library relationship"
```

---

### Task 2: Request `include=catalog` when paging the library

**Files:**
- Modify: `src/needledrop/connectors/apple_music.py`
- Test: `tests/connectors/test_apple_music.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/connectors/test_apple_music.py`:

```python
def test_iter_library_albums_requests_include_catalog():
    seen = {}

    def handler(request):
        seen["include"] = request.url.params.get("include")
        return httpx.Response(200, json={"data": [
            {"id": "l.a", "attributes": {"name": "A"},
             "relationships": {"catalog": {"data": [{"id": "1", "type": "albums",
                                                     "attributes": {"upc": "U1"}}]}}}
        ]})

    albums = list(_connector(handler).iter_library_albums())
    assert seen["include"] == "catalog"
    assert albums[0].upc == "U1"


def test_iter_library_playlists_does_not_request_include():
    seen = {}

    def handler(request):
        seen["include"] = request.url.params.get("include")
        return httpx.Response(200, json={"data": [{"id": "p.1", "attributes": {"name": "Faves"}}]})

    list(_connector(handler).iter_library_playlists())
    assert seen["include"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_music.py -v`
Expected: FAIL — `include` is None for albums (currently no include param) / `albums[0].upc` AttributeError if Task 1 not present (it is).

- [ ] **Step 3: Implement**

In `src/needledrop/connectors/apple_music.py`, change `_paginate` to accept an `include` and have the album/song iterators pass `include="catalog"` (playlists unchanged). Replace the `_paginate` method and the three `iter_library_*` methods with:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_music.py -v`
Expected: PASS (existing pagination tests still green — the `next` cursor on subsequent pages already encodes its own query, so only the first request carries `limit`/`include`).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/connectors/apple_music.py tests/connectors/test_apple_music.py
git commit -m "feat: enrich library reads with include=catalog"
```

---

### Task 3: Artist name-dedup fallback

**Files:**
- Modify: `src/needledrop/db/repository.py`
- Test: `tests/db/test_repository.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/db/test_repository.py`:

```python
def test_upsert_artist_dedupes_by_name_when_no_ids():
    con = _con()
    first = upsert_artist(con, canonical_name="Radiohead")
    again = upsert_artist(con, canonical_name="Radiohead")
    assert again == first
    assert con.execute("SELECT count(*) FROM artists").fetchone()[0] == 1


def test_upsert_artist_name_dedup_does_not_collide_with_id_matched():
    # An MBID-identified artist and a later name-only upsert of the same name merge
    # (acceptable heuristic: one canonical 'Radiohead' for an Apple-only library).
    con = _con()
    with_mbid = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    name_only = upsert_artist(con, canonical_name="Radiohead")
    assert name_only == with_mbid
    assert con.execute("SELECT count(*) FROM artists").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `test_upsert_artist_dedupes_by_name_when_no_ids` gets 2 rows (no name dedup yet).

- [ ] **Step 3: Implement**

In `src/needledrop/db/repository.py`, in `upsert_artist`, insert a name-dedup branch immediately before the final `INSERT` (after the apple-id branch):

```python
    row = con.execute("SELECT id FROM artists WHERE canonical_name = ?", [canonical_name]).fetchone()
    if row:
        con.execute(
            "UPDATE artists SET sort_name = COALESCE(?, sort_name), mbid = COALESCE(?, mbid), "
            "external_ids_json = ? WHERE id = ?",
            [sort_name, mbid, ext_json, row[0]],
        )
        return row[0]
```

Update the `upsert_artist` docstring to: `"""Insert or update an artist, deduping by MBID, then Apple external id, then exact canonical name. Returns its id."""`. Add an inline comment above the new branch: `# Last-resort dedup: same display name (name collisions are accepted for an Apple-only library; MBID disambiguates when present).`

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: PASS (existing repository tests + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/db/repository.py tests/db/test_repository.py
git commit -m "feat: add name-based artist dedup fallback"
```

---

### Task 4: `sync_library` orchestration

**Files:**
- Create: `src/needledrop/services/__init__.py`
- Create: `src/needledrop/services/sync.py`
- Test: `tests/services/test_sync.py`

- [ ] **Step 1: Write the failing test**

`tests/services/test_sync.py`:

```python
from datetime import datetime

import duckdb

from needledrop.connectors.apple_models import LibraryAlbum, LibrarySong
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.services.sync import sync_library


def _seed_mb(con):
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit (id INTEGER, name VARCHAR)")
    con.execute(
        "CREATE TABLE mb_artist_credit_name "
        "(artist_credit INTEGER, position INTEGER, artist INTEGER, name VARCHAR, join_phrase VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, type INTEGER)"
    )
    con.execute(
        "CREATE TABLE mb_release (id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, "
        "release_group INTEGER, barcode VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mb_recording (id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, "
        "length INTEGER)"
    )
    con.execute("CREATE TABLE mb_isrc (id INTEGER, recording INTEGER, isrc VARCHAR)")
    con.execute("INSERT INTO mb_artist VALUES (1, 'gid-radiohead', 'Radiohead', 'Radiohead')")
    con.execute("INSERT INTO mb_artist_credit VALUES (10, 'Radiohead')")
    con.execute("INSERT INTO mb_artist_credit_name VALUES (10, 0, 1, 'Radiohead', '')")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'gid-okc', 'OK Computer', 10, 1)")
    con.execute(
        "INSERT INTO mb_release VALUES (1000, 'gid-rel', 'OK Computer', 10, 100, '0724385522123')"
    )


class FakeConnector:
    def __init__(self, albums=(), songs=(), playlists=()):
        self._albums, self._songs, self._playlists = albums, songs, playlists

    def iter_library_albums(self):
        return iter(self._albums)

    def iter_library_songs(self):
        return iter(self._songs)

    def iter_library_playlists(self):
        return iter(self._playlists)


def _db():
    con = connect(":memory:")
    init_schema(con)
    _seed_mb(con)
    return con


def test_sync_matches_album_by_upc_and_records_item():
    con = _db()
    now = datetime(2026, 6, 15, 12, 0, 0)
    connector = FakeConnector(albums=[
        LibraryAlbum(id="l.a1", name="OK Computer", artist_name="Radiohead", upc="0724385522123")
    ])
    summary = sync_library(connector, con, now=now)

    assert summary == {"added": 1, "removed": 0, "present": 1}
    row = con.execute(
        "SELECT li.match_method, a.release_group_mbid "
        "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "WHERE li.service_item_id = 'l.a1'"
    ).fetchone()
    assert row == ("upc", "gid-okc")


def test_sync_unmatched_album_saves_candidates():
    con = _db()
    now = datetime(2026, 6, 15, 12, 0, 0)
    connector = FakeConnector(albums=[
        LibraryAlbum(id="l.b1", name="In Rainbows", artist_name="Radiohead")
    ])
    sync_library(connector, con, now=now)
    item_id = con.execute(
        "SELECT id FROM library_items WHERE service_item_id = 'l.b1'"
    ).fetchone()[0]
    method = con.execute(
        "SELECT match_method FROM library_items WHERE id = ?", [item_id]
    ).fetchone()[0]
    assert method == "none"
    # The artist's release-group is offered as a review candidate.
    candidates = con.execute(
        "SELECT candidate_mbid FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchall()
    assert ("gid-okc",) in candidates


def test_sync_marks_unseen_items_removed_across_runs():
    con = _db()
    run1 = datetime(2026, 6, 1, 10, 0, 0)
    sync_library(con=con, connector=FakeConnector(albums=[
        LibraryAlbum(id="l.gone", name="OK Computer", artist_name="Radiohead", upc="0724385522123")
    ]), now=run1)
    run2 = datetime(2026, 6, 15, 12, 0, 0)
    summary = sync_library(con=con, connector=FakeConnector(albums=[]), now=run2)
    assert summary["removed"] == 1
    status = con.execute(
        "SELECT status FROM library_items WHERE service_item_id = 'l.gone'"
    ).fetchone()[0]
    assert status == "removed"


def test_sync_matches_track_by_isrc():
    con = _db()
    con.execute("INSERT INTO mb_recording VALUES (5000, 'gid-karma', 'Karma Police', 10, 261000)")
    con.execute("INSERT INTO mb_isrc VALUES (1, 5000, 'GBAYE9700116')")
    now = datetime(2026, 6, 15, 12, 0, 0)
    connector = FakeConnector(songs=[
        LibrarySong(id="l.s1", name="Karma Police", artist_name="Radiohead", isrc="GBAYE9700116")
    ])
    sync_library(connector, con, now=now)
    row = con.execute(
        "SELECT li.match_method, t.recording_mbid "
        "FROM library_items li JOIN tracks t ON li.canonical_id = t.id "
        "WHERE li.service_item_id = 'l.s1'"
    ).fetchone()
    assert row == ("isrc", "gid-karma")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/services/test_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.services'`.

- [ ] **Step 3: Implement**

`src/needledrop/services/__init__.py`: empty file.

`src/needledrop/services/sync.py`:

```python
"""Library synchronization: pull → match → persist → snapshot."""

from __future__ import annotations

from datetime import datetime

import duckdb

from needledrop.connectors.base import MusicConnector
from needledrop.db.repository import (
    complete_sync_run,
    mark_unseen_removed,
    record_library_item,
    save_match_candidates,
    start_sync_run,
    upsert_album,
    upsert_artist,
    upsert_track,
)
from needledrop.matching.matcher import AlbumQuery, TrackQuery, match_album, match_track
from needledrop.models.match import MatchCandidate
from needledrop.normalize.album_versions import classify_album_version


def _candidate_dict(candidate: MatchCandidate) -> dict:
    return {
        "candidate_mbid": candidate.candidate_mbid,
        "candidate_kind": candidate.candidate_kind.value,
        "score": candidate.score,
        "method": candidate.method.value,
    }


def sync_library(
    connector: MusicConnector,
    con: duckdb.DuckDBPyConnection,
    *,
    now: datetime,
    service: str = "apple_music",
) -> dict:
    """Pull the connector's library, match + persist it, reconcile the snapshot.

    Returns the run summary {added, removed, present}.
    """
    run_id = start_sync_run(con, service=service, started_at=now)

    for album in connector.iter_library_albums():
        _sync_album(con, album, now=now, service=service)
    for song in connector.iter_library_songs():
        _sync_track(con, song, now=now, service=service)
    for playlist in connector.iter_library_playlists():
        record_library_item(
            con, service=service, service_item_id=playlist.id, item_type="playlist", seen_at=now,
        )

    removed = mark_unseen_removed(con, service=service, run_started_at=now)
    added = con.execute(
        "SELECT count(*) FROM library_items WHERE added_at = ?", [now]
    ).fetchone()[0]
    present = con.execute(
        "SELECT count(*) FROM library_items WHERE status = 'present'"
    ).fetchone()[0]
    summary = {"added": added, "removed": removed, "present": present}
    complete_sync_run(con, run_id=run_id, completed_at=now, summary=summary)
    return summary


def _sync_album(con, album, *, now, service) -> None:
    artist_id = (
        upsert_artist(con, canonical_name=album.artist_name) if album.artist_name else None
    )
    result = match_album(
        con, AlbumQuery(title=album.name, artist_name=album.artist_name, upc=album.upc)
    )
    canonical_id = upsert_album(
        con,
        title=album.name,
        artist_id=artist_id,
        release_group_mbid=result.mbid,
        version_class=classify_album_version(album.name).value,
        external_ids={"apple": album.id},
    )
    item_id = record_library_item(
        con, service=service, service_item_id=album.id, item_type="album",
        canonical_id=canonical_id, match_confidence=result.confidence,
        match_method=result.method.value, seen_at=now,
    )
    save_match_candidates(
        con, library_item_id=item_id, candidates=[_candidate_dict(c) for c in result.candidates]
    )


def _sync_track(con, song, *, now, service) -> None:
    artist_id = (
        upsert_artist(con, canonical_name=song.artist_name) if song.artist_name else None
    )
    result = match_track(
        con, TrackQuery(title=song.name, artist_name=song.artist_name, isrc=song.isrc)
    )
    canonical_id = upsert_track(
        con,
        title=song.name,
        artist_id=artist_id,
        recording_mbid=result.mbid,
        isrc=song.isrc,
        external_ids={"apple": song.id},
    )
    item_id = record_library_item(
        con, service=service, service_item_id=song.id, item_type="track",
        canonical_id=canonical_id, match_confidence=result.confidence,
        match_method=result.method.value, seen_at=now,
    )
    save_match_candidates(
        con, library_item_id=item_id, candidates=[_candidate_dict(c) for c in result.candidates]
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/services/test_sync.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/services/__init__.py src/needledrop/services/sync.py tests/services/test_sync.py
git commit -m "feat: add library sync orchestration"
```

---

### Task 5: `diff_sync`

**Files:**
- Modify: `src/needledrop/services/sync.py`
- Test: `tests/services/test_sync.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/services/test_sync.py`:

```python
from needledrop.services.sync import diff_sync


def test_diff_sync_returns_latest_completed_run_summary():
    con = _db()
    sync_library(FakeConnector(albums=[
        LibraryAlbum(id="l.a1", name="OK Computer", artist_name="Radiohead", upc="0724385522123")
    ]), con, now=datetime(2026, 6, 1, 10, 0, 0))
    sync_library(FakeConnector(albums=[
        LibraryAlbum(id="l.a1", name="OK Computer", artist_name="Radiohead", upc="0724385522123"),
        LibraryAlbum(id="l.a2", name="Kid A", artist_name="Radiohead"),
    ]), con, now=datetime(2026, 6, 15, 12, 0, 0))
    diff = diff_sync(con)
    assert diff == {"added": 1, "removed": 0, "present": 2}


def test_diff_sync_no_runs_returns_empty():
    con = _db()
    assert diff_sync(con) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/services/test_sync.py -v`
Expected: FAIL — `ImportError: cannot import name 'diff_sync'`.

- [ ] **Step 3: Implement (append to `src/needledrop/services/sync.py`)**

Add `import json` to the top of `sync.py` (with the other imports), then append:

```python
def diff_sync(con: duckdb.DuckDBPyConnection) -> dict:
    """Return the most recent completed sync run's summary (its added/removed/present diff)."""
    row = con.execute(
        "SELECT summary_json FROM sync_runs WHERE status = 'completed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return json.loads(row[0]) if row else {}
```

The top import block of `sync.py` should now begin:

```python
from __future__ import annotations

import json
from datetime import datetime

import duckdb
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/services/test_sync.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/services/sync.py tests/services/test_sync.py
git commit -m "feat: add diff_sync"
```

---

### Task 6: `needledrop sync` CLI

**Files:**
- Modify: `src/needledrop/cli.py`
- Test: `tests/test_cli_sync.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_sync.py`:

```python
from unittest.mock import patch

from typer.testing import CliRunner

from needledrop.cli import app

runner = CliRunner()


def test_sync_command_reports_summary():
    with patch("needledrop.cli.AppleMusicConnector") as connector_cls, \
         patch("needledrop.cli.open_db"), \
         patch("needledrop.cli.sync_library") as sync_fn:
        sync_fn.return_value = {"added": 3, "removed": 1, "present": 42}
        result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert connector_cls.from_keystore.called
    assert sync_fn.called
    assert "3 added" in result.stdout
    assert "1 removed" in result.stdout
    assert "42 present" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_cli_sync.py -v`
Expected: FAIL — `sync` command not found (exit code 2) / patched names not importable from `needledrop.cli`.

- [ ] **Step 3: Implement (modify `src/needledrop/cli.py`)**

Add these imports near the top of `cli.py` (alongside the existing imports; `load_settings` and `connect` may already be imported — don't duplicate):

```python
from datetime import datetime

from needledrop.connectors.apple_music import AppleMusicConnector
from needledrop.db.duckdb_store import open_db
from needledrop.services.sync import sync_library
```

(`open_db` connects AND bootstraps the canonical schema — `connect` alone leaves
the tables uncreated, which would crash `sync` on a fresh DB.)

Add the `sync` command (a top-level command on `app`, after the existing `auth`/`mb` wiring, before `def main()`):

```python
@app.command("sync")
def sync() -> None:
    """Pull the Apple Music library, match it against MusicBrainz, and persist it."""
    settings = load_settings()
    con = open_db(settings.db_path)
    connector = AppleMusicConnector.from_keystore()
    summary = sync_library(connector, con, now=datetime.now())
    typer.echo(
        f"Synced: {summary['added']} added, {summary['removed']} removed, "
        f"{summary['present']} present."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_cli_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite + lint gate**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest`
Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/cli.py tests/test_cli_sync.py
git commit -m "feat: add needledrop sync command"
```

---

## Self-Review

**1. Spec coverage (spec §3 data flow, §6.7 sync.py):**
- Catalog enrichment (UPC/ISRC inline) → Tasks 1–2. ✓
- `sync_service_library` (pull → normalize → match → persist → snapshot) → `sync_library` Task 4. ✓
- `diff_sync` → Task 5. ✓
- `needledrop sync` CLI → Task 6. ✓
- Artist persistence with a workable dedup (name fallback, since the matcher gives a release-group MBID not an artist MBID) → Task 3. ✓
- Deferred by design: the `playlists` table (name/description) — sync records playlist *presence* as `library_items` for snapshot reconciliation, but populating the `playlists` table is a later enhancement; `get_artist_collection`/discography analysis is Plan 7; mutations Plan 8. Known-carried follow-ups from Plan 5 (external_ids merge; track field refresh) remain open.

**2. Placeholder scan:** No TBD/TODO. Every code step shows complete code; every run step has the command and expected result. `now` is injected into `sync_library` for deterministic tests; the CLI passes `datetime.now()`.

**3. Type/name consistency:** `sync_library(connector, con, *, now, service="apple_music")` and `diff_sync(con)` match their tests. `_sync_album`/`_sync_track` call `match_album`/`match_track` (Plan 4) with `AlbumQuery`/`TrackQuery`, then `upsert_artist`/`upsert_album`/`upsert_track`/`record_library_item`/`save_match_candidates` (Plan 5) with matching keyword args. `result.mbid`/`result.confidence`/`result.method` and `result.candidates[].candidate_mbid/.candidate_kind/.score/.method` are the Plan-4 `MatchResult`/`MatchCandidate` fields; `_candidate_dict` emits the dict shape `save_match_candidates` expects. `LibraryAlbum.upc`/`LibrarySong.isrc` (Task 1) feed `AlbumQuery.upc`/`TrackQuery.isrc`. `record_library_item(..., item_type="album"|"track"|"playlist")` and the `added_at == now` / `mark_unseen_removed(run_started_at=now)` snapshot logic match the Plan-5 contract. The CLI patches `needledrop.cli.AppleMusicConnector`/`connect`/`sync_library` — all imported into `cli` in Task 6.

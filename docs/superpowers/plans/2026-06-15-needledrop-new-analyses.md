# NeedleDrop New Analyses (track-linkage, duplicate-track, partial-album, single-replaced) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the track→album linkage sync currently drops, then add three cleanup analyses that depend on it — duplicate-track, partial-album, and single-track-replaced-by-album — wired into the cleanup scan and exposed as MCP tools.

**Architecture:** A small schema add (`albums.total_tracks`) plus a sync change that (a) persists an owned album's track count and (b) links each owned song to a canonical album row (matching the owned album item by artist+title when present, else a minimal song-only album row — a *last-resort* fallback that never touches the existing album-item dedup keyed on `release_mbid`/Apple-id). Three new analysis functions follow the established `find_*(con) -> list[CleanupFinding]` pattern, all MB-free (they read only canonical tables). They are wired into `run_cleanup_scan` and surfaced as read-only MCP tools.

**Tech Stack:** DuckDB (incl. idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` migration), Pydantic v2, FastMCP 3.4.2, pytest.

---

## Background & Key Facts (read before starting)

**Environment:** Python interpreter `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python` (use directly; NOT `mamba run`). No Docker/network needed.

**Gates:** `... -m pytest -q` and `... -m ruff check .`. Ruff: line-length 100 (≤100 chars/line), select E/F/I/UP/B, B008 ignored.

**Why this plan exists / the linkage gap (verified):**
- `tracks.album_id` exists in the schema but **sync never populates it** — every owned track is currently orphaned from its album. `src/needledrop/services/sync.py` `_sync_track` calls `upsert_track(...)` without `album_id`/`track_number`/`disc_number`/`duration_ms`, even though `upsert_track` already accepts all of them and `LibrarySong` carries `album_name`, `track_number`, `disc_number`, `duration_ms`.
- A library **song** carries only its album *name* (string) — no album ID. So linking a song to an album means matching by `(artist_id, album_name)`.
- `upsert_album` dedups by `release_mbid` → Apple-id (`external_ids.apple`) → else `INSERT` (no name dedup). Every owned album **item** has an Apple id, so it always dedups by Apple-id. We must NOT add name dedup to `upsert_album` (it would risk merging distinct editions and break duplicate-album). Instead, song→album linkage uses a *separate* `find_or_create_song_album` that prefers an existing Apple-id-bearing row, else reuses/creates a song-only row — leaving `upsert_album` untouched.
- `LibraryAlbum.track_count` is available → persist it as `albums.total_tracks` for partial-album detection.

**Finding types already exist** (`src/needledrop/models/enums.py`): `FindingType.DUPLICATE_TRACK`, `PARTIAL_ALBUM`, `SINGLE_REPLACED_BY_ALBUM`. `FindingSeverity.{INFO,LOW,MEDIUM,HIGH}`. No enum changes needed.

**Schema facts (`src/needledrop/db/schema.sql`):**
- `tracks(id, recording_mbid, album_id REFERENCES albums(id), artist_id, title, isrc, disc_number, track_number, duration_ms, external_ids_json)`.
- `albums(id, release_group_mbid, release_mbid, artist_id, title, version_class, external_ids_json)` — you will add `total_tracks INTEGER`.
- `library_items(... item_type, canonical_id, ... match_method, status ...)`. `canonical_id` is polymorphic: `albums.id` for `item_type='album'`, `tracks.id` for `item_type='track'`.

**Migration mechanics (verified):**
- `open_db(db_path)` = `init_schema` (runs baseline `schema.sql`, `CREATE TABLE IF NOT EXISTS`) then `apply_migrations` (runs `*.sql` in the packaged `src/needledrop/db/migrations/` dir, in lexical order, each recorded in `schema_migrations`).
- `ADD COLUMN IF NOT EXISTS` is idempotent in DuckDB (verified). So: add `total_tracks` to the baseline `schema.sql` AND ship a migration `0001_add_albums_total_tracks.sql` using `ADD COLUMN IF NOT EXISTS`. On a fresh DB the baseline already created the column and the migration is a harmless no-op; on a pre-existing DB the migration adds it. **Most tests use `connect()+init_schema()` directly (not `open_db`), so the column MUST be in the baseline `schema.sql` or those tests won't have it.**
- The migrations dir currently contains only `.gitkeep`. `apply_migrations`'s statement splitter drops whole-line `--` comments and splits on `;` — keep one statement per `;`.

**Existing analysis pattern (`src/needledrop/analysis/duplicates.py`):** `find_duplicate_albums(con) -> list[CleanupFinding]` groups owned album items by `release_group_mbid`, returns `CleanupFinding` with a `Recommendation(action, detail, payload)`. New analyses mirror this. `run_cleanup_scan` (`src/needledrop/services/cleanup.py`) calls each `find_*` and `save_cleanup_findings`. mb_* access is guarded with `table_exists` — but the THREE new analyses here are MB-free (canonical tables only), so no guard is needed.

**MCP server (`src/needledrop/mcp_server.py`):** `create_server(con, *, sync_runner=None)`. Tools are inner `@mcp.tool` functions over `con`; imports are aliased with `_` prefix (one `from ... import (X as _X)` per symbol). Finding lists are returned via `[f.model_dump(mode="json") for f in ...]`.

**Scope:** the linkage sync change + three analyses + cleanup wiring + MCP tools. **Out of scope:** catalog browse tools, Apple-library mutations (later plans).

---

## File Structure

- **Modify** `src/needledrop/db/schema.sql` — add `albums.total_tracks INTEGER`.
- **Create** `src/needledrop/db/migrations/0001_add_albums_total_tracks.sql` — idempotent column add.
- **Modify** `tests/db/test_duckdb_store.py` — assert the column exists via `open_db` and via the migration on a legacy table.
- **Modify** `src/needledrop/db/repository.py` — `upsert_album` gains `total_tracks`; add `find_or_create_song_album`.
- **Modify** `src/needledrop/services/sync.py` — `_sync_album` persists `total_tracks`; `_sync_track` links `album_id` + structural fields.
- **Modify** `tests/db/test_repository.py`, `tests/services/test_sync.py` — linkage + persistence + idempotency tests.
- **Create** `src/needledrop/analysis/duplicate_tracks.py`, `partial_albums.py`, `single_replaced.py` (+ tests under `tests/analysis/`).
- **Modify** `src/needledrop/services/cleanup.py` — wire the three analyses.
- **Modify** `src/needledrop/mcp_server.py` + `tests/test_mcp_server.py` — three new tools + tool-surface assertion.

---

## Task 1: Schema — `albums.total_tracks` (baseline + idempotent migration)

**Files:**
- Modify: `src/needledrop/db/schema.sql`
- Create: `src/needledrop/db/migrations/0001_add_albums_total_tracks.sql`
- Test: `tests/db/test_duckdb_store.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/db/test_duckdb_store.py` (it already imports `connect, init_schema, open_db`):

```python
def test_open_db_albums_has_total_tracks(tmp_path):
    con = open_db(tmp_path / "fresh.duckdb")
    cols = [r[1] for r in con.execute("PRAGMA table_info('albums')").fetchall()]
    assert "total_tracks" in cols
    # Opening again must not raise (migration is idempotent).
    open_db(tmp_path / "fresh.duckdb")


def test_total_tracks_migration_upgrades_legacy_albums(tmp_path):
    from importlib import resources

    from needledrop.db.duckdb_store import apply_migrations
    con = connect(tmp_path / "legacy.duckdb")
    # Simulate a pre-migration albums table lacking total_tracks.
    con.execute("CREATE TABLE albums (id INTEGER, title VARCHAR)")
    migrations = resources.files("needledrop.db").joinpath("migrations")
    applied = apply_migrations(con, migrations)
    assert "0001_add_albums_total_tracks" in applied
    cols = [r[1] for r in con.execute("PRAGMA table_info('albums')").fetchall()]
    assert "total_tracks" in cols
```

- [ ] **Step 2: Run to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_duckdb_store.py -q -k total_tracks`
Expected: FAIL (`total_tracks` not in columns / migration not found).

- [ ] **Step 3: Add the column to the baseline schema**

In `src/needledrop/db/schema.sql`, in the `albums` table definition, add `total_tracks INTEGER` after the `version_class VARCHAR` line:

```sql
CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_albums'),
    release_group_mbid VARCHAR,
    release_mbid VARCHAR,
    artist_id INTEGER REFERENCES artists(id),
    title VARCHAR NOT NULL,
    version_class VARCHAR,
    total_tracks INTEGER,
    external_ids_json VARCHAR NOT NULL DEFAULT '{}'
);
```

- [ ] **Step 4: Add the idempotent migration**

Create `src/needledrop/db/migrations/0001_add_albums_total_tracks.sql`:

```sql
-- Add albums.total_tracks for partial-album detection.
-- Idempotent: a no-op on fresh DBs that already created it from the baseline schema.
ALTER TABLE albums ADD COLUMN IF NOT EXISTS total_tracks INTEGER;
```

- [ ] **Step 5: Run to verify pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_duckdb_store.py -q`
Expected: PASS (all store tests).

- [ ] **Step 6: Lint + commit**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check tests/db/test_duckdb_store.py`
Expected: clean.

```bash
git add src/needledrop/db/schema.sql src/needledrop/db/migrations/0001_add_albums_total_tracks.sql tests/db/test_duckdb_store.py
git commit -m "feat: add albums.total_tracks column (baseline + idempotent migration)"
```

---

## Task 2: Sync linkage — persist `total_tracks` and link tracks to albums

**Files:**
- Modify: `src/needledrop/db/repository.py` (`upsert_album` + new `find_or_create_song_album`)
- Modify: `src/needledrop/services/sync.py` (`_sync_album`, `_sync_track`)
- Test: `tests/db/test_repository.py`, `tests/services/test_sync.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/db/test_repository.py` (extend the repository import block with `find_or_create_song_album`):

```python
def test_upsert_album_persists_and_preserves_total_tracks(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    album_id = upsert_album(con, title="Dookie", total_tracks=15,
                            external_ids={"apple": "a.1"})
    assert con.execute(
        "SELECT total_tracks FROM albums WHERE id = ?", [album_id]
    ).fetchone()[0] == 15
    # Re-upsert (same Apple id) without total_tracks must preserve the existing value.
    same = upsert_album(con, title="Dookie", external_ids={"apple": "a.1"})
    assert same == album_id
    assert con.execute(
        "SELECT total_tracks FROM albums WHERE id = ?", [album_id]
    ).fetchone()[0] == 15


def test_find_or_create_song_album_prefers_apple_row_then_reuses(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    con.execute("INSERT INTO artists (canonical_name) VALUES ('Green Day')")
    artist_id = con.execute("SELECT id FROM artists").fetchone()[0]
    # An owned album item row (has Apple id).
    owned = upsert_album(con, title="Dookie", artist_id=artist_id,
                         external_ids={"apple": "a.dookie"})
    # A song on that album links to the owned row, not a new one.
    linked = find_or_create_song_album(con, artist_id=artist_id, title="Dookie")
    assert linked == owned
    # A song on an unowned album creates exactly one row, reused on a second call.
    first = find_or_create_song_album(con, artist_id=artist_id, title="Kerplunk")
    second = find_or_create_song_album(con, artist_id=artist_id, title="Kerplunk")
    assert first == second
    assert con.execute(
        "SELECT count(*) FROM albums WHERE title = 'Kerplunk'"
    ).fetchone()[0] == 1
```

Add to `tests/services/test_sync.py` — first READ the file to reuse its fake-connector fixtures (`LibraryAlbum`/`LibrarySong` shaped objects). Add a test mirroring its existing style; the connector must yield one album `Dookie` (id `a.dookie`, track_count 2) and two songs on `Dookie`:

```python
def test_sync_links_tracks_to_album_and_persists_total_tracks():
    # Build a fake connector: album 'Dookie' (track_count=2) + two songs on it.
    # (Mirror the existing fake-connector construction in this file.)
    connector = _FakeConnector(
        albums=[_album(id="a.dookie", name="Dookie", artist_name="Green Day", track_count=2)],
        songs=[
            _song(id="s.1", name="Burnout", artist_name="Green Day", album_name="Dookie",
                  track_number=1),
            _song(id="s.2", name="Having a Blast", artist_name="Green Day",
                  album_name="Dookie", track_number=2),
        ],
        playlists=[],
    )
    con = _fresh_con()
    sync_library(connector, con, now=datetime(2026, 6, 15))
    # Album persisted with its track count.
    album_id, total = con.execute(
        "SELECT id, total_tracks FROM albums WHERE title = 'Dookie'"
    ).fetchone()
    assert total == 2
    # Both songs link to that album row.
    linked = con.execute(
        "SELECT count(*) FROM tracks WHERE album_id = ?", [album_id]
    ).fetchone()[0]
    assert linked == 2
    # Idempotent: a second sync does not create duplicate album rows.
    sync_library(connector, con, now=datetime(2026, 6, 16))
    assert con.execute(
        "SELECT count(*) FROM albums WHERE title = 'Dookie'"
    ).fetchone()[0] == 1
```

If `tests/services/test_sync.py` does not already have `_FakeConnector`/`_album`/`_song`/`_fresh_con` helpers, adapt this test to the file's actual fixture style (read it first). The behavioral assertions (total_tracks persisted, both tracks linked, no duplicate album rows on re-sync) must hold.

- [ ] **Step 2: Run to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py tests/services/test_sync.py -q -k "total_tracks or song_album or links_tracks"`
Expected: FAIL.

- [ ] **Step 3: Implement `upsert_album` total_tracks + `find_or_create_song_album`**

In `src/needledrop/db/repository.py`, modify `upsert_album` to thread `total_tracks` through. Add the parameter to the signature:

```python
def upsert_album(
    con: duckdb.DuckDBPyConnection,
    *,
    title: str,
    artist_id: int | None = None,
    release_group_mbid: str | None = None,
    release_mbid: str | None = None,
    version_class: str | None = None,
    total_tracks: int | None = None,
    external_ids: dict[str, str] | None = None,
) -> int:
```

In the `release_mbid` UPDATE branch, add `total_tracks = COALESCE(?, total_tracks)` and bind `total_tracks` (place it consistently in the params list, e.g. right after `version_class`):

```python
        if row:
            con.execute(
                "UPDATE albums SET title = ?, artist_id = COALESCE(?, artist_id), "
                "release_group_mbid = COALESCE(?, release_group_mbid), "
                "version_class = COALESCE(?, version_class), "
                "total_tracks = COALESCE(?, total_tracks), external_ids_json = ? WHERE id = ?",
                [title, artist_id, release_group_mbid, version_class, total_tracks,
                 ext_json, row[0]],
            )
            return row[0]
```

In the Apple-id UPDATE branch, likewise add `total_tracks = COALESCE(?, total_tracks)`:

```python
        if row:
            con.execute(
                "UPDATE albums SET title = ?, artist_id = COALESCE(?, artist_id), "
                "release_group_mbid = COALESCE(?, release_group_mbid), "
                "release_mbid = COALESCE(?, release_mbid), "
                "version_class = COALESCE(?, version_class), "
                "total_tracks = COALESCE(?, total_tracks), external_ids_json = ? WHERE id = ?",
                [title, artist_id, release_group_mbid, release_mbid, version_class,
                 total_tracks, ext_json, row[0]],
            )
            return row[0]
```

In the final `INSERT`, add the `total_tracks` column and value:

```python
    return con.execute(
        "INSERT INTO albums "
        "(release_group_mbid, release_mbid, artist_id, title, version_class, total_tracks, "
        "external_ids_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
        [release_group_mbid, release_mbid, artist_id, title, version_class, total_tracks,
         ext_json],
    ).fetchone()[0]
```

Then append `find_or_create_song_album` (after `upsert_album`):

```python
def find_or_create_song_album(
    con: duckdb.DuckDBPyConnection, *, artist_id: int | None, title: str
) -> int:
    """Resolve the canonical album id for a library song, by (artist, title).

    Prefers an existing album already tied to an Apple library-album item (so songs
    fold into the album you own); otherwise reuses an existing song-only row or
    inserts a minimal one. This is a last-resort, name-based link used ONLY for
    song→album association — it deliberately does NOT touch upsert_album's
    edition-dedup (release_mbid / Apple-id), so distinct editions stay separate.
    """
    row = con.execute(
        "SELECT id FROM albums "
        "WHERE (artist_id = ? OR (artist_id IS NULL AND ? IS NULL)) AND title = ? "
        "ORDER BY (json_extract_string(external_ids_json, '$.apple') IS NOT NULL) DESC, id "
        "LIMIT 1",
        [artist_id, artist_id, title],
    ).fetchone()
    if row:
        return row[0]
    return con.execute(
        "INSERT INTO albums (artist_id, title) VALUES (?, ?) RETURNING id",
        [artist_id, title],
    ).fetchone()[0]
```

- [ ] **Step 4: Implement the sync change**

In `src/needledrop/services/sync.py`, update `_sync_album` to persist the track count (pass `total_tracks=album.track_count` to `upsert_album`):

```python
    canonical_id = upsert_album(
        con,
        title=album.name,
        artist_id=artist_id,
        release_group_mbid=result.mbid,
        version_class=classify_album_version(album.name).value,
        total_tracks=album.track_count,
        external_ids={"apple": album.id},
    )
```

Update `_sync_track` to link the album and persist structural fields. Import `find_or_create_song_album` (add it to the `from needledrop.db.repository import (...)` block at the top of sync.py), then:

```python
def _sync_track(con, song, *, now, service) -> None:
    artist_id = (
        upsert_artist(con, canonical_name=song.artist_name) if song.artist_name else None
    )
    album_id = (
        find_or_create_song_album(con, artist_id=artist_id, title=song.album_name)
        if song.album_name
        else None
    )
    result = match_track(
        con, TrackQuery(title=song.name, artist_name=song.artist_name, isrc=song.isrc)
    )
    canonical_id = upsert_track(
        con,
        title=song.name,
        album_id=album_id,
        artist_id=artist_id,
        recording_mbid=result.mbid,
        isrc=song.isrc,
        disc_number=song.disc_number,
        track_number=song.track_number,
        duration_ms=song.duration_ms,
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

Note: the album loop runs before the song loop in `sync_library`, so the Apple-id-bearing album row already exists when songs are linked. `upsert_track` already dedups by `recording_mbid`/Apple-id; passing `album_id` updates the canonical track's link on re-sync without creating new rows.

- [ ] **Step 5: Run tests + full suite**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py tests/services/test_sync.py -q`
Expected: PASS.

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest -q`
Expected: PASS (verify the existing sync tests still pass with the linkage change).

- [ ] **Step 6: Lint + commit**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check src/needledrop/db/repository.py src/needledrop/services/sync.py tests/`
Expected: clean.

```bash
git add src/needledrop/db/repository.py src/needledrop/services/sync.py tests/db/test_repository.py tests/services/test_sync.py
git commit -m "feat: link library songs to albums and persist album track counts in sync"
```

---

## Task 3: Duplicate-track analysis

**Files:**
- Create: `src/needledrop/analysis/duplicate_tracks.py`
- Test: `tests/analysis/test_duplicate_tracks.py`

- [ ] **Step 1: Write the failing test**

Create `tests/analysis/test_duplicate_tracks.py`:

```python
from needledrop.analysis.duplicate_tracks import find_duplicate_tracks
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.models.enums import FindingType


def _add_track(con, *, service_item_id, title, recording_mbid=None, isrc=None):
    if recording_mbid is not None:
        con.execute(
            "INSERT INTO tracks (title, recording_mbid) VALUES (?, ?)", [title, recording_mbid]
        )
    else:
        con.execute("INSERT INTO tracks (title, isrc) VALUES (?, ?)", [title, isrc])
    track_id = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', ?, 'track', ?, 'present')",
        [service_item_id, track_id],
    )


def test_find_duplicate_tracks_groups_by_recording_mbid():
    con = connect(":memory:")
    init_schema(con)
    _add_track(con, service_item_id="s.1", title="Creep", recording_mbid="rec-creep")
    _add_track(con, service_item_id="s.2", title="Creep", recording_mbid="rec-creep")
    _add_track(con, service_item_id="s.3", title="No Surprises", recording_mbid="rec-ns")
    findings = find_duplicate_tracks(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.DUPLICATE_TRACK
    assert "2 copies" in findings[0].description


def test_find_duplicate_tracks_groups_unmatched_by_isrc():
    con = connect(":memory:")
    init_schema(con)
    _add_track(con, service_item_id="s.1", title="Creep", isrc="GBAYE9900001")
    _add_track(con, service_item_id="s.2", title="Creep", isrc="GBAYE9900001")
    findings = find_duplicate_tracks(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.DUPLICATE_TRACK


def test_find_duplicate_tracks_ignores_singletons_and_unidentified():
    con = connect(":memory:")
    init_schema(con)
    _add_track(con, service_item_id="s.1", title="Creep", recording_mbid="rec-creep")
    _add_track(con, service_item_id="s.2", title="Unknown")  # no mbid/isrc -> not grouped
    assert find_duplicate_tracks(con) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_duplicate_tracks.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/needledrop/analysis/duplicate_tracks.py`:

```python
"""Duplicate-track detection: the same recording owned more than once."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def find_duplicate_tracks(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Owned tracks sharing a recording identity (recording MBID, else ISRC)."""
    rows = con.execute(
        "WITH owned AS ("
        "  SELECT tr.id AS track_id, tr.title AS title, "
        "    CASE WHEN tr.recording_mbid IS NOT NULL THEN 'rec:' || tr.recording_mbid "
        "         WHEN tr.isrc IS NOT NULL THEN 'isrc:' || tr.isrc END AS key "
        "  FROM library_items li JOIN tracks tr ON li.canonical_id = tr.id "
        "  WHERE li.status = 'present' AND li.item_type = 'track') "
        "SELECT key, count(*) AS n, min(title) AS title, list(track_id) AS track_ids "
        "FROM owned WHERE key IS NOT NULL "
        "GROUP BY key HAVING count(*) > 1 "
        "ORDER BY key"
    ).fetchall()
    findings: list[CleanupFinding] = []
    for key, n, title, track_ids in rows:
        findings.append(
            CleanupFinding(
                finding_type=FindingType.DUPLICATE_TRACK,
                severity=FindingSeverity.LOW,
                entity_id=track_ids[0],
                description=f"You own {n} copies of the track '{title}'.",
                recommendation=Recommendation(
                    action="review_duplicate_tracks",
                    detail=f"{n} copies share identity {key}.",
                    payload={"identity": key, "track_ids": track_ids},
                ),
            )
        )
    return findings
```

- [ ] **Step 4: Run to verify pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_duplicate_tracks.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
git add src/needledrop/analysis/duplicate_tracks.py tests/analysis/test_duplicate_tracks.py
git commit -m "feat: add duplicate-track detection"
```

---

## Task 4: Partial-album analysis

**Files:**
- Create: `src/needledrop/analysis/partial_albums.py`
- Test: `tests/analysis/test_partial_albums.py`

Definition: for an owned album item with a known `total_tracks`, count its owned tracks (present track items whose canonical `tracks.album_id` points at the album). If `0 < owned < total_tracks`, it's a partial album. (`owned == 0` is excluded — that indicates no linked songs, typically a name-link miss, not a partial album.)

- [ ] **Step 1: Write the failing test**

Create `tests/analysis/test_partial_albums.py`:

```python
from needledrop.analysis.partial_albums import find_partial_albums
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.models.enums import FindingType


def _own_album(con, *, title, total_tracks, apple_id):
    con.execute(
        "INSERT INTO albums (title, total_tracks, external_ids_json) "
        "VALUES (?, ?, json_object('apple', ?))",
        [title, total_tracks, apple_id],
    )
    album_id = con.execute("SELECT id FROM albums WHERE title = ?", [title]).fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', ?, 'album', ?, 'present')",
        [apple_id, album_id],
    )
    return album_id


def _own_track_on(con, *, album_id, title, service_item_id):
    con.execute("INSERT INTO tracks (title, album_id) VALUES (?, ?)", [title, album_id])
    track_id = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', ?, 'track', ?, 'present')",
        [service_item_id, track_id],
    )


def test_find_partial_albums_flags_incomplete_album():
    con = connect(":memory:")
    init_schema(con)
    album_id = _own_album(con, title="Dookie", total_tracks=3, apple_id="a.dookie")
    _own_track_on(con, album_id=album_id, title="Burnout", service_item_id="s.1")
    _own_track_on(con, album_id=album_id, title="Having a Blast", service_item_id="s.2")
    findings = find_partial_albums(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.PARTIAL_ALBUM
    assert "2 of 3" in findings[0].description


def test_find_partial_albums_ignores_complete_and_empty():
    con = connect(":memory:")
    init_schema(con)
    complete = _own_album(con, title="EP", total_tracks=2, apple_id="a.ep")
    _own_track_on(con, album_id=complete, title="A", service_item_id="s.1")
    _own_track_on(con, album_id=complete, title="B", service_item_id="s.2")
    # An owned album with NO linked tracks (owned==0) is not "partial".
    _own_album(con, title="Empty", total_tracks=5, apple_id="a.empty")
    # An album with unknown total_tracks is skipped.
    unknown = _own_album(con, title="Mystery", total_tracks=None, apple_id="a.mystery")
    _own_track_on(con, album_id=unknown, title="X", service_item_id="s.3")
    assert find_partial_albums(con) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_partial_albums.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/needledrop/analysis/partial_albums.py`:

```python
"""Partial-album detection: you own only some tracks of an album you added."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def find_partial_albums(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Owned album items where the owned linked-track count is below total_tracks."""
    rows = con.execute(
        "SELECT a.id, a.title, a.total_tracks, ("
        "  SELECT count(*) FROM library_items lit JOIN tracks t ON lit.canonical_id = t.id "
        "  WHERE lit.status = 'present' AND lit.item_type = 'track' AND t.album_id = a.id"
        ") AS owned "
        "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "WHERE li.status = 'present' AND li.item_type = 'album' "
        "AND a.total_tracks IS NOT NULL AND a.total_tracks > 0 "
        "ORDER BY a.title"
    ).fetchall()
    findings: list[CleanupFinding] = []
    for album_id, title, total, owned in rows:
        if 0 < owned < total:
            findings.append(
                CleanupFinding(
                    finding_type=FindingType.PARTIAL_ALBUM,
                    severity=FindingSeverity.MEDIUM,
                    entity_id=album_id,
                    description=f"You own {owned} of {total} tracks from '{title}'.",
                    recommendation=Recommendation(
                        action="complete_album",
                        detail=f"{total - owned} track(s) missing.",
                        payload={"album_id": album_id, "owned": owned, "total_tracks": total},
                    ),
                )
            )
    return findings
```

- [ ] **Step 4: Run to verify pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_partial_albums.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
git add src/needledrop/analysis/partial_albums.py tests/analysis/test_partial_albums.py
git commit -m "feat: add partial-album detection"
```

---

## Task 5: Single-track-replaced-by-album analysis

**Files:**
- Create: `src/needledrop/analysis/single_replaced.py`
- Test: `tests/analysis/test_single_replaced.py`

Definition: an owned standalone track (its `tracks.album_id` is NULL or points at an album you do NOT own as a library item) whose `recording_mbid` is ALSO owned via a track that IS on an owned album item. The standalone copy is redundant.

- [ ] **Step 1: Write the failing test**

Create `tests/analysis/test_single_replaced.py`:

```python
from needledrop.analysis.single_replaced import find_single_replaced
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.models.enums import FindingType


def _own_album_item(con, *, title, apple_id):
    con.execute(
        "INSERT INTO albums (title, external_ids_json) VALUES (?, json_object('apple', ?))",
        [title, apple_id],
    )
    album_id = con.execute("SELECT id FROM albums WHERE title = ?", [title]).fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', ?, 'album', ?, 'present')",
        [apple_id, album_id],
    )
    return album_id


def _own_track(con, *, service_item_id, title, recording_mbid, album_id=None):
    con.execute(
        "INSERT INTO tracks (title, recording_mbid, album_id) VALUES (?, ?, ?)",
        [title, recording_mbid, album_id],
    )
    track_id = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, status) "
        "VALUES ('apple_music', ?, 'track', ?, 'present')",
        [service_item_id, track_id],
    )
    return track_id


def test_find_single_replaced_flags_standalone_also_on_owned_album():
    con = connect(":memory:")
    init_schema(con)
    album_id = _own_album_item(con, title="Dookie", apple_id="a.dookie")
    # The album's own copy of the recording.
    _own_track(con, service_item_id="s.album", title="Basket Case",
               recording_mbid="rec-bc", album_id=album_id)
    # A standalone single of the same recording (no album link).
    single = _own_track(con, service_item_id="s.single", title="Basket Case",
                        recording_mbid="rec-bc", album_id=None)
    findings = find_single_replaced(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.SINGLE_REPLACED_BY_ALBUM
    assert findings[0].entity_id == single


def test_find_single_replaced_ignores_when_no_owned_album_copy():
    con = connect(":memory:")
    init_schema(con)
    # Only a standalone copy exists; not on any owned album -> not redundant.
    _own_track(con, service_item_id="s.single", title="Basket Case",
               recording_mbid="rec-bc", album_id=None)
    assert find_single_replaced(con) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_single_replaced.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/needledrop/analysis/single_replaced.py`:

```python
"""Single-replaced-by-album detection: a standalone single you also own on an album."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def find_single_replaced(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Owned standalone tracks whose recording is also owned on an album you have."""
    rows = con.execute(
        "WITH owned_albums AS ("
        "  SELECT a.id FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "  WHERE li.status = 'present' AND li.item_type = 'album'), "
        "owned_tracks AS ("
        "  SELECT tr.id AS track_id, tr.title AS title, tr.recording_mbid AS rec, "
        "    tr.album_id AS album_id "
        "  FROM library_items li JOIN tracks tr ON li.canonical_id = tr.id "
        "  WHERE li.status = 'present' AND li.item_type = 'track' "
        "    AND tr.recording_mbid IS NOT NULL) "
        "SELECT ot.track_id, ot.title, ot.rec FROM owned_tracks ot "
        "WHERE (ot.album_id IS NULL OR ot.album_id NOT IN (SELECT id FROM owned_albums)) "
        "AND EXISTS ("
        "  SELECT 1 FROM owned_tracks alb "
        "  WHERE alb.rec = ot.rec AND alb.album_id IN (SELECT id FROM owned_albums)) "
        "ORDER BY ot.track_id"
    ).fetchall()
    findings: list[CleanupFinding] = []
    for track_id, title, rec in rows:
        findings.append(
            CleanupFinding(
                finding_type=FindingType.SINGLE_REPLACED_BY_ALBUM,
                severity=FindingSeverity.LOW,
                entity_id=track_id,
                description=(
                    f"You own a standalone copy of '{title}' that's also on an album you own."
                ),
                recommendation=Recommendation(
                    action="remove_redundant_single",
                    detail="The album already includes this track.",
                    payload={"track_id": track_id, "recording_mbid": rec},
                ),
            )
        )
    return findings
```

- [ ] **Step 4: Run to verify pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_single_replaced.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
git add src/needledrop/analysis/single_replaced.py tests/analysis/test_single_replaced.py
git commit -m "feat: add single-replaced-by-album detection"
```

---

## Task 6: Wire into cleanup scan + MCP tools

**Files:**
- Modify: `src/needledrop/services/cleanup.py`
- Modify: `src/needledrop/mcp_server.py`
- Test: `tests/services/test_cleanup.py`, `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_cleanup.py` (read the file first to reuse its fixtures/imports). Add a test that seeds a duplicate-track scenario and asserts `run_cleanup_scan` returns a `duplicate_track` count:

```python
def test_run_cleanup_scan_includes_duplicate_tracks(tmp_path):
    from datetime import datetime

    from needledrop.db.duckdb_store import connect, init_schema
    from needledrop.services.cleanup import run_cleanup_scan
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    for sid in ("s.1", "s.2"):
        con.execute("INSERT INTO tracks (title, recording_mbid) VALUES ('Creep', 'rec-creep')")
        track_id = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
        con.execute(
            "INSERT INTO library_items "
            "(service, service_item_id, item_type, canonical_id, status) "
            "VALUES ('apple_music', ?, 'track', ?, 'present')",
            [sid, track_id],
        )
    counts = run_cleanup_scan(con, now=datetime(2026, 6, 15))
    assert counts.get("duplicate_track") == 1
```

Add to `tests/test_mcp_server.py`:

```python
def test_find_duplicate_tracks_tool_reports_dupes():
    con = _fresh_con()
    for sid in ("s.1", "s.2"):
        con.execute("INSERT INTO tracks (title, recording_mbid) VALUES ('Creep', 'rec-creep')")
        track_id = con.execute("SELECT max(id) FROM tracks").fetchone()[0]
        con.execute(
            "INSERT INTO library_items "
            "(service, service_item_id, item_type, canonical_id, status) "
            "VALUES ('apple_music', ?, 'track', ?, 'present')",
            [sid, track_id],
        )
    findings = _call(create_server(con), "find_duplicate_tracks")
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "duplicate_track"
```

And extend `test_server_exposes_expected_tools` to add `"find_duplicate_tracks"`, `"find_partial_albums"`, `"find_single_replaced"` to the asserted set.

- [ ] **Step 2: Run to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/services/test_cleanup.py tests/test_mcp_server.py -q -k "duplicate_track or partial or single_replaced or expected_tools"`
Expected: FAIL.

- [ ] **Step 3: Wire the cleanup scan**

In `src/needledrop/services/cleanup.py`, add imports and include the three analyses in `run_cleanup_scan`:

```python
from needledrop.analysis.duplicate_tracks import find_duplicate_tracks
from needledrop.analysis.partial_albums import find_partial_albums
from needledrop.analysis.single_replaced import find_single_replaced
```

```python
    findings = [
        *find_duplicate_albums(con),
        *find_compilation_pollution(con),
        *find_missing_core_albums(con),
        *find_duplicate_tracks(con),
        *find_partial_albums(con),
        *find_single_replaced(con),
    ]
```

- [ ] **Step 4: Add the MCP tools**

In `src/needledrop/mcp_server.py`, add aliased imports (one statement per symbol, matching the file's style):

```python
from needledrop.analysis.duplicate_tracks import find_duplicate_tracks as _find_duplicate_tracks
from needledrop.analysis.partial_albums import find_partial_albums as _find_partial_albums
from needledrop.analysis.single_replaced import find_single_replaced as _find_single_replaced
```

Register three tools inside `create_server` (after `find_missing_core_albums`, before `generate_cleanup_report`):

```python
    @mcp.tool
    def find_duplicate_tracks() -> list[dict]:
        """Tracks you own more than one copy of (same recording identity)."""
        return [f.model_dump(mode="json") for f in _find_duplicate_tracks(con)]

    @mcp.tool
    def find_partial_albums() -> list[dict]:
        """Albums you added but own only some of the tracks from."""
        return [f.model_dump(mode="json") for f in _find_partial_albums(con)]

    @mcp.tool
    def find_single_replaced() -> list[dict]:
        """Standalone singles you also own on a full album (redundant)."""
        return [f.model_dump(mode="json") for f in _find_single_replaced(con)]
```

- [ ] **Step 5: Run tests + full suite + lint (CI-parity gate)**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest -q`
Expected: PASS (all tests).

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/services/cleanup.py src/needledrop/mcp_server.py tests/services/test_cleanup.py tests/test_mcp_server.py
git commit -m "feat: wire new analyses into cleanup scan and MCP tools"
```

---

## Self-Review (completed)

**Spec coverage:** All three target analyses delivered — duplicate-track (Task 3), partial-album (Task 4), single-replaced (Task 5) — on top of the track→album linkage + `total_tracks` persistence (Tasks 1–2), wired into `run_cleanup_scan` and exposed as MCP tools (Task 6).

**Data-model safety:** `total_tracks` is added to baseline `schema.sql` (so `init_schema`-only tests have it) AND via an idempotent `ADD COLUMN IF NOT EXISTS` migration (so pre-existing real DBs are upgraded) — verified non-conflicting. The song→album link uses a dedicated `find_or_create_song_album` (prefer Apple-id-bearing row → reuse song-only row → insert), leaving `upsert_album`'s edition dedup (release_mbid/Apple-id) untouched, so the duplicate-album analysis is unaffected. Idempotency across re-syncs is asserted (no duplicate album rows).

**Placeholder scan:** No TBD/TODO. Every code step shows complete code. The one place adapted to the file's existing fixtures (the sync test) names the exact behavioral assertions that must hold.

**Type consistency:** All three `find_*` return `list[CleanupFinding]`; MCP tools serialize via `model_dump(mode="json")`; cleanup counts use the `FindingType` string values (`duplicate_track`, `partial_album`, `single_replaced_by_album`). `upsert_album` gains `total_tracks: int | None = None`, threaded through all three branches; `_sync_album` passes `album.track_count`; `find_or_create_song_album(con, *, artist_id, title)` matches its call site in `_sync_track`. Partial-album's `owned`/`total` and single-replaced's owned-album-set logic match their tests.

**Edge cases:** duplicate-track ignores singletons and tracks with neither MBID nor ISRC; partial-album excludes complete albums, `owned==0` (link misses), and unknown `total_tracks`; single-replaced requires both a standalone copy and an owned-album copy of the same recording, and yields nothing when no owned album contains the recording (incl. the empty owned-albums set).

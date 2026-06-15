# NeedleDrop Persistence Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the DuckDB persistence layer the sync service will use — upserting canonical artists/albums/tracks, recording library-item presence + match results, persisting review-queue candidates, managing sync runs (full-snapshot semantics), and read helpers for summaries/listings.

**Architecture:** A `db/repository.py` module of focused functions over the canonical schema from Plan 1 (kept separate from `db/duckdb_store.py`, which owns connection + schema lifecycle — different responsibility). Canonical entities are deduped by MBID then by Apple external id, so one canonical row exists per owned item with its MusicBrainz ids attached; `library_items` carry the match result and presence status. Everything is exercised against a real in-memory DuckDB created via `init_schema` (no fixtures-of-fixtures, no network, no Docker). Timestamps are passed in explicitly for deterministic tests.

**Tech Stack:** Python 3.13, DuckDB (`INSERT ... RETURNING`, `ON CONFLICT`, `json_extract_string`), stdlib `json`/`datetime`. Builds on merged Plans 1–4 (canonical schema, models, matcher).

**Plan series:** Plan 5 of 8. The original "sync" scope split: this plan is the persistence layer; the next plan wires the Apple connector's `include=catalog` enrichment, the sync orchestration (pull → enrich → match → persist via these functions → snapshot), `diff_sync`, and the `needledrop sync` CLI. Design spec: `docs/superpowers/specs/2026-06-15-needledrop-mcp-design.md` (§4.2–4.5, §6.7).

---

## Environment notes for implementers

- Python via the project env interpreter: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python` (NOT `mamba run`). Tests e.g.: `... -m pytest tests/db/test_repository.py -v`.
- CI-parity gate before "done": `... -m pytest` green AND `... -m ruff check .` clean. ruff line-length 100 — wrap long lines; don't quote forward-ref annotations (UP037).
- No new dependencies.

## Data-model decisions this plan locks in (from spec §4.2–4.5)

- **Canonical dedup:** one `artists`/`albums`/`tracks` row per real-world entity. Dedup key precedence: **MBID** (`artists.mbid` / `albums.release_group_mbid` / `tracks.recording_mbid`) when matched, else the **Apple external id** in `external_ids_json` (`$.apple`). This means each owned Apple album becomes one canonical album carrying its `release_group_mbid` (shared across editions) — so the dedup analysis (Plan 7) groups `library_items`' albums by `release_group_mbid`.
- **`library_items`** is the per-service presence record (`UNIQUE (service, service_item_id, item_type)` from Plan 1). It holds `canonical_id`, `match_confidence`, `match_method`, `added_at` (first seen), `last_seen_at` (this run), `status`.
- **Full-snapshot sync:** every run stamps `last_seen_at` on items it sees; afterward, items whose `last_seen_at` predates the run are marked `removed`.

---

## File Structure

```text
src/needledrop/db/repository.py   # NEW — entity CRUD/upserts, library items, sync runs
tests/db/test_repository.py       # NEW
```

---

### Task 1: Upsert artist

**Files:**
- Create: `src/needledrop/db/repository.py`
- Test: `tests/db/test_repository.py`

- [ ] **Step 1: Write the failing test**

`tests/db/test_repository.py`:

```python
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import upsert_artist


def _con():
    con = connect(":memory:")
    init_schema(con)
    return con


def test_upsert_artist_inserts_and_returns_id():
    con = _con()
    artist_id = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r", sort_name="Radiohead")
    assert isinstance(artist_id, int)
    row = con.execute("SELECT canonical_name, mbid FROM artists WHERE id = ?", [artist_id]).fetchone()
    assert row == ("Radiohead", "mbid-r")


def test_upsert_artist_dedupes_by_mbid():
    con = _con()
    first = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    again = upsert_artist(con, canonical_name="Radiohead (updated)", mbid="mbid-r")
    assert again == first
    assert con.execute("SELECT count(*) FROM artists").fetchone()[0] == 1
    assert con.execute("SELECT canonical_name FROM artists").fetchone()[0] == "Radiohead (updated)"


def test_upsert_artist_dedupes_by_apple_id_when_no_mbid():
    con = _con()
    first = upsert_artist(con, canonical_name="Radiohead", external_ids={"apple": "A1"})
    again = upsert_artist(con, canonical_name="Radiohead", external_ids={"apple": "A1"}, mbid="mbid-r")
    assert again == first
    assert con.execute("SELECT count(*) FROM artists").fetchone()[0] == 1
    # The later call backfills the MBID.
    assert con.execute("SELECT mbid FROM artists").fetchone()[0] == "mbid-r"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.db.repository'`.

- [ ] **Step 3: Implement**

`src/needledrop/db/repository.py`:

```python
"""Persistence for canonical entities, library items, and sync runs (DuckDB).

Separate from db/duckdb_store.py (connection + schema lifecycle): this module is
the entity/CRUD layer the sync service drives.
"""

from __future__ import annotations

import json

import duckdb


def _dump_external_ids(external_ids: dict[str, str] | None) -> str:
    return json.dumps(external_ids or {}, sort_keys=True)


def upsert_artist(
    con: duckdb.DuckDBPyConnection,
    *,
    canonical_name: str,
    mbid: str | None = None,
    sort_name: str | None = None,
    external_ids: dict[str, str] | None = None,
) -> int:
    """Insert or update an artist, deduping by MBID then Apple external id. Returns its id."""
    external_ids = external_ids or {}
    ext_json = _dump_external_ids(external_ids)

    if mbid:
        row = con.execute("SELECT id FROM artists WHERE mbid = ?", [mbid]).fetchone()
        if row:
            con.execute(
                "UPDATE artists SET canonical_name = ?, sort_name = ?, external_ids_json = ? "
                "WHERE id = ?",
                [canonical_name, sort_name, ext_json, row[0]],
            )
            return row[0]

    apple_id = external_ids.get("apple")
    if apple_id:
        row = con.execute(
            "SELECT id FROM artists WHERE json_extract_string(external_ids_json, '$.apple') = ?",
            [apple_id],
        ).fetchone()
        if row:
            con.execute(
                "UPDATE artists SET canonical_name = ?, sort_name = ?, "
                "mbid = COALESCE(?, mbid), external_ids_json = ? WHERE id = ?",
                [canonical_name, sort_name, mbid, ext_json, row[0]],
            )
            return row[0]

    return con.execute(
        "INSERT INTO artists (mbid, canonical_name, sort_name, external_ids_json) "
        "VALUES (?, ?, ?, ?) RETURNING id",
        [mbid, canonical_name, sort_name, ext_json],
    ).fetchone()[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/db/repository.py tests/db/test_repository.py
git commit -m "feat: add artist upsert"
```

---

### Task 2: Upsert album and track

**Files:**
- Modify: `src/needledrop/db/repository.py`
- Test: `tests/db/test_repository.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/db/test_repository.py`:

```python
from needledrop.db.repository import upsert_album, upsert_track


def test_upsert_album_dedupes_by_apple_id_and_backfills_mbid():
    con = _con()
    artist_id = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    first = upsert_album(
        con, title="OK Computer", artist_id=artist_id, external_ids={"apple": "alb1"}
    )
    again = upsert_album(
        con,
        title="OK Computer",
        artist_id=artist_id,
        release_group_mbid="rg-okc",
        version_class="standard",
        external_ids={"apple": "alb1"},
    )
    assert again == first
    assert con.execute("SELECT count(*) FROM albums").fetchone()[0] == 1
    row = con.execute(
        "SELECT release_group_mbid, version_class FROM albums WHERE id = ?", [first]
    ).fetchone()
    assert row == ("rg-okc", "standard")


def test_upsert_album_dedupes_by_release_mbid():
    con = _con()
    artist_id = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    a = upsert_album(con, title="OK Computer", artist_id=artist_id, release_mbid="rel-okc")
    b = upsert_album(con, title="OK Computer", artist_id=artist_id, release_mbid="rel-okc")
    assert a == b
    assert con.execute("SELECT count(*) FROM albums").fetchone()[0] == 1


def test_upsert_album_keeps_distinct_editions_of_one_release_group():
    # Standard + Deluxe share a release-group but are separate owned editions.
    con = _con()
    artist_id = upsert_artist(con, canonical_name="Green Day", mbid="mbid-gd")
    standard = upsert_album(
        con, title="Dookie", artist_id=artist_id, release_group_mbid="rg-dookie",
        version_class="standard", external_ids={"apple": "alb-std"},
    )
    deluxe = upsert_album(
        con, title="Dookie (Deluxe)", artist_id=artist_id, release_group_mbid="rg-dookie",
        version_class="deluxe", external_ids={"apple": "alb-dlx"},
    )
    assert standard != deluxe
    assert con.execute("SELECT count(*) FROM albums").fetchone()[0] == 2


def test_upsert_track_inserts_with_recording_mbid():
    con = _con()
    artist_id = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    album_id = upsert_album(con, title="OK Computer", artist_id=artist_id)
    track_id = upsert_track(
        con,
        title="Karma Police",
        album_id=album_id,
        artist_id=artist_id,
        recording_mbid="rec-karma",
        isrc="GBAYE9700116",
        external_ids={"apple": "trk1"},
    )
    row = con.execute(
        "SELECT recording_mbid, isrc FROM tracks WHERE id = ?", [track_id]
    ).fetchone()
    assert row == ("rec-karma", "GBAYE9700116")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `ImportError: cannot import name 'upsert_album'`.

- [ ] **Step 3: Implement (append to `repository.py`)**

```python
def upsert_album(
    con: duckdb.DuckDBPyConnection,
    *,
    title: str,
    artist_id: int | None = None,
    release_group_mbid: str | None = None,
    release_mbid: str | None = None,
    version_class: str | None = None,
    external_ids: dict[str, str] | None = None,
) -> int:
    """Insert or update an album, deduping by release (edition) MBID then Apple external id.

    `release_group_mbid` is the version-cluster grouping attribute, NOT a dedup key:
    distinct editions share a release-group but stay separate canonical rows so each
    keeps its own version_class (the duplicate-album analysis groups by release_group_mbid).
    """
    external_ids = external_ids or {}
    ext_json = _dump_external_ids(external_ids)

    if release_mbid:
        row = con.execute(
            "SELECT id FROM albums WHERE release_mbid = ?", [release_mbid]
        ).fetchone()
        if row:
            con.execute(
                "UPDATE albums SET title = ?, artist_id = COALESCE(?, artist_id), "
                "release_group_mbid = COALESCE(?, release_group_mbid), "
                "version_class = COALESCE(?, version_class), external_ids_json = ? WHERE id = ?",
                [title, artist_id, release_group_mbid, version_class, ext_json, row[0]],
            )
            return row[0]

    apple_id = external_ids.get("apple")
    if apple_id:
        row = con.execute(
            "SELECT id FROM albums WHERE json_extract_string(external_ids_json, '$.apple') = ?",
            [apple_id],
        ).fetchone()
        if row:
            con.execute(
                "UPDATE albums SET title = ?, artist_id = COALESCE(?, artist_id), "
                "release_group_mbid = COALESCE(?, release_group_mbid), "
                "release_mbid = COALESCE(?, release_mbid), "
                "version_class = COALESCE(?, version_class), external_ids_json = ? WHERE id = ?",
                [title, artist_id, release_group_mbid, release_mbid, version_class, ext_json, row[0]],
            )
            return row[0]

    return con.execute(
        "INSERT INTO albums "
        "(release_group_mbid, release_mbid, artist_id, title, version_class, external_ids_json) "
        "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
        [release_group_mbid, release_mbid, artist_id, title, version_class, ext_json],
    ).fetchone()[0]


def upsert_track(
    con: duckdb.DuckDBPyConnection,
    *,
    title: str,
    album_id: int | None = None,
    artist_id: int | None = None,
    recording_mbid: str | None = None,
    isrc: str | None = None,
    disc_number: int | None = None,
    track_number: int | None = None,
    duration_ms: int | None = None,
    external_ids: dict[str, str] | None = None,
) -> int:
    """Insert or update a track, deduping by recording MBID then Apple external id."""
    external_ids = external_ids or {}
    ext_json = _dump_external_ids(external_ids)

    if recording_mbid:
        row = con.execute(
            "SELECT id FROM tracks WHERE recording_mbid = ?", [recording_mbid]
        ).fetchone()
        if row:
            con.execute(
                "UPDATE tracks SET title = ?, album_id = COALESCE(?, album_id), "
                "artist_id = COALESCE(?, artist_id), isrc = COALESCE(?, isrc), "
                "external_ids_json = ? WHERE id = ?",
                [title, album_id, artist_id, isrc, ext_json, row[0]],
            )
            return row[0]

    apple_id = external_ids.get("apple")
    if apple_id:
        row = con.execute(
            "SELECT id FROM tracks WHERE json_extract_string(external_ids_json, '$.apple') = ?",
            [apple_id],
        ).fetchone()
        if row:
            con.execute(
                "UPDATE tracks SET title = ?, album_id = COALESCE(?, album_id), "
                "artist_id = COALESCE(?, artist_id), recording_mbid = COALESCE(?, recording_mbid), "
                "isrc = COALESCE(?, isrc), external_ids_json = ? WHERE id = ?",
                [title, album_id, artist_id, recording_mbid, isrc, ext_json, row[0]],
            )
            return row[0]

    return con.execute(
        "INSERT INTO tracks "
        "(recording_mbid, album_id, artist_id, title, isrc, disc_number, track_number, "
        "duration_ms, external_ids_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        [recording_mbid, album_id, artist_id, title, isrc, disc_number, track_number,
         duration_ms, ext_json],
    ).fetchone()[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: PASS (6 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/db/repository.py tests/db/test_repository.py
git commit -m "feat: add album and track upserts"
```

---

### Task 3: Record library item

**Files:**
- Modify: `src/needledrop/db/repository.py`
- Test: `tests/db/test_repository.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/db/test_repository.py`:

```python
from datetime import datetime

from needledrop.db.repository import record_library_item


def test_record_library_item_inserts_present():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    item_id = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album",
        canonical_id=None, match_confidence=None, match_method="none", seen_at=t,
    )
    row = con.execute(
        "SELECT status, added_at, last_seen_at, match_method FROM library_items WHERE id = ?",
        [item_id],
    ).fetchone()
    assert row[0] == "present"
    assert row[1] == t and row[2] == t
    assert row[3] == "none"


def test_record_library_item_upserts_preserving_added_at():
    con = _con()
    t1 = datetime(2026, 6, 1, 10, 0, 0)
    t2 = datetime(2026, 6, 15, 12, 0, 0)
    first = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album", seen_at=t1,
    )
    again = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album",
        canonical_id=42, match_confidence=1.0, match_method="upc", seen_at=t2,
    )
    assert again == first
    row = con.execute(
        "SELECT added_at, last_seen_at, canonical_id, match_confidence, match_method "
        "FROM library_items WHERE id = ?",
        [first],
    ).fetchone()
    assert row[0] == t1          # added_at preserved
    assert row[1] == t2          # last_seen_at advanced
    assert row[2] == 42 and row[3] == 1.0 and row[4] == "upc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `ImportError: cannot import name 'record_library_item'`.

- [ ] **Step 3: Implement (append to `repository.py`)**

```python
from datetime import datetime


def record_library_item(
    con: duckdb.DuckDBPyConnection,
    *,
    service: str,
    service_item_id: str,
    item_type: str,
    seen_at: datetime,
    canonical_id: int | None = None,
    match_confidence: float | None = None,
    match_method: str = "none",
) -> int:
    """Insert or update a library item (present), preserving added_at across runs."""
    return con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_confidence, match_method, "
        "added_at, last_seen_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'present') "
        "ON CONFLICT (service, service_item_id, item_type) DO UPDATE SET "
        "canonical_id = excluded.canonical_id, match_confidence = excluded.match_confidence, "
        "match_method = excluded.match_method, last_seen_at = excluded.last_seen_at, "
        "status = 'present' "
        "RETURNING id",
        [service, service_item_id, item_type, canonical_id, match_confidence, match_method,
         seen_at, seen_at],
    ).fetchone()[0]
```

Move the `from datetime import datetime` import to the top of `repository.py` with the other imports (so it isn't mid-file). The import block should read:

```python
from __future__ import annotations

import json
from datetime import datetime

import duckdb
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: PASS (8 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/db/repository.py tests/db/test_repository.py
git commit -m "feat: add library item recording with snapshot upsert"
```

---

### Task 4: Save match candidates

**Files:**
- Modify: `src/needledrop/db/repository.py`
- Test: `tests/db/test_repository.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/db/test_repository.py`:

```python
from needledrop.db.repository import save_match_candidates


def test_save_match_candidates_replaces_pending():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    item_id = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album", seen_at=t,
    )
    save_match_candidates(con, library_item_id=item_id, candidates=[
        {"candidate_mbid": "rg-1", "candidate_kind": "release_group", "score": 0.8, "method": "fuzzy"},
        {"candidate_mbid": "rg-2", "candidate_kind": "release_group", "score": 0.6, "method": "fuzzy"},
    ])
    assert con.execute(
        "SELECT count(*) FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchone()[0] == 2

    # Re-saving replaces the prior pending set rather than appending.
    save_match_candidates(con, library_item_id=item_id, candidates=[
        {"candidate_mbid": "rg-3", "candidate_kind": "release_group", "score": 0.9, "method": "fuzzy"},
    ])
    rows = con.execute(
        "SELECT candidate_mbid, status FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchall()
    assert rows == [("rg-3", "pending")]


def test_save_match_candidates_empty_is_noop():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    item_id = record_library_item(
        con, service="apple_music", service_item_id="l.a1", item_type="album", seen_at=t,
    )
    save_match_candidates(con, library_item_id=item_id, candidates=[])
    assert con.execute(
        "SELECT count(*) FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `ImportError: cannot import name 'save_match_candidates'`.

- [ ] **Step 3: Implement (append to `repository.py`)**

```python
def save_match_candidates(
    con: duckdb.DuckDBPyConnection,
    *,
    library_item_id: int,
    candidates: list[dict],
) -> None:
    """Replace the pending review-queue candidates for a library item.

    Each candidate dict: candidate_mbid, candidate_kind, score, method.
    """
    con.execute(
        "DELETE FROM match_candidates WHERE library_item_id = ? AND status = 'pending'",
        [library_item_id],
    )
    for c in candidates:
        con.execute(
            "INSERT INTO match_candidates "
            "(library_item_id, candidate_mbid, candidate_kind, score, method, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            [library_item_id, c["candidate_mbid"], c["candidate_kind"], c["score"], c["method"]],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/db/repository.py tests/db/test_repository.py
git commit -m "feat: add match candidate persistence"
```

---

### Task 5: Sync runs + snapshot reconciliation

**Files:**
- Modify: `src/needledrop/db/repository.py`
- Test: `tests/db/test_repository.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/db/test_repository.py`:

```python
import json as _json

from needledrop.db.repository import (
    complete_sync_run,
    mark_unseen_removed,
    start_sync_run,
)


def test_sync_run_lifecycle():
    con = _con()
    started = datetime(2026, 6, 15, 12, 0, 0)
    run_id = start_sync_run(con, service="apple_music", started_at=started)
    assert con.execute(
        "SELECT status FROM sync_runs WHERE id = ?", [run_id]
    ).fetchone()[0] == "running"

    completed = datetime(2026, 6, 15, 12, 5, 0)
    complete_sync_run(con, run_id=run_id, completed_at=completed, summary={"albums": 3})
    row = con.execute(
        "SELECT status, completed_at, summary_json FROM sync_runs WHERE id = ?", [run_id]
    ).fetchone()
    assert row[0] == "completed"
    assert row[1] == completed
    assert _json.loads(row[2]) == {"albums": 3}


def test_mark_unseen_removed():
    con = _con()
    old = datetime(2026, 6, 1, 10, 0, 0)
    now = datetime(2026, 6, 15, 12, 0, 0)
    stale = record_library_item(
        con, service="apple_music", service_item_id="l.gone", item_type="album", seen_at=old,
    )
    fresh = record_library_item(
        con, service="apple_music", service_item_id="l.here", item_type="album", seen_at=now,
    )
    removed_count = mark_unseen_removed(con, service="apple_music", run_started_at=now)
    assert removed_count == 1
    statuses = dict(
        con.execute("SELECT id, status FROM library_items").fetchall()
    )
    assert statuses[stale] == "removed"
    assert statuses[fresh] == "present"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `ImportError: cannot import name 'start_sync_run'`.

- [ ] **Step 3: Implement (append to `repository.py`)**

```python
def start_sync_run(con: duckdb.DuckDBPyConnection, *, service: str, started_at: datetime) -> int:
    """Open a sync run (status 'running'); returns its id."""
    return con.execute(
        "INSERT INTO sync_runs (service, started_at, status, summary_json) "
        "VALUES (?, ?, 'running', '{}') RETURNING id",
        [service, started_at],
    ).fetchone()[0]


def complete_sync_run(
    con: duckdb.DuckDBPyConnection, *, run_id: int, completed_at: datetime, summary: dict
) -> None:
    """Mark a sync run completed with a summary."""
    con.execute(
        "UPDATE sync_runs SET completed_at = ?, status = 'completed', summary_json = ? WHERE id = ?",
        [completed_at, json.dumps(summary, sort_keys=True), run_id],
    )


def mark_unseen_removed(
    con: duckdb.DuckDBPyConnection, *, service: str, run_started_at: datetime
) -> int:
    """Mark still-present items not seen during this run as removed; returns the count."""
    rows = con.execute(
        "UPDATE library_items SET status = 'removed' "
        "WHERE service = ? AND status = 'present' "
        "AND (last_seen_at IS NULL OR last_seen_at < ?) "
        "RETURNING id",
        [service, run_started_at],
    ).fetchall()
    return len(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: PASS (12 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/db/repository.py tests/db/test_repository.py
git commit -m "feat: add sync-run lifecycle and snapshot reconciliation"
```

---

### Task 6: Read helpers

**Files:**
- Modify: `src/needledrop/db/repository.py`
- Test: `tests/db/test_repository.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/db/test_repository.py`:

```python
from needledrop.db.repository import get_library_albums, get_library_summary


def _seed_album(con, *, apple_id, title, rg_mbid, method, seen_at):
    artist_id = upsert_artist(con, canonical_name="Radiohead", mbid="mbid-r")
    album_id = upsert_album(
        con, title=title, artist_id=artist_id, release_group_mbid=rg_mbid,
        external_ids={"apple": apple_id},
    )
    record_library_item(
        con, service="apple_music", service_item_id=apple_id, item_type="album",
        canonical_id=album_id, match_confidence=1.0, match_method=method, seen_at=seen_at,
    )


def test_get_library_summary_counts_present_by_type_and_match():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    _seed_album(con, apple_id="l.a1", title="OK Computer", rg_mbid="rg1", method="upc", seen_at=t)
    _seed_album(con, apple_id="l.a2", title="Kid A", rg_mbid="rg2", method="none", seen_at=t)
    summary = get_library_summary(con)
    assert summary["album"] == 2
    assert summary["matched"] == 1  # method != 'none'
    assert summary["unmatched"] == 1


def test_get_library_albums_returns_present_albums():
    con = _con()
    t = datetime(2026, 6, 15, 12, 0, 0)
    _seed_album(con, apple_id="l.a1", title="OK Computer", rg_mbid="rg1", method="upc", seen_at=t)
    albums = get_library_albums(con)
    assert len(albums) == 1
    assert albums[0]["title"] == "OK Computer"
    assert albums[0]["release_group_mbid"] == "rg1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_library_summary'`.

- [ ] **Step 3: Implement (append to `repository.py`)**

```python
def get_library_summary(con: duckdb.DuckDBPyConnection) -> dict:
    """Counts of present library items by type, plus matched/unmatched totals."""
    summary: dict[str, int] = {}
    for item_type, count in con.execute(
        "SELECT item_type, count(*) FROM library_items WHERE status = 'present' GROUP BY item_type"
    ).fetchall():
        summary[item_type] = count
    matched, unmatched = con.execute(
        "SELECT "
        "count(*) FILTER (WHERE match_method <> 'none'), "
        "count(*) FILTER (WHERE match_method = 'none') "
        "FROM library_items WHERE status = 'present'"
    ).fetchone()
    summary["matched"] = matched
    summary["unmatched"] = unmatched
    return summary


def get_library_albums(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Present library albums joined to their canonical album rows."""
    rows = con.execute(
        "SELECT a.id, a.title, a.release_group_mbid, a.version_class, "
        "li.match_method, li.match_confidence "
        "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "WHERE li.status = 'present' AND li.item_type = 'album' "
        "ORDER BY a.title"
    ).fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "release_group_mbid": r[2],
            "version_class": r[3],
            "match_method": r[4],
            "match_confidence": r[5],
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: PASS (14 tests total).

- [ ] **Step 5: Full suite + lint gate**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest`
Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/db/repository.py tests/db/test_repository.py
git commit -m "feat: add library summary and album read helpers"
```

---

## Self-Review

**1. Spec coverage (spec §4.2–4.5, §6.7 persistence needs; the db functions Plan 1 deferred):**
- `upsert_artist`/`upsert_album`/`upsert_track` (canonical entities, MBID-then-Apple-id dedup) → Tasks 1–2. ✓
- `record_library_item` (presence + match result, snapshot upsert) → Task 3. ✓
- review-queue `match_candidates` persistence → Task 4. ✓ (matches the Plan-4 matcher's candidate dicts.)
- `sync_runs` lifecycle + full-snapshot `mark_unseen_removed` → Task 5. ✓
- read helpers (`get_library_summary`, `get_library_albums`) for the MCP/CLI surface → Task 6. ✓
- Deferred to Plan 6 (sync): connector `include=catalog` enrichment, the sync orchestration that calls these functions, `diff_sync`, and the `needledrop sync` CLI. `save_cleanup_findings`/findings read helpers are Plan 7 (analysis).

**2. Placeholder scan:** No TBD/TODO. Every code step shows complete code; every run step has the command and expected result. Candidate dicts use plain string keys matching the Plan-4 matcher's `MatchCandidate` fields (`candidate_mbid`, `candidate_kind`, `score`, `method`).

**3. Type/name consistency:** All functions take a `duckdb.DuckDBPyConnection` first arg and keyword-only params. Dedup keys are consistent: artists by `mbid`/`$.apple`, albums by `release_group_mbid`/`$.apple`, tracks by `recording_mbid`/`$.apple`. `record_library_item`'s `ON CONFLICT (service, service_item_id, item_type)` matches the Plan-1 `library_items` UNIQUE constraint. Column names (`release_group_mbid`, `version_class`, `match_method`, `match_confidence`, `added_at`, `last_seen_at`, `status`, `summary_json`) match the Plan-1 `schema.sql`. Enum string values used in tests (`'none'`, `'upc'`, `'present'`, `'removed'`, `'release_group'`, `'pending'`, `'completed'`, `'running'`) match the Plan-1 enums and schema defaults. `seen_at`/`started_at`/`completed_at`/`run_started_at` are passed-in `datetime`s for determinism.

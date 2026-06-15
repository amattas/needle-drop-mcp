# NeedleDrop Analysis Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the library-intelligence layer — analysis engines that detect duplicate albums, compilation pollution, and missing core albums over the canonical + `mb_*` tables, persist them as `cleanup_findings`, and a cleanup service that runs the scan and resolves/ignores findings.

**Architecture:** Pure read-only SQL analyses in `analysis/` (each returns `CleanupFinding` objects from Plan 1), findings persistence in `db/repository.py` (`save_cleanup_findings` replaces open findings while respecting prior resolve/ignore; `get_findings` reads them back), and `services/cleanup.py` orchestrating a scan + per-finding resolve/ignore. Everything is exercised against an in-memory DuckDB seeded with the real canonical schema (`init_schema`) plus minimal `mb_*` rows — no network, no Docker.

**Tech Stack:** Python 3.13, DuckDB, stdlib `json`/`datetime`. Builds on merged Plans 1–6.

**Plan series:** Plan 7 of 9. The read-only FastMCP server + `serve` CLI (which expose these analyses as tools) are Plan 8; mutations + discography/recommendations are Plan 9. Design spec: `docs/superpowers/specs/2026-06-15-needledrop-mcp-design.md` (§4.4, §4.5, §6.6, §6.7). **Deferred:** partial-album / single-track detection (spec §4.5) — it needs a track→album linkage the current sync doesn't persist (canonical tracks are stored with `album_id=NULL`); that's a sync-layer prerequisite for a focused follow-up, not part of this plan.

---

## Environment notes for implementers

- Python via the project env interpreter: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python` (NOT `mamba run`). Tests e.g.: `... -m pytest tests/analysis/test_duplicates.py -v`.
- CI-parity gate before "done": `... -m pytest` green AND `... -m ruff check .` clean. ruff line-length 100 — wrap long lines; don't quote forward-ref annotations (UP037).
- No new dependencies.

## Verified `mb_*` schema facts these analyses rely on (from prior research)

- `gid` is the MBID; joins use `id`. `mb_release_group(id, gid, name, artist_credit, type)`; `type` → `mb_release_group_primary_type(id, name)` (e.g. 'Album').
- `mb_release_group_secondary_type_join(release_group, secondary_type)` → `mb_release_group_secondary_type(id, name)` (e.g. 'Compilation', 'Soundtrack', 'Live').
- `mb_artist_credit_name(artist_credit, position, artist, name, join_phrase)` links an `artist_credit` to `mb_artist(id, gid, name, sort_name)`.
- Various Artists `mb_artist.gid` = `89ad4ac3-39f7-470e-963a-56509c546377`.
- Canonical `albums.release_group_mbid` equals `mb_release_group.gid` for matched albums (one canonical row per owned edition; editions share a release-group).

---

## File Structure

```text
src/needledrop/db/repository.py        # MODIFY: save_cleanup_findings, get_findings
src/needledrop/analysis/__init__.py    # NEW (empty)
src/needledrop/analysis/duplicates.py  # NEW: find_duplicate_albums
src/needledrop/analysis/compilation_pollution.py  # NEW: find_compilation_pollution
src/needledrop/analysis/missing_albums.py          # NEW: find_missing_core_albums
src/needledrop/services/cleanup.py     # NEW: run_cleanup_scan, mark_finding_resolved, ignore_finding

tests/db/test_repository.py            # MODIFY: findings persistence
tests/analysis/test_duplicates.py      # NEW
tests/analysis/test_compilation_pollution.py  # NEW
tests/analysis/test_missing_albums.py  # NEW
tests/services/test_cleanup.py         # NEW
```

---

### Task 1: Findings persistence

**Files:**
- Modify: `src/needledrop/db/repository.py`
- Test: `tests/db/test_repository.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/db/test_repository.py`:

```python
from needledrop.db.repository import get_findings, save_cleanup_findings
from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def test_save_and_get_findings_roundtrip():
    con = _con()
    finding = CleanupFinding(
        finding_type=FindingType.DUPLICATE_ALBUM,
        severity=FindingSeverity.LOW,
        entity_id=7,
        description="You own 2 versions of 'Dookie'.",
        recommendation=Recommendation(action="review_duplicates", payload={"n": 2}),
    )
    save_cleanup_findings(con, [finding])
    got = get_findings(con)
    assert len(got) == 1
    assert got[0].finding_type == FindingType.DUPLICATE_ALBUM
    assert got[0].description == "You own 2 versions of 'Dookie'."
    assert got[0].recommendation.action == "review_duplicates"
    assert got[0].recommendation.payload == {"n": 2}


def test_save_replaces_open_findings():
    con = _con()
    save_cleanup_findings(con, [CleanupFinding(
        finding_type=FindingType.DUPLICATE_ALBUM, severity=FindingSeverity.LOW, description="old"
    )])
    save_cleanup_findings(con, [CleanupFinding(
        finding_type=FindingType.DUPLICATE_ALBUM, severity=FindingSeverity.LOW, description="new"
    )])
    descriptions = [f.description for f in get_findings(con)]
    assert descriptions == ["new"]


def test_save_respects_ignored_finding_across_scans():
    con = _con()
    save_cleanup_findings(con, [CleanupFinding(
        finding_type=FindingType.COMPILATION_POLLUTION, severity=FindingSeverity.INFO,
        description="'Now 100' is a compilation.",
    )])
    # User ignores it.
    fid = get_findings(con)[0].id
    con.execute("UPDATE cleanup_findings SET ignored_at = now() WHERE id = ?", [fid])
    # A re-scan surfaces the same issue, but it must not reappear as an open finding.
    save_cleanup_findings(con, [CleanupFinding(
        finding_type=FindingType.COMPILATION_POLLUTION, severity=FindingSeverity.INFO,
        description="'Now 100' is a compilation.",
    )])
    assert get_findings(con) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: FAIL — `ImportError: cannot import name 'save_cleanup_findings'`.

- [ ] **Step 3: Implement (append to `repository.py`)**

Add `from needledrop.models.enums import FindingSeverity, FindingType` and `from needledrop.models.findings import CleanupFinding, Recommendation` to the top imports, then append:

```python
def save_cleanup_findings(con: duckdb.DuckDBPyConnection, findings: list[CleanupFinding]) -> None:
    """Replace the open (unresolved, unignored) findings with a fresh scan's results.

    Findings whose (type, description) the user already resolved or ignored are
    NOT re-inserted, so prior decisions survive a re-scan.
    """
    con.execute(
        "DELETE FROM cleanup_findings WHERE resolved_at IS NULL AND ignored_at IS NULL"
    )
    suppressed = {
        (row[0], row[1])
        for row in con.execute(
            "SELECT finding_type, description FROM cleanup_findings "
            "WHERE resolved_at IS NOT NULL OR ignored_at IS NOT NULL"
        ).fetchall()
    }
    for finding in findings:
        if (finding.finding_type.value, finding.description) in suppressed:
            continue
        recommendation_json = json.dumps(
            finding.recommendation.model_dump() if finding.recommendation else None,
            sort_keys=True,
            default=str,
        )
        con.execute(
            "INSERT INTO cleanup_findings "
            "(finding_type, severity, entity_id, description, recommendation_json) "
            "VALUES (?, ?, ?, ?, ?)",
            [finding.finding_type.value, finding.severity.value, finding.entity_id,
             finding.description, recommendation_json],
        )


def get_findings(con: duckdb.DuckDBPyConnection, *, include_closed: bool = False) -> list[CleanupFinding]:
    """Read findings as CleanupFinding objects (open ones only, unless include_closed)."""
    sql = (
        "SELECT id, finding_type, severity, entity_id, description, recommendation_json, "
        "resolved_at, ignored_at FROM cleanup_findings"
    )
    if not include_closed:
        sql += " WHERE resolved_at IS NULL AND ignored_at IS NULL"
    sql += " ORDER BY id"
    findings: list[CleanupFinding] = []
    for row in con.execute(sql).fetchall():
        recommendation = None
        if row[5]:
            data = json.loads(row[5])
            if data:
                recommendation = Recommendation(**data)
        findings.append(
            CleanupFinding(
                id=row[0],
                finding_type=FindingType(row[1]),
                severity=FindingSeverity(row[2]),
                entity_id=row[3],
                description=row[4],
                recommendation=recommendation,
                resolved_at=row[6],
                ignored_at=row[7],
            )
        )
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -v`
Expected: PASS (existing repository tests + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/db/repository.py tests/db/test_repository.py
git commit -m "feat: add cleanup findings persistence"
```

---

### Task 2: Duplicate album detection

**Files:**
- Create: `src/needledrop/analysis/__init__.py`
- Create: `src/needledrop/analysis/duplicates.py`
- Test: `tests/analysis/test_duplicates.py`

- [ ] **Step 1: Write the failing test**

`tests/analysis/test_duplicates.py`:

```python
from needledrop.analysis.duplicates import find_duplicate_albums
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import record_library_item, upsert_album
from needledrop.models.enums import FindingType


def _db():
    con = connect(":memory:")
    init_schema(con)
    return con


def _own_album(con, *, apple_id, title, rg_mbid, version_class):
    from datetime import datetime
    album_id = upsert_album(
        con, title=title, release_group_mbid=rg_mbid, version_class=version_class,
        external_ids={"apple": apple_id},
    )
    record_library_item(
        con, service="apple_music", service_item_id=apple_id, item_type="album",
        canonical_id=album_id, match_method="upc", seen_at=datetime(2026, 6, 15, 12, 0, 0),
    )


def test_finds_two_editions_of_one_release_group():
    con = _db()
    _own_album(con, apple_id="l.1", title="Dookie", rg_mbid="rg-dookie", version_class="standard")
    _own_album(con, apple_id="l.2", title="Dookie (Deluxe)", rg_mbid="rg-dookie", version_class="deluxe")
    findings = find_duplicate_albums(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.DUPLICATE_ALBUM
    assert "2 versions" in findings[0].description
    assert findings[0].recommendation.payload["release_group_mbid"] == "rg-dookie"


def test_single_edition_is_not_a_duplicate():
    con = _db()
    _own_album(con, apple_id="l.1", title="Dookie", rg_mbid="rg-dookie", version_class="standard")
    assert find_duplicate_albums(con) == []


def test_unmatched_albums_are_ignored():
    con = _db()
    _own_album(con, apple_id="l.1", title="Unknown A", rg_mbid=None, version_class="standard")
    _own_album(con, apple_id="l.2", title="Unknown B", rg_mbid=None, version_class="standard")
    assert find_duplicate_albums(con) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_duplicates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.analysis'`.

- [ ] **Step 3: Implement**

`src/needledrop/analysis/__init__.py`: empty file.

`src/needledrop/analysis/duplicates.py`:

```python
"""Duplicate-album detection: multiple owned editions of one release-group."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation


def find_duplicate_albums(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Owned album editions sharing a release-group (you own more than one version)."""
    rows = con.execute(
        "SELECT a.release_group_mbid, count(*) AS n, min(a.title) AS title, "
        "list(a.id) AS album_ids, list(a.version_class) AS versions "
        "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "WHERE li.status = 'present' AND li.item_type = 'album' "
        "AND a.release_group_mbid IS NOT NULL "
        "GROUP BY a.release_group_mbid HAVING count(*) > 1"
    ).fetchall()
    findings: list[CleanupFinding] = []
    for rg_mbid, n, title, album_ids, versions in rows:
        labels = ", ".join(str(v) for v in versions)
        findings.append(
            CleanupFinding(
                finding_type=FindingType.DUPLICATE_ALBUM,
                severity=FindingSeverity.LOW,
                entity_id=album_ids[0],
                description=f"You own {n} versions of '{title}' ({labels}).",
                recommendation=Recommendation(
                    action="review_duplicates",
                    detail=f"Editions: {labels}",
                    payload={"release_group_mbid": rg_mbid, "album_ids": album_ids},
                ),
            )
        )
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_duplicates.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/analysis/__init__.py src/needledrop/analysis/duplicates.py tests/analysis/test_duplicates.py
git commit -m "feat: add duplicate album detection"
```

---

### Task 3: Compilation pollution detection

**Files:**
- Create: `src/needledrop/analysis/compilation_pollution.py`
- Test: `tests/analysis/test_compilation_pollution.py`

- [ ] **Step 1: Write the failing test**

`tests/analysis/test_compilation_pollution.py`:

```python
from datetime import datetime

from needledrop.analysis.compilation_pollution import find_compilation_pollution
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import record_library_item, upsert_album
from needledrop.models.enums import FindingType

VARIOUS_ARTISTS_GID = "89ad4ac3-39f7-470e-963a-56509c546377"


def _db():
    con = connect(":memory:")
    init_schema(con)
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit_name (artist_credit INTEGER, position INTEGER, "
                "artist INTEGER, name VARCHAR, join_phrase VARCHAR)")
    con.execute("CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR, "
                "artist_credit INTEGER, type INTEGER)")
    con.execute("CREATE TABLE mb_release_group_secondary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type_join "
                "(release_group INTEGER, secondary_type INTEGER)")
    con.execute("INSERT INTO mb_release_group_secondary_type VALUES (1, 'Compilation'), (2, 'Soundtrack')")
    return con


def _own_album(con, *, apple_id, title, rg_mbid):
    album_id = upsert_album(con, title=title, release_group_mbid=rg_mbid,
                            external_ids={"apple": apple_id})
    record_library_item(con, service="apple_music", service_item_id=apple_id, item_type="album",
                        canonical_id=album_id, match_method="upc", seen_at=datetime(2026, 6, 15, 12, 0, 0))


def test_flags_compilation_secondary_type():
    con = _db()
    con.execute("INSERT INTO mb_release_group VALUES (10, 'rg-comp', 'Now 100', 50, 1)")
    con.execute("INSERT INTO mb_release_group_secondary_type_join VALUES (10, 1)")  # Compilation
    _own_album(con, apple_id="l.1", title="Now 100", rg_mbid="rg-comp")
    findings = find_compilation_pollution(con)
    assert len(findings) == 1
    assert findings[0].finding_type == FindingType.COMPILATION_POLLUTION


def test_flags_various_artists_credit():
    con = _db()
    con.execute("INSERT INTO mb_artist VALUES (99, '%s', 'Various Artists', 'Various Artists')"
                % VARIOUS_ARTISTS_GID)
    con.execute("INSERT INTO mb_artist_credit_name VALUES (60, 0, 99, 'Various Artists', '')")
    con.execute("INSERT INTO mb_release_group VALUES (11, 'rg-va', 'Movie OST', 60, 1)")
    _own_album(con, apple_id="l.2", title="Movie OST", rg_mbid="rg-va")
    findings = find_compilation_pollution(con)
    assert len(findings) == 1


def test_regular_album_not_flagged():
    con = _db()
    con.execute("INSERT INTO mb_release_group VALUES (12, 'rg-ok', 'OK Computer', 70, 1)")
    _own_album(con, apple_id="l.3", title="OK Computer", rg_mbid="rg-ok")
    assert find_compilation_pollution(con) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_compilation_pollution.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/needledrop/analysis/compilation_pollution.py`:

```python
"""Compilation-pollution detection: soundtracks, compilations, Various-Artists records."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation

_VARIOUS_ARTISTS_GID = "89ad4ac3-39f7-470e-963a-56509c546377"


def find_compilation_pollution(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Owned albums whose release-group is a compilation/soundtrack or Various-Artists."""
    rows = con.execute(
        "SELECT DISTINCT a.id, a.title "
        "FROM library_items li JOIN albums a ON li.canonical_id = a.id "
        "JOIN mb_release_group rg ON a.release_group_mbid = rg.gid "
        "WHERE li.status = 'present' AND li.item_type = 'album' AND ("
        "  EXISTS (SELECT 1 FROM mb_release_group_secondary_type_join j "
        "          JOIN mb_release_group_secondary_type st ON j.secondary_type = st.id "
        "          WHERE j.release_group = rg.id AND st.name IN ('Compilation', 'Soundtrack')) "
        "  OR EXISTS (SELECT 1 FROM mb_artist_credit_name acn JOIN mb_artist ar ON acn.artist = ar.id "
        "             WHERE acn.artist_credit = rg.artist_credit AND ar.gid = ?)) "
        "ORDER BY a.title",
        [_VARIOUS_ARTISTS_GID],
    ).fetchall()
    return [
        CleanupFinding(
            finding_type=FindingType.COMPILATION_POLLUTION,
            severity=FindingSeverity.INFO,
            entity_id=album_id,
            description=f"'{title}' is a compilation, soundtrack, or Various-Artists release.",
            recommendation=Recommendation(action="review_compilation"),
        )
        for album_id, title in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_compilation_pollution.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/analysis/compilation_pollution.py tests/analysis/test_compilation_pollution.py
git commit -m "feat: add compilation pollution detection"
```

---

### Task 4: Missing core album detection

**Files:**
- Create: `src/needledrop/analysis/missing_albums.py`
- Test: `tests/analysis/test_missing_albums.py`

- [ ] **Step 1: Write the failing test**

`tests/analysis/test_missing_albums.py`:

```python
from datetime import datetime

from needledrop.analysis.missing_albums import find_missing_core_albums
from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import record_library_item, upsert_album
from needledrop.models.enums import FindingType


def _db():
    con = connect(":memory:")
    init_schema(con)
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit_name (artist_credit INTEGER, position INTEGER, "
                "artist INTEGER, name VARCHAR, join_phrase VARCHAR)")
    con.execute("CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR, "
                "artist_credit INTEGER, type INTEGER)")
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type_join "
                "(release_group INTEGER, secondary_type INTEGER)")
    con.execute("INSERT INTO mb_release_group_primary_type VALUES (1, 'Album'), (2, 'Single')")
    con.execute("INSERT INTO mb_release_group_secondary_type VALUES (5, 'Live'), (6, 'Compilation')")
    # One artist (Linkin Park) with two studio albums + a live album.
    con.execute("INSERT INTO mb_artist VALUES (1, 'gid-lp', 'Linkin Park', 'Linkin Park')")
    con.execute("INSERT INTO mb_artist_credit_name VALUES (10, 0, 1, 'Linkin Park', '')")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'rg-ht', 'Hybrid Theory', 10, 1)")
    con.execute("INSERT INTO mb_release_group VALUES (101, 'rg-met', 'Meteora', 10, 1)")
    con.execute("INSERT INTO mb_release_group VALUES (102, 'rg-live', 'Live in Texas', 10, 1)")
    con.execute("INSERT INTO mb_release_group_secondary_type_join VALUES (102, 5)")  # live
    return con


def _own_album(con, *, apple_id, title, rg_mbid):
    album_id = upsert_album(con, title=title, release_group_mbid=rg_mbid,
                            external_ids={"apple": apple_id})
    record_library_item(con, service="apple_music", service_item_id=apple_id, item_type="album",
                        canonical_id=album_id, match_method="upc", seen_at=datetime(2026, 6, 15, 12, 0, 0))


def test_finds_unowned_studio_album_by_owned_artist():
    con = _db()
    _own_album(con, apple_id="l.1", title="Hybrid Theory", rg_mbid="rg-ht")
    findings = find_missing_core_albums(con)
    # Meteora is missing; the live album is excluded.
    assert [f.finding_type for f in findings] == [FindingType.MISSING_CORE_ALBUM]
    assert "Meteora" in findings[0].description
    assert findings[0].recommendation.payload["release_group_mbid"] == "rg-met"


def test_owning_all_studio_albums_yields_nothing():
    con = _db()
    _own_album(con, apple_id="l.1", title="Hybrid Theory", rg_mbid="rg-ht")
    _own_album(con, apple_id="l.2", title="Meteora", rg_mbid="rg-met")
    assert find_missing_core_albums(con) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_missing_albums.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/needledrop/analysis/missing_albums.py`:

```python
"""Missing-core-album detection: studio albums by owned artists that aren't owned."""

from __future__ import annotations

import duckdb

from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, Recommendation

_VARIOUS_ARTISTS_GID = "89ad4ac3-39f7-470e-963a-56509c546377"

_QUERY = """
WITH owned AS (
    SELECT DISTINCT a.release_group_mbid AS gid
    FROM library_items li JOIN albums a ON li.canonical_id = a.id
    WHERE li.status = 'present' AND li.item_type = 'album' AND a.release_group_mbid IS NOT NULL
),
owned_artists AS (
    SELECT DISTINCT acn.artist AS artist_id
    FROM owned o
    JOIN mb_release_group rg ON o.gid = rg.gid
    JOIN mb_artist_credit_name acn ON rg.artist_credit = acn.artist_credit
    JOIN mb_artist ar ON acn.artist = ar.id
    WHERE ar.gid <> ?
)
SELECT DISTINCT rg.gid, rg.name, ar.name
FROM owned_artists oa
JOIN mb_artist ar ON ar.id = oa.artist_id
JOIN mb_artist_credit_name acn ON acn.artist = oa.artist_id
JOIN mb_release_group rg ON rg.artist_credit = acn.artist_credit
JOIN mb_release_group_primary_type pt ON rg.type = pt.id
WHERE pt.name = 'Album'
  AND rg.gid NOT IN (SELECT gid FROM owned)
  AND NOT EXISTS (
      SELECT 1 FROM mb_release_group_secondary_type_join j
      JOIN mb_release_group_secondary_type st ON j.secondary_type = st.id
      WHERE j.release_group = rg.id AND st.name IN ('Compilation', 'Live', 'Soundtrack')
  )
ORDER BY ar.name, rg.name
"""


def find_missing_core_albums(con: duckdb.DuckDBPyConnection) -> list[CleanupFinding]:
    """Studio albums (Album primary type, non-compilation/live) by owned artists, not owned."""
    rows = con.execute(_QUERY, [_VARIOUS_ARTISTS_GID]).fetchall()
    return [
        CleanupFinding(
            finding_type=FindingType.MISSING_CORE_ALBUM,
            severity=FindingSeverity.INFO,
            entity_id=None,
            description=f"Missing: {artist} — {title}",
            recommendation=Recommendation(
                action="add_album",
                payload={"release_group_mbid": gid, "artist": artist, "title": title},
            ),
        )
        for gid, title, artist in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/analysis/test_missing_albums.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/analysis/missing_albums.py tests/analysis/test_missing_albums.py
git commit -m "feat: add missing core album detection"
```

---

### Task 5: Cleanup service

**Files:**
- Create: `src/needledrop/services/cleanup.py`
- Test: `tests/services/test_cleanup.py`

- [ ] **Step 1: Write the failing test**

`tests/services/test_cleanup.py`:

```python
from datetime import datetime

from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import get_findings, record_library_item, upsert_album
from needledrop.services.cleanup import ignore_finding, mark_finding_resolved, run_cleanup_scan


def _db():
    con = connect(":memory:")
    init_schema(con)
    # Minimal mb_* so the compilation/missing analyses can run (empty is fine).
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    con.execute("CREATE TABLE mb_artist_credit_name (artist_credit INTEGER, position INTEGER, "
                "artist INTEGER, name VARCHAR, join_phrase VARCHAR)")
    con.execute("CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR, "
                "artist_credit INTEGER, type INTEGER)")
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type_join "
                "(release_group INTEGER, secondary_type INTEGER)")
    return con


def _own_album(con, *, apple_id, title, rg_mbid):
    album_id = upsert_album(con, title=title, release_group_mbid=rg_mbid,
                            external_ids={"apple": apple_id})
    record_library_item(con, service="apple_music", service_item_id=apple_id, item_type="album",
                        canonical_id=album_id, match_method="upc", seen_at=datetime(2026, 6, 15, 12, 0, 0))


def test_run_cleanup_scan_persists_duplicate_finding():
    con = _db()
    _own_album(con, apple_id="l.1", title="Dookie", rg_mbid="rg-d")
    _own_album(con, apple_id="l.2", title="Dookie (Deluxe)", rg_mbid="rg-d")
    counts = run_cleanup_scan(con, now=datetime(2026, 6, 15, 12, 0, 0))
    assert counts == {"duplicate_album": 1}
    assert len(get_findings(con)) == 1


def test_mark_finding_resolved_removes_from_open():
    con = _db()
    _own_album(con, apple_id="l.1", title="Dookie", rg_mbid="rg-d")
    _own_album(con, apple_id="l.2", title="Dookie (Deluxe)", rg_mbid="rg-d")
    run_cleanup_scan(con, now=datetime(2026, 6, 15, 12, 0, 0))
    fid = get_findings(con)[0].id
    mark_finding_resolved(con, fid, now=datetime(2026, 6, 16, 9, 0, 0))
    assert get_findings(con) == []
    assert len(get_findings(con, include_closed=True)) == 1


def test_ignore_finding_removes_from_open():
    con = _db()
    _own_album(con, apple_id="l.1", title="Dookie", rg_mbid="rg-d")
    _own_album(con, apple_id="l.2", title="Dookie (Deluxe)", rg_mbid="rg-d")
    run_cleanup_scan(con, now=datetime(2026, 6, 15, 12, 0, 0))
    fid = get_findings(con)[0].id
    ignore_finding(con, fid, now=datetime(2026, 6, 16, 9, 0, 0))
    assert get_findings(con) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/services/test_cleanup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.services.cleanup'`.

- [ ] **Step 3: Implement**

`src/needledrop/services/cleanup.py`:

```python
"""Cleanup workflow: run all analyses, persist findings, resolve/ignore them."""

from __future__ import annotations

from datetime import datetime

import duckdb

from needledrop.analysis.compilation_pollution import find_compilation_pollution
from needledrop.analysis.duplicates import find_duplicate_albums
from needledrop.analysis.missing_albums import find_missing_core_albums
from needledrop.db.repository import get_findings, save_cleanup_findings
from needledrop.models.findings import CleanupReport


def run_cleanup_scan(con: duckdb.DuckDBPyConnection, *, now: datetime) -> dict[str, int]:
    """Run every analysis, persist the findings, and return counts by finding type."""
    findings = [
        *find_duplicate_albums(con),
        *find_compilation_pollution(con),
        *find_missing_core_albums(con),
    ]
    save_cleanup_findings(con, findings)
    report = CleanupReport(findings=get_findings(con), generated_at=now)
    return report.count_by_type()


def mark_finding_resolved(con: duckdb.DuckDBPyConnection, finding_id: int, *, now: datetime) -> None:
    con.execute("UPDATE cleanup_findings SET resolved_at = ? WHERE id = ?", [now, finding_id])


def ignore_finding(con: duckdb.DuckDBPyConnection, finding_id: int, *, now: datetime) -> None:
    con.execute("UPDATE cleanup_findings SET ignored_at = ? WHERE id = ?", [now, finding_id])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/services/test_cleanup.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Full suite + lint gate**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest`
Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/services/cleanup.py tests/services/test_cleanup.py
git commit -m "feat: add cleanup scan service"
```

---

## Self-Review

**1. Spec coverage (spec §4.4 finding types, §4.5 analysis queries, §6.6 analysis/, §6.7 cleanup.py):**
- `find_duplicate_albums` (owned albums sharing a release_group_mbid) → Task 2. ✓
- `find_compilation_pollution` (RG secondary type Compilation/Soundtrack or Various-Artists) → Task 3. ✓
- `find_missing_core_albums` (owned artist's unowned Album-primary release-groups, excluding compilation/live/soundtrack) → Task 4. ✓
- `save_cleanup_findings`/`get_findings` (respecting resolve/ignore) → Task 1. ✓
- `run_cleanup_scan`/`mark_finding_resolved`/`ignore_finding` → Task 5. ✓
- All analyses filter on `li.status='present'`; matched-only where MBID-dependent (duplicate/compilation/missing require `release_group_mbid`). ✓
- **Deferred (documented):** `find_partial_albums`/`find_single_track_albums` (need track→album linkage sync doesn't persist). `metadata_problem` findings not in scope. The MCP tools that expose these analyses are Plan 8.

**2. Placeholder scan:** No TBD/TODO. Every code step shows complete code; every run step has the command and expected result. `now` is injected into the cleanup service for deterministic timestamps.

**3. Type/name consistency:** All analysis functions are `find_*(con) -> list[CleanupFinding]` returning the Plan-1 `CleanupFinding`/`Recommendation` models with `FindingType`/`FindingSeverity` enums. `save_cleanup_findings(con, findings)` / `get_findings(con, *, include_closed=False)` round-trip those models (recommendation via `model_dump()`/`Recommendation(**data)`); `recommendation_json` and the `cleanup_findings` columns (`finding_type`, `severity`, `entity_id`, `description`, `recommendation_json`, `resolved_at`, `ignored_at`) match the Plan-1 schema. `run_cleanup_scan` composes the three `find_*` functions and returns `CleanupReport.count_by_type()` (Plan-1 model). The Various-Artists gid constant matches the researched MB value. SQL column names (`mb_release_group.type`/`.artist_credit`/`.gid`, `mb_release_group_secondary_type_join.release_group`/`.secondary_type`, `mb_artist_credit_name.artist_credit`/`.artist`, `mb_artist.gid`, `mb_release_group_primary_type.name`) match the verified schema and the Plan-2 materialization.

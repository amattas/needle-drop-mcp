# NeedleDrop Match Review-Queue Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator review and resolve the matching review queue — confirm a pending MusicBrainz candidate for an unmatched/low-confidence library item, or reject all candidates — completing the "tiered matching with a review queue" feature, exposed as MCP tools.

**Architecture:** Three repository functions over the existing local tables (`library_items`, `match_candidates`, canonical `albums`/`tracks`): `get_review_queue` (list items with pending candidates, with optional MB display names), `resolve_match` (confirm one candidate — link the canonical row to its MBID, mark the item manually matched, flip candidate statuses, atomically), and `reject_match` (reject all pending candidates for an item). Three read/write MCP tools wrap them on the existing FastMCP server. No new connector, no Apple writes, no schema change — only local-DB reads and targeted writes.

**Tech Stack:** DuckDB, FastMCP 3.4.2 (`@mcp.tool`, in-memory `Client` tests), Pydantic v2, pytest (`asyncio.run` wrapper — no pytest-asyncio).

---

## Background & Key Facts (read before starting)

**Environment:** Python interpreter is `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python` (use directly; NOT `mamba run`). No Docker/network needed.

**Gates:**
- `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest -q`
- `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`

Ruff: line-length 100 (keep every line ≤100 chars), select E/F/I/UP/B, B008 ignored.

**How the review queue is populated today (already merged on main):**
- `src/needledrop/matching/matcher.py` returns a `MatchResult`. When no candidate clears the threshold, `MatchResult.mbid is None`, `method=NONE`, and `candidates` is a list of `MatchCandidate` (kind `release_group` for albums, `recording` for tracks).
- `src/needledrop/services/sync.py` (`_sync_album`/`_sync_track`) records the library item, then calls `save_match_candidates(con, library_item_id=item_id, candidates=[...])` to persist those candidates as rows in `match_candidates` with `status='pending'`.
- So after a sync, a low-confidence item has `library_items.match_method='none'` AND one or more `match_candidates` rows with `status='pending'`. That is exactly the review queue.

**Schema facts (`src/needledrop/db/schema.sql`):**
- `match_candidates(id, library_item_id, candidate_mbid, candidate_kind, score, method, status)`. `candidate_kind` is `'release_group'` or `'recording'`. `status` is `'pending'|'confirmed'|'rejected'` (default `'pending'`).
- `library_items(id, service, service_item_id, item_type, canonical_id, match_confidence, match_method, ..., status)`. `item_type` is `'album'|'track'|'playlist'`. `canonical_id` is a polymorphic soft reference: `albums.id` when `item_type='album'`, `tracks.id` when `item_type='track'` (sync always creates a canonical row, even for unmatched items).
- `albums(id, ..., release_group_mbid, ...)`, `tracks(id, ..., recording_mbid, ...)`.

**Enums (`src/needledrop/models/enums.py`):** `MatchMethod.MANUAL = "manual"`, `MatchStatus.{PENDING,CONFIRMED,REJECTED}`, `CandidateKind.{RELEASE_GROUP="release_group", RECORDING="recording"}`.

**Resolution semantics (decisions for this plan):**
- Confirming a candidate links the item's *canonical row* to the chosen MBID: for an album item set `albums.release_group_mbid = candidate_mbid`; for a track item set `tracks.recording_mbid = candidate_mbid`.
- A manual confirmation is authoritative: set `library_items.match_method='manual'`, `match_confidence=1.0`.
- The chosen candidate becomes `confirmed`; all the item's other `pending` candidates become `rejected`.
- A candidate's `candidate_kind` must match the item's `item_type` (`release_group`↔`album`, `recording`↔`track`); otherwise it's an error.
- `resolve_match` is wrapped in a transaction so its multi-table updates commit atomically.
- `reject_match` rejects every pending candidate for an item and leaves the item unmatched (`match_method` stays `'none'`).

**Existing helpers you will reuse:** `table_exists(con, table_name)` in `src/needledrop/db/duckdb_store.py` (filters `table_schema='main' AND table_type='BASE TABLE'`). Use it to guard optional `mb_*` name enrichment so the queue works whether or not `needledrop mb import` has run.

**MCP server facts (`src/needledrop/mcp_server.py`):** `create_server(con, *, sync_runner=None) -> FastMCP`. Tools are inner functions decorated `@mcp.tool`, closing over `con`. Imported helpers are aliased with a leading `_` to avoid the tool-name-vs-import shadowing trap. Errors raised in a tool surface to the client as `fastmcp.exceptions.ToolError`. In-memory test pattern: `async with Client(server) as client: (await client.call_tool(name, args)).data`, wrapped in `asyncio.run`.

**Scope:** review-queue listing + resolve/reject, as repository functions and MCP tools. **Out of scope (later plans):** a `review` CLI command, Apple-library mutations, catalog browse tools, new analyses.

---

## File Structure

- **Modify** `src/needledrop/db/repository.py` — add `get_review_queue`, `resolve_match`, `reject_match` (+ a private `_enrich_candidate_names` helper).
- **Modify** `tests/db/test_repository.py` — tests for the three functions.
- **Modify** `src/needledrop/mcp_server.py` — add `list_review_queue`, `resolve_match`, `reject_match` tools (aliased imports).
- **Modify** `tests/test_mcp_server.py` — client-level tests for the three tools.

---

## Task 1: Repository — review-queue read + resolve/reject

**Files:**
- Modify: `src/needledrop/db/repository.py` (append after `search_library`)
- Test: `tests/db/test_repository.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/db/test_repository.py`. Extend the existing `from needledrop.db.repository import (...)` block to include `get_review_queue, resolve_match, reject_match`. Confirm `connect`, `init_schema` are imported (existing tests use them). Append:

```python
def _seed_review_item(con, *, item_type="album", canonical_title="Kid A",
                      service_item_id="l.kida"):
    """A present, unmatched library item with two pending candidates."""
    if item_type == "album":
        con.execute("INSERT INTO albums (title) VALUES (?)", [canonical_title])
        canonical_id = con.execute(
            "SELECT id FROM albums WHERE title = ?", [canonical_title]
        ).fetchone()[0]
        kind = "release_group"
    else:
        con.execute("INSERT INTO tracks (title) VALUES (?)", [canonical_title])
        canonical_id = con.execute(
            "SELECT id FROM tracks WHERE title = ?", [canonical_title]
        ).fetchone()[0]
        kind = "recording"
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_method, status) "
        "VALUES ('apple_music', ?, ?, ?, 'none', 'present')",
        [service_item_id, item_type, canonical_id],
    )
    item_id = con.execute(
        "SELECT id FROM library_items WHERE service_item_id = ?", [service_item_id]
    ).fetchone()[0]
    for mbid, score in [("rg-good", 0.81), ("rg-meh", 0.74)]:
        con.execute(
            "INSERT INTO match_candidates "
            "(library_item_id, candidate_mbid, candidate_kind, score, method, status) "
            "VALUES (?, ?, ?, ?, 'fuzzy', 'pending')",
            [item_id, mbid, kind, score],
        )
    return item_id, canonical_id


def test_get_review_queue_lists_items_with_pending_candidates(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    item_id, _ = _seed_review_item(con)
    queue = get_review_queue(con)
    assert len(queue) == 1
    entry = queue[0]
    assert entry["library_item_id"] == item_id
    assert entry["item_type"] == "album"
    assert entry["title"] == "Kid A"
    # Candidates ordered by score desc; names are None without mb_* tables.
    assert [c["candidate_mbid"] for c in entry["candidates"]] == ["rg-good", "rg-meh"]
    assert entry["candidates"][0]["candidate_kind"] == "release_group"
    assert entry["candidates"][0]["name"] is None


def test_get_review_queue_enriches_names_when_mb_present(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    _seed_review_item(con)
    con.execute("CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR)")
    con.execute("INSERT INTO mb_release_group VALUES (1, 'rg-good', 'Kid A (MB)')")
    queue = get_review_queue(con)
    names = {c["candidate_mbid"]: c["name"] for c in queue[0]["candidates"]}
    assert names["rg-good"] == "Kid A (MB)"
    assert names["rg-meh"] is None  # not present in mb_release_group


def test_resolve_match_links_canonical_and_flips_statuses(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    item_id, canonical_id = _seed_review_item(con)
    chosen = con.execute(
        "SELECT id FROM match_candidates WHERE candidate_mbid = 'rg-good'"
    ).fetchone()[0]
    result = resolve_match(con, candidate_id=chosen)
    assert result == {
        "library_item_id": item_id,
        "item_type": "album",
        "candidate_mbid": "rg-good",
    }
    # Canonical album now linked to the chosen release-group.
    assert con.execute(
        "SELECT release_group_mbid FROM albums WHERE id = ?", [canonical_id]
    ).fetchone()[0] == "rg-good"
    # Item is now manually matched.
    method, conf = con.execute(
        "SELECT match_method, match_confidence FROM library_items WHERE id = ?", [item_id]
    ).fetchone()
    assert method == "manual"
    assert conf == 1.0
    # Chosen confirmed, sibling rejected, none left pending.
    statuses = dict(con.execute(
        "SELECT candidate_mbid, status FROM match_candidates WHERE library_item_id = ?", [item_id]
    ).fetchall())
    assert statuses == {"rg-good": "confirmed", "rg-meh": "rejected"}
    # Resolved item no longer appears in the review queue.
    assert get_review_queue(con) == []


def test_resolve_match_links_recording_for_track(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    item_id, canonical_id = _seed_review_item(
        con, item_type="track", canonical_title="Idioteque", service_item_id="l.idio"
    )
    chosen = con.execute(
        "SELECT id FROM match_candidates WHERE candidate_mbid = 'rg-good'"
    ).fetchone()[0]
    resolve_match(con, candidate_id=chosen)
    assert con.execute(
        "SELECT recording_mbid FROM tracks WHERE id = ?", [canonical_id]
    ).fetchone()[0] == "rg-good"


def test_resolve_match_rejects_unknown_or_nonpending(tmp_path):
    import pytest

    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    item_id, _ = _seed_review_item(con)
    with pytest.raises(ValueError):
        resolve_match(con, candidate_id=99999)  # unknown
    chosen = con.execute(
        "SELECT id FROM match_candidates WHERE candidate_mbid = 'rg-good'"
    ).fetchone()[0]
    resolve_match(con, candidate_id=chosen)
    with pytest.raises(ValueError):
        resolve_match(con, candidate_id=chosen)  # already confirmed, not pending


def test_reject_match_rejects_all_pending(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    item_id, _ = _seed_review_item(con)
    rejected = reject_match(con, library_item_id=item_id)
    assert rejected == 2
    pending = con.execute(
        "SELECT count(*) FROM match_candidates "
        "WHERE library_item_id = ? AND status = 'pending'", [item_id]
    ).fetchone()[0]
    assert pending == 0
    # Item stays unmatched and leaves the queue.
    assert con.execute(
        "SELECT match_method FROM library_items WHERE id = ?", [item_id]
    ).fetchone()[0] == "none"
    assert get_review_queue(con) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -q -k "review_queue or resolve_match or reject_match"`
Expected: FAIL with `ImportError`/`NameError` (functions not defined).

- [ ] **Step 3: Implement the functions**

Append to `src/needledrop/db/repository.py` (after `search_library`). Add `from needledrop.db.duckdb_store import table_exists` to the import block at the top of the file.

```python
def get_review_queue(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Present library items that still have pending match candidates.

    Each entry: library_item_id, item_type, resolved canonical title, current
    match_method, and the pending candidates (score-desc). Candidate `name` is
    filled from the mb_* authority tables when present, else None.
    """
    rows = con.execute(
        "SELECT li.id, li.item_type, COALESCE(al.title, tr.title) AS title, li.match_method "
        "FROM library_items li "
        "LEFT JOIN albums al ON li.item_type = 'album' AND li.canonical_id = al.id "
        "LEFT JOIN tracks tr ON li.item_type = 'track' AND li.canonical_id = tr.id "
        "WHERE li.status = 'present' AND EXISTS ("
        "  SELECT 1 FROM match_candidates mc "
        "  WHERE mc.library_item_id = li.id AND mc.status = 'pending') "
        "ORDER BY title"
    ).fetchall()
    queue: list[dict] = []
    for item_id, item_type, title, match_method in rows:
        cand_rows = con.execute(
            "SELECT id, candidate_mbid, candidate_kind, score, method "
            "FROM match_candidates WHERE library_item_id = ? AND status = 'pending' "
            "ORDER BY score DESC, id",
            [item_id],
        ).fetchall()
        candidates = [
            {
                "candidate_id": c[0],
                "candidate_mbid": c[1],
                "candidate_kind": c[2],
                "score": c[3],
                "method": c[4],
                "name": None,
            }
            for c in cand_rows
        ]
        queue.append(
            {
                "library_item_id": item_id,
                "item_type": item_type,
                "title": title,
                "match_method": match_method,
                "candidates": candidates,
            }
        )
    _enrich_candidate_names(con, queue)
    return queue


def _enrich_candidate_names(con: duckdb.DuckDBPyConnection, queue: list[dict]) -> None:
    """Fill candidate `name` from mb_* tables (release_group / recording), if present."""
    by_kind = {"release_group": "mb_release_group", "recording": "mb_recording"}
    names: dict[str, str] = {}
    for kind, table in by_kind.items():
        gids = {
            c["candidate_mbid"]
            for entry in queue
            for c in entry["candidates"]
            if c["candidate_kind"] == kind
        }
        if not gids or not table_exists(con, table):
            continue
        placeholders = ", ".join("?" * len(gids))
        for gid, name in con.execute(
            f"SELECT gid, name FROM {table} WHERE gid IN ({placeholders})", list(gids)
        ).fetchall():
            names[gid] = name
    for entry in queue:
        for c in entry["candidates"]:
            c["name"] = names.get(c["candidate_mbid"])


def resolve_match(con: duckdb.DuckDBPyConnection, *, candidate_id: int) -> dict:
    """Confirm one pending candidate: link the canonical row to its MBID, mark the
    item manually matched, confirm the choice, reject its siblings — atomically.

    Returns {library_item_id, item_type, candidate_mbid}. Raises ValueError if the
    candidate is unknown / not pending, the item is missing, the candidate kind does
    not match the item type, or the item has no canonical row to link.
    """
    cand = con.execute(
        "SELECT library_item_id, candidate_mbid, candidate_kind, status "
        "FROM match_candidates WHERE id = ?",
        [candidate_id],
    ).fetchone()
    if cand is None:
        raise ValueError(f"No match candidate with id {candidate_id}.")
    library_item_id, candidate_mbid, candidate_kind, status = cand
    if status != "pending":
        raise ValueError(f"Candidate {candidate_id} is not pending (status={status}).")

    item = con.execute(
        "SELECT item_type, canonical_id FROM library_items WHERE id = ?",
        [library_item_id],
    ).fetchone()
    if item is None:
        raise ValueError(f"Library item {library_item_id} not found.")
    item_type, canonical_id = item
    expected_kind = {"album": "release_group", "track": "recording"}.get(item_type)
    if candidate_kind != expected_kind:
        raise ValueError(
            f"Candidate kind '{candidate_kind}' cannot resolve a '{item_type}' item."
        )
    if canonical_id is None:
        raise ValueError(f"Library item {library_item_id} has no canonical row to link.")

    con.execute("BEGIN TRANSACTION")
    try:
        if item_type == "album":
            con.execute(
                "UPDATE albums SET release_group_mbid = ? WHERE id = ?",
                [candidate_mbid, canonical_id],
            )
        else:
            con.execute(
                "UPDATE tracks SET recording_mbid = ? WHERE id = ?",
                [candidate_mbid, canonical_id],
            )
        con.execute(
            "UPDATE library_items SET match_method = 'manual', match_confidence = 1.0 "
            "WHERE id = ?",
            [library_item_id],
        )
        con.execute(
            "UPDATE match_candidates SET status = 'confirmed' WHERE id = ?", [candidate_id]
        )
        con.execute(
            "UPDATE match_candidates SET status = 'rejected' "
            "WHERE library_item_id = ? AND id <> ? AND status = 'pending'",
            [library_item_id, candidate_id],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {
        "library_item_id": library_item_id,
        "item_type": item_type,
        "candidate_mbid": candidate_mbid,
    }


def reject_match(con: duckdb.DuckDBPyConnection, *, library_item_id: int) -> int:
    """Reject every pending candidate for an item (user declined them); returns the count.

    The item is left unmatched (match_method stays whatever it was, typically 'none').
    """
    rows = con.execute(
        "UPDATE match_candidates SET status = 'rejected' "
        "WHERE library_item_id = ? AND status = 'pending' RETURNING id",
        [library_item_id],
    ).fetchall()
    return len(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/db/test_repository.py -q`
Expected: PASS (all repository tests).

- [ ] **Step 5: Lint**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check src/needledrop/db/repository.py tests/db/test_repository.py`
Expected: no errors. (Note: the f-string with `{placeholders}` interpolates only a `?,?` placeholder string — values are bound parameters, so there is no injection; `table` is from a fixed internal dict, not user input.)

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/db/repository.py tests/db/test_repository.py
git commit -m "feat: add review-queue read and resolve/reject repository functions"
```

---

## Task 2: MCP tools — list/resolve/reject the review queue

**Files:**
- Modify: `src/needledrop/mcp_server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mcp_server.py` (it already has `_fresh_con`, `_call`, `create_server`, and imports). Append:

```python
def _seed_review_queue(con):
    """A present, unmatched album item with two pending release-group candidates."""
    con.execute("INSERT INTO albums (title) VALUES ('Kid A')")
    canonical_id = con.execute("SELECT id FROM albums WHERE title = 'Kid A'").fetchone()[0]
    con.execute(
        "INSERT INTO library_items "
        "(service, service_item_id, item_type, canonical_id, match_method, status) "
        "VALUES ('apple_music', 'l.kida', 'album', ?, 'none', 'present')",
        [canonical_id],
    )
    item_id = con.execute(
        "SELECT id FROM library_items WHERE service_item_id = 'l.kida'"
    ).fetchone()[0]
    for mbid, score in [("rg-good", 0.81), ("rg-meh", 0.74)]:
        con.execute(
            "INSERT INTO match_candidates "
            "(library_item_id, candidate_mbid, candidate_kind, score, method, status) "
            "VALUES (?, ?, 'release_group', ?, 'fuzzy', 'pending')",
            [item_id, mbid, score],
        )
    return item_id, canonical_id


def test_list_review_queue_tool_returns_pending_items():
    con = _fresh_con()
    _seed_review_queue(con)
    queue = _call(create_server(con), "list_review_queue")
    assert len(queue) == 1
    assert queue[0]["title"] == "Kid A"
    assert [c["candidate_mbid"] for c in queue[0]["candidates"]] == ["rg-good", "rg-meh"]


def test_resolve_match_tool_links_and_clears_queue():
    con = _fresh_con()
    item_id, canonical_id = _seed_review_queue(con)
    chosen = con.execute(
        "SELECT id FROM match_candidates WHERE candidate_mbid = 'rg-good'"
    ).fetchone()[0]
    server = create_server(con)
    result = _call(server, "resolve_match", {"candidate_id": chosen})
    assert result == {
        "library_item_id": item_id,
        "item_type": "album",
        "candidate_mbid": "rg-good",
    }
    assert con.execute(
        "SELECT release_group_mbid FROM albums WHERE id = ?", [canonical_id]
    ).fetchone()[0] == "rg-good"
    assert _call(server, "list_review_queue") == []


def test_reject_match_tool_clears_queue():
    con = _fresh_con()
    item_id, _ = _seed_review_queue(con)
    server = create_server(con)
    result = _call(server, "reject_match", {"library_item_id": item_id})
    assert result == {"rejected": 2}
    assert _call(server, "list_review_queue") == []


def test_resolve_match_tool_unknown_candidate_errors():
    con = _fresh_con()
    _seed_review_queue(con)
    from fastmcp.exceptions import ToolError
    import pytest

    async def go():
        async with Client(create_server(con)) as client:
            await client.call_tool("resolve_match", {"candidate_id": 99999})

    with pytest.raises(ToolError):
        asyncio.run(go())
```

Note: `Client`, `asyncio`, and `create_server` are already imported at the top of `tests/test_mcp_server.py`. `pytest` is already imported at module top (added in Plan 8). If the existing module top already imports `pytest`, drop the inner `import pytest` to satisfy ruff.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_mcp_server.py -q -k "review_queue or resolve_match or reject_match"`
Expected: FAIL — tools not registered (`call_tool` raises for unknown tool name).

- [ ] **Step 3: Implement the tools**

In `src/needledrop/mcp_server.py`, add aliased imports to the `from needledrop.db.repository import (...)` group (the file imports each repository helper as its own aliased statement — follow that pattern):

```python
from needledrop.db.repository import (
    get_review_queue as _get_review_queue,
)
from needledrop.db.repository import (
    reject_match as _reject_match,
)
from needledrop.db.repository import (
    resolve_match as _resolve_match,
)
```

Then register three new tools inside `create_server` (place them after `search_library`, before `trigger_sync`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_mcp_server.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + lint (CI-parity gate)**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest -q`
Expected: PASS (all prior + new tests).

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add review-queue MCP tools (list/resolve/reject)"
```

---

## Self-Review (completed)

**Spec coverage:** The "tiered matching with a review queue" feature is completed — the queue persisted by sync is now listable (`get_review_queue` / `list_review_queue`) and resolvable (`resolve_match` confirm, `reject_match` decline), via repository functions (Task 1) and MCP tools (Task 2).

**Placeholder scan:** No TBD/TODO; every code step shows complete code, including the error paths and the atomic transaction.

**Type consistency:** `resolve_match(con, *, candidate_id)` returns `{library_item_id, item_type, candidate_mbid}` — matched by both the repository test and the MCP-tool test. `reject_match(con, *, library_item_id) -> int`; the tool wraps it as `{"rejected": n}` — matched by its test. `get_review_queue` entry shape (`library_item_id, item_type, title, match_method, candidates[{candidate_id, candidate_mbid, candidate_kind, score, method, name}]`) is consistent across the function, its enrichment helper, and both layers of tests. Candidate-kind/item-type mapping (`album↔release_group`, `track↔recording`) is applied identically in `resolve_match` and the seeding helpers.

**Edge cases:** unknown candidate, non-pending candidate, kind/type mismatch, and missing canonical row all raise `ValueError` (→ `ToolError` at the MCP boundary, covered by a test). mb_* name enrichment is guarded by `table_exists`, so the queue works before `mb import` (name=None) — covered by two tests. The multi-table confirm is wrapped in BEGIN/COMMIT/ROLLBACK for atomicity, consistent with `apply_migrations` in the same DB module.

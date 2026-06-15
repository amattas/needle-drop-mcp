import asyncio
from datetime import datetime

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from needledrop.db.duckdb_store import connect, init_schema
from needledrop.db.repository import record_library_item, upsert_album
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
        "list_review_queue",
        "resolve_match",
        "reject_match",
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


def test_find_compilation_pollution_tool_with_mb_data():
    con = _fresh_con()
    # Seed mb_* tables mirroring tests/analysis/test_compilation_pollution.py
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    con.execute(
        "CREATE TABLE mb_artist_credit_name "
        "(artist_credit INTEGER, position INTEGER, artist INTEGER, "
        "name VARCHAR, join_phrase VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, type INTEGER)"
    )
    con.execute("CREATE TABLE mb_release_group_secondary_type (id INTEGER, name VARCHAR)")
    con.execute(
        "CREATE TABLE mb_release_group_secondary_type_join "
        "(release_group INTEGER, secondary_type INTEGER)"
    )
    con.execute(
        "INSERT INTO mb_release_group_secondary_type VALUES (1, 'Compilation'), (2, 'Soundtrack')"
    )
    # A compilation release group
    con.execute("INSERT INTO mb_release_group VALUES (10, 'rg-comp', 'Now 100', 50, 1)")
    con.execute("INSERT INTO mb_release_group_secondary_type_join VALUES (10, 1)")
    # Own the album whose release_group_mbid points to the compilation
    album_id = upsert_album(con, title="Now 100", release_group_mbid="rg-comp",
                            external_ids={"apple": "l.comp1"})
    record_library_item(
        con, service="apple_music", service_item_id="l.comp1", item_type="album",
        canonical_id=album_id, match_method="upc", seen_at=datetime(2026, 6, 15, 12, 0, 0),
    )
    findings = _call(create_server(con), "find_compilation_pollution")
    assert len(findings) >= 1
    assert findings[0]["finding_type"] == "compilation_pollution"


def test_trigger_sync_without_runner_reports_error():
    con = _fresh_con()
    server = create_server(con)

    async def go():
        async with Client(server) as client:
            await client.call_tool("trigger_sync", {})

    with pytest.raises(ToolError):
        asyncio.run(go())


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

    async def go():
        async with Client(create_server(con)) as client:
            await client.call_tool("resolve_match", {"candidate_id": 99999})

    with pytest.raises(ToolError):
        asyncio.run(go())


def test_find_missing_core_albums_tool_with_mb_data():
    con = _fresh_con()
    # Seed mb_* tables mirroring tests/analysis/test_missing_albums.py:
    # an artist with two studio albums; own only one → expect one missing-core-album finding.
    con.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    con.execute(
        "CREATE TABLE mb_artist_credit_name "
        "(artist_credit INTEGER, position INTEGER, artist INTEGER, "
        "name VARCHAR, join_phrase VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, type INTEGER)"
    )
    con.execute("CREATE TABLE mb_release_group_primary_type (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE mb_release_group_secondary_type (id INTEGER, name VARCHAR)")
    con.execute(
        "CREATE TABLE mb_release_group_secondary_type_join "
        "(release_group INTEGER, secondary_type INTEGER)"
    )
    con.execute("INSERT INTO mb_release_group_primary_type VALUES (1, 'Album'), (2, 'Single')")
    con.execute(
        "INSERT INTO mb_release_group_secondary_type VALUES (5, 'Live'), (6, 'Compilation')"
    )
    con.execute("INSERT INTO mb_artist VALUES (1, 'gid-lp', 'Linkin Park', 'Linkin Park')")
    con.execute("INSERT INTO mb_artist_credit_name VALUES (10, 0, 1, 'Linkin Park', '')")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'rg-ht', 'Hybrid Theory', 10, 1)")
    con.execute("INSERT INTO mb_release_group VALUES (101, 'rg-met', 'Meteora', 10, 1)")
    # Own only Hybrid Theory; Meteora is unowned → should appear as missing
    album_id = upsert_album(
        con, title="Hybrid Theory", release_group_mbid="rg-ht", external_ids={"apple": "l.ht"}
    )
    record_library_item(
        con,
        service="apple_music",
        service_item_id="l.ht",
        item_type="album",
        canonical_id=album_id,
        match_method="upc",
        seen_at=datetime(2026, 6, 15, 12, 0, 0),
    )
    findings = _call(create_server(con), "find_missing_core_albums")
    assert len(findings) >= 1
    assert findings[0]["finding_type"] == "missing_core_album"

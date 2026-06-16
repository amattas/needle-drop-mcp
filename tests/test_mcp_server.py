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


class _KeepOpen:
    """Forwards to a shared connection but makes close() a no-op, so the server's
    per-call open/close doesn't close the test's in-memory connection."""

    def __init__(self, con):
        self._con = con

    def close(self) -> None:  # noqa: D401 - per-call close is a no-op in tests
        pass

    def __getattr__(self, name):
        return getattr(self._con, name)


def _server(con, **kwargs):
    """create_server wired to the shared test connection: the per-call connect
    factory returns the same in-memory connection (kept open across calls)."""
    return create_server(lambda: _KeepOpen(con), **kwargs)


def test_db_tools_open_and_close_a_connection_per_call():
    # The server must not hold DuckDB's single-writer lock between calls: each
    # DB-backed tool opens a fresh connection via the factory and closes it when
    # the call ends.
    con = _fresh_con()
    _seed(con)
    opened = []

    class _Tracked:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

        def __getattr__(self, name):
            return getattr(con, name)

    def connect():
        tracked = _Tracked()
        opened.append(tracked)
        return tracked

    server = create_server(connect)
    _call(server, "get_library_summary")
    _call(server, "list_albums")

    assert len(opened) == 2  # one connection per tool call
    assert all(t.closed for t in opened)  # each closed when the call finished


def test_server_exposes_expected_tools():
    con = _fresh_con()
    server = _server(con)

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
        "find_duplicate_tracks",
        "find_partial_albums",
        "find_single_replaced",
        "generate_cleanup_report",
        "list_unmatched",
        "search_library",
        "list_review_queue",
        "resolve_match",
        "reject_match",
        "get_artist_collection",
        "get_album_versions",
        "get_song_detail",
        "get_album_detail",
        "search_catalog",
        "trigger_sync",
        "add_album",
        "remove_album",
        "create_playlist",
    }.issubset(names)


def test_get_library_summary_tool_counts_items():
    con = _fresh_con()
    _seed(con)
    summary = _call(_server(con), "get_library_summary")
    assert summary["album"] == 3
    assert summary["matched"] == 2
    assert summary["unmatched"] == 1


def test_list_albums_tool_returns_present_albums():
    con = _fresh_con()
    _seed(con)
    albums = _call(_server(con), "list_albums")
    titles = {a["title"] for a in albums}
    assert {"Dookie", "Dookie (Deluxe)", "Untagged Bootleg"}.issubset(titles)


def test_find_duplicate_albums_tool_reports_release_group_dupes():
    con = _fresh_con()
    _seed(con)
    findings = _call(_server(con), "find_duplicate_albums")
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "duplicate_album"


def test_list_unmatched_tool_returns_unmatched_only():
    con = _fresh_con()
    _seed(con)
    rows = _call(_server(con), "list_unmatched")
    assert [r["title"] for r in rows] == ["Untagged Bootleg"]


def test_search_library_tool_filters_by_title():
    con = _fresh_con()
    _seed(con)
    rows = _call(_server(con), "search_library", {"query": "deluxe"})
    assert [r["title"] for r in rows] == ["Dookie (Deluxe)"]


def test_generate_cleanup_report_tool_runs_scan_and_returns_findings():
    con = _fresh_con()
    _seed(con)
    report = _call(_server(con), "generate_cleanup_report")
    assert report["counts"]["duplicate_album"] == 1
    descriptions = {f["description"] for f in report["findings"]}
    assert any("versions of 'Dookie'" in d for d in descriptions)


def test_trigger_sync_tool_invokes_injected_runner():
    con = _fresh_con()
    calls = []

    def runner():
        calls.append(True)
        return {"added": 5, "removed": 1, "present": 42}

    summary = _call(_server(con, sync_runner=runner), "trigger_sync")
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
    findings = _call(_server(con), "find_compilation_pollution")
    assert len(findings) >= 1
    assert findings[0]["finding_type"] == "compilation_pollution"


def test_trigger_sync_without_runner_reports_error():
    con = _fresh_con()
    server = _server(con)

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
    queue = _call(_server(con), "list_review_queue")
    assert len(queue) == 1
    assert queue[0]["title"] == "Kid A"
    assert [c["candidate_mbid"] for c in queue[0]["candidates"]] == ["rg-good", "rg-meh"]


def test_resolve_match_tool_links_and_clears_queue():
    con = _fresh_con()
    item_id, canonical_id = _seed_review_queue(con)
    chosen = con.execute(
        "SELECT id FROM match_candidates WHERE candidate_mbid = 'rg-good'"
    ).fetchone()[0]
    server = _server(con)
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
    server = _server(con)
    result = _call(server, "reject_match", {"library_item_id": item_id})
    assert result == {"rejected": 2}
    assert _call(server, "list_review_queue") == []


def test_resolve_match_tool_unknown_candidate_errors():
    con = _fresh_con()
    _seed_review_queue(con)

    async def go():
        async with Client(_server(con)) as client:
            await client.call_tool("resolve_match", {"candidate_id": 99999})

    with pytest.raises(ToolError):
        asyncio.run(go())


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
    findings = _call(_server(con), "find_duplicate_tracks")
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "duplicate_track"


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
    findings = _call(_server(con), "find_missing_core_albums")
    assert len(findings) >= 1
    assert findings[0]["finding_type"] == "missing_core_album"


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
    result = _call(_server(con), "get_artist_collection", {"artist_mbid": "artist-rh"})
    assert [r["title"] for r in result] == ["Kid A"]
    assert result[0]["owned"] is False


def test_get_album_versions_tool_returns_editions():
    con = _fresh_con()
    con.execute("CREATE TABLE mb_release_group (id INTEGER, gid VARCHAR, name VARCHAR)")
    con.execute(
        "CREATE TABLE mb_release "
        "(id INTEGER, gid VARCHAR, name VARCHAR, barcode VARCHAR, release_group INTEGER)"
    )
    con.execute("CREATE TABLE mb_medium (release INTEGER, track_count INTEGER)")
    con.execute("INSERT INTO mb_release_group VALUES (100, 'rg-kida', 'Kid A')")
    con.execute("INSERT INTO mb_release VALUES (200, 'rel-kida-std', 'Kid A', '0123', 100)")
    con.execute("INSERT INTO mb_medium VALUES (200, 10)")
    result = _call(
        _server(con), "get_album_versions", {"release_group_mbid": "rg-kida"}
    )
    assert [r["title"] for r in result] == ["Kid A"]
    assert result[0]["track_count"] == 10
    assert result[0]["owned"] is False


def test_search_catalog_tool_uses_injected_callable():
    con = _fresh_con()
    calls = []

    def catalog_search(term, types, limit):
        calls.append((term, types, limit))
        return {"albums": [{"id": "a.1", "name": "Dookie"}], "songs": []}

    server = _server(con, catalog_search=catalog_search)
    result = _call(server, "search_catalog", {"term": "dookie"})
    assert calls == [("dookie", ("albums", "songs"), 25)]
    assert result["albums"][0]["name"] == "Dookie"


def test_search_catalog_tool_without_callable_errors():
    con = _fresh_con()
    from fastmcp.exceptions import ToolError

    async def go():
        async with Client(_server(con)) as client:
            await client.call_tool("search_catalog", {"term": "x"})

    with pytest.raises(ToolError):
        asyncio.run(go())


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
    result = _call(_server(con, mutator=mut), "add_album", {"catalog_album_id": "c.1"})
    assert result["dry_run"] is True
    assert mut.added == []


def test_add_album_tool_applies_when_not_dry_run():
    con = _fresh_con()
    mut = _FakeMutator()
    result = _call(
        _server(con, mutator=mut),
        "add_album",
        {"catalog_album_id": "c.1", "dry_run": False},
    )
    assert result["dry_run"] is False
    assert mut.added == [["c.1"]]


def test_remove_album_tool_applies_when_not_dry_run():
    con = _fresh_con()
    mut = _FakeMutator()
    _call(
        _server(con, mutator=mut),
        "remove_album",
        {"library_album_id": "l.9", "dry_run": False},
    )
    assert mut.removed == ["l.9"]


def test_create_playlist_tool_applies_when_not_dry_run():
    con = _fresh_con()
    mut = _FakeMutator()
    result = _call(
        _server(con, mutator=mut),
        "create_playlist",
        {"name": "Cleanup", "track_ids": ["s.1"], "dry_run": False},
    )
    assert mut.created == [("Cleanup", None, ("s.1",))]
    assert result["created_playlist"]["id"] == "p.1"


def test_mutating_tool_without_mutator_errors_when_applying():
    con = _fresh_con()
    from fastmcp.exceptions import ToolError

    async def go():
        async with Client(_server(con)) as client:
            await client.call_tool("add_album", {"catalog_album_id": "c.1", "dry_run": False})

    with pytest.raises(ToolError):
        asyncio.run(go())


def test_mutating_tool_dry_run_works_without_mutator():
    con = _fresh_con()
    result = _call(_server(con), "remove_album", {"library_album_id": "l.9"})
    assert result["dry_run"] is True


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
    detail = _call(_server(con), "get_album_detail", {"release_group_mbid": "rg-okc"})
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
    detail = _call(_server(con), "get_song_detail", {"recording_mbid": "rec-lucky"})
    assert [a["title"] for a in detail["library_albums"]] == ["OK Computer"]
    assert detail["appears_on"] == []

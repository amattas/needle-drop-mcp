---
sidebar_position: 4
---
# MCP Server

`needledrop mcp` runs NeedleDrop as a [Model Context
Protocol](https://modelcontextprotocol.io) server over **stdio**, exposing your
library and MusicBrainz authority as tools an LLM can call.

```bash
needledrop mcp
```

The process speaks MCP on stdin/stdout, so you don't run it directly in a
terminal for long — an MCP client launches and talks to it.

## Connecting an MCP client

Most clients take a command to spawn and communicate with over stdio. For
**Claude Desktop**, add NeedleDrop to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "needledrop": {
      "command": "needledrop",
      "args": ["mcp"],
      "env": {
        "NEEDLEDROP_DB_PATH": "/absolute/path/to/library.duckdb"
      }
    }
  }
}
```

Use an **absolute** `NEEDLEDROP_DB_PATH` so the server finds the same database
you synced, regardless of the working directory the client launches it from. If
`needledrop` isn't on the client's `PATH`, give the full path to the executable
(e.g. the one printed by `which needledrop`). Restart the client after editing
its config.

## What the client can do

Once connected, the client has the full [tool catalog](../reference/mcp-tools.md):

- **Inspect** — `get_library_summary`, `list_albums`, `search_library`, `list_unmatched`.
- **Analyze** — `find_duplicate_albums`, `find_duplicate_tracks`, `find_partial_albums`, `find_single_replaced`, `find_compilation_pollution`, `find_missing_core_albums`, and `generate_cleanup_report` to run them all and persist findings.
- **Review matches** — `list_review_queue`, `resolve_match`, `reject_match`.
- **Browse** — `get_artist_collection`, `get_album_versions`, `get_song_detail`, `get_album_detail`, `search_catalog`.
- **Act** — `add_album`, `remove_album`, `create_playlist`, and `trigger_sync`.

A typical session: ask the client to run a cleanup report, walk the duplicates
and review queue with you, then apply the decisions you approve.

## Safety model

The server is read-only by default. Two categories of writes exist, both guarded:

- **Local-only writes** — `generate_cleanup_report` persists findings to your
  DuckDB; `trigger_sync` re-pulls your library into it. Neither touches Apple.
- **Apple-library mutations** — `add_album`, `remove_album`, and `create_playlist`
  default to a **dry-run preview** and only apply when called explicitly with
  `dry_run=false`. The server also needs to be able to reach your credentials to
  apply a change; a real mutation with no connector available is rejected rather
  than silently skipped.

The connector (and therefore your credentials and any network call) is built
**lazily** — the server starts and answers read queries without credentials, and
only authenticates the first time a sync, catalog search, or applied mutation
actually needs Apple. See the [cleanup &amp; consolidation guide](cleanup.md) for the
recommended dry-run-then-apply workflow.

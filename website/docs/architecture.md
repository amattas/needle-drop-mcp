---
sidebar_position: 4
---
# Architecture

NeedleDrop is a pipeline: pull a library from Apple Music, resolve every item to a
canonical MusicBrainz identity, store both in a local DuckDB, analyze it, and
expose the result (and a few guarded actions) over MCP.

```text
Apple Music ──connector──► sync ──matcher──► canonical store (DuckDB)
                                   │                  ▲
                          MusicBrainz authority ──────┘
                          (mb_* tables in DuckDB)
                                   │
            analyses · review queue · discography · mutations
                                   │
                              MCP server  ◄──► MCP client (LLM)
```

## Connector

`AppleMusicConnector` is the only connector. It reads the library (albums, songs,
playlists) and the catalog, and — for the guarded mutations — writes (add/remove
album, create playlist). It authenticates with a locally minted ES256 developer
token plus your Music-User-Token. The abstract `MusicConnector` base is read-only;
write methods live on the Apple implementation, and the server receives them as an
injected, lazily-built dependency so it can start without credentials.

## MusicBrainz authority

The full MusicBrainz dump is materialized into the same DuckDB as `mb_*` tables
(see [MusicBrainz authority](guide/musicbrainz.md)). It's the identity source for
matching, version grouping, and discography. Code that reads `mb_*` is guarded by
a table-existence check, so the system degrades to empty results rather than
errors when the authority hasn't been imported.

## Tiered matcher

For each item the matcher tries, in order:

1. **Exact identifier** — UPC → release-group (albums), ISRC → recording (tracks).
2. **Fuzzy** — exact artist (accent/case-folded) then a rapidfuzz score on the
   title; accepted at or above `NEEDLEDROP_FUZZY_THRESHOLD`.
3. **Review candidates** — below threshold, the top candidates are queued for a
   human/LLM decision rather than guessed.

## Canonical store

Sync persists a canonical model — `artists`, `albums`, `tracks` — keyed by
MusicBrainz ids where known and Apple ids otherwise, plus a `library_items`
snapshot recording what is currently present vs. removed and how confidently each
item matched. Tracks are linked to their album, and albums carry their total
track count, which is what makes partial-album detection and consolidation views
possible. `match_candidates` holds the review queue; `cleanup_findings` persists
analysis results (and your resolve/ignore decisions).

## Analyses

Each analysis is a focused query over the canonical store (and, where needed, the
authority): duplicate albums/tracks, partial albums, single-replaced-by-album,
compilation pollution, and missing core albums. `generate_cleanup_report` runs
them together and persists the findings; settled findings don't resurface on
re-scan.

## MCP layer

`needledrop serve` builds a FastMCP server over stdio whose tools are thin
delegations to the layers above. Read tools query the local database directly;
the catalog search and mutations route through the injected, lazy connector. The
three Apple-library mutations default to a dry-run preview. See the
[MCP tools reference](reference/mcp-tools.md) and the
[cleanup workflow](guide/cleanup.md).

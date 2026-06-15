# NeedleDrop MCP — Design Specification

**Date:** 2026-06-15
**Status:** Approved design, pre-implementation
**Supersedes:** the preliminary `SPEC.md` skeleton at repo root

NeedleDrop is an MCP server for intelligent music library management — cleanup,
duplicate detection, version grouping, metadata normalization, and discography
completion. It is not a player. This document is the authoritative design;
`SPEC.md` remains as the original brain-dump and is non-binding where the two
disagree.

---

## 1. Scope

**One spec, one connector: Apple Music.** It covers all four functional phases
from the original roadmap — inventory, album management (mutating), the cleanup
assistant, and MusicBrainz-backed discography analysis — but only Apple Music as
a data source.

The abstract connector interface (`connectors/base.py`) is still defined so that
Spotify, Plex/Navidrome, and local-file connectors can be added later. Those
connectors are explicitly **out of scope** for this spec and will each get their
own spec → plan → build cycle.

The original roadmap phases become the **build order** within this single spec,
not separate specs:

1. **Inventory** — Apple auth, MB import, DuckDB schema, sync, matching, basic
   MCP server, duplicate + partial-album detection.
2. **Album management** — catalog search, album addition (mutating, dry-run),
   batch import, collection inspection.
3. **Cleanup assistant** — version grouping, metadata repair, duplicate cleanup,
   recommendation engine, the review queue.
4. **Discography** — full MusicBrainz-driven missing-album and gap analysis,
   compilation-pollution detection.

### Non-goals
- Playback or streaming.
- Connectors other than Apple Music.
- A GUI. The product surface is the MCP tool set plus an operator CLI.
- Multi-user / multi-tenant. Single local user, single DuckDB file.

---

## 2. Key decisions (and why)

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **MusicBrainz is the canonical identity authority.** Canonical album identity = MB **release-group MBID**; track identity = MB **recording MBID**; artist identity = MB **artist MBID**. | Release-groups already cluster every edition of an album, and an artist's release-groups *are* their discography. Version-grouping, dedup, and "what am I missing" become lookups against an authority instead of heuristics. |
| 2 | **The MB data is hosted locally**, not queried live. | Eliminates the ~1 req/s API rate limit; enables batch matching and full-discography sweeps. |
| 3 | **MB ingestion:** download the full Postgres dump → restore into an ephemeral local Postgres (Docker) → materialize the music-core tables into DuckDB via the DuckDB `postgres` extension → drop Postgres. | The DuckDB `postgres` extension attaches to a *running* server; it cannot parse `pg_dump`/`mbdump` archives directly. Postgres is therefore a transient loader. End state is a single portable DuckDB file; no server at query time. |
| 4 | **Apple Music user token via a bundled auth helper.** `needledrop auth apple` serves a local MusicKit JS page; the user authorizes once in a browser; the token is captured and persisted. | Apple mints Music User Tokens only through interactive MusicKit authorization — there is no headless server-side flow. Cross-platform (any browser). Re-run on expiry/revocation. |
| 5 | **Tiered matching with a review queue.** Exact identifiers (ISRC, UPC/barcode) auto-link; high-confidence fuzzy (artist + title + year over threshold) auto-links with the score stored; low-confidence and no-match become `unmatched_item` findings resolved via an MCP tool. | Keeps canonical data trustworthy. Ambiguity becomes actionable work instead of silent corruption of every downstream analysis. |
| 6 | **Secrets in the OS keyring behind a pluggable backend.** `keyring` by default (macOS Keychain / freedesktop Secret Service / Windows Credential Manager); a 1Password backend can be dropped in. Non-secret config in a plain file. | No plaintext credentials on disk; portable for any user; accommodates a zero-knowledge workflow without forcing 1Password on others. |
| 7 | **FastMCP** as the MCP framework; **stdio** transport. | Ergonomic tool definitions; stdio fits local Claude Desktop / Claude Code clients. HTTP can be added later without surface changes. |

---

## 3. Architecture & data flow

```text
needledrop mb import   →  download full MB dump
                          →  restore into ephemeral Postgres (Docker)
                          →  DuckDB ATTACH (postgres ext) + CREATE TABLE mb_* AS SELECT …
                          →  drop Postgres container
                          →  DuckDB now holds the mb_* authority tables

needledrop auth apple  →  local MusicKit JS page (http://localhost:<port>)
                          →  user clicks Authorize once
                          →  Music User Token captured → OS keyring

needledrop sync        →  Apple Music API (paginated library read)
                          →  normalize (text / album-version / artist)
                          →  Matching engine
                                 tier 1: ISRC (track) / UPC+barcode (album) exact
                                 tier 2: fuzzy artist+title+year over threshold
                                 tier 3: below threshold / no match → review queue
                          →  DuckDB: canonical entities (→ MBIDs) + library_items
                                     + match_candidates + sync_runs

needledrop scan        →  Analysis engines
                                 duplicates / partial / missing / compilation
                          →  DuckDB: cleanup_findings

needledrop serve       →  FastMCP server (stdio)
                                 read-only tools  →  query DuckDB
                                 mutating tools   →  Apple Music API (dry-run default)
```

**Runtime invariant:** the MCP server reads **DuckDB only**. Postgres and the
network are touched exclusively during `mb import`, `auth`, and `sync`.

---

## 4. Data model

The schema lives in `db/schema.sql`, evolved through `db/migrations/`.

### 4.1 MusicBrainz authority tables (`mb_*`, read-only, materialized)

A curated music-core subset of MB, sourced from the full dump so the SELECT list
can be extended later without re-architecting:

- `mb_artist`
- `mb_artist_credit`, `mb_artist_credit_name`
- `mb_release_group`, `mb_release_group_primary_type`,
  `mb_release_group_secondary_type`, `mb_release_group_secondary_type_join`
- `mb_release` (carries barcode / UPC)
- `mb_medium`
- `mb_track`
- `mb_recording`
- `mb_isrc`

These are never written by sync/analysis — only replaced by `mb import`.

### 4.2 Canonical entities (keyed to MBIDs where matched)

- **`artists`** — `id`, `mbid?`, `canonical_name`, `sort_name`,
  `external_ids_json` (Apple artist id).
- **`albums`** — `id`, `release_group_mbid?` (the version cluster),
  `release_mbid?` (the specific edition), `artist_id`, `title`, `version_class`
  (derived: standard / deluxe / expanded / remaster / anniversary / live /
  compilation / clean / explicit), `external_ids_json` (Apple album id, UPC).
- **`tracks`** — `id`, `recording_mbid?`, `album_id`, `isrc?`, `disc_number`,
  `track_number`, `duration_ms`, `external_ids_json`.

`version_class` is derived from MB release-group primary + secondary types, with
normalization used only as a display/fuzzy aid — it is no longer the grouping
authority. `version_group_key` from the original SPEC **is** `release_group_mbid`.

### 4.3 Library & operational tables

- **`library_items`** — `id`, `service` (`'apple_music'`), `service_item_id`,
  `item_type` (`album` | `track` | `playlist`), `canonical_id`,
  **`match_confidence`** (0–1), **`match_method`**
  (`isrc` | `upc` | `fuzzy` | `manual` | `none`), `added_at`, `last_seen_at`,
  `status` (`present` | `removed`).
- **`match_candidates`** (review queue) — `id`, `library_item_id`,
  `candidate_mbid`, `candidate_kind` (`release_group` | `recording` | `artist`),
  `score`, `method`, `status` (`pending` | `confirmed` | `rejected`).
- **`playlists`** — `id`, `service`, `service_playlist_id`, `name`, `description`.
- **`sync_runs`** — `id`, `service`, `started_at`, `completed_at`, `status`,
  `summary_json`.
- **`cleanup_findings`** — `id`, `finding_type`, `severity`, `entity_id`,
  `description`, `recommendation_json`, `resolved_at`, `ignored_at`.

### 4.4 Finding types

`duplicate_album`, `duplicate_track`, `partial_album`,
`single_replaced_by_album`, `missing_core_album`, `compilation_pollution`,
`metadata_problem`, **`unmatched_item`** (new — drives the review queue).

### 4.5 How the hard features reduce to queries

- **Duplicate albums** — more than one `library_item` album sharing a
  `release_group_mbid`.
- **Partial albums** — owned recordings on a release/medium fewer than the
  authoritative MB medium track count ("you own N of M").
- **Missing core albums** — an artist's core-type release-groups (album primary
  type, excluding compilation/single/live unless requested) with no
  `library_item`.
- **Compilation pollution** — release-group secondary type Compilation /
  Soundtrack / Tribute, or a Various-Artists artist credit.

All analysis queries filter on `match_confidence` so low-confidence links do not
corrupt results.

---

## 5. Module structure

Extends the original SPEC layout; **bold** entries are additions or elevations.

```text
needledrop-mcp/
├── pyproject.toml
├── README.md
├── docs/superpowers/specs/2026-06-15-needledrop-mcp-design.md
│
├── src/needledrop/
│   ├── mcp_server.py              # FastMCP app — public tool surface
│   ├── cli.py                     # typer: auth / mb import / sync / scan / serve / status   (new)
│   ├── config.py                  # non-secret config (DB path, ports, thresholds)           (new)
│   ├── keystore.py                 # pluggable secret backend (keyring default, 1Password)     (new)
│   │
│   ├── connectors/
│   │   ├── base.py                # abstract MusicConnector interface
│   │   ├── apple_music.py         # MusicKit catalog + library + mutations
│   │   └── apple_auth.py          # MusicKit JS localhost authorization helper               (new)
│   │
│   ├── musicbrainz/               # dump download, ephemeral-PG restore, DuckDB materialize   (new)
│   │
│   ├── matching/                  # tiered matcher + scoring (elevated; MB-spine core)        (new)
│   │
│   ├── normalize/                 # text.py, album_versions.py, artists.py
│   ├── db/                        # schema.sql, duckdb_store.py, migrations/
│   ├── analysis/                  # duplicates.py, partial_albums.py, missing_albums.py,
│   │                              #   compilation_pollution.py
│   ├── services/                  # sync.py, catalog.py, cleanup.py
│   └── models/                    # canonical.py, findings.py, match.py                       (match.py new)
│
└── tests/
```

Design intent: each module has one clear responsibility, a well-defined
interface, and is testable in isolation. `matching/` and `musicbrainz/` are
first-class packages because the MB-spine decision makes them central rather than
helpers buried in `catalog.py`.

---

## 6. Components

### 6.1 `connectors/apple_music.py`
Implements `base.MusicConnector`. Developer JWT signed from the `.p8` key
(Team ID + Key ID), Music User Token from the keyring. Responsibilities: read
library albums/tracks/playlists (paginated), search the catalog, add albums,
create playlists. All HTTP via `httpx`; responses parsed with `orjson` into
Pydantic models.

### 6.2 `connectors/apple_auth.py`
`needledrop auth apple` starts a short-lived local HTTP server serving a MusicKit
JS page initialized with the developer token. The user authorizes; the page POSTs
the Music User Token back to localhost; the token is written to the keyring; the
server shuts down. Re-run on expiry/revocation.

### 6.3 `musicbrainz/`
Orchestrates `needledrop mb import`: download the current full dump, stand up an
ephemeral Postgres (Docker) and restore into it, `ATTACH` from DuckDB via the
`postgres` extension, `CREATE TABLE mb_* AS SELECT …` for the music-core subset,
tear down Postgres. Idempotent and re-runnable to refresh.

### 6.4 `matching/`
The tiered matcher (decision #5). Inputs: a normalized library item. Tier 1
exact identifier lookup against `mb_*`. Tier 2 fuzzy (rapidfuzz) on
artist+title+year above a configurable threshold. Tier 3 emits `match_candidates`
rows for review. Writes `canonical_id`, `match_confidence`, `match_method` on
`library_items`.

### 6.5 `normalize/`
`text.py` (lowercase/trim/punctuation), `album_versions.py` (base title, version
classification, group key), `artists.py` (split artist credit, various-artists
detection). Used as matching aids and for display; not the identity authority.

### 6.6 `analysis/`
Pure DuckDB queries over canonical + `mb_*` tables producing `cleanup_findings`
(see §4.5).

### 6.7 `services/`
`sync.py` (pull library, full-snapshot per run, `diff_sync` against the previous
run via `last_seen_at`), `catalog.py` (catalog search + best-candidate
resolution), `cleanup.py` (run all analysis, mark/ignore findings).

### 6.8 `mcp_server.py`
FastMCP app exposing the tool set (§7) over stdio.

---

## 7. MCP tool surface

### Read-only
`sync_library`, `get_library_summary`, `search_library`, `search_catalog`,
`get_artist_collection`, `get_album_versions`, `find_duplicate_albums`,
`find_duplicate_tracks`, `find_partial_albums`, `find_single_track_albums`,
`find_compilation_pollution`, `find_missing_core_albums`,
`generate_cleanup_report`, **`list_unmatched`** (review queue).

### Mutating
`add_album`, `add_albums`, `remove_album`, `create_playlist`,
`add_album_to_playlist`, `mark_finding_resolved`, `ignore_finding`,
**`resolve_match`** (confirm/reject a candidate).

### Safety model
- Album-adding tools (`add_album`, `add_albums`, `create_playlist`,
  `add_album_to_playlist`) default to **`dry_run=True`**; they return the planned
  action without executing until called with `dry_run=False`.
- **`remove_album` is destructive** and requires an explicit confirm flag; it
  never executes implicitly. Apple's library-removal API is best-effort and
  historically limited — the tool reports outcomes honestly rather than
  guaranteeing removal.
- Every executed mutation is recorded.

---

## 8. CLI (`needledrop`)

`typer` + `rich`:

- `needledrop auth apple` — capture the Music User Token (§6.2).
- `needledrop mb import` — build/refresh the MB authority tables (§6.3).
- `needledrop sync` — pull and match the Apple library.
- `needledrop scan` — run analysis, write findings.
- `needledrop serve` — run the MCP server (stdio).
- `needledrop status` — DB path, last sync run, MB import freshness, token state.

---

## 9. Cross-cutting concerns

### 9.1 Secrets & config
`keystore.py` exposes a backend interface; the default is `keyring`. Stored:
Apple `.p8` contents (or a path reference), Team ID, Key ID, and the Music User
Token. A 1Password backend implements the same interface. `config.py` holds
non-secret settings — DuckDB path, auth-helper port, fuzzy-match threshold — in a
plain file. `.env.example` is reframed as non-secret config defaults plus
keyring-setup guidance, not a place for live credentials.

### 9.2 Testing
- **Normalization** — pure unit tests (`test_normalize_album_titles.py`, etc.).
- **Matching & analysis** — run against a small fixture DuckDB seeded with a
  handful of `mb_*` rows and canonical/library rows; fully deterministic, no
  network.
- **Apple connector** — recorded `httpx` cassettes; never hits live Apple Music
  in CI.
- **MB import** — the orchestration is integration-tested with a tiny dump
  fixture; the heavy full import is a manual/operational path, not a CI gate.
- Gate: full suite green + `ruff` clean (CI-parity) before any work is called
  done.

### 9.3 Environment & tooling
- `mamba` env named `needledrop`; newest Python the dependency set allows
  (target 3.13, fall back if a pin blocks it).
- Dependencies: `fastmcp`, `duckdb` (+ `postgres` extension), `pydantic`,
  `httpx`, `orjson`, `rapidfuzz`, `rich`, `typer`, `keyring`. `mutagen` is
  deferred with the local-files connector.
- Lint/format with `ruff`; tests with `pytest`.

---

## 10. Risks & operational notes

- **MB import is the one heavyweight operation.** The full dump is multi-GB
  compressed; restoring into Postgres and materializing into DuckDB takes real
  time and disk. It is a periodic, Docker-based op — acceptable for a local /
  home-lab setup, but the single most demanding step.
- **Apple write API is asymmetric.** Adding by catalog ID is well-supported;
  library *removal* is best-effort and historically limited. `remove_album`
  ships as best-effort with clear reporting, not a guarantee.
- **Match-confidence discipline is load-bearing.** Every analysis must filter on
  confidence; skipping that re-introduces the silent-corruption failure mode the
  tiered matcher exists to prevent.

---

## 11. Build sequence

1. Project scaffold: `pyproject.toml`, env, `config.py`, `keystore.py`, DuckDB
   schema + migrations, models.
2. `musicbrainz/` import pipeline → `mb_*` tables.
3. `connectors/apple_auth.py` + `connectors/apple_music.py` (read paths) with
   recorded-cassette tests.
4. `normalize/` + `matching/` → populate canonical entities and the review queue.
5. `services/sync.py` + `diff_sync`.
6. `analysis/` (duplicates, partial) → `cleanup_findings`.
7. `mcp_server.py` read-only tools + `serve` CLI.
8. `services/catalog.py` + mutating tools (dry-run) + `resolve_match`.
9. `analysis/` (missing, compilation) + recommendation surface in
   `generate_cleanup_report`.

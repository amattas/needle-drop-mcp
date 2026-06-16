# NeedleDrop Backlog

**Status:** The full designed system is built and merged — foundation, local
MusicBrainz import, persistence, normalization + tiered matching, Apple Music
connector, sync, all six cleanup analyses, the match review queue, catalog &
discography browse, and the dry-run-by-default mutations — plus a Docusaurus docs
site. **225 unit tests green, ruff clean.**

Everything is unit-tested with fakes/mocks. The items below are what's left:
live validation first, then known limitations, then deferred features.

---

## P0 — Live validation (never run against real services)

The code paths that touch the outside world have only been exercised against
fakes/mocks. Running them for real is the next milestone and the most likely
source of first-run surprises.

- [ ] **`auth apple login`** — the MusicKit-JS browser flow and Music-User-Token capture, end to end against live Apple.
- [ ] **`mb import`** — the real multi-GB MusicBrainz dump → ephemeral Postgres → DuckDB materialization. (Integration test exists: `pytest -m integration tests/musicbrainz/`, Docker required.)
- [ ] **Validate `mb_*` column assumptions against a real dump** — `get_album_versions` / `get_song_detail` use `mb_release.barcode`, `mb_medium.track_count`, and recording→release joins that were only verified against *seeded* test tables. Confirm they match the actual MusicBrainz schema.
- [ ] **`sync`** — pull a real Apple Music library and confirm the Pydantic models parse the live API response shapes (albums, songs, playlists, embedded catalog UPC/ISRC).
- [ ] **`serve` end-to-end** — connect a real MCP client (e.g. Claude Desktop) and exercise the tool surface against a synced DB; sanity-check a dry-run → apply mutation round-trip.

## P1 — Known limitations & hardening (non-blocking)

- [ ] **Partial-album fragility** — `find_partial_albums` relies on Apple's `track_count` plus the name-based song→album linkage; it misses when a song's album name differs from the album item's. Consider an MB-tracklist-based fallback.
- [ ] **Same-title / null-artist album fold** — `find_or_create_song_album` collapses distinct same-title albums when songs lack an artist name (rare; only affects the song→album convenience link, not album-item dedup).
- [ ] **`upsert_album` artist_id backfill** — skipped on FK-referenced album rows to avoid a DuckDB 1.5.3 FK-on-update crash; an album that gains an artist only after tracks link to it keeps a NULL artist_id. Revisit if/when DuckDB lifts the limitation.
- [ ] **`search_library` wildcards** — `%` and `_` in the query act as SQL LIKE wildcards (documented as intentional). Add an `ESCAPE` clause if literal-substring matching is wanted.
- [ ] **`duplicate_tracks` representative** — `entity_id` is the first id from an unordered `list()` aggregate (harmless; findings dedup on description). Make it deterministic (`min(track_id)`) if a stable representative is ever needed downstream.

## P2 — Deferred features (in the vision, not yet built)

- [ ] **Metadata normalization** — the `METADATA_PROBLEM` and `UNMATCHED_ITEM` finding types exist in the enums but no analysis produces them. Build the metadata-fix analysis (and the corresponding fix mutations).
- [ ] **Playlist management beyond create** — editing, reordering, removing playlists; adding/removing tracks on an existing playlist.
- [ ] **Mutation ergonomics** — batch mutations (remove N redundant editions in one call), and an undo/confirmation trail.
- [ ] **Review/cleanup from the CLI** — these are MCP-only today; a `needledrop review` / `needledrop cleanup` could drive them from the terminal.
- [ ] **Mutation safety extras** — an optional server-level allow-mutations gate and/or an audit log of applied changes (offered during design, deliberately not built).
- [ ] **Additional connectors** — Spotify et al. were explicitly out of scope (Apple-only by decision); the connector base is read-only and ready if that changes.

## P3 — Project & release meta

- [ ] **Enable GitHub Pages** — Settings → Pages → "GitHub Actions"; the `docs.yml` workflow deploys on push to `main`.
- [ ] **Expand the README** — currently a stub pointing at the design specs.
- [ ] **Cut docs versioning at first release** — the Docusaurus config has a note showing how to enable pydmp-style versioning.
- [ ] **Packaging / release** — bump from `0.1.0` and decide on distribution (PyPI publish vs. install-from-source).

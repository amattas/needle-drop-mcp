# NeedleDrop Backlog

**Status:** The full designed system is built and merged — foundation, local
MusicBrainz import, persistence, normalization + tiered matching, Apple Music
connector, sync, all six cleanup analyses, the match review queue, catalog &
discography browse, and the dry-run-by-default mutations — plus a Docusaurus docs
site. **230 unit tests green, ruff clean.**

Live validation against real Apple Music is underway (see P0): auth, full-library
sync, and the read tools/analyses are confirmed working on a real ~16k-item
library; the MusicBrainz import and the end-to-end MCP round-trip remain.

---

## P0 — Live validation (never run against real services)

The code paths that touch the outside world have only been exercised against
fakes/mocks. Running them for real is the next milestone and the most likely
source of first-run surprises.

Fixes landed during live validation: the MusicKit auth page (await `configure`,
click-to-authorize, load-race guard); a matcher guard so `sync` runs before
`mb import`; and 429/5xx retry on Apple library pagination.

- [x] **`auth apple login`** — MusicKit-JS browser flow + Music-User-Token capture. Working.
- [x] **`sync`** — full real library pulled, parsed, and persisted (16,464 items). Working.
- [x] **Read tools & analyses on real data** — summary, track→album linkage (15,038/15,056 linked), `partial_albums` (123 real findings), `duplicate_tracks` all sane.
- [ ] **`mb import`** — the real multi-GB MusicBrainz dump → ephemeral Postgres → DuckDB materialization. (Integration test: `pytest -m integration tests/musicbrainz/`, Docker required.)
- [ ] **Validate `mb_*` column assumptions against a real dump** — `get_album_versions` / `get_song_detail` / discography use `mb_release.barcode`, `mb_medium.track_count`, and recording→release joins verified only against *seeded* tables.
- [ ] **Re-sync to match the library** — after `mb import`, re-run `sync` and confirm items match and the review queue populates.
- [ ] **`serve` end-to-end** — connect a real MCP client, exercise the tools, sanity-check a dry-run → apply mutation round-trip.

## P1 — Known limitations & hardening (non-blocking)

- [ ] **Sparse catalog enrichment (UPC/ISRC)** — a real sync populated ISRC on only 99/15,056 tracks; Apple's `include=catalog` doesn't reliably inline catalog attributes for *library* resources (UPC on albums is likely similarly sparse). This weakens the exact-identifier match tier, so matching leans fuzzy → a larger review queue. Fix: resolve each library item's catalog id and fetch the catalog resource's attributes explicitly (batched) rather than relying on the embedded relationship.
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

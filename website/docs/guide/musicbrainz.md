---
sidebar_position: 3
---
# MusicBrainz Authority

NeedleDrop uses [MusicBrainz](https://musicbrainz.org/) as the canonical identity
authority — the source of truth for "this is the same album/recording", which
editions belong to one release-group, and what an artist's full discography is.
To avoid rate limits and stay fully offline, the **entire MusicBrainz dump is
hosted locally** inside your DuckDB database.

## Import it

```bash
needledrop mb import
```

This one command:

1. **Downloads** the MusicBrainz full export from the configured mirror.
2. **Loads** it into an **ephemeral Postgres** container (MusicBrainz ships its
   own schema and `COPY` data for Postgres).
3. **Materializes** the tables NeedleDrop needs into your local DuckDB as `mb_*`
   tables, using DuckDB's Postgres extension.
4. **Tears down** the Postgres container — only the DuckDB copy remains.

On success it reports the schema sequence, the dump tag, and how many tables were
materialized.

## Requirements

- **Docker** — the import spins up a temporary `postgres` container (image and
  port configurable). Nothing is left running afterward.
- **Disk** — the dump and the materialized tables are large; budget tens of GB of
  free space.
- **Time and bandwidth** — downloading and loading the full export takes a while.
  This is a deliberate one-time cost for a rate-limit-free local authority.

## What it powers

Once imported, the `mb_*` tables drive:

- **Tiered matching** — exact UPC→release-group and ISRC→recording lookups, plus
  the fuzzy artist+title fallback.
- **Version grouping** — every release (edition) under a release-group, so
  `get_album_versions` and `get_album_detail` can show you what you own vs. what
  exists.
- **Discography** — `get_artist_collection` lists an artist's release-groups;
  `find_missing_core_albums` flags studio albums you don't own.
- **Clutter detection** — `find_compilation_pollution` reads release-group
  secondary types (Compilation / Soundtrack) and the Various-Artists credit.

## Running without it

NeedleDrop degrades gracefully when the `mb_*` tables aren't present: analyses
and lookups that depend on MusicBrainz return empty results instead of erroring,
and matching falls back to identifier/fuzzy logic over whatever data it has. You
can sync and run the server before importing — but the discography-aware features
only light up once `mb import` has run.

## Refreshing

Re-run `needledrop mb import` whenever you want newer authority data. Tuning for
the download mirror, container image/port, and working directory lives in
[Configuration](../reference/configuration.md) under the `NEEDLEDROP_MB_*`
settings.

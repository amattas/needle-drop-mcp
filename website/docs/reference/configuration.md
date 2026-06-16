---
sidebar_position: 3
---
# Configuration

Non-secret settings are read from environment variables (prefix `NEEDLEDROP_`)
and an optional `.env` file in the working directory. Secrets (Apple credentials,
the user token) are **not** configured here — they live in the OS keyring, set
via [`auth apple`](../guide/authentication.md).

## Core

| Variable | Default | Description |
| --- | --- | --- |
| `NEEDLEDROP_DB_PATH` | `./library.duckdb` | Path to the DuckDB database holding the canonical library and the materialized MusicBrainz tables. Use an absolute path when launching from an MCP client. |
| `NEEDLEDROP_AUTH_PORT` | `8787` | Local port for the `auth apple login` MusicKit-JS browser flow. |
| `NEEDLEDROP_FUZZY_THRESHOLD` | `0.87` | Match confidence (0–1) at or above which a fuzzy artist+title match is accepted; below it, the item becomes a review-queue candidate. |

## MusicBrainz import

Used only by [`mb import`](../guide/musicbrainz.md).

| Variable | Default | Description |
| --- | --- | --- |
| `NEEDLEDROP_MB_DUMP_BASE_URL` | `https://data.metabrainz.org/pub/musicbrainz/data/fullexport/` | Base URL of the MusicBrainz full-export mirror. |
| `NEEDLEDROP_MB_SERVER_RAW_BASE` | `https://raw.githubusercontent.com/metabrainz/musicbrainz-server` | Base URL for fetching the MusicBrainz server's table DDL. |
| `NEEDLEDROP_MB_DATA_DIR` | `./mb-dumps` | Working directory for the downloaded dump. |
| `NEEDLEDROP_MB_POSTGRES_IMAGE` | `postgres:18` | Docker image for the ephemeral import database. |
| `NEEDLEDROP_MB_POSTGRES_CONTAINER` | `needledrop-mb-import` | Name of the ephemeral container. |
| `NEEDLEDROP_MB_POSTGRES_PORT` | `55432` | Host port mapped to the ephemeral Postgres. |
| `NEEDLEDROP_MB_POSTGRES_DB` | `musicbrainz` | Database name inside the container. |
| `NEEDLEDROP_MB_POSTGRES_USER` | `musicbrainz` | Database user inside the container. |
| `NEEDLEDROP_MB_POSTGRES_PASSWORD` | `needledrop-ephemeral` | Password for the ephemeral, throwaway container. |

## Example `.env`

```bash
NEEDLEDROP_DB_PATH=/Users/me/music/library.duckdb
NEEDLEDROP_AUTH_PORT=8910
NEEDLEDROP_FUZZY_THRESHOLD=0.9
NEEDLEDROP_MB_DATA_DIR=/Volumes/scratch/mb-dumps
```

:::note
The MusicBrainz Postgres settings describe a **throwaway** container that exists
only during `mb import` and is torn down afterward — the password is not a secret
in any meaningful sense. Real secrets never appear in environment configuration.
:::

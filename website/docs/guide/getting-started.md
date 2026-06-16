---
sidebar_position: 1
---
# Getting Started

This guide walks you through standing up NeedleDrop end to end: authenticating
with Apple Music, building the local MusicBrainz authority, syncing your
library, and exposing it to an MCP client.

## Core concepts

A few principles shape how NeedleDrop works:

- **Apple Music is the only connector.** NeedleDrop reads (and, when you ask it to, writes) your Apple Music library. It is not a player.
- **MusicBrainz is the identity authority, hosted locally.** The full MusicBrainz dump is materialized into a local DuckDB so matching, version grouping, and discography lookups run offline with no rate limits.
- **A local canonical store.** Your library is mirrored into DuckDB as canonical artists/albums/tracks plus a per-item snapshot, so analyses are fast and repeatable.
- **Tiered matching with a review queue.** Items are matched by exact identifier (UPC/ISRC), then fuzzy artist+title; anything below the confidence threshold becomes a review-queue candidate rather than a silent guess.
- **Mutations are dry-run by default.** The tools that change your real Apple Music library preview by default and only apply when explicitly told to.

## Prerequisites

- **Python 3.11+**.
- An **Apple Developer account** with a MusicKit identifier and a private key (`.p8`) — see [Authentication](authentication.md).
- **Docker** and roughly **tens of GB of free disk** plus some time, for the one-time MusicBrainz import — see [MusicBrainz authority](musicbrainz.md).

## Install

```bash
git clone https://github.com/amattas/needle-drop.git
cd needle-drop
pip install .
```

This installs the `needledrop` console command.

## The flow

### 1. Store developer credentials

```bash
needledrop auth apple set-credentials \
  --team-id TEAMID --key-id KEYID --p8 ./AuthKey.p8
```

The Team ID, Key ID, and `.p8` contents are written to your OS keyring — never to disk in the project. See [Authentication](authentication.md).

### 2. Authorize your Apple Music account

```bash
needledrop auth apple login
```

This mints a short-lived developer token, opens a local MusicKit-JS page in your
browser, and captures the resulting **Music-User-Token** into the keyring. That
token is what authorizes reads and writes against *your* library.

### 3. Build the MusicBrainz authority (one-time)

```bash
needledrop mb import
```

Downloads the MusicBrainz full export, loads it through an ephemeral Postgres
container, materializes the tables into your local DuckDB, and tears the
container down. This is heavy and only needs to be re-run when you want fresher
authority data. Details and tuning: [MusicBrainz authority](musicbrainz.md).

### 4. Sync your library

```bash
needledrop sync
```

Pulls every album, song, and playlist from your Apple Music library, matches
each against MusicBrainz, and persists the canonical model plus a present/removed
snapshot. It prints a summary like `Synced: 12 added, 3 removed, 480 present.`
Re-run it any time to reconcile; it preserves prior decisions and only re-pulls
what changed.

### 5. Serve to an MCP client

```bash
needledrop serve
```

Runs the MCP server over stdio. Point an MCP client (such as Claude Desktop) at
this command and it gains the full [tool catalog](../reference/mcp-tools.md):
library summaries, the cleanup analyses, the review queue, discography lookups,
and the guarded mutations. See [MCP server](mcp-server.md) for client config.

## What's one-time vs recurring

| Step | Cadence |
| --- | --- |
| `auth apple set-credentials` | Once (until the key rotates) |
| `auth apple login` | Occasionally (when the user token expires) |
| `mb import` | Once, then whenever you want fresher MusicBrainz data |
| `sync` | Recurring — whenever your library changes |
| `serve` | Every session (or run it persistently) |

## Where things live

NeedleDrop keeps a single DuckDB database (default `./library.duckdb`) holding
both your canonical library and the materialized MusicBrainz tables. Paths,
ports, and the match threshold are configurable — see
[Configuration](../reference/configuration.md).

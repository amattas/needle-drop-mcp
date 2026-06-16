---
sidebar_position: 1
---
# CLI

The `needledrop` command is the operator interface — credentials, the MusicBrainz
import, syncing, and launching the MCP server. Run `needledrop --help` (or any
subcommand with `--help`) for usage at any time.

```text
needledrop [COMMAND]

Commands:
  auth apple set-credentials   Store Apple developer credentials in the keystore
  auth apple login             Authorize Apple Music and capture the Music-User-Token
  mb import                    Build the local MusicBrainz authority
  sync                         Pull, match, and persist your Apple Music library
  mcp                          Run the MCP server over stdio
```

## `auth apple set-credentials`

Store your Apple MusicKit developer credentials in the OS keyring.

```bash
needledrop auth apple set-credentials \
  --team-id TEAMID --key-id KEYID --p8 ./AuthKey.p8
```

| Option | Required | Description |
| --- | --- | --- |
| `--team-id` | yes | Apple Developer Team ID |
| `--key-id` | yes | MusicKit Key ID |
| `--p8` | yes | Path to the MusicKit `.p8` private key (contents are read and stored) |

See [Authentication](../guide/authentication.md).

## `auth apple login`

Authorize your Apple Music account in the browser and capture the
**Music-User-Token**. Mints a developer token from your stored credentials,
serves a local MusicKit-JS page (default port `8787`, set via
`NEEDLEDROP_AUTH_PORT`), and stores the resulting user token in the keyring.

```bash
needledrop auth apple login
```

## `mb import`

Download the MusicBrainz full export and materialize it into the local DuckDB
(via an ephemeral Postgres container). One-time/periodic; requires Docker, disk,
and time. Prints the schema sequence, dump tag, and number of tables
materialized.

```bash
needledrop mb import
```

Tuning lives under the `NEEDLEDROP_MB_*` settings — see
[Configuration](configuration.md) and the [MusicBrainz guide](../guide/musicbrainz.md).

## `sync`

Pull every album, song, and playlist from your Apple Music library, match each
against MusicBrainz, persist the canonical model, and reconcile the present/removed
snapshot. Prints a summary:

```bash
needledrop sync
# Synced: 12 added, 3 removed, 480 present.
```

Re-run any time; it preserves prior match decisions and only re-pulls what
changed.

## `mcp`

Run the MCP server over stdio. An MCP client launches and communicates with this
process; it is not meant to be used interactively in a shell. See
[MCP server](../guide/mcp-server.md).

```bash
needledrop mcp
```

The connector is built lazily, so the server starts and answers read-only queries
without credentials; it authenticates only when a sync, catalog search, or applied
mutation first needs Apple Music.

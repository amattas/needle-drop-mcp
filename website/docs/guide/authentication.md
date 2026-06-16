---
sidebar_position: 2
---
# Authentication

NeedleDrop talks to Apple Music with two credentials: a **developer token**
(minted locally from your MusicKit private key) and a **Music-User-Token** (which
authorizes access to *your* library). Both are captured through the CLI and
stored in your OS keyring.

## What you need from Apple

From your [Apple Developer](https://developer.apple.com/) account:

- **Team ID** — your 10-character developer Team ID.
- **MusicKit Key ID** — the identifier of a MusicKit private key.
- **`.p8` private key** — the key file you download once when creating the MusicKit key.

These come from creating a *Media Identifier* and a *MusicKit private key* in the
Apple Developer portal. The `.p8` is shown for download exactly once — keep it
safe.

## Store developer credentials

```bash
needledrop auth apple set-credentials \
  --team-id TEAMID --key-id KEYID --p8 ./AuthKey.p8
```

| Option | Meaning |
| --- | --- |
| `--team-id` | Apple Developer Team ID |
| `--key-id` | MusicKit Key ID |
| `--p8` | Path to the MusicKit `.p8` private key (its contents are read and stored) |

The values are written to the keyring. From them, NeedleDrop signs a short-lived
ES256 developer token on demand — the `.p8` itself never leaves your machine in a
request.

## Authorize your account

```bash
needledrop auth apple login
```

This:

1. Mints a developer token from your stored credentials.
2. Starts a small local web server (default port `8787`) and opens a MusicKit-JS
   page in your browser.
3. You approve access; the page hands back a **Music-User-Token**, which is
   stored in the keyring.

The user token is what lets NeedleDrop read your library and — when you
explicitly apply a mutation — add, remove, or create playlists. Re-run
`auth apple login` if the token later expires.

:::tip
The login port is configurable with `NEEDLEDROP_AUTH_PORT` if `8787` is in use —
see [Configuration](../reference/configuration.md).
:::

## Where secrets live

Credentials are kept behind a **pluggable secret backend**, defaulting to your
operating system keyring (Keychain on macOS, the Secret Service / equivalent
elsewhere). NeedleDrop never writes secrets into the project directory, config
files, or the DuckDB database. If the keyring is unavailable, commands that need
credentials fail loudly rather than falling back to an insecure store.

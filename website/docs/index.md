---
slug: /
sidebar_position: 1
sidebar_label: Overview
title: NeedleDrop
---

import Link from '@docusaurus/Link';

NeedleDrop is an [MCP](https://modelcontextprotocol.io) server for intelligent
**Apple Music library management** — duplicate detection, version grouping,
compilation cleanup, discography gaps, and a review queue for low-confidence
matches. It pairs your library with a **local MusicBrainz authority** so an LLM
can reason about what to keep, remove, or add — and act on it through guarded,
dry-run-by-default tools. It is a librarian, not a player.

<div className="nd-cards">
  <Link className="nd-card" to="/guide/getting-started">
    <span className="nd-card-kicker">Guide</span>
    <span className="nd-card-title">Getting started</span>
    <span className="nd-card-desc">Install, authenticate, import MusicBrainz, sync, and serve — the full flow.</span>
  </Link>
  <Link className="nd-card" to="/guide/authentication">
    <span className="nd-card-kicker">Guide</span>
    <span className="nd-card-title">Authentication</span>
    <span className="nd-card-desc">MusicKit developer key, the browser login, and where secrets live.</span>
  </Link>
  <Link className="nd-card" to="/guide/musicbrainz">
    <span className="nd-card-kicker">Guide</span>
    <span className="nd-card-title">MusicBrainz authority</span>
    <span className="nd-card-desc">Import the full dump into a local DuckDB — the matching &amp; discography brain.</span>
  </Link>
  <Link className="nd-card" to="/guide/mcp-server">
    <span className="nd-card-kicker">Guide</span>
    <span className="nd-card-title">MCP server</span>
    <span className="nd-card-desc">Run <code>needledrop serve</code> and point Claude (or any MCP client) at it.</span>
  </Link>
  <Link className="nd-card" to="/guide/cleanup">
    <span className="nd-card-kicker">Guide</span>
    <span className="nd-card-title">Cleanup &amp; consolidation</span>
    <span className="nd-card-desc">The analyses, the review queue, and deciding which duplicate to keep.</span>
  </Link>
  <Link className="nd-card" to="/architecture">
    <span className="nd-card-kicker">Reference</span>
    <span className="nd-card-title">Architecture</span>
    <span className="nd-card-desc">Connector, MusicBrainz authority, tiered matcher, canonical store, MCP layer.</span>
  </Link>
  <Link className="nd-card" to="/reference/cli">
    <span className="nd-card-kicker">Reference</span>
    <span className="nd-card-title">CLI</span>
    <span className="nd-card-desc">Every <code>needledrop</code> command and its options.</span>
  </Link>
  <Link className="nd-card" to="/reference/mcp-tools">
    <span className="nd-card-kicker">Reference</span>
    <span className="nd-card-title">MCP tools</span>
    <span className="nd-card-desc">The full tool catalog the server exposes — read tools and guarded mutations.</span>
  </Link>
</div>

## What it does

- **Finds duplicates** — multiple owned editions of one release-group (standard / deluxe / remaster), and the same recording owned more than once.
- **Spots clutter** — compilations, soundtracks, and Various-Artists records polluting an otherwise tidy library.
- **Surfaces gaps** — studio albums by artists you own but don't have, and albums you only partly own.
- **Groups versions** — every edition of an album, with track counts and which you own, so you can consolidate confidently.
- **Reviews matches** — a queue of low-confidence MusicBrainz matches you (or the LLM) confirm or reject.
- **Acts, carefully** — add an album, remove a redundant edition, or build a playlist; every mutation is a **dry-run preview** unless you explicitly apply it.

## Install

NeedleDrop is a Python 3.11+ application. Install it from source:

```bash
git clone https://github.com/amattas/needle-drop.git
cd needle-drop
pip install .
```

This installs the `needledrop` command. (`pip install -e .` for a development checkout; `pip install ".[dev]"` adds the test/lint toolchain.)

## Quick start

```bash
# 1. Store your Apple MusicKit developer credentials (kept in the OS keyring)
needledrop auth apple set-credentials --team-id TEAMID --key-id KEYID --p8 ./AuthKey.p8

# 2. Authorize your Apple Music account in the browser (captures a Music-User-Token)
needledrop auth apple login

# 3. Build the local MusicBrainz authority (one-time, heavy — see the guide)
needledrop mb import

# 4. Pull your library, match it against MusicBrainz, and persist it
needledrop sync

# 5. Run the MCP server and point an MCP client at it
needledrop serve
```

Then connect an MCP client (e.g. Claude Desktop) to the `needledrop serve` process
over stdio — see the [MCP server guide](guide/mcp-server.md) — and ask it to scan
your library, work the review queue, and propose cleanups.

:::note
Steps 1–4 are the one-time/periodic setup. Day to day you run `needledrop serve`
(or trigger a re-sync from the MCP client). The MusicBrainz import (step 3)
requires Docker and a chunk of disk and time — see the [MusicBrainz guide](guide/musicbrainz.md).
:::

---
sidebar_position: 2
---
# MCP Tools

The tools `needledrop serve` exposes to an MCP client. All read tools operate on
your local DuckDB (and the materialized MusicBrainz authority); they're safe to
call freely. The MusicBrainz-backed tools return empty results until you've run
[`mb import`](../guide/musicbrainz.md).

## Library & inspection

| Tool | Signature | Returns |
| --- | --- | --- |
| `get_library_summary` | `()` | Counts of present items by type, plus matched/unmatched totals. |
| `list_albums` | `()` | Present library albums joined to their canonical metadata. |
| `search_library` | `(query)` | Case-insensitive substring search over present album & track titles. |
| `list_unmatched` | `()` | Present items with no MusicBrainz match (candidates for review). |

## Cleanup analyses

| Tool | Signature | Flags |
| --- | --- | --- |
| `find_duplicate_albums` | `()` | Multiple owned editions of one release-group. |
| `find_duplicate_tracks` | `()` | The same recording owned more than once. |
| `find_partial_albums` | `()` | Albums you added but only own some tracks of. |
| `find_single_replaced` | `()` | Standalone singles you also own on a full album. |
| `find_compilation_pollution` | `()` | Compilations, soundtracks, Various-Artists records. |
| `find_missing_core_albums` | `()` | Studio albums by owned artists you don't have. |
| `generate_cleanup_report` | `()` | Runs every analysis, persists findings, returns counts + open findings. |

## Review queue

| Tool | Signature | Effect |
| --- | --- | --- |
| `list_review_queue` | `()` | Present items with pending MusicBrainz candidates, scored. |
| `resolve_match` | `(candidate_id)` | Confirm a candidate as the item's match (links the canonical row, marks it manually matched). |
| `reject_match` | `(library_item_id)` | Reject all pending candidates for an item. |

## Discography & catalog

| Tool | Signature | Returns |
| --- | --- | --- |
| `get_artist_collection` | `(artist_mbid)` | An artist's full release-group discography (MusicBrainz), flagged by ownership. |
| `get_album_versions` | `(release_group_mbid)` | All release editions of a release-group, with track counts, flagged by ownership. |
| `get_song_detail` | `(recording_mbid)` | Owned albums containing a recording + the release-groups it appears on. |
| `get_album_detail` | `(release_group_mbid)` | Consolidation view: owned editions (with Apple ids + completeness) and available editions. |
| `search_catalog` | `(term, types?, limit?)` | Search the Apple Music catalog (albums/songs) by text. |

## Sync & mutations

| Tool | Signature | Effect |
| --- | --- | --- |
| `trigger_sync` | `()` | Re-pull your Apple Music library into the local database. |
| `add_album` | `(catalog_album_id, dry_run=true)` | Add a catalog album to your library. |
| `remove_album` | `(library_album_id, dry_run=true)` | Remove an album from your library. |
| `create_playlist` | `(name, description?, track_ids?, dry_run=true)` | Create a playlist in your library. |

:::warning Mutations are dry-run by default
`add_album`, `remove_album`, and `create_playlist` change your real Apple Music
library. Each **defaults to `dry_run=true`** and returns a preview without
contacting Apple. Pass `dry_run=false` to apply — and only then, with reachable
credentials, does the change happen. `add_album` takes an Apple **catalog** id
(from `search_catalog` / `get_album_detail.available_versions`); `remove_album`
takes an Apple **library** id (from `get_album_detail.owned_editions`).
:::

## Notes on identifiers

- **`recording_mbid` / `release_group_mbid` / `artist_mbid`** are MusicBrainz
  identifiers (`gid`s). The analyses surface them in their findings, so the usual
  flow is analysis → detail lookup → action.
- **`candidate_id`** comes from `list_review_queue`; **`library_item_id`** and
  Apple **catalog/library** ids come from the inspection and detail tools.

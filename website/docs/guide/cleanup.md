---
sidebar_position: 5
---
# Cleanup & Consolidation

This is the heart of NeedleDrop: turning a synced library into findings, working
the review queue so matches are trustworthy, and consolidating duplicates with
confidence. Everything here is driven through the [MCP tools](../reference/mcp-tools.md);
the workflow below is what an LLM (or you) follows.

## 1. Run a cleanup report

`generate_cleanup_report` runs every analysis, persists the findings, and returns
counts plus the open findings. The analyses:

| Finding | What it flags |
| --- | --- |
| `duplicate_album` | More than one owned edition of a single release-group (standard / deluxe / remaster). |
| `duplicate_track` | The same recording owned more than once (by recording MBID, else ISRC). |
| `partial_album` | An album you added but only own *some* tracks of. |
| `single_replaced_by_album` | A standalone single you also own on a full album — the single is redundant. |
| `compilation_pollution` | Owned albums that are compilations, soundtracks, or Various-Artists records. |
| `missing_core_album` | Studio albums by artists you own that are missing from your library. |

Findings persist across scans: anything you resolve or ignore stays decided, so
re-running the report doesn't resurface settled items.

## 2. Work the review queue

Matching is tiered — exact UPC/ISRC, then fuzzy artist+title. Anything below the
confidence threshold is **not** guessed; it becomes a review-queue entry instead.
A wrong match would corrupt the analyses, so this queue is worth clearing.

- `list_review_queue` — present items with pending MusicBrainz candidates, each
  candidate scored (and named, when the authority is imported).
- `resolve_match` — confirm a candidate (by its `candidate_id`). This links the
  item's canonical row to that MusicBrainz identity and marks it manually matched.
- `reject_match` — dismiss all candidates for an item (none of them are right).

Resolved matches immediately feed the analyses — e.g. confirming a release-group
makes that album eligible for duplicate detection.

## 3. Consolidate duplicates

The payoff: you own three versions of an album — which do you keep, which do you
delete, and should you add a different edition entirely? NeedleDrop gives the LLM
the context to decide before it touches anything.

1. **Find the set** — `find_duplicate_albums` returns each release-group you own
   more than one edition of (with their `release_group_mbid`).
2. **Inspect it** — `get_album_detail(release_group_mbid)` returns:
   - `owned_editions` — every owned edition, each with its **Apple library id**
     (for removal), `version_class`, `total_tracks`, and `owned_track_count`
     (completeness).
   - `available_versions` — all editions per MusicBrainz, flagged by ownership —
     so you can see whether a *better* edition exists that you don't own.
3. **Decide** — keep the complete deluxe, drop the partial standard, maybe add a
   remaster you're missing.
4. **Act, safely** — `remove_album(library_album_id)` and `add_album(catalog_album_id)`
   both **default to a dry-run preview**. Review the preview, then re-issue with
   `dry_run=false` to apply.

`get_song_detail(recording_mbid)` is the track-level analogue: it shows the owned
albums a recording lives on plus the release-groups it appears on per MusicBrainz —
handy for deciding whether a standalone single is safe to drop.

## 4. Apply

Mutations act on Apple Music only; your local database catches up on the next
`sync` (or a `trigger_sync` from the client). Recommended loop:

```text
generate_cleanup_report      → see what's wrong
list_review_queue / resolve  → make matches trustworthy
get_album_detail             → compare the duplicate set
remove_album (dry_run=true)  → preview
remove_album (dry_run=false) → apply
trigger_sync                 → reconcile the local snapshot
```

:::warning Dry-run is the default for a reason
`add_album`, `remove_album`, and `create_playlist` change your real Apple Music
library. They preview unless you pass `dry_run=false`. Read the preview before
applying.
:::

"""Tiered matcher: link a library item to a MusicBrainz entity via the mb_* tables.

Read-only over mb_*. Returns a MatchResult whose `mbid` is a MusicBrainz `gid`
(release-group for albums, recording for tracks). Tiers:
  1. exact identifier — UPC (album barcode) / ISRC (track),
  2. fuzzy — exact artist (accent/case-folded) then rapidfuzz on the title,
  3. neither over threshold → review-queue candidates.
Persisting matches and assigning each candidate's `library_item_id` is the sync
layer's job; candidates are returned with `library_item_id=0` as a placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from needledrop.db.duckdb_store import table_exists
from needledrop.matching.scoring import title_score
from needledrop.models.enums import CandidateKind, MatchMethod
from needledrop.models.match import MatchCandidate, MatchResult
from needledrop.normalize.album_versions import get_album_base_title
from needledrop.normalize.text import fold_accents, normalize_name

DEFAULT_THRESHOLD = 0.87
MAX_CANDIDATES = 5


@dataclass
class AlbumQuery:
    title: str
    artist_name: str | None = None
    upc: str | None = None


@dataclass
class TrackQuery:
    title: str
    artist_name: str | None = None
    isrc: str | None = None


def match_album(
    con: duckdb.DuckDBPyConnection, query: AlbumQuery, *, threshold: float = DEFAULT_THRESHOLD
) -> MatchResult:
    # No MusicBrainz authority imported yet → nothing to match against. Degrade to
    # "no match" so sync still runs (items are recorded unmatched and pick up a
    # match on a re-sync after `mb import`). All mb_* tables materialize together,
    # so mb_release_group is a sufficient sentinel.
    if not table_exists(con, "mb_release_group"):
        return MatchResult(mbid=None, confidence=0.0, method=MatchMethod.NONE)
    if query.upc:
        row = con.execute(
            "SELECT CAST(rg.gid AS VARCHAR) FROM mb_release r "
            "JOIN mb_release_group rg ON r.release_group = rg.id "
            "WHERE r.barcode = ? LIMIT 1",
            [query.upc],
        ).fetchone()
        if row:
            return MatchResult(mbid=row[0], confidence=1.0, method=MatchMethod.UPC)

    scored = _score_by_artist(
        con,
        artist_name=query.artist_name,
        target=normalize_name(get_album_base_title(query.title)),
        sql=(
            "SELECT DISTINCT CAST(rg.gid AS VARCHAR), rg.name FROM mb_release_group rg "
            "JOIN mb_artist_credit_name acn ON rg.artist_credit = acn.artist_credit "
            "JOIN mb_artist a ON acn.artist = a.id "
            "WHERE lower(strip_accents(a.name)) = ?"
        ),
        normalize_candidate=lambda name: normalize_name(get_album_base_title(name)),
    )
    return _best_or_candidates(scored, threshold, CandidateKind.RELEASE_GROUP)


def match_track(
    con: duckdb.DuckDBPyConnection, query: TrackQuery, *, threshold: float = DEFAULT_THRESHOLD
) -> MatchResult:
    # See match_album: degrade to "no match" when the MusicBrainz authority is absent.
    if not table_exists(con, "mb_release_group"):
        return MatchResult(mbid=None, confidence=0.0, method=MatchMethod.NONE)
    if query.isrc:
        row = con.execute(
            "SELECT CAST(rec.gid AS VARCHAR) FROM mb_isrc i "
            "JOIN mb_recording rec ON i.recording = rec.id "
            "WHERE i.isrc = ? LIMIT 1",
            [query.isrc],
        ).fetchone()
        if row:
            return MatchResult(mbid=row[0], confidence=1.0, method=MatchMethod.ISRC)

    scored = _score_by_artist(
        con,
        artist_name=query.artist_name,
        target=normalize_name(query.title),
        sql=(
            "SELECT DISTINCT CAST(rec.gid AS VARCHAR), rec.name FROM mb_recording rec "
            "JOIN mb_artist_credit_name acn ON rec.artist_credit = acn.artist_credit "
            "JOIN mb_artist a ON acn.artist = a.id "
            "WHERE lower(strip_accents(a.name)) = ?"
        ),
        normalize_candidate=normalize_name,
    )
    return _best_or_candidates(scored, threshold, CandidateKind.RECORDING)


def _score_by_artist(
    con: duckdb.DuckDBPyConnection, *, artist_name, target, sql, normalize_candidate
) -> list[tuple[float, str]]:
    """Fetch the named artist's entities and rapidfuzz-score their titles."""
    if not artist_name:
        return []
    rows = con.execute(sql, [fold_accents(artist_name)]).fetchall()
    scored = [(title_score(target, normalize_candidate(name)), gid) for gid, name in rows]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def _best_or_candidates(scored, threshold, kind) -> MatchResult:
    if scored and scored[0][0] >= threshold:
        return MatchResult(mbid=scored[0][1], confidence=scored[0][0], method=MatchMethod.FUZZY)
    candidates = [
        MatchCandidate(
            library_item_id=0,
            candidate_mbid=gid,
            candidate_kind=kind,
            score=score,
            method=MatchMethod.FUZZY,
        )
        for score, gid in scored[:MAX_CANDIDATES]
    ]
    return MatchResult(mbid=None, confidence=0.0, method=MatchMethod.NONE, candidates=candidates)

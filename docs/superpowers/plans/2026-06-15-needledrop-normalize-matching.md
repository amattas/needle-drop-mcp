# NeedleDrop Normalize + Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the normalization helpers and the tiered matching engine that links a library item to a MusicBrainz entity — exact identifier (UPC for albums, ISRC for tracks) → fuzzy (exact artist + rapidfuzz title) → review-queue candidates — as a read-only function over the materialized `mb_*` tables.

**Architecture:** Two packages. `normalize/` holds pure text helpers (name canonicalization, album-edition stripping + version classification, artist-credit splitting). `matching/` holds the scorer (rapidfuzz) and the matcher, which queries `mb_*` read-only and returns a `MatchResult` (mbid = MusicBrainz `gid`, confidence, method, and review candidates). The matcher does NOT write to the database or know about `library_items` — persisting matches and assigning `library_item_id`s is the sync layer's job (next plan). Everything is unit-tested; the matcher is tested against a small in-memory DuckDB seeded with a handful of `mb_*` rows (no network, no Docker).

**Tech Stack:** Python 3.13, `rapidfuzz` (already a dependency), DuckDB, stdlib `unicodedata`/`re`. Builds on merged Plans 1–3 (`models`, `db`, `mb_*` schema, connector).

**Plan series:** This was the first half of the original "Plan 4"; `services/sync.py` (pulling the Apple library, enriching it with catalog ISRC/UPC, persisting canonical entities + `library_items`, invoking this matcher, and `diff_sync`) is now its own following plan — that's where the ISRC/UPC-acquisition decision lives. Design spec: `docs/superpowers/specs/2026-06-15-needledrop-mcp-design.md` (§6.4, §6.5, decision 5).

---

## Environment notes for implementers

- Python via the project env interpreter: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python` (NOT `mamba run`). Tests e.g.: `... -m pytest tests/matching/test_matcher.py -v`.
- CI-parity gate before "done": `... -m pytest` green AND `... -m ruff check .` clean. ruff line-length is 100 — wrap long lines; with `from __future__ import annotations` present, don't quote forward-ref annotations (UP037).
- No new dependencies (`rapidfuzz` is already installed).

## Verified MusicBrainz schema facts this plan relies on (from research; tables are materialized as `mb_<table>`)

- `gid` (UUID) is the **MBID**; `id` (int) is the internal surrogate key — **all joins use `id`**, MBIDs are `gid`.
- `mb_artist(id, gid, name, sort_name)`.
- `mb_artist_credit(id, name)`; `mb_artist_credit_name(artist_credit, position, artist, name, join_phrase)` — links a credit (`artist_credit`→`mb_artist_credit.id`) to an artist (`artist`→`mb_artist.id`).
- `mb_release_group(id, gid, name, artist_credit, type)` — `type`→`mb_release_group_primary_type.id`.
- `mb_release(id, gid, name, artist_credit, release_group, barcode)` — `release_group`→`mb_release_group.id`; `barcode` is VARCHAR (UPC, leading zeros preserved).
- `mb_recording(id, gid, name, artist_credit, length)` — `length` is ms.
- `mb_isrc(id, recording, isrc)` — `recording`→`mb_recording.id`; `isrc` is the 12-char value (a recording may have several).
- No normalized-name column exists → normalize in SQL. DuckDB has `strip_accents`, `lower`, `regexp_replace`, `trim`.
- Album identity for grouping = `mb_release_group.gid`; track identity = `mb_recording.gid`.

---

## File Structure

```text
src/needledrop/
├── normalize/
│   ├── __init__.py
│   ├── text.py             # fold_accents, normalize_name
│   ├── album_versions.py   # get_album_base_title, classify_album_version
│   └── artists.py          # split_artist_credit, is_various_artists
└── matching/
    ├── __init__.py
    ├── scoring.py          # title_score (rapidfuzz)
    └── matcher.py          # AlbumQuery/TrackQuery, match_album, match_track

tests/normalize/{test_text,test_album_versions,test_artists}.py
tests/matching/{test_scoring,test_matcher}.py
```

---

### Task 1: Text normalization

**Files:**
- Create: `src/needledrop/normalize/__init__.py`
- Create: `src/needledrop/normalize/text.py`
- Test: `tests/normalize/test_text.py`

- [ ] **Step 1: Write the failing test**

`tests/normalize/test_text.py`:

```python
from needledrop.normalize.text import fold_accents, normalize_name


def test_fold_accents_lowercases_and_strips_diacritics():
    assert fold_accents("Beyoncé") == "beyonce"
    assert fold_accents("Sigur Rós") == "sigur ros"
    assert fold_accents("Jay-Z") == "jay-z"  # punctuation preserved by fold


def test_normalize_name_strips_punctuation_and_collapses():
    assert normalize_name("Beyoncé!") == "beyonce"
    assert normalize_name("Jay-Z") == "jay z"
    assert normalize_name("  AC/DC  ") == "ac dc"
    assert normalize_name("OK Computer") == "ok computer"


def test_normalize_name_empty():
    assert normalize_name("   ") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/normalize/test_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.normalize'`.

- [ ] **Step 3: Implement**

`src/needledrop/normalize/__init__.py`: empty file.

`src/needledrop/normalize/text.py`:

```python
"""General text normalization used for matching and display."""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")


def fold_accents(value: str) -> str:
    """Lowercase and strip diacritics, preserving punctuation/structure.

    Mirrors DuckDB `lower(strip_accents(...))` for exact artist-name matching.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.lower().strip()


def normalize_name(value: str) -> str:
    """Fold accents, drop punctuation, and collapse whitespace (for fuzzy keys)."""
    folded = fold_accents(value)
    no_punct = _NON_ALNUM.sub(" ", folded)
    return _WHITESPACE.sub(" ", no_punct).strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/normalize/test_text.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/normalize/__init__.py src/needledrop/normalize/text.py tests/normalize/test_text.py
git commit -m "feat: add text normalization helpers"
```

---

### Task 2: Album-edition stripping + version classification

**Files:**
- Create: `src/needledrop/normalize/album_versions.py`
- Test: `tests/normalize/test_album_versions.py`

- [ ] **Step 1: Write the failing test**

`tests/normalize/test_album_versions.py`:

```python
from needledrop.models.enums import VersionClass
from needledrop.normalize.album_versions import classify_album_version, get_album_base_title


def test_get_album_base_title_strips_parenthetical_editions():
    assert get_album_base_title("American Idiot (20th Anniversary Deluxe Edition)") == "American Idiot"
    assert get_album_base_title("Meteora (Bonus Track Version)") == "Meteora"
    assert get_album_base_title("Abbey Road [Remastered]") == "Abbey Road"


def test_get_album_base_title_strips_trailing_dash_edition():
    assert get_album_base_title("Dookie - Deluxe") == "Dookie"


def test_get_album_base_title_leaves_plain_titles():
    assert get_album_base_title("OK Computer") == "OK Computer"


def test_classify_album_version():
    assert classify_album_version("Dookie (30th Anniversary Edition)") == VersionClass.ANNIVERSARY
    assert classify_album_version("Abbey Road (Remastered)") == VersionClass.REMASTER
    assert classify_album_version("Nevermind (Deluxe Edition)") == VersionClass.DELUXE
    assert classify_album_version("MTV Unplugged (Live)") == VersionClass.LIVE
    assert classify_album_version("Dookie") == VersionClass.STANDARD


def test_classify_live_uses_word_boundary():
    # "Deliverance" contains the letters l-i-v-e but is not Live.
    assert classify_album_version("Deliverance") == VersionClass.STANDARD


def test_classify_clean_uses_trailing_word_boundary():
    # "Cleaning" must not classify as CLEAN (prefix-only match would be a bug).
    assert classify_album_version("Spring Cleaning") == VersionClass.STANDARD


def test_classify_remastered_still_matches_with_boundaries():
    assert classify_album_version("The Wall (Remaster)") == VersionClass.REMASTER
    assert classify_album_version("The Wall (Remastered)") == VersionClass.REMASTER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/normalize/test_album_versions.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/needledrop/normalize/album_versions.py`:

```python
"""Album title version intelligence: base-title extraction + version classing."""

from __future__ import annotations

import re

from needledrop.models.enums import VersionClass

_EDITION_WORDS = (
    r"deluxe|expanded|remaster(?:ed)?|anniversary|edition|version|bonus|"
    r"special|reissue|explicit|clean|mono|stereo"
)
# A parenthetical/bracketed suffix containing an edition word.
_BRACKET_EDITION = re.compile(rf"\s*[\(\[][^\)\]]*\b(?:{_EDITION_WORDS})\b[^\)\]]*[\)\]]", re.IGNORECASE)
# A trailing " - <edition>" suffix.
_DASH_EDITION = re.compile(rf"\s*-\s*(?:{_EDITION_WORDS}|single|ep)\b.*$", re.IGNORECASE)

# Ordered: first match wins (anniversary before deluxe for "Anniversary Deluxe").
_VERSION_CHECKS: tuple[tuple[str, VersionClass], ...] = (
    ("anniversary", VersionClass.ANNIVERSARY),
    ("deluxe", VersionClass.DELUXE),
    ("expanded", VersionClass.EXPANDED),
    ("remaster(?:ed)?", VersionClass.REMASTER),
    ("live", VersionClass.LIVE),
    ("explicit", VersionClass.EXPLICIT),
    ("clean", VersionClass.CLEAN),
)


def get_album_base_title(title: str) -> str:
    """Strip edition/version noise to the core album title."""
    stripped = _BRACKET_EDITION.sub("", title)
    stripped = _DASH_EDITION.sub("", stripped)
    return stripped.strip()


def classify_album_version(title: str) -> VersionClass:
    """Classify an album title's version from keyword cues (word-boundary matched)."""
    lowered = title.lower()
    for keyword, version in _VERSION_CHECKS:
        if re.search(rf"\b{keyword}\b", lowered):
            return version
    return VersionClass.STANDARD
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/normalize/test_album_versions.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/normalize/album_versions.py tests/normalize/test_album_versions.py
git commit -m "feat: add album base-title and version classification"
```

---

### Task 3: Artist-credit normalization

**Files:**
- Create: `src/needledrop/normalize/artists.py`
- Test: `tests/normalize/test_artists.py`

- [ ] **Step 1: Write the failing test**

`tests/normalize/test_artists.py`:

```python
from needledrop.normalize.artists import is_various_artists, split_artist_credit


def test_split_artist_credit_separators():
    assert split_artist_credit("Jay-Z & Kanye West") == ["Jay-Z", "Kanye West"]
    assert split_artist_credit("blink-182 feat. Robert Smith") == ["blink-182", "Robert Smith"]
    assert split_artist_credit("Calvin Harris featuring Rihanna") == ["Calvin Harris", "Rihanna"]


def test_split_artist_credit_single():
    assert split_artist_credit("Radiohead") == ["Radiohead"]


def test_is_various_artists():
    assert is_various_artists("Various Artists") is True
    assert is_various_artists("VA") is True
    assert is_various_artists("various") is True
    assert is_various_artists("Green Day") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/normalize/test_artists.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/needledrop/normalize/artists.py`:

```python
"""Artist-credit normalization."""

from __future__ import annotations

import re

from needledrop.normalize.text import normalize_name

# Splits collaboration separators. Conservative: only well-signalled joiners
# (`feat.`/`featuring`, `&`, `/`, comma) — not `and`/`x`/`vs`, which over-split
# legitimate single names. Note: "Earth, Wind & Fire" still over-splits — a known
# heuristic limitation; matching primarily uses the first (primary) credit.
_SPLIT = re.compile(r"\s*(?:,|&|/|\bfeat\.?(?=\s)|\bfeaturing\b)\s*", re.IGNORECASE)

_VARIOUS = {"various artists", "various", "va"}


def split_artist_credit(credit: str) -> list[str]:
    """Split a combined artist credit into individual artist names."""
    return [part.strip() for part in _SPLIT.split(credit) if part.strip()]


def is_various_artists(name: str) -> bool:
    """True if the name denotes a Various-Artists compilation credit."""
    return normalize_name(name) in _VARIOUS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/normalize/test_artists.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/normalize/artists.py tests/normalize/test_artists.py
git commit -m "feat: add artist-credit normalization"
```

---

### Task 4: Fuzzy scorer

**Files:**
- Create: `src/needledrop/matching/__init__.py`
- Create: `src/needledrop/matching/scoring.py`
- Test: `tests/matching/test_scoring.py`

- [ ] **Step 1: Write the failing test**

`tests/matching/test_scoring.py`:

```python
from needledrop.matching.scoring import title_score


def test_identical_titles_score_one():
    assert title_score("ok computer", "ok computer") == 1.0


def test_word_order_insensitive():
    assert title_score("computer ok", "ok computer") == 1.0


def test_close_titles_score_high():
    assert title_score("the bends", "bends") >= 0.7


def test_different_titles_score_low():
    assert title_score("kid a", "ok computer") < 0.5


def test_both_empty_score_one():
    assert title_score("", "") == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/matching/test_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/needledrop/matching/__init__.py`: empty file.

`src/needledrop/matching/scoring.py`:

```python
"""Fuzzy similarity scoring for matching."""

from __future__ import annotations

from rapidfuzz import fuzz


def title_score(a: str, b: str) -> float:
    """Word-order-insensitive similarity of two normalized titles, in [0, 1]."""
    if not a and not b:
        return 1.0
    return fuzz.token_sort_ratio(a, b) / 100.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/matching/test_scoring.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/matching/__init__.py src/needledrop/matching/scoring.py tests/matching/test_scoring.py
git commit -m "feat: add fuzzy title scorer"
```

---

### Task 5: Album matcher

**Files:**
- Create: `src/needledrop/matching/matcher.py`
- Test: `tests/matching/test_matcher.py`

- [ ] **Step 1: Write the failing test**

`tests/matching/test_matcher.py` (the `_seed` helper builds a minimal `mb_*` fixture — only the columns the matcher uses — and is reused by the track test in Task 6):

```python
import duckdb
import pytest

from needledrop.matching.matcher import AlbumQuery, match_album
from needledrop.models.enums import MatchMethod


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE mb_artist (id INTEGER, gid VARCHAR, name VARCHAR, sort_name VARCHAR)")
    c.execute("CREATE TABLE mb_artist_credit (id INTEGER, name VARCHAR)")
    c.execute(
        "CREATE TABLE mb_artist_credit_name "
        "(artist_credit INTEGER, position INTEGER, artist INTEGER, name VARCHAR, join_phrase VARCHAR)"
    )
    c.execute(
        "CREATE TABLE mb_release_group "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, type INTEGER)"
    )
    c.execute(
        "CREATE TABLE mb_release "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, release_group INTEGER, barcode VARCHAR)"
    )
    c.execute(
        "CREATE TABLE mb_recording "
        "(id INTEGER, gid VARCHAR, name VARCHAR, artist_credit INTEGER, length INTEGER)"
    )
    c.execute("CREATE TABLE mb_isrc (id INTEGER, recording INTEGER, isrc VARCHAR)")
    c.execute("INSERT INTO mb_artist VALUES (1, 'gid-radiohead', 'Radiohead', 'Radiohead')")
    c.execute("INSERT INTO mb_artist_credit VALUES (10, 'Radiohead')")
    c.execute("INSERT INTO mb_artist_credit_name VALUES (10, 0, 1, 'Radiohead', '')")
    c.execute("INSERT INTO mb_release_group VALUES (100, 'gid-okc', 'OK Computer', 10, 1)")
    c.execute("INSERT INTO mb_release_group VALUES (101, 'gid-kida', 'Kid A', 10, 1)")
    c.execute(
        "INSERT INTO mb_release VALUES (1000, 'gid-okc-rel', 'OK Computer', 10, 100, '0724385522123')"
    )
    c.execute("INSERT INTO mb_recording VALUES (5000, 'gid-karma', 'Karma Police', 10, 261000)")
    c.execute("INSERT INTO mb_isrc VALUES (1, 5000, 'GBAYE9700116')")
    return c


def test_match_album_by_upc(con):
    result = match_album(con, AlbumQuery(title="OK Computer", artist_name="Radiohead", upc="0724385522123"))
    assert result.method == MatchMethod.UPC
    assert result.mbid == "gid-okc"
    assert result.confidence == 1.0


def test_match_album_fuzzy_ignores_edition_noise(con):
    result = match_album(con, AlbumQuery(title="OK Computer (Remastered)", artist_name="Radiohead"))
    assert result.method == MatchMethod.FUZZY
    assert result.mbid == "gid-okc"
    assert result.confidence >= 0.87


def test_match_album_accented_artist_matches(con):
    # Library artist arrives accented/differently-cased; should still resolve.
    result = match_album(con, AlbumQuery(title="Kid A", artist_name="radiohead"))
    assert result.mbid == "gid-kida"


def test_match_album_no_match_returns_candidates(con):
    result = match_album(con, AlbumQuery(title="In Rainbows", artist_name="Radiohead"))
    assert result.mbid is None
    assert result.method == MatchMethod.NONE
    # Both of Radiohead's release-groups are offered as (low-scoring) candidates.
    assert {c.candidate_mbid for c in result.candidates} == {"gid-okc", "gid-kida"}


def test_match_album_unknown_artist_no_candidates(con):
    result = match_album(con, AlbumQuery(title="Whatever", artist_name="Nonexistent Band"))
    assert result.mbid is None
    assert result.candidates == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/matching/test_matcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.matching.matcher'`.

- [ ] **Step 3: Implement**

`src/needledrop/matching/matcher.py`:

```python
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
    if query.upc:
        row = con.execute(
            "SELECT rg.gid FROM mb_release r "
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
            "SELECT DISTINCT rg.gid, rg.name FROM mb_release_group rg "
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
    if query.isrc:
        row = con.execute(
            "SELECT rec.gid FROM mb_isrc i "
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
            "SELECT DISTINCT rec.gid, rec.name FROM mb_recording rec "
            "JOIN mb_artist_credit_name acn ON rec.artist_credit = acn.artist_credit "
            "JOIN mb_artist a ON acn.artist = a.id "
            "WHERE lower(strip_accents(a.name)) = ?"
        ),
        normalize_candidate=normalize_name,
    )
    return _best_or_candidates(scored, threshold, CandidateKind.RECORDING)


def _score_by_artist(con, *, artist_name, target, sql, normalize_candidate) -> list[tuple[float, str]]:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/matching/test_matcher.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/matching/matcher.py tests/matching/test_matcher.py
git commit -m "feat: add tiered album matcher"
```

---

### Task 6: Track matcher tests

**Files:**
- Modify: `tests/matching/test_matcher.py` (add track tests; `match_track` was implemented in Task 5)
- Test: `tests/matching/test_matcher.py`

> Note: `match_track` ships in `matcher.py` from Task 5 alongside `match_album` (they share `_score_by_artist`/`_best_or_candidates`). This task proves the track path end-to-end against the same `mb_*` fixture.

- [ ] **Step 1: Write the failing test**

Append to `tests/matching/test_matcher.py` (and add `match_track`, `TrackQuery` to the existing import from `needledrop.matching.matcher`):

```python
def test_match_track_by_isrc(con):
    from needledrop.matching.matcher import TrackQuery, match_track

    result = match_track(con, TrackQuery(title="Karma Police", artist_name="Radiohead", isrc="GBAYE9700116"))
    assert result.method == MatchMethod.ISRC
    assert result.mbid == "gid-karma"
    assert result.confidence == 1.0


def test_match_track_fuzzy(con):
    from needledrop.matching.matcher import TrackQuery, match_track

    result = match_track(con, TrackQuery(title="Karma Police", artist_name="Radiohead"))
    assert result.method == MatchMethod.FUZZY
    assert result.mbid == "gid-karma"


def test_match_track_no_match_returns_candidates(con):
    from needledrop.matching.matcher import TrackQuery, match_track

    result = match_track(con, TrackQuery(title="Paranoid Android", artist_name="Radiohead"))
    assert result.mbid is None
    assert result.method == MatchMethod.NONE
    assert {c.candidate_mbid for c in result.candidates} == {"gid-karma"}
```

- [ ] **Step 2: Run test to verify it fails (or passes)**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/matching/test_matcher.py -v`
Expected: the three new track tests PASS (since `match_track` already exists from Task 5). If any fail, fix `match_track`/`_score_by_artist` in `matcher.py` (do not weaken the tests) until green.

- [ ] **Step 3: Full suite + lint gate**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest`
Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 4: Commit**

```bash
git add tests/matching/test_matcher.py
git commit -m "test: cover tiered track matcher"
```

---

## Self-Review

**1. Spec coverage (spec §6.4, §6.5, decision 5; build step 4):**
- `normalize/text.py` (normalize_name) → Task 1. ✓
- `normalize/album_versions.py` (base title, version classification) → Task 2. ✓ (`make_version_group_key` from the original SPEC is intentionally NOT needed: the version-group key IS `release_group_mbid`, set by matching — noted in the spec §4.2.)
- `normalize/artists.py` (split_artist_credit, is_various_artists) → Task 3. ✓
- Tiered matcher: exact UPC/ISRC → fuzzy (artist+title) → review candidates → Tasks 5–6. ✓ Writes `canonical_id`/`match_confidence`/`match_method`: the matcher RETURNS a `MatchResult` carrying these; persistence is the sync layer (next plan) — documented in the matcher docstring and this plan's header.
- Match-confidence discipline (spec §4.5): the matcher returns a confidence the sync/analysis layers store and filter on. ✓

**2. Placeholder scan:** No TBD/TODO. Every code step shows complete code; every run step has the command and expected result. The `library_item_id=0` placeholder on returned candidates is explicitly documented (sync assigns the real id on persist) — not a stub.

**3. Type/name consistency:** `match_album(con, AlbumQuery, *, threshold)` and `match_track(con, TrackQuery, *, threshold)` signatures match their tests. `AlbumQuery(title, artist_name=None, upc=None)` / `TrackQuery(title, artist_name=None, isrc=None)` fields match test construction. `MatchResult(mbid, confidence, method, candidates)` and `MatchCandidate(library_item_id, candidate_mbid, candidate_kind, score, method, status)` are the Plan-1 models (imported from `needledrop.models.match`), and the enums (`MatchMethod.UPC/ISRC/FUZZY/NONE`, `CandidateKind.RELEASE_GROUP/RECORDING`) are the Plan-1 enums. `fold_accents`/`normalize_name` (Task 1) are used by the matcher (Task 5); `get_album_base_title` (Task 2) is used for album base-title scoring; `title_score` (Task 4) is the scorer the matcher calls. The SQL column names (`mb_release.barcode`, `mb_release.release_group`, `mb_release_group.gid`, `mb_artist_credit_name.artist_credit`/`.artist`, `mb_isrc.isrc`, `mb_isrc.recording`, `mb_recording.gid`) match the researched MusicBrainz schema.

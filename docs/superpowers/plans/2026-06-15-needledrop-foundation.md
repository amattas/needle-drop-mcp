# NeedleDrop Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the installable `needledrop` package with its configuration, secrets backend, DuckDB canonical schema + migration runner, and strongly-typed domain models — the substrate every later plan builds on.

**Architecture:** A `src/`-layout Python package. Pydantic v2 models are the in-memory domain types; DuckDB is the on-disk canonical store created from an idempotent `schema.sql` baseline plus a versioned migration runner. Secrets go through a small pluggable backend (OS keyring by default); non-secret settings come from `pydantic-settings`. No network, no MCP, no connectors yet — this plan is pure substrate and is fully unit-testable offline.

**Tech Stack:** Python 3.13 (mamba env), DuckDB, Pydantic v2, pydantic-settings, keyring, pytest, ruff.

**Plan series:** This is Plan 1 of 6 (see the design spec at `docs/superpowers/specs/2026-06-15-needledrop-mcp-design.md`). Types deferred to later plans on purpose: `CatalogAlbum`/`CatalogTrack` (Plan 6 — catalog search), the `mb_*` tables (Plan 2 — created by `mb import`, not by `schema.sql`), and the `needledrop` console-script entry point (Plan 5 — added with `cli.py`).

---

## File Structure

Files created in this plan and their single responsibility:

```text
needledrop-mcp/
├── pyproject.toml                       # packaging, deps, ruff + pytest config
├── src/needledrop/
│   ├── __init__.py                      # package version
│   ├── config.py                        # non-secret settings (Settings)
│   ├── keystore.py                       # SecretBackend protocol + KeyringBackend
│   ├── models/
│   │   ├── __init__.py
│   │   ├── enums.py                     # all domain enums (single source)
│   │   ├── canonical.py                 # CanonicalArtist/Album/Track, LibraryItem, Playlist
│   │   ├── findings.py                  # Recommendation, CleanupFinding, CleanupReport
│   │   └── match.py                     # MatchCandidate, MatchResult
│   └── db/
│       ├── __init__.py
│       ├── schema.sql                   # canonical/library/operational baseline (idempotent)
│       ├── duckdb_store.py              # connect(), init_schema(), apply_migrations()
│       └── migrations/
│           └── .gitkeep                 # versioned *.sql migrations land here later
└── tests/
    ├── test_version.py
    ├── test_config.py
    ├── test_keystore.py
    ├── models/
    │   ├── test_enums.py
    │   ├── test_canonical.py
    │   ├── test_findings.py
    │   └── test_match.py
    └── db/
        ├── test_duckdb_store.py
        └── test_migrations.py
```

---

## Prerequisites (one-time, before Task 1)

Create and activate the project environment (per project conventions — mamba/Miniforge, never base, never venv):

```bash
mamba create -y -n needledrop python=3.13
mamba activate needledrop
```

Confirm the active env is `needledrop` (not `base`) before installing anything:

```bash
python -c "import sys; print(sys.prefix)"
# Expected: a path ending in /envs/needledrop
```

You will run `pip install -e ".[dev]"` inside this env at the end of Task 1 (editable install of the local project is standard pip usage; the runtime deps it pulls are pure-Python and may alternatively be installed via `mamba install -c conda-forge ...` if preferred).

---

### Task 1: Project scaffold & tooling

**Files:**
- Create: `pyproject.toml`
- Create: `src/needledrop/__init__.py`
- Create: `src/needledrop/models/__init__.py`
- Create: `src/needledrop/db/__init__.py`
- Create: `src/needledrop/db/migrations/.gitkeep`
- Test: `tests/test_version.py`

- [ ] **Step 1: Write the failing test**

`tests/test_version.py`:

```python
def test_package_exposes_version():
    import needledrop

    assert needledrop.__version__ == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop'` (package not yet installed).

- [ ] **Step 3: Create the scaffold**

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "needledrop"
version = "0.1.0"
description = "An MCP server for intelligent music library management"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "fastmcp",
    "duckdb",
    "pydantic>=2",
    "pydantic-settings",
    "httpx",
    "orjson",
    "rapidfuzz",
    "rich",
    "typer",
    "keyring",
]

[project.optional-dependencies]
dev = ["pytest", "ruff"]

[tool.hatch.build.targets.wheel]
packages = ["src/needledrop"]

[tool.hatch.build.targets.wheel.force-include]
"src/needledrop/db/schema.sql" = "needledrop/db/schema.sql"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

`src/needledrop/__init__.py`:

```python
"""NeedleDrop — an MCP server for intelligent music library management."""

__version__ = "0.1.0"
```

`src/needledrop/models/__init__.py`:

```python
```

`src/needledrop/db/__init__.py`:

```python
```

`src/needledrop/db/migrations/.gitkeep`:

```text
```

A `README.md` is referenced by `readme`. Create a one-line placeholder so the build resolves:

`README.md`:

```markdown
# NeedleDrop MCP

An MCP server for intelligent music library management. See `docs/superpowers/specs/` for the design.
```

Then install the package into the active env:

```bash
pip install -e ".[dev]"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_version.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml README.md src/needledrop/__init__.py src/needledrop/models/__init__.py src/needledrop/db/__init__.py src/needledrop/db/migrations/.gitkeep tests/test_version.py
git commit -m "chore: scaffold needledrop package and tooling"
```

---

### Task 2: Configuration module

**Files:**
- Create: `src/needledrop/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:

```python
from pathlib import Path

from needledrop.config import Settings, load_settings


def test_defaults():
    settings = Settings()
    assert settings.db_path == Path("./library.duckdb")
    assert settings.auth_port == 8787
    assert settings.fuzzy_threshold == 0.87


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("NEEDLEDROP_DB_PATH", "/tmp/custom.duckdb")
    monkeypatch.setenv("NEEDLEDROP_FUZZY_THRESHOLD", "0.95")
    settings = load_settings()
    assert settings.db_path == Path("/tmp/custom.duckdb")
    assert settings.fuzzy_threshold == 0.95


def test_threshold_is_bounded():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(fuzzy_threshold=1.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.config'`.

- [ ] **Step 3: Write the implementation**

`src/needledrop/config.py`:

```python
"""Non-secret application configuration (DB path, ports, matching thresholds)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets are NOT stored here — see needledrop.keystore."""

    model_config = SettingsConfigDict(
        env_prefix="NEEDLEDROP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: Path = Field(default=Path("./library.duckdb"))
    auth_port: int = Field(default=8787, ge=1, le=65535)
    fuzzy_threshold: float = Field(default=0.87, ge=0.0, le=1.0)


def load_settings() -> Settings:
    """Load settings from environment and optional .env file."""
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/config.py tests/test_config.py
git commit -m "feat: add non-secret settings module"
```

---

### Task 3: Secrets backend

**Files:**
- Create: `src/needledrop/keystore.py`
- Test: `tests/test_keystore.py`

- [ ] **Step 1: Write the failing test**

`tests/test_keystore.py`:

```python
import needledrop.keystore as keystore_mod
from needledrop.keystore import KeyringBackend, get_backend, set_backend


class InMemoryBackend:
    def __init__(self):
        self._store = {}

    def get(self, key):
        return self._store.get(key)

    def set(self, key, value):
        self._store[key] = value

    def delete(self, key):
        self._store.pop(key, None)


def test_pluggable_backend_roundtrip():
    original = get_backend()
    try:
        fake = InMemoryBackend()
        set_backend(fake)
        assert get_backend() is fake
        get_backend().set("apple_user_token", "abc123")
        assert get_backend().get("apple_user_token") == "abc123"
        get_backend().delete("apple_user_token")
        assert get_backend().get("apple_user_token") is None
    finally:
        set_backend(original)


def test_keyring_backend_uses_keyring(monkeypatch):
    calls = {}

    def fake_set(service, key, value):
        calls[(service, key)] = value

    def fake_get(service, key):
        return calls.get((service, key))

    monkeypatch.setattr(keystore_mod.keyring, "set_password", fake_set)
    monkeypatch.setattr(keystore_mod.keyring, "get_password", fake_get)

    backend = KeyringBackend(service_name="needledrop-test")
    backend.set("team_id", "TEAM123")
    assert backend.get("team_id") == "TEAM123"
    assert calls[("needledrop-test", "team_id")] == "TEAM123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_keystore.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.keystore'`.

- [ ] **Step 3: Write the implementation**

`src/needledrop/keystore.py`:

```python
"""Pluggable secret storage. Default backend is the OS keyring; a 1Password
backend (or any object satisfying SecretBackend) can be swapped in via set_backend.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import keyring

SERVICE_NAME = "needledrop"


@runtime_checkable
class SecretBackend(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class KeyringBackend:
    """Stores secrets in the OS keychain via the `keyring` library."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self._service = service_name

    def get(self, key: str) -> str | None:
        return keyring.get_password(self._service, key)

    def set(self, key: str, value: str) -> None:
        keyring.set_password(self._service, key, value)

    def delete(self, key: str) -> None:
        try:
            keyring.delete_password(self._service, key)
        except keyring.errors.PasswordDeleteError:
            pass


_backend: SecretBackend = KeyringBackend()


def get_backend() -> SecretBackend:
    return _backend


def set_backend(backend: SecretBackend) -> None:
    global _backend
    _backend = backend
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_keystore.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/keystore.py tests/test_keystore.py
git commit -m "feat: add pluggable secrets backend with keyring default"
```

---

### Task 4: Domain enums

**Files:**
- Create: `src/needledrop/models/enums.py`
- Test: `tests/models/test_enums.py`

- [ ] **Step 1: Write the failing test**

`tests/models/test_enums.py`:

```python
from needledrop.models.enums import (
    CandidateKind,
    FindingSeverity,
    FindingType,
    ItemType,
    LibraryStatus,
    MatchMethod,
    MatchStatus,
    Service,
    VersionClass,
)


def test_enum_values_are_stable_strings():
    assert Service.APPLE_MUSIC.value == "apple_music"
    assert ItemType.ALBUM.value == "album"
    assert LibraryStatus.REMOVED.value == "removed"
    assert VersionClass.DELUXE.value == "deluxe"
    assert MatchMethod.ISRC.value == "isrc"
    assert MatchStatus.PENDING.value == "pending"
    assert CandidateKind.RELEASE_GROUP.value == "release_group"
    assert FindingType.UNMATCHED_ITEM.value == "unmatched_item"
    assert FindingSeverity.HIGH.value == "high"


def test_enums_are_str_subclasses():
    # str-Enum lets values serialize directly to the DB and JSON.
    assert isinstance(Service.APPLE_MUSIC, str)
    assert MatchMethod("fuzzy") is MatchMethod.FUZZY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/models/test_enums.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.models.enums'`.

- [ ] **Step 3: Write the implementation**

`src/needledrop/models/enums.py`:

```python
"""Single source of truth for domain enums shared across models and the DB layer."""

from __future__ import annotations

from enum import Enum


class Service(str, Enum):
    APPLE_MUSIC = "apple_music"


class ItemType(str, Enum):
    ALBUM = "album"
    TRACK = "track"
    PLAYLIST = "playlist"


class LibraryStatus(str, Enum):
    PRESENT = "present"
    REMOVED = "removed"


class VersionClass(str, Enum):
    STANDARD = "standard"
    DELUXE = "deluxe"
    EXPANDED = "expanded"
    REMASTER = "remaster"
    ANNIVERSARY = "anniversary"
    LIVE = "live"
    COMPILATION = "compilation"
    CLEAN = "clean"
    EXPLICIT = "explicit"
    UNKNOWN = "unknown"


class MatchMethod(str, Enum):
    ISRC = "isrc"
    UPC = "upc"
    FUZZY = "fuzzy"
    MANUAL = "manual"
    NONE = "none"


class MatchStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class CandidateKind(str, Enum):
    RELEASE_GROUP = "release_group"
    RECORDING = "recording"
    ARTIST = "artist"


class FindingType(str, Enum):
    DUPLICATE_ALBUM = "duplicate_album"
    DUPLICATE_TRACK = "duplicate_track"
    PARTIAL_ALBUM = "partial_album"
    SINGLE_REPLACED_BY_ALBUM = "single_replaced_by_album"
    MISSING_CORE_ALBUM = "missing_core_album"
    COMPILATION_POLLUTION = "compilation_pollution"
    METADATA_PROBLEM = "metadata_problem"
    UNMATCHED_ITEM = "unmatched_item"


class FindingSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/models/test_enums.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/models/enums.py tests/models/test_enums.py
git commit -m "feat: add domain enums"
```

---

### Task 5: Canonical models

**Files:**
- Create: `src/needledrop/models/canonical.py`
- Test: `tests/models/test_canonical.py`

- [ ] **Step 1: Write the failing test**

`tests/models/test_canonical.py`:

```python
from needledrop.models.canonical import (
    CanonicalAlbum,
    CanonicalArtist,
    CanonicalTrack,
    LibraryItem,
    Playlist,
)
from needledrop.models.enums import ItemType, MatchMethod, Service, VersionClass


def test_artist_defaults_external_ids_empty():
    artist = CanonicalArtist(canonical_name="Linkin Park")
    assert artist.id is None
    assert artist.mbid is None
    assert artist.external_ids == {}


def test_album_carries_mbids_and_version():
    album = CanonicalAlbum(
        title="Meteora",
        release_group_mbid="rg-123",
        release_mbid="rel-456",
        version_class=VersionClass.STANDARD,
        external_ids={"apple": "1234", "upc": "093624867", },
    )
    assert album.release_group_mbid == "rg-123"
    assert album.version_class is VersionClass.STANDARD
    assert album.external_ids["apple"] == "1234"


def test_track_optional_fields():
    track = CanonicalTrack(title="Numb", isrc="USWB10300001", track_number=8)
    assert track.recording_mbid is None
    assert track.isrc == "USWB10300001"
    assert track.track_number == 8


def test_library_item_match_defaults():
    item = LibraryItem(
        service=Service.APPLE_MUSIC,
        service_item_id="l.abc",
        item_type=ItemType.ALBUM,
    )
    assert item.match_method is MatchMethod.NONE
    assert item.match_confidence is None
    assert item.status.value == "present"


def test_playlist_minimal():
    pl = Playlist(service=Service.APPLE_MUSIC, service_playlist_id="p.1", name="Faves")
    assert pl.description is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/models/test_canonical.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.models.canonical'`.

- [ ] **Step 3: Write the implementation**

`src/needledrop/models/canonical.py`:

```python
"""Provider-independent canonical music entities and library presence records."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from needledrop.models.enums import (
    ItemType,
    LibraryStatus,
    MatchMethod,
    Service,
    VersionClass,
)


class CanonicalArtist(BaseModel):
    id: int | None = None
    mbid: str | None = None
    canonical_name: str
    sort_name: str | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)


class CanonicalAlbum(BaseModel):
    id: int | None = None
    release_group_mbid: str | None = None
    release_mbid: str | None = None
    artist_id: int | None = None
    title: str
    version_class: VersionClass | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)


class CanonicalTrack(BaseModel):
    id: int | None = None
    recording_mbid: str | None = None
    album_id: int | None = None
    artist_id: int | None = None
    title: str
    isrc: str | None = None
    disc_number: int | None = None
    track_number: int | None = None
    duration_ms: int | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)


class LibraryItem(BaseModel):
    id: int | None = None
    service: Service
    service_item_id: str
    item_type: ItemType
    canonical_id: int | None = None
    match_confidence: float | None = None
    match_method: MatchMethod = MatchMethod.NONE
    added_at: datetime | None = None
    last_seen_at: datetime | None = None
    status: LibraryStatus = LibraryStatus.PRESENT


class Playlist(BaseModel):
    id: int | None = None
    service: Service
    service_playlist_id: str
    name: str
    description: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/models/test_canonical.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/models/canonical.py tests/models/test_canonical.py
git commit -m "feat: add canonical domain models"
```

---

### Task 6: Findings models

**Files:**
- Create: `src/needledrop/models/findings.py`
- Test: `tests/models/test_findings.py`

- [ ] **Step 1: Write the failing test**

`tests/models/test_findings.py`:

```python
from needledrop.models.enums import FindingSeverity, FindingType
from needledrop.models.findings import CleanupFinding, CleanupReport, Recommendation


def test_finding_with_recommendation():
    finding = CleanupFinding(
        finding_type=FindingType.PARTIAL_ALBUM,
        severity=FindingSeverity.MEDIUM,
        entity_id=42,
        description="You own 2 of 13 tracks from 'Meteora'.",
        recommendation=Recommendation(action="add_album", detail="Add the full album"),
    )
    assert finding.recommendation.action == "add_album"
    assert finding.resolved_at is None


def test_report_counts_by_type():
    report = CleanupReport(
        findings=[
            CleanupFinding(
                finding_type=FindingType.DUPLICATE_ALBUM,
                severity=FindingSeverity.LOW,
                description="dup a",
            ),
            CleanupFinding(
                finding_type=FindingType.DUPLICATE_ALBUM,
                severity=FindingSeverity.LOW,
                description="dup b",
            ),
            CleanupFinding(
                finding_type=FindingType.PARTIAL_ALBUM,
                severity=FindingSeverity.MEDIUM,
                description="partial",
            ),
        ]
    )
    assert report.count_by_type() == {"duplicate_album": 2, "partial_album": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/models/test_findings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.models.findings'`.

- [ ] **Step 3: Write the implementation**

`src/needledrop/models/findings.py`:

```python
"""Cleanup result models produced by the analysis engines."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from needledrop.models.enums import FindingSeverity, FindingType


class Recommendation(BaseModel):
    action: str
    detail: str | None = None
    payload: dict[str, str] = Field(default_factory=dict)


class CleanupFinding(BaseModel):
    id: int | None = None
    finding_type: FindingType
    severity: FindingSeverity
    entity_id: int | None = None
    description: str
    recommendation: Recommendation | None = None
    resolved_at: datetime | None = None
    ignored_at: datetime | None = None


class CleanupReport(BaseModel):
    findings: list[CleanupFinding] = Field(default_factory=list)
    generated_at: datetime | None = None

    def count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            key = finding.finding_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/models/test_findings.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/models/findings.py tests/models/test_findings.py
git commit -m "feat: add findings models"
```

---

### Task 7: Match models

**Files:**
- Create: `src/needledrop/models/match.py`
- Test: `tests/models/test_match.py`

- [ ] **Step 1: Write the failing test**

`tests/models/test_match.py`:

```python
from needledrop.models.enums import CandidateKind, MatchMethod, MatchStatus
from needledrop.models.match import MatchCandidate, MatchResult


def test_candidate_defaults_pending():
    candidate = MatchCandidate(
        library_item_id=7,
        candidate_mbid="rg-999",
        candidate_kind=CandidateKind.RELEASE_GROUP,
        score=0.91,
        method=MatchMethod.FUZZY,
    )
    assert candidate.status is MatchStatus.PENDING
    assert candidate.candidate_kind is CandidateKind.RELEASE_GROUP


def test_result_holds_candidates():
    result = MatchResult(
        mbid=None,
        confidence=0.0,
        method=MatchMethod.NONE,
        candidates=[
            MatchCandidate(
                library_item_id=7,
                candidate_mbid="rg-1",
                candidate_kind=CandidateKind.RELEASE_GROUP,
                score=0.6,
                method=MatchMethod.FUZZY,
            )
        ],
    )
    assert result.mbid is None
    assert len(result.candidates) == 1
    assert result.candidates[0].score == 0.6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/models/test_match.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.models.match'`.

- [ ] **Step 3: Write the implementation**

`src/needledrop/models/match.py`:

```python
"""Matching models: review-queue candidates and the result of a match attempt."""

from __future__ import annotations

from pydantic import BaseModel, Field

from needledrop.models.enums import CandidateKind, MatchMethod, MatchStatus


class MatchCandidate(BaseModel):
    id: int | None = None
    library_item_id: int
    candidate_mbid: str
    candidate_kind: CandidateKind
    score: float
    method: MatchMethod
    status: MatchStatus = MatchStatus.PENDING


class MatchResult(BaseModel):
    """Outcome of matching one library item against the MB authority."""

    mbid: str | None
    confidence: float
    method: MatchMethod
    candidates: list[MatchCandidate] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/models/test_match.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/models/match.py tests/models/test_match.py
git commit -m "feat: add match models"
```

---

### Task 8: DuckDB schema + store

**Files:**
- Create: `src/needledrop/db/schema.sql`
- Create: `src/needledrop/db/duckdb_store.py`
- Test: `tests/db/test_duckdb_store.py`

- [ ] **Step 1: Write the failing test**

`tests/db/test_duckdb_store.py`:

```python
from needledrop.db.duckdb_store import connect, init_schema

EXPECTED_TABLES = {
    "artists",
    "albums",
    "tracks",
    "library_items",
    "match_candidates",
    "playlists",
    "sync_runs",
    "cleanup_findings",
}


def test_init_schema_creates_all_tables(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert EXPECTED_TABLES.issubset(names)


def test_init_schema_is_idempotent(tmp_path):
    db = tmp_path / "library.duckdb"
    con = connect(db)
    init_schema(con)
    init_schema(con)  # must not raise
    count = con.execute("SELECT count(*) FROM artists").fetchone()[0]
    assert count == 0


def test_albums_sequence_autoassigns_ids(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    con.execute("INSERT INTO artists (canonical_name) VALUES ('Green Day')")
    artist_id = con.execute("SELECT id FROM artists").fetchone()[0]
    con.execute(
        "INSERT INTO albums (artist_id, title) VALUES (?, 'Dookie')", [artist_id]
    )
    con.execute(
        "INSERT INTO albums (artist_id, title) VALUES (?, 'Insomniac')", [artist_id]
    )
    ids = [r[0] for r in con.execute("SELECT id FROM albums ORDER BY id").fetchall()]
    assert ids == [1, 2]


def test_library_items_unique_constraint(tmp_path):
    import duckdb
    import pytest

    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    con.execute(
        "INSERT INTO library_items (service, service_item_id, item_type) "
        "VALUES ('apple_music', 'l.1', 'album')"
    )
    with pytest.raises(duckdb.ConstraintException):
        con.execute(
            "INSERT INTO library_items (service, service_item_id, item_type) "
            "VALUES ('apple_music', 'l.1', 'album')"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_duckdb_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.db.duckdb_store'`.

- [ ] **Step 3: Write the implementation**

`src/needledrop/db/schema.sql`:

```sql
-- NeedleDrop canonical schema (baseline, idempotent).
-- mb_* authority tables are created by `needledrop mb import`, not here.

CREATE SEQUENCE IF NOT EXISTS seq_artists START 1;
CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_artists'),
    mbid VARCHAR,
    canonical_name VARCHAR NOT NULL,
    sort_name VARCHAR,
    external_ids_json VARCHAR NOT NULL DEFAULT '{}'
);

CREATE SEQUENCE IF NOT EXISTS seq_albums START 1;
CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_albums'),
    release_group_mbid VARCHAR,
    release_mbid VARCHAR,
    artist_id INTEGER REFERENCES artists(id),
    title VARCHAR NOT NULL,
    version_class VARCHAR,
    external_ids_json VARCHAR NOT NULL DEFAULT '{}'
);

CREATE SEQUENCE IF NOT EXISTS seq_tracks START 1;
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_tracks'),
    recording_mbid VARCHAR,
    album_id INTEGER REFERENCES albums(id),
    artist_id INTEGER REFERENCES artists(id),
    title VARCHAR NOT NULL,
    isrc VARCHAR,
    disc_number INTEGER,
    track_number INTEGER,
    duration_ms INTEGER,
    external_ids_json VARCHAR NOT NULL DEFAULT '{}'
);

-- canonical_id is a polymorphic soft reference (album or track by item_type),
-- so it is intentionally not a foreign key.
CREATE SEQUENCE IF NOT EXISTS seq_library_items START 1;
CREATE TABLE IF NOT EXISTS library_items (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_library_items'),
    service VARCHAR NOT NULL,
    service_item_id VARCHAR NOT NULL,
    item_type VARCHAR NOT NULL,
    canonical_id INTEGER,
    match_confidence DOUBLE,
    match_method VARCHAR NOT NULL DEFAULT 'none',
    added_at TIMESTAMP,
    last_seen_at TIMESTAMP,
    status VARCHAR NOT NULL DEFAULT 'present',
    UNIQUE (service, service_item_id, item_type)
);

CREATE SEQUENCE IF NOT EXISTS seq_match_candidates START 1;
CREATE TABLE IF NOT EXISTS match_candidates (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_match_candidates'),
    library_item_id INTEGER NOT NULL REFERENCES library_items(id),
    candidate_mbid VARCHAR NOT NULL,
    candidate_kind VARCHAR NOT NULL,
    score DOUBLE NOT NULL,
    method VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending'
);

CREATE SEQUENCE IF NOT EXISTS seq_playlists START 1;
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_playlists'),
    service VARCHAR NOT NULL,
    service_playlist_id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    description VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS seq_sync_runs START 1;
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_sync_runs'),
    service VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status VARCHAR NOT NULL,
    summary_json VARCHAR NOT NULL DEFAULT '{}'
);

CREATE SEQUENCE IF NOT EXISTS seq_cleanup_findings START 1;
CREATE TABLE IF NOT EXISTS cleanup_findings (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_cleanup_findings'),
    finding_type VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    entity_id INTEGER,
    description VARCHAR NOT NULL,
    recommendation_json VARCHAR NOT NULL DEFAULT '{}',
    resolved_at TIMESTAMP,
    ignored_at TIMESTAMP
);
```

`src/needledrop/db/duckdb_store.py`:

```python
"""DuckDB connection management and schema lifecycle (baseline + migrations)."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import duckdb


def connect(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) a DuckDB database at db_path."""
    return duckdb.connect(str(db_path))


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Apply the idempotent baseline schema."""
    sql = resources.files("needledrop.db").joinpath("schema.sql").read_text(encoding="utf-8")
    for statement in _split_statements(sql):
        con.execute(statement)


def apply_migrations(con: duckdb.DuckDBPyConnection, migrations_dir: str | Path) -> list[str]:
    """Apply pending *.sql migrations in lexical order; return versions applied."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version VARCHAR PRIMARY KEY, applied_at TIMESTAMP DEFAULT now())"
    )
    applied = {
        row[0] for row in con.execute("SELECT version FROM schema_migrations").fetchall()
    }
    newly_applied: list[str] = []
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        for statement in _split_statements(path.read_text(encoding="utf-8")):
            con.execute(statement)
        con.execute("INSERT INTO schema_migrations (version) VALUES (?)", [version])
        newly_applied.append(version)
    return newly_applied


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into statements, dropping comment-only lines."""
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    cleaned = "\n".join(lines)
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/db/test_duckdb_store.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/db/schema.sql src/needledrop/db/duckdb_store.py tests/db/test_duckdb_store.py
git commit -m "feat: add DuckDB canonical schema and store"
```

---

### Task 9: Migration runner

**Files:**
- Modify: `src/needledrop/db/duckdb_store.py` (already includes `apply_migrations` from Task 8 — this task adds its test coverage and verifies behavior)
- Test: `tests/db/test_migrations.py`

> Note: `apply_migrations` and `_split_statements` were written in Task 8 so the
> store module is complete in one place. This task proves the migration runner
> works end-to-end (applies once, is idempotent, and actually alters the schema).

- [ ] **Step 1: Write the failing test**

`tests/db/test_migrations.py`:

```python
from needledrop.db.duckdb_store import apply_migrations, connect, init_schema

MIGRATION_SQL = "ALTER TABLE artists ADD COLUMN country VARCHAR;"


def _write_migration(migrations_dir):
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "0001_add_artist_country.sql").write_text(MIGRATION_SQL)


def test_migration_applied_once_and_alters_schema(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    migrations_dir = tmp_path / "migrations"
    _write_migration(migrations_dir)

    applied = apply_migrations(con, migrations_dir)
    assert applied == ["0001_add_artist_country"]

    # The new column exists and is usable.
    con.execute("INSERT INTO artists (canonical_name, country) VALUES ('Muse', 'GB')")
    row = con.execute("SELECT canonical_name, country FROM artists").fetchone()
    assert row == ("Muse", "GB")


def test_migration_is_idempotent(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    migrations_dir = tmp_path / "migrations"
    _write_migration(migrations_dir)

    apply_migrations(con, migrations_dir)
    second = apply_migrations(con, migrations_dir)
    assert second == []  # nothing re-applied

    recorded = con.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    assert recorded == 1


def test_no_migrations_dir_entries_is_noop(tmp_path):
    con = connect(tmp_path / "library.duckdb")
    init_schema(con)
    empty_dir = tmp_path / "migrations"
    empty_dir.mkdir()
    assert apply_migrations(con, empty_dir) == []
```

- [ ] **Step 2: Run test to verify it fails (or passes if Task 8 is complete)**

Run: `pytest tests/db/test_migrations.py -v`
Expected: PASS if `apply_migrations` from Task 8 is correct. If it FAILS, fix `apply_migrations`/`_split_statements` in `duckdb_store.py` until green — do not weaken the test.

- [ ] **Step 3: Confirm the runner handles the real (empty) package migrations dir**

Run:

```bash
python -c "
from importlib import resources
from needledrop.db.duckdb_store import apply_migrations, connect, init_schema
con = connect(':memory:')
init_schema(con)
mig = resources.files('needledrop.db').joinpath('migrations')
print('applied:', apply_migrations(con, mig))
"
```

Expected: `applied: []` (the package ships with no migrations yet — only `.gitkeep`).

- [ ] **Step 4: Run the full suite + lint (CI-parity gate)**

Run:

```bash
pytest -q
ruff check .
```

Expected: all tests pass; ruff reports no errors.

- [ ] **Step 5: Commit**

```bash
git add tests/db/test_migrations.py
git commit -m "test: cover schema migration runner"
```

---

## Self-Review

**1. Spec coverage (Plan 1 scope = spec §11 step 1 + §4 schema + §9.1 secrets/config):**
- §4.1 `mb_*` tables — intentionally NOT created here (built by `mb import`, Plan 2). Documented in the header.
- §4.2 canonical entities → `artists`/`albums`/`tracks` tables + `CanonicalArtist/Album/Track` models. ✓ (`version_class` present; `version_group_key` = `release_group_mbid` column.)
- §4.3 library/operational tables → `library_items`, `match_candidates`, `playlists`, `sync_runs`, `cleanup_findings` + `LibraryItem`, `Playlist`, `MatchCandidate` models. ✓
- §4.4 finding types incl. `unmatched_item` → `FindingType` enum. ✓
- §9.1 secrets backend (keyring default, pluggable) + non-secret config → `keystore.py`, `config.py`. ✓
- §9.3 env/tooling (mamba 3.13, ruff, pytest) → Prerequisites + pyproject. ✓
- Deferred-by-design and noted: `CatalogAlbum`/`CatalogTrack` (Plan 6), `cli.py` entry point (Plan 5).

**2. Placeholder scan:** No "TBD/TODO/handle edge cases" steps; every code step shows complete code; every run step shows the command and expected result. ✓

**3. Type consistency:** Enums defined once in `enums.py` and imported everywhere (`MatchMethod`, `FindingType`, `VersionClass`, `CandidateKind`, etc.). `LibraryItem.match_method: MatchMethod`, `MatchCandidate.candidate_kind: CandidateKind`, schema column names (`release_group_mbid`, `match_confidence`, `match_method`, `version_class`) match the model field names. `apply_migrations`/`init_schema`/`_split_statements` signatures are consistent between Task 8 (definition) and Task 9 (use). ✓

# NeedleDrop MusicBrainz Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `needledrop mb import` — download the full MusicBrainz export, load it into an ephemeral `postgres:18` using MusicBrainz's own versioned schema DDL (no third-party importer), materialize the entire core schema into local DuckDB `mb_*` tables via the DuckDB `postgres` extension, then drop Postgres.

**Architecture:** A `musicbrainz/` package split into focused, individually-testable modules — `dumps.py` (acquire/verify/extract the export), `schema_sql.py` (map the dump's `SCHEMA_SEQUENCE` to the matching `musicbrainz-server` git tag and fetch the DDL), `postgres.py` (ephemeral Docker Postgres lifecycle via the `docker` CLI), `materialize.py` (DuckDB `ATTACH` + `CREATE TABLE mb_<t> AS SELECT`), and `importer.py` (the orchestrator with guaranteed teardown). Pure logic (URL/SQL/argv builders, the version map, orchestration sequencing) is unit-tested with mocks; the real Postgres↔DuckDB bridge is proven by one Docker-gated integration test against a tiny synthetic schema; the full ~7 GB import is a documented manual run, never a CI gate.

**Tech Stack:** Python 3.13, DuckDB (+ `postgres` extension), `httpx`, `tarfile`/`hashlib` (stdlib), the `docker` CLI, `typer`. Builds on the merged Plan 1 foundation (`config.py`, `db/duckdb_store.py`).

**Plan series:** Plan 2 of 6 (design spec: `docs/superpowers/specs/2026-06-15-needledrop-mcp-design.md`, §2 decision 3, §4.1, §6.3). This plan introduces `cli.py` and the `needledrop` console entry point (the Plan-1 note deferred it; this is where it's first needed).

---

## Environment notes for implementers

- Run Python via the project env interpreter (the env is `needledrop`, Python 3.13): `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python`. Run tests as e.g. `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_dumps.py -v`. (`mamba run` is unreliable in this environment; use the direct interpreter.)
- After adding new imports/deps, reinstall editable so they resolve: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pip install --no-cache-dir -e ".[dev]"`.
- The CI-parity gate is `... -m pytest` (full suite green) **and** `... -m ruff check .` (clean). Run both before declaring any task done.
- Tests must NOT download the real dump or pull Docker images, except the single integration test in Task 10, which is `@pytest.mark.skipif`-guarded on `docker` being present.

## Verified facts this plan relies on (from research; sources in the spec)

- Full export index: `https://data.metabrainz.org/pub/musicbrainz/data/fullexport/`; `LATEST` is a text file naming the dated dir (e.g. `20260613-002047`); each dir has `mbdump.tar.bz2`, a sibling `SCHEMA_SEQUENCE` (a single integer), and `SHA256SUMS`.
- `mbdump.tar.bz2` extracts to an `mbdump/` dir of headerless, tab-delimited, `\N`-null `COPY` text files named exactly after their `musicbrainz`-schema table, plus UPPERCASE metadata files (`SCHEMA_SEQUENCE`, `TIMESTAMP`, …).
- DDL lives in `musicbrainz-server` at `admin/sql/{Extensions,CreateCollations,CreateTypes,CreateTables}.sql`, applied in that order; **schema sequence 31 ⇄ tag `v-2026-05-11.0-schema-change`**, 30 ⇄ `v-2025-05-23.0-schema-change`. PKs/FKs/indexes are NOT needed (read-only/transient).
- MB's current schema requires **PostgreSQL 18**; the official `postgres:18` image bundles ICU and the `cube`/`earthdistance`/`unaccent` contrib modules the DDL needs.
- DuckDB: `INSTALL postgres; LOAD postgres; ATTACH '<libpq conn str>' AS pg (TYPE postgres, READ_ONLY);` then `CREATE TABLE mb_<t> AS SELECT * FROM pg.musicbrainz."<t>"`.

---

## File Structure

```text
src/needledrop/
├── config.py                       # MODIFY: add MB import settings
├── cli.py                          # NEW: typer app + `needledrop mb import`
└── musicbrainz/                    # NEW package
    ├── __init__.py
    ├── dumps.py                    # acquire/verify/extract the export
    ├── schema_sql.py               # SCHEMA_SEQUENCE → tag, DDL URLs
    ├── postgres.py                 # ephemeral postgres:18 via docker CLI
    ├── materialize.py              # DuckDB ATTACH + materialize mb_*
    └── importer.py                 # orchestrator (guaranteed teardown)

pyproject.toml                      # MODIFY: add console_script entry point

tests/musicbrainz/
├── test_dumps.py
├── test_schema_sql.py
├── test_postgres.py
├── test_materialize.py
├── test_importer.py
└── test_import_integration.py      # Docker-gated
tests/test_cli.py
```

---

### Task 1: MusicBrainz import settings

**Files:**
- Modify: `src/needledrop/config.py`
- Test: `tests/test_config_mb.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config_mb.py`:

```python
from needledrop.config import Settings


def test_mb_defaults():
    s = Settings()
    assert s.mb_dump_base_url.startswith("https://data.metabrainz.org/")
    assert s.mb_server_raw_base.startswith("https://raw.githubusercontent.com/metabrainz/")
    assert s.mb_postgres_image == "postgres:18"
    assert s.mb_postgres_container == "needledrop-mb-import"
    assert s.mb_postgres_port == 55432
    assert s.mb_postgres_db == "musicbrainz"
    assert s.mb_postgres_user == "musicbrainz"
    assert s.mb_postgres_password  # non-empty throwaway default


def test_mb_env_override(monkeypatch):
    monkeypatch.setenv("NEEDLEDROP_MB_POSTGRES_PORT", "5599")
    assert Settings().mb_postgres_port == 5599
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_config_mb.py -v`
Expected: FAIL — `AttributeError`/validation error (fields not defined yet).

- [ ] **Step 3: Add the fields to `Settings`**

In `src/needledrop/config.py`, add these fields to the `Settings` class (after `fuzzy_threshold`). The Postgres password is a throwaway credential for a local, ephemeral container only — not a real secret — so a default is acceptable here.

```python
    # --- MusicBrainz import (used only by `needledrop mb import`) ---
    mb_dump_base_url: str = Field(
        default="https://data.metabrainz.org/pub/musicbrainz/data/fullexport/"
    )
    mb_server_raw_base: str = Field(
        default="https://raw.githubusercontent.com/metabrainz/musicbrainz-server"
    )
    mb_data_dir: Path = Field(default=Path("./mb-dumps"))
    mb_postgres_image: str = Field(default="postgres:18")
    mb_postgres_container: str = Field(default="needledrop-mb-import")
    mb_postgres_port: int = Field(default=55432, ge=1, le=65535)
    mb_postgres_db: str = Field(default="musicbrainz")
    mb_postgres_user: str = Field(default="musicbrainz")
    mb_postgres_password: str = Field(default="needledrop-ephemeral")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_config_mb.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/config.py tests/test_config_mb.py
git commit -m "feat: add MusicBrainz import settings"
```

---

### Task 2: Dump parsing helpers (pure, no I/O)

**Files:**
- Create: `src/needledrop/musicbrainz/__init__.py`
- Create: `src/needledrop/musicbrainz/dumps.py`
- Test: `tests/musicbrainz/test_dumps.py`

- [ ] **Step 1: Write the failing test**

`tests/musicbrainz/test_dumps.py`:

```python
from needledrop.musicbrainz.dumps import (
    fullexport_url,
    list_table_files,
    parse_sha256sums,
    read_schema_sequence,
    resolve_latest,
    sha256_file,
)


def test_resolve_latest_strips():
    assert resolve_latest("20260613-002047\n") == "20260613-002047"


def test_resolve_latest_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        resolve_latest("   \n")


def test_fullexport_url_joins():
    url = fullexport_url(
        "https://data.metabrainz.org/pub/musicbrainz/data/fullexport/",
        "20260613-002047",
        "mbdump.tar.bz2",
    )
    assert url == (
        "https://data.metabrainz.org/pub/musicbrainz/data/fullexport/"
        "20260613-002047/mbdump.tar.bz2"
    )


def test_read_schema_sequence(tmp_path):
    p = tmp_path / "SCHEMA_SEQUENCE"
    p.write_text("31\n")
    assert read_schema_sequence(p) == 31


def test_parse_sha256sums():
    body = "abc123  mbdump.tar.bz2\ndef456 *other.tar.bz2\n\n"
    assert parse_sha256sums(body) == {
        "mbdump.tar.bz2": "abc123",
        "other.tar.bz2": "def456",
    }


def test_sha256_file(tmp_path):
    import hashlib

    p = tmp_path / "f.bin"
    p.write_bytes(b"needledrop")
    assert sha256_file(p) == hashlib.sha256(b"needledrop").hexdigest()


def test_list_table_files_skips_metadata(tmp_path):
    mbdump = tmp_path / "mbdump"
    mbdump.mkdir()
    (mbdump / "artist").write_text("")
    (mbdump / "release_group").write_text("")
    (mbdump / "SCHEMA_SEQUENCE").write_text("31")
    (mbdump / "TIMESTAMP").write_text("x")
    names = [name for name, _ in list_table_files(mbdump)]
    assert names == ["artist", "release_group"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_dumps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.musicbrainz'`.

- [ ] **Step 3: Implement**

`src/needledrop/musicbrainz/__init__.py`: empty file.

`src/needledrop/musicbrainz/dumps.py`:

```python
"""Acquire, verify, and extract the MusicBrainz full export."""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path


def resolve_latest(latest_body: str) -> str:
    """Parse the fullexport `LATEST` file body into the dated directory name."""
    name = latest_body.strip()
    if not name:
        raise ValueError("LATEST file is empty")
    return name


def fullexport_url(base_url: str, latest: str, filename: str) -> str:
    """Build the URL for a file inside a dated fullexport directory."""
    return f"{base_url.rstrip('/')}/{latest}/{filename}"


def read_schema_sequence(path: str | Path) -> int:
    """Read a `SCHEMA_SEQUENCE` file (a single integer)."""
    return int(Path(path).read_text(encoding="utf-8").strip())


def parse_sha256sums(body: str) -> dict[str, str]:
    """Parse a `SHA256SUMS` file ('<hash>  <filename>' per line) into {filename: hash}."""
    sums: dict[str, str] = {}
    for line in body.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            sums[parts[-1].lstrip("*")] = parts[0]
    return sums


def sha256_file(path: str | Path) -> str:
    """Stream-hash a file with SHA-256."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def list_table_files(mbdump_dir: str | Path) -> list[tuple[str, Path]]:
    """List (table_name, path) for each data file under `mbdump/`.

    Metadata files (UPPERCASE names like SCHEMA_SEQUENCE, TIMESTAMP) are skipped;
    table data files are lowercase, named exactly after their musicbrainz table.
    """
    out: list[tuple[str, Path]] = []
    for p in sorted(Path(mbdump_dir).iterdir()):
        if p.is_file() and not p.name.isupper():
            out.append((p.name, p))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_dumps.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/musicbrainz/__init__.py src/needledrop/musicbrainz/dumps.py tests/musicbrainz/test_dumps.py
git commit -m "feat: add MusicBrainz dump parsing helpers"
```

---

### Task 3: Dump download + extraction (I/O)

**Files:**
- Modify: `src/needledrop/musicbrainz/dumps.py`
- Test: `tests/musicbrainz/test_dumps_io.py`

- [ ] **Step 1: Write the failing test**

`tests/musicbrainz/test_dumps_io.py`:

```python
import io
import tarfile

import httpx

from needledrop.musicbrainz.dumps import download_file, extract_tarball


def test_download_file_streams(tmp_path):
    def handler(request):
        return httpx.Response(200, content=b"hello-dump")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    dest = tmp_path / "sub" / "file.bin"
    out = download_file("https://example.test/file.bin", dest, client=client)
    assert out == dest
    assert dest.read_bytes() == b"hello-dump"


def test_download_file_raises_on_error(tmp_path):
    import pytest

    def handler(request):
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        download_file("https://example.test/missing", tmp_path / "x", client=client)


def test_extract_tarball_returns_mbdump_dir(tmp_path):
    # Build a tiny .tar.bz2 containing mbdump/artist.
    tarball = tmp_path / "mbdump.tar.bz2"
    with tarfile.open(tarball, "w:bz2") as tar:
        data = b"1\tNine Inch Nails\n"
        info = tarfile.TarInfo("mbdump/artist")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    mbdump = extract_tarball(tarball, tmp_path / "out")
    assert mbdump.name == "mbdump"
    assert (mbdump / "artist").read_bytes() == b"1\tNine Inch Nails\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_dumps_io.py -v`
Expected: FAIL — `ImportError: cannot import name 'download_file'`.

- [ ] **Step 3: Implement (append to `dumps.py`)**

Add `import httpx` to the imports at the top of `src/needledrop/musicbrainz/dumps.py` (in the third-party group, after the stdlib imports), then append:

```python
def download_file(url: str, dest: str | Path, *, client: "httpx.Client | None" = None) -> Path:
    """Stream-download `url` to `dest` (creating parent dirs). Raises on HTTP error.

    If `client` is provided the caller owns its lifecycle; otherwise a client is
    created and closed here. Uses no timeout — exports are large.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    owns_client = client is None
    client = client or httpx.Client(timeout=None, follow_redirects=True)
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in response.iter_bytes(1024 * 1024):
                    f.write(chunk)
    finally:
        if owns_client:
            client.close()
    return dest


def extract_tarball(tarball: str | Path, dest_dir: str | Path) -> Path:
    """Extract a `.tar.bz2` into `dest_dir`; return the path to the `mbdump/` dir."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:bz2") as tar:
        tar.extractall(dest_dir, filter="data")
    return dest_dir / "mbdump"
```

The top imports block should now read:

```python
from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import httpx
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_dumps_io.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/musicbrainz/dumps.py tests/musicbrainz/test_dumps_io.py
git commit -m "feat: add MusicBrainz dump download and extraction"
```

---

### Task 4: Schema-version → tag mapping and DDL URLs

**Files:**
- Create: `src/needledrop/musicbrainz/schema_sql.py`
- Test: `tests/musicbrainz/test_schema_sql.py`

- [ ] **Step 1: Write the failing test**

`tests/musicbrainz/test_schema_sql.py`:

```python
import pytest

from needledrop.musicbrainz.schema_sql import (
    DDL_FILES,
    ddl_file_urls,
    tag_for_schema_sequence,
)


def test_tag_for_known_sequence():
    assert tag_for_schema_sequence(31) == "v-2026-05-11.0-schema-change"


def test_tag_for_unknown_sequence_raises():
    with pytest.raises(ValueError) as exc:
        tag_for_schema_sequence(999)
    # Fail-loud: the message must name the unknown sequence and how to fix it.
    assert "999" in str(exc.value)
    assert "SCHEMA_SEQUENCE_TAGS" in str(exc.value)


def test_ddl_file_order():
    assert DDL_FILES == (
        "Extensions.sql",
        "CreateCollations.sql",
        "CreateTypes.sql",
        "CreateTables.sql",
    )


def test_ddl_file_urls():
    urls = ddl_file_urls(
        "https://raw.githubusercontent.com/metabrainz/musicbrainz-server",
        "v-2026-05-11.0-schema-change",
    )
    assert urls[0] == (
        "https://raw.githubusercontent.com/metabrainz/musicbrainz-server/"
        "v-2026-05-11.0-schema-change/admin/sql/Extensions.sql"
    )
    assert len(urls) == 4
    assert urls[-1].endswith("/admin/sql/CreateTables.sql")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_schema_sql.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/needledrop/musicbrainz/schema_sql.py`:

```python
"""Map a MusicBrainz SCHEMA_SEQUENCE to the matching musicbrainz-server git tag,
and build URLs for the schema DDL files needed to create the (empty) tables.
"""

from __future__ import annotations

# SCHEMA_SEQUENCE -> musicbrainz-server git tag carrying that schema's DDL.
# Add an entry when MusicBrainz ships a schema change (the "-schema-change" tags
# at https://github.com/metabrainz/musicbrainz-server/tags).
SCHEMA_SEQUENCE_TAGS: dict[int, str] = {
    30: "v-2025-05-23.0-schema-change",
    31: "v-2026-05-11.0-schema-change",
}

# DDL files in the exact order they must be applied to an empty database.
# PKs/FKs/indexes/functions/triggers/views are intentionally omitted — the data
# is read-only and transient (we SELECT it into DuckDB and drop Postgres).
DDL_FILES: tuple[str, ...] = (
    "Extensions.sql",
    "CreateCollations.sql",
    "CreateTypes.sql",
    "CreateTables.sql",
)


def tag_for_schema_sequence(seq: int) -> str:
    """Return the musicbrainz-server tag for a schema sequence, or fail loudly."""
    try:
        return SCHEMA_SEQUENCE_TAGS[seq]
    except KeyError:
        known = ", ".join(str(k) for k in sorted(SCHEMA_SEQUENCE_TAGS))
        raise ValueError(
            f"Unknown MusicBrainz SCHEMA_SEQUENCE {seq} (known: {known}). "
            "Add a mapping to SCHEMA_SEQUENCE_TAGS in "
            "needledrop.musicbrainz.schema_sql — find the matching '-schema-change' "
            "tag at https://github.com/metabrainz/musicbrainz-server/tags."
        ) from None


def ddl_file_urls(raw_base: str, tag: str) -> list[str]:
    """Raw-GitHub URLs for the ordered DDL files at a given tag."""
    base = raw_base.rstrip("/")
    return [f"{base}/{tag}/admin/sql/{name}" for name in DDL_FILES]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_schema_sql.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/musicbrainz/schema_sql.py tests/musicbrainz/test_schema_sql.py
git commit -m "feat: add MusicBrainz schema-version tag mapping"
```

---

### Task 5: Postgres command + SQL builders (pure)

**Files:**
- Create: `src/needledrop/musicbrainz/postgres.py`
- Test: `tests/musicbrainz/test_postgres.py`

- [ ] **Step 1: Write the failing test**

`tests/musicbrainz/test_postgres.py`:

```python
from pathlib import Path

from needledrop.musicbrainz.postgres import (
    PostgresSpec,
    copy_table_sql,
    docker_run_args,
    pg_isready_args,
    psql_args,
    teardown_args,
)

SPEC = PostgresSpec(
    image="postgres:18",
    container="needledrop-mb-import",
    port=55432,
    db="musicbrainz",
    user="musicbrainz",
    password="pw",
)


def test_docker_run_args(tmp_path):
    dump = tmp_path / "out"
    dump.mkdir()
    args = docker_run_args(SPEC, dump)
    assert args[:3] == ["docker", "run", "-d"]
    assert "--rm" in args
    assert "--name" in args and "needledrop-mb-import" in args
    assert "-p" in args and "55432:5432" in args
    assert f"{dump.resolve()}:/dump:ro" in args
    assert args[-1] == "postgres:18"
    assert "POSTGRES_PASSWORD=pw" in args


def test_pg_isready_args():
    assert pg_isready_args(SPEC) == [
        "docker", "exec", "needledrop-mb-import",
        "pg_isready", "-U", "musicbrainz", "-d", "musicbrainz",
    ]


def test_psql_args():
    assert psql_args(SPEC) == [
        "docker", "exec", "-i", "needledrop-mb-import",
        "psql", "-v", "ON_ERROR_STOP=1", "-U", "musicbrainz", "-d", "musicbrainz",
    ]


def test_teardown_args():
    assert teardown_args(SPEC) == ["docker", "rm", "-f", "needledrop-mb-import"]


def test_copy_table_sql():
    sql = copy_table_sql("release_group", "/dump/mbdump/release_group")
    assert sql == (
        'COPY musicbrainz."release_group" FROM '
        "'/dump/mbdump/release_group' WITH (FORMAT text);"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_postgres.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement (builders only — the lifecycle class comes in Task 6)**

`src/needledrop/musicbrainz/postgres.py`:

```python
"""Ephemeral MusicBrainz Postgres lifecycle via the `docker` CLI."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PostgresSpec:
    image: str
    container: str
    port: int
    db: str
    user: str
    password: str


def docker_run_args(spec: PostgresSpec, dump_dir: str | Path) -> list[str]:
    """argv to start the ephemeral container with the dump dir mounted read-only."""
    return [
        "docker", "run", "-d", "--rm",
        "--name", spec.container,
        "-e", f"POSTGRES_DB={spec.db}",
        "-e", f"POSTGRES_USER={spec.user}",
        "-e", f"POSTGRES_PASSWORD={spec.password}",
        "-p", f"{spec.port}:5432",
        "-v", f"{Path(dump_dir).resolve()}:/dump:ro",
        spec.image,
    ]


def pg_isready_args(spec: PostgresSpec) -> list[str]:
    return ["docker", "exec", spec.container, "pg_isready", "-U", spec.user, "-d", spec.db]


def psql_args(spec: PostgresSpec) -> list[str]:
    return [
        "docker", "exec", "-i", spec.container,
        "psql", "-v", "ON_ERROR_STOP=1", "-U", spec.user, "-d", spec.db,
    ]


def teardown_args(spec: PostgresSpec) -> list[str]:
    return ["docker", "rm", "-f", spec.container]


def copy_table_sql(table: str, container_path: str) -> str:
    """Server-side COPY of a headerless tab/`\\N` dump file into a musicbrainz table."""
    return f"COPY musicbrainz.\"{table}\" FROM '{container_path}' WITH (FORMAT text);"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_postgres.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/musicbrainz/postgres.py tests/musicbrainz/test_postgres.py
git commit -m "feat: add ephemeral Postgres command builders"
```

---

### Task 6: `EphemeralPostgres` lifecycle (mocked subprocess)

**Files:**
- Modify: `src/needledrop/musicbrainz/postgres.py`
- Test: `tests/musicbrainz/test_postgres_lifecycle.py`

- [ ] **Step 1: Write the failing test**

`tests/musicbrainz/test_postgres_lifecycle.py`:

```python
import subprocess

import pytest

from needledrop.musicbrainz.postgres import EphemeralPostgres, PostgresSpec

SPEC = PostgresSpec("postgres:18", "c", 55432, "musicbrainz", "musicbrainz", "pw")


class FakeRunner:
    """Records calls; returns queued returncodes for pg_isready."""

    def __init__(self, isready_codes=(0,)):
        self.calls = []
        self._isready = list(isready_codes)

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        rc = 0
        if "pg_isready" in args:
            rc = self._isready.pop(0) if self._isready else 0
        return subprocess.CompletedProcess(args, rc)


def test_start_runs_docker_run(tmp_path):
    runner = FakeRunner()
    pg = EphemeralPostgres(SPEC, tmp_path, runner=runner)
    pg.start()
    assert runner.calls[0][0][:3] == ["docker", "run", "-d"]
    assert runner.calls[0][1].get("check") is True


def test_wait_ready_polls_until_zero(tmp_path):
    runner = FakeRunner(isready_codes=(1, 1, 0))
    slept = []
    pg = EphemeralPostgres(SPEC, tmp_path, runner=runner)
    pg.wait_ready(attempts=5, sleep=0.01, sleeper=slept.append)
    assert sum(1 for c, _ in runner.calls if "pg_isready" in c) == 3
    assert len(slept) == 2  # slept after the two failures, not after success


def test_wait_ready_times_out(tmp_path):
    runner = FakeRunner(isready_codes=(1, 1, 1))
    pg = EphemeralPostgres(SPEC, tmp_path, runner=runner)
    with pytest.raises(TimeoutError):
        pg.wait_ready(attempts=3, sleep=0.0, sleeper=lambda _s: None)


def test_run_sql_pipes_input(tmp_path):
    runner = FakeRunner()
    pg = EphemeralPostgres(SPEC, tmp_path, runner=runner)
    pg.run_sql("SELECT 1;")
    args, kwargs = runner.calls[-1]
    assert args[:4] == ["docker", "exec", "-i", "c"]
    assert kwargs["input"] == b"SELECT 1;"
    assert kwargs.get("check") is True


def test_teardown_does_not_check(tmp_path):
    runner = FakeRunner()
    pg = EphemeralPostgres(SPEC, tmp_path, runner=runner)
    pg.teardown()
    args, kwargs = runner.calls[-1]
    assert args == ["docker", "rm", "-f", "c"]
    assert kwargs.get("check") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_postgres_lifecycle.py -v`
Expected: FAIL — `ImportError: cannot import name 'EphemeralPostgres'`.

- [ ] **Step 3: Implement (append to `postgres.py`)**

```python
class EphemeralPostgres:
    """Manages a throwaway postgres:18 container for one import run."""

    def __init__(self, spec: PostgresSpec, dump_dir: str | Path, *, runner=subprocess.run):
        self._spec = spec
        self._dump_dir = Path(dump_dir)
        self._run = runner

    def start(self) -> None:
        self._run(docker_run_args(self._spec, self._dump_dir), check=True)

    def wait_ready(self, *, attempts: int = 60, sleep: float = 2.0, sleeper=time.sleep) -> None:
        for _ in range(attempts):
            result = self._run(pg_isready_args(self._spec), capture_output=True)
            if result.returncode == 0:
                return
            sleeper(sleep)
        raise TimeoutError(
            f"Postgres container '{self._spec.container}' not ready after {attempts} attempts"
        )

    def run_sql(self, sql: str) -> None:
        self._run(psql_args(self._spec), input=sql.encode("utf-8"), check=True)

    def copy_table(self, table: str, container_path: str) -> None:
        self.run_sql(copy_table_sql(table, container_path))

    def teardown(self) -> None:
        # Best-effort: never raise from teardown (it runs in `finally`).
        self._run(teardown_args(self._spec), check=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_postgres_lifecycle.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/musicbrainz/postgres.py tests/musicbrainz/test_postgres_lifecycle.py
git commit -m "feat: add EphemeralPostgres lifecycle wrapper"
```

---

### Task 7: DuckDB materialization

**Files:**
- Create: `src/needledrop/musicbrainz/materialize.py`
- Test: `tests/musicbrainz/test_materialize.py`

- [ ] **Step 1: Write the failing test**

`tests/musicbrainz/test_materialize.py`:

```python
from needledrop.musicbrainz.materialize import attach_sql, materialize_sql


def test_attach_sql():
    sql = attach_sql(host="127.0.0.1", port=55432, db="musicbrainz", user="mb", password="pw")
    assert sql == (
        "ATTACH 'host=127.0.0.1 port=55432 dbname=musicbrainz user=mb password=pw' "
        "AS pg (TYPE postgres, READ_ONLY)"
    )


def test_materialize_sql_prefixes_and_quotes():
    assert materialize_sql("release_group") == (
        'CREATE OR REPLACE TABLE "mb_release_group" AS '
        'SELECT * FROM pg.musicbrainz."release_group"'
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_materialize.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/needledrop/musicbrainz/materialize.py`:

```python
"""Materialize an attached MusicBrainz Postgres into local DuckDB mb_* tables."""

from __future__ import annotations

import duckdb

# Lists tables in the attached Postgres `musicbrainz` schema via DuckDB's catalog.
LIST_TABLES_SQL = (
    "SELECT table_name FROM duckdb_tables() "
    "WHERE database_name = 'pg' AND schema_name = 'musicbrainz' "
    "ORDER BY table_name"
)


def attach_sql(*, host: str, port: int, db: str, user: str, password: str) -> str:
    """ATTACH statement for the running Postgres (read-only)."""
    conn = f"host={host} port={port} dbname={db} user={user} password={password}"
    return f"ATTACH '{conn}' AS pg (TYPE postgres, READ_ONLY)"


def materialize_sql(table: str) -> str:
    """CTAS that copies one Postgres musicbrainz table into a local `mb_<table>`."""
    return (
        f'CREATE OR REPLACE TABLE "mb_{table}" AS '
        f'SELECT * FROM pg.musicbrainz."{table}"'
    )


def attach(con: duckdb.DuckDBPyConnection, *, host: str, port: int, db: str,
           user: str, password: str) -> None:
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    con.execute(attach_sql(host=host, port=port, db=db, user=user, password=password))


def list_pg_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [row[0] for row in con.execute(LIST_TABLES_SQL).fetchall()]


def materialize_all(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Materialize every attached musicbrainz-schema table as mb_<table>. Returns the names."""
    tables = list_pg_tables(con)
    for table in tables:
        con.execute(materialize_sql(table))
    return tables
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_materialize.py -v`
Expected: PASS (2 tests). (`attach`, `list_pg_tables`, `materialize_all` are exercised by the Docker-gated integration test in Task 10.)

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/musicbrainz/materialize.py tests/musicbrainz/test_materialize.py
git commit -m "feat: add DuckDB materialization of MusicBrainz tables"
```

---

### Task 8: Import orchestrator

**Files:**
- Create: `src/needledrop/musicbrainz/importer.py`
- Test: `tests/musicbrainz/test_importer.py`

- [ ] **Step 1: Write the failing test**

`tests/musicbrainz/test_importer.py`:

```python
import pytest

from needledrop.musicbrainz.importer import SCHEMA_BOOTSTRAP_SQL, run_import


class SpyPostgres:
    def __init__(self):
        self.events = []

    def start(self):
        self.events.append("start")

    def wait_ready(self):
        self.events.append("wait_ready")

    def run_sql(self, sql):
        self.events.append(("run_sql", sql))

    def copy_table(self, table, path):
        self.events.append(("copy", table, path))

    def teardown(self):
        self.events.append("teardown")


class FakeDuck:
    pass


def test_run_import_sequences_and_materializes(tmp_path):
    pg = SpyPostgres()
    materialized = []

    def fake_materializer(con):
        materialized.append(con)
        return ["artist", "release_group"]

    tables = run_import(
        pg=pg,
        duckdb_con=FakeDuck(),
        ddl_sql_texts=["-- extensions", "-- tables"],
        table_files=[("artist", "/dump/mbdump/artist")],
        attach=lambda con: pg.events.append("attach"),
        materializer=fake_materializer,
    )

    assert tables == ["artist", "release_group"]
    # Order: start -> wait -> bootstrap schema -> each DDL -> COPY -> attach -> materialize -> teardown
    assert pg.events[0] == "start"
    assert pg.events[1] == "wait_ready"
    assert pg.events[2] == ("run_sql", SCHEMA_BOOTSTRAP_SQL)
    assert ("run_sql", "-- extensions") in pg.events
    assert ("copy", "artist", "/dump/mbdump/artist") in pg.events
    assert "attach" in pg.events
    assert pg.events[-1] == "teardown"


def test_run_import_tears_down_on_failure():
    pg = SpyPostgres()

    def boom(con):
        raise RuntimeError("materialize failed")

    with pytest.raises(RuntimeError):
        run_import(
            pg=pg,
            duckdb_con=FakeDuck(),
            ddl_sql_texts=[],
            table_files=[],
            attach=lambda con: None,
            materializer=boom,
        )
    assert pg.events[-1] == "teardown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_importer.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/needledrop/musicbrainz/importer.py`:

```python
"""Orchestrates `needledrop mb import` with guaranteed Postgres teardown."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from needledrop.config import Settings
from needledrop.db.duckdb_store import connect
from needledrop.musicbrainz import dumps, schema_sql
from needledrop.musicbrainz.materialize import attach as _duck_attach
from needledrop.musicbrainz.materialize import materialize_all
from needledrop.musicbrainz.postgres import EphemeralPostgres, PostgresSpec

# All schemas MusicBrainz's InitDb creates before applying CreateTables.sql.
_MB_SCHEMAS: tuple[str, ...] = (
    "musicbrainz", "cover_art_archive", "documentation", "event_art_archive",
    "json_dump", "report", "sitemaps", "statistics", "wikidocs", "dbmirror2",
)
SCHEMA_BOOTSTRAP_SQL = "\n".join(f"CREATE SCHEMA IF NOT EXISTS {s};" for s in _MB_SCHEMAS)
_SEARCH_PATH = "SET search_path = musicbrainz, public;\n"


def run_import(
    *,
    pg,
    duckdb_con,
    ddl_sql_texts: Sequence[str],
    table_files: Iterable[tuple[str, str]],
    attach: Callable[[object], None],
    materializer: Callable[[object], list[str]] = materialize_all,
) -> list[str]:
    """Load DDL + data into `pg`, then materialize into DuckDB. Always tears down.

    `attach` connects `duckdb_con` to the running Postgres; `materializer` returns
    the materialized table names. Both are injected so this is unit-testable.
    """
    try:
        pg.start()
        pg.wait_ready()
        pg.run_sql(SCHEMA_BOOTSTRAP_SQL)
        for ddl in ddl_sql_texts:
            pg.run_sql(_SEARCH_PATH + ddl)
        for table, container_path in table_files:
            pg.copy_table(table, container_path)
        attach(duckdb_con)
        return materializer(duckdb_con)
    finally:
        pg.teardown()


def import_musicbrainz(settings: Settings, *, http=None) -> dict:
    """Full entry point: acquire the export, load it, materialize into DuckDB.

    Heavy I/O path — exercised by the documented manual run, not CI. Returns a
    summary dict {schema_sequence, tag, tables}.
    """
    import httpx

    owns_http = http is None
    http = http or httpx.Client(timeout=None, follow_redirects=True)
    data_dir = Path(settings.mb_data_dir)
    try:
        latest = dumps.resolve_latest(
            http.get(f"{settings.mb_dump_base_url.rstrip('/')}/LATEST").raise_for_status().text
        )
        seq = int(
            http.get(dumps.fullexport_url(settings.mb_dump_base_url, latest, "SCHEMA_SEQUENCE"))
            .raise_for_status()
            .text.strip()
        )
        tag = schema_sql.tag_for_schema_sequence(seq)  # fail-loud before the big download
        ddl_texts = [
            http.get(url).raise_for_status().text
            for url in schema_sql.ddl_file_urls(settings.mb_server_raw_base, tag)
        ]
        tarball = dumps.download_file(
            dumps.fullexport_url(settings.mb_dump_base_url, latest, "mbdump.tar.bz2"),
            data_dir / latest / "mbdump.tar.bz2",
            client=http,
        )
        mbdump_dir = dumps.extract_tarball(tarball, data_dir / latest / "extracted")
        table_files = [
            (name, f"/dump/mbdump/{path.name}")
            for name, path in dumps.list_table_files(mbdump_dir)
        ]

        spec = PostgresSpec(
            image=settings.mb_postgres_image,
            container=settings.mb_postgres_container,
            port=settings.mb_postgres_port,
            db=settings.mb_postgres_db,
            user=settings.mb_postgres_user,
            password=settings.mb_postgres_password,
        )
        pg = EphemeralPostgres(spec, mbdump_dir.parent, dump_dir_mount=mbdump_dir.parent)
        con = connect(settings.db_path)

        def _attach(c):
            _duck_attach(
                c, host="127.0.0.1", port=spec.port, db=spec.db,
                user=spec.user, password=spec.password,
            )

        tables = run_import(
            pg=pg,
            duckdb_con=con,
            ddl_sql_texts=ddl_texts,
            table_files=table_files,
            attach=_attach,
        )
        return {"schema_sequence": seq, "tag": tag, "tables": tables}
    finally:
        if owns_http:
            http.close()
```

Note: `EphemeralPostgres` mounts the directory passed as `dump_dir`; the extracted layout is `<data_dir>/<latest>/extracted/mbdump/...`, so the mount root is `mbdump_dir.parent` (the `extracted/` dir), making the container path `/dump/mbdump/<table>`. The `dump_dir_mount=` keyword in the snippet above is illustrative — pass the mount root as the existing `dump_dir` positional/keyword that `EphemeralPostgres.__init__` already accepts (from Task 6: `EphemeralPostgres(spec, dump_dir, *, runner=...)`). Correct the call to `EphemeralPostgres(spec, mbdump_dir.parent)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_importer.py -v`
Expected: PASS (2 tests). Only `run_import` is unit-tested; `import_musicbrainz` is covered by the manual run (Task 10).

- [ ] **Step 5: Fix the `EphemeralPostgres` call and re-run the full suite**

Edit `import_musicbrainz` so the line reads exactly:

```python
        pg = EphemeralPostgres(spec, mbdump_dir.parent)
```

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest` and `... -m ruff check .`
Expected: all green; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/musicbrainz/importer.py tests/musicbrainz/test_importer.py
git commit -m "feat: add MusicBrainz import orchestrator"
```

---

### Task 9: CLI — `needledrop mb import`

**Files:**
- Create: `src/needledrop/cli.py`
- Modify: `pyproject.toml` (console entry point)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
from unittest.mock import patch

from typer.testing import CliRunner

from needledrop.cli import app

runner = CliRunner()


def test_mb_import_invokes_importer():
    with patch("needledrop.cli.import_musicbrainz") as mock_import:
        mock_import.return_value = {"schema_sequence": 31, "tag": "v-x", "tables": ["artist"]}
        result = runner.invoke(app, ["mb", "import"])
    assert result.exit_code == 0
    assert mock_import.called
    assert "31" in result.stdout


def test_help_lists_mb():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "mb" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.cli'`.

- [ ] **Step 3: Implement**

`src/needledrop/cli.py`:

```python
"""NeedleDrop operator CLI."""

from __future__ import annotations

import typer

from needledrop.config import load_settings
from needledrop.musicbrainz.importer import import_musicbrainz

app = typer.Typer(help="NeedleDrop — intelligent music library management", no_args_is_help=True)
mb_app = typer.Typer(help="MusicBrainz authority data", no_args_is_help=True)
app.add_typer(mb_app, name="mb")


@mb_app.command("import")
def mb_import() -> None:
    """Download the MusicBrainz export and materialize it into the local DuckDB."""
    summary = import_musicbrainz(load_settings())
    typer.echo(
        f"Imported MusicBrainz schema sequence {summary['schema_sequence']} "
        f"(tag {summary['tag']}): {len(summary['tables'])} tables materialized."
    )


def main() -> None:
    app()
```

Add the console entry point to `pyproject.toml` — insert this block after the `[project.optional-dependencies]` section:

```toml
[project.scripts]
needledrop = "needledrop.cli:main"
```

Reinstall so the entry point and imports resolve:

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pip install --no-cache-dir -e ".[dev]"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_cli.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/cli.py pyproject.toml tests/test_cli.py
git commit -m "feat: add needledrop CLI with mb import command"
```

---

### Task 10: Docker-gated bridge integration test + manual verification

**Files:**
- Create: `tests/musicbrainz/test_import_integration.py`
- Modify: `pyproject.toml` (register the `integration` marker)
- Create: `docs/superpowers/plans/notes/mb-import-manual-verification.md`

- [ ] **Step 1: Register the marker and write the integration test**

Add to `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
markers = [
    "integration: tests that require Docker / external services (deselect with -m 'not integration')",
]
```

`tests/musicbrainz/test_import_integration.py` — proves the real DDL→COPY→ATTACH→materialize bridge against a tiny synthetic `musicbrainz` schema (no MB DDL, no 7 GB download), only when Docker is available:

```python
import shutil
import subprocess
import time

import duckdb
import pytest

from needledrop.musicbrainz.materialize import attach, materialize_all
from needledrop.musicbrainz.postgres import EphemeralPostgres, PostgresSpec

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker")

SPEC = PostgresSpec(
    image="postgres:18",
    container="needledrop-mb-import-test",
    port=55439,
    db="musicbrainz",
    user="musicbrainz",
    password="testpw",
)


@pytest.mark.skipif(not DOCKER, reason="docker not available")
def test_bridge_end_to_end(tmp_path):
    # Synthetic dump: mbdump/<table> tab-separated, \N nulls, no header.
    mbdump = tmp_path / "extracted" / "mbdump"
    mbdump.mkdir(parents=True)
    (mbdump / "artist").write_text("1\tNine Inch Nails\n2\tAphex Twin\n")

    pg = EphemeralPostgres(SPEC, tmp_path / "extracted")
    try:
        pg.start()
        pg.wait_ready(attempts=60, sleep=1.0)
        pg.run_sql("CREATE SCHEMA IF NOT EXISTS musicbrainz;")
        pg.run_sql(
            "SET search_path = musicbrainz, public;\n"
            "CREATE TABLE musicbrainz.artist (id INTEGER, name TEXT);"
        )
        pg.copy_table("artist", "/dump/mbdump/artist")

        con = duckdb.connect(str(tmp_path / "library.duckdb"))
        attach(con, host="127.0.0.1", port=SPEC.port, db=SPEC.db,
               user=SPEC.user, password=SPEC.password)
        tables = materialize_all(con)

        assert "artist" in tables
        rows = con.execute('SELECT id, name FROM "mb_artist" ORDER BY id').fetchall()
        assert rows == [(1, "Nine Inch Nails"), (2, "Aphex Twin")]
    finally:
        pg.teardown()
        time.sleep(0.1)
```

- [ ] **Step 2: Run the integration test (only if Docker is present)**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/musicbrainz/test_import_integration.py -v -m integration`
Expected (Docker present): PASS — proves DDL+COPY into real `postgres:18`, DuckDB `ATTACH (READ_ONLY)`, `duckdb_tables()` discovery, and `mb_artist` materialization all work together. (If Docker is absent: SKIPPED — acceptable.)

If it fails on `duckdb_tables()` not listing the attached catalog, adjust `LIST_TABLES_SQL` in `materialize.py` to use `postgres_query('pg', 'SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = ''musicbrainz''')` wrapped as `SELECT tablename FROM postgres_query(...)`, re-run, and keep the unit tests in Task 7 green.

- [ ] **Step 3: Confirm the default suite excludes integration and stays green**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest -m "not integration"`
Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: all unit tests pass; ruff clean.

- [ ] **Step 4: Write the manual-verification note**

`docs/superpowers/plans/notes/mb-import-manual-verification.md`:

```markdown
# MB import — manual verification (full run)

The full import is not a CI gate (≈7 GB download + multi-GB Postgres load). Verify
it manually after changing the import pipeline:

1. Ensure Docker is running and `postgres:18` can be pulled.
2. Run: `needledrop mb import`  (or `python -m needledrop.cli mb import`)
3. Expect it to: read LATEST, resolve the SCHEMA_SEQUENCE → tag, download mbdump.tar.bz2,
   start the ephemeral container, apply the 4 DDL files, COPY all core tables, materialize
   `mb_*` into the DuckDB at `NEEDLEDROP_DB_PATH`, then remove the container.
4. Spot-check in DuckDB:
   - `SELECT count(*) FROM mb_artist;`  (expect ~2.5M+)
   - `SELECT count(*) FROM mb_release_group;`
   - `SELECT count(*) FROM mb_isrc;`
   - Confirm no container remains: `docker ps -a | grep needledrop-mb-import` → empty.
5. If the SCHEMA_SEQUENCE is unknown, the run fails loudly before the big download —
   add the new mapping to `needledrop.musicbrainz.schema_sql.SCHEMA_SEQUENCE_TAGS`.
```

- [ ] **Step 5: Commit**

```bash
git add tests/musicbrainz/test_import_integration.py pyproject.toml docs/superpowers/plans/notes/mb-import-manual-verification.md
git commit -m "test: add Docker-gated MB import bridge integration test + manual verification note"
```

---

## Self-Review

**1. Spec coverage (spec §2 decision 3, §4.1, §6.3, build step 2):**
- Download full export + verify + extract → `dumps.py` (Tasks 2–3). ✓
- `SCHEMA_SEQUENCE` → tag, fail-loud version guard, DDL fetch → `schema_sql.py` (Task 4) + `importer.import_musicbrainz` (Task 8). ✓
- Ephemeral `postgres:18` via Docker, apply MB DDL (Extensions→Collations→Types→Tables), COPY all core TSVs → `postgres.py` (Tasks 5–6) + orchestrator (Task 8). ✓
- DuckDB `ATTACH (READ_ONLY)` + materialize the **entire** core schema as `mb_*` → `materialize.py` (Task 7). ✓
- Guaranteed teardown → `run_import` `finally` (Task 8), verified by `test_run_import_tears_down_on_failure`. ✓
- `needledrop mb import` CLI + entry point → `cli.py` (Task 9). ✓
- Testing strategy (unit + Docker-gated integration + manual) matches spec §9.2. ✓

**2. Placeholder scan:** No TBD/TODO. Every code step has complete code; every run step has the command and expected result. The one illustrative wrinkle (the `EphemeralPostgres` call in Task 8 Step 3) is explicitly corrected in Task 8 Step 5 with the exact final line — not left ambiguous.

**3. Type/name consistency:** `PostgresSpec` fields (`image/container/port/db/user/password`) are used consistently across `docker_run_args`, `EphemeralPostgres`, and `import_musicbrainz`. `EphemeralPostgres(spec, dump_dir, *, runner=...)` signature (Task 6) matches its call in Task 8 (after the Step-5 correction). `attach(con, *, host, port, db, user, password)` and `materialize_all(con)->list[str]` (Task 7) match their use in the integration test (Task 10) and orchestrator (`_attach`, `materializer=materialize_all`). `run_import` keyword params match both its tests (Task 8) and the orchestrator call. CLI imports `import_musicbrainz` (Task 8) which returns the `{schema_sequence, tag, tables}` dict the CLI prints.

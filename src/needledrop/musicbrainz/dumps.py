"""Acquire, verify, and extract the MusicBrainz full export."""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import httpx


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


def verify_sha256(path: str | Path, sums: dict[str, str], filename: str) -> None:
    """Raise ValueError if `path`'s SHA-256 doesn't match `sums[filename]`."""
    expected = sums.get(filename)
    if expected is None:
        raise ValueError(f"{filename} not listed in SHA256SUMS")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(
            f"SHA256 mismatch for {filename}: expected {expected}, got {actual}"
        )


def download_file(url: str, dest: str | Path, *, client: httpx.Client | None = None) -> Path:
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

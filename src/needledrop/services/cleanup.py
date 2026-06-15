"""Cleanup workflow: run all analyses, persist findings, resolve/ignore them."""

from __future__ import annotations

from datetime import datetime

import duckdb

from needledrop.analysis.compilation_pollution import find_compilation_pollution
from needledrop.analysis.duplicates import find_duplicate_albums
from needledrop.analysis.missing_albums import find_missing_core_albums
from needledrop.db.repository import get_findings, save_cleanup_findings
from needledrop.models.findings import CleanupReport


def run_cleanup_scan(con: duckdb.DuckDBPyConnection, *, now: datetime) -> dict[str, int]:
    """Run every analysis, persist the findings, and return counts by finding type."""
    findings = [
        *find_duplicate_albums(con),
        *find_compilation_pollution(con),
        *find_missing_core_albums(con),
    ]
    save_cleanup_findings(con, findings)
    report = CleanupReport(findings=get_findings(con), generated_at=now)
    return report.count_by_type()


def mark_finding_resolved(
    con: duckdb.DuckDBPyConnection, finding_id: int, *, now: datetime
) -> None:
    con.execute("UPDATE cleanup_findings SET resolved_at = ? WHERE id = ?", [now, finding_id])


def ignore_finding(
    con: duckdb.DuckDBPyConnection, finding_id: int, *, now: datetime
) -> None:
    con.execute("UPDATE cleanup_findings SET ignored_at = ? WHERE id = ?", [now, finding_id])

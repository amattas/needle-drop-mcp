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

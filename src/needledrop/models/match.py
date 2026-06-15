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

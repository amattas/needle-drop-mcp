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

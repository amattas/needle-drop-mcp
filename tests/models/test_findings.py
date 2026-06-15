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

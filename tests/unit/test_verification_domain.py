"""The status classification decision table and the approval lifecycle gate."""

from datetime import UTC, datetime

from countersign.contracts.enums import ApprovalDecision, JobStatus, SourceAuthority
from countersign.contracts.verification import VerificationResult
from countersign.domain.gates import allowed_decisions_for
from countersign.domain.verification import classify_job_status


def classify(
    *,
    http_status: int = 200,
    apply_markers: bool = False,
    closed_markers: bool = False,
    authority: SourceAuthority = SourceAuthority.OFFICIAL_ATS,
) -> JobStatus:
    status, _ = classify_job_status(
        http_status=http_status,
        apply_markers_found=apply_markers,
        closed_markers_found=closed_markers,
        authority=authority,
    )
    return status


def test_gone_or_closed_markers_mean_closed() -> None:
    assert classify(http_status=404) == JobStatus.CLOSED
    assert classify(http_status=410) == JobStatus.CLOSED
    assert classify(closed_markers=True) == JobStatus.CLOSED


def test_http_errors_are_unknown() -> None:
    assert classify(http_status=500) == JobStatus.UNKNOWN_STALE_RISK
    assert classify(http_status=403) == JobStatus.UNKNOWN_STALE_RISK


def test_open_requires_official_authority_and_markers() -> None:
    assert classify(apply_markers=True) == JobStatus.OPEN
    assert (
        classify(apply_markers=True, authority=SourceAuthority.OFFICIAL_COMPANY) == JobStatus.OPEN
    )
    assert (
        classify(apply_markers=True, authority=SourceAuthority.COMPANY_CONTROLLED)
        == JobStatus.OPEN_THIRD_PARTY_ONLY
    )
    assert (
        classify(apply_markers=True, authority=SourceAuthority.DISCOVERY_ONLY)
        == JobStatus.OPEN_THIRD_PARTY_ONLY
    )


def test_http_200_alone_is_never_open() -> None:
    assert classify() == JobStatus.UNKNOWN_STALE_RISK


def _verification(status: JobStatus) -> VerificationResult:
    return VerificationResult(
        case_id="c1",
        status=status,
        authority=SourceAuthority.OFFICIAL_ATS,
        checked_at=datetime.now(UTC),
        http_status=200,
        final_url="https://boards.greenhouse.io/acme/jobs/1",
        reason="test",
        apply_markers_found=status == JobStatus.OPEN,
        closed_markers_found=False,
    )


def test_plain_approve_requires_verified_open() -> None:
    """The lifecycle gate, not a display concern: without a verified OPEN,
    the approval request itself must not accept a plain APPROVE."""

    assert ApprovalDecision.APPROVE in allowed_decisions_for(_verification(JobStatus.OPEN))
    for status in (
        JobStatus.OPEN_THIRD_PARTY_ONLY,
        JobStatus.UNKNOWN_STALE_RISK,
    ):
        allowed = allowed_decisions_for(_verification(status))
        assert ApprovalDecision.APPROVE not in allowed, status
        assert ApprovalDecision.REJECT in allowed
        assert ApprovalDecision.REQUEST_EVIDENCE in allowed
    assert ApprovalDecision.APPROVE not in allowed_decisions_for(None)

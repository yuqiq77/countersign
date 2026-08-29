"""Verification contracts: authority-aware official status observations."""

from datetime import datetime

from countersign.contracts.base import ContractModel
from countersign.contracts.enums import JobStatus, SourceAuthority


class VerificationResult(ContractModel):
    """Official application status at a point in time, with its evidence.

    The verification is a persisted second observation, not just a probe:
    `snapshot_id` binds it to the stored re-observation body, and
    `content_changed` compares normalized text against the first snapshot so
    an approval never silently pairs old content with new status.
    """

    case_id: str
    status: JobStatus
    authority: SourceAuthority
    checked_at: datetime
    http_status: int | None
    final_url: str
    reason: str
    apply_markers_found: bool
    closed_markers_found: bool
    observed_content_hash: str | None = None
    content_changed: bool | None = None
    snapshot_id: str | None = None

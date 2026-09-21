"""Verification contracts: authority-aware official status observations."""

from datetime import datetime

from countersign.contracts.base import ContractModel
from countersign.contracts.enums import JobStatus, SourceAuthority


class VerificationResult(ContractModel):
    """Official application status at a point in time, with its evidence.

    `snapshot_id` and `observed_content_hash` bind to the response governing
    status. The reading separately identifies the posting body used for
    evaluation; `content_changed` compares that body's normalized text to
    the preceding body snapshot (the intake snapshot on the first run).
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
    # The bounded page reading this verification was derived from (ADR-012);
    # None for verifications made before the reader existed.
    reading_id: str | None = None

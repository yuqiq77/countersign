"""Pure verification semantics: authority-aware status classification.

The system's flagship rule lives here as a unit-testable decision table:
`OPEN` is never inferred from HTTP 200 alone (v4 §12). I/O stays in the
Activity; every observer of a source (verify now, scheduled rechecks in
Slice 2) classifies through this one function.
"""

from countersign.contracts.enums import JobStatus, SourceAuthority

_OFFICIAL_AUTHORITIES = (
    SourceAuthority.OFFICIAL_ATS,
    SourceAuthority.OFFICIAL_COMPANY,
)


def is_official_authority(authority: SourceAuthority) -> bool:
    """Only official tiers may carry the apply-now framing or an official salary."""

    return authority in _OFFICIAL_AUTHORITIES


def classify_job_status(
    *,
    http_status: int,
    apply_markers_found: bool,
    closed_markers_found: bool,
    authority: SourceAuthority,
) -> tuple[JobStatus, str]:
    """Map one observation to a `JobStatus` and a human-readable reason."""

    if http_status in (404, 410) or closed_markers_found:
        return JobStatus.CLOSED, "gone or closed markers observed"
    if http_status >= 400:
        return JobStatus.UNKNOWN_STALE_RISK, f"http {http_status} from source"
    if apply_markers_found and authority in _OFFICIAL_AUTHORITIES:
        return JobStatus.OPEN, "apply markers on an official source"
    if apply_markers_found:
        return (
            JobStatus.OPEN_THIRD_PARTY_ONLY,
            "apply markers on a non-official source",
        )
    return (
        JobStatus.UNKNOWN_STALE_RISK,
        "no apply markers found; never inferred from http 200 alone",
    )

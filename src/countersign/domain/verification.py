"""Pure verification semantics: authority-aware status classification.

The system's flagship rule lives here as a unit-testable decision table:
`OPEN` is never inferred from HTTP 200 alone (v4 §12). I/O stays in the
Activity; every observer of a source (verify now, scheduled rechecks in
Slice 2) classifies through this one function.
"""

from datetime import UTC, datetime

from countersign.contracts.enums import ApplyEntryKind, EvidenceKind, JobStatus, SourceAuthority
from countersign.contracts.page_reading import ApplyEntry, PageDossier, PageEvidence

_OFFICIAL_AUTHORITIES = (
    SourceAuthority.OFFICIAL_ATS,
    SourceAuthority.OFFICIAL_COMPANY,
)


def is_official_authority(authority: SourceAuthority) -> bool:
    """Only official tiers may carry the apply-now framing or an official salary."""

    return authority in _OFFICIAL_AUTHORITIES


def parse_valid_through(value: str | None) -> datetime | None:
    """JSON-LD `validThrough` as an aware datetime, or None when unparseable."""

    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def classify_from_evidence(
    *,
    http_status: int,
    apply_entry_kind: ApplyEntryKind | None,
    closed_notice_found: bool,
    valid_through: str | None,
    now: datetime,
    authority: SourceAuthority,
) -> tuple[JobStatus, str]:
    """The evidence-ranked decision table used by the page reader.

    Closed notices and a past `validThrough` win over any apply entry; an
    apply entry counts as OPEN only on an official source; nothing is
    inferred from HTTP 200 alone.
    """

    if http_status in (404, 410):
        return JobStatus.CLOSED, "gone or closed notice observed"
    if not 200 <= http_status < 300:
        return JobStatus.UNVERIFIED, f"http {http_status} from source"
    if closed_notice_found:
        return JobStatus.CLOSED, "closed notice observed"
    expiry = parse_valid_through(valid_through)
    if expiry is not None and expiry < now:
        return JobStatus.EXPIRED, f"JSON-LD validThrough {valid_through} has passed"
    if apply_entry_kind is not None and authority in _OFFICIAL_AUTHORITIES:
        return JobStatus.OPEN, f"apply entry ({apply_entry_kind.value}) on an official source"
    if apply_entry_kind is not None:
        return (
            JobStatus.OPEN_THIRD_PARTY_ONLY,
            f"apply entry ({apply_entry_kind.value}) on a non-official source",
        )
    return (
        JobStatus.UNVERIFIED,
        "no apply entry found; never inferred from http 200 alone",
    )


def classify_reading(
    observations: list[PageDossier],
    apply_entry: ApplyEntry | None,
    evidence: list[PageEvidence],
    *,
    now: datetime,
) -> tuple[int, JobStatus, str]:
    """Classify sources separately, then choose the governing observation.

    A re-fetch/render replaces the older response for that URL, including
    redirects. Prefer official sources; within that tier closed/expired
    evidence wins, then the observation carrying the verified apply entry,
    then the latest observation. A stale board cannot close an observed
    official posting, a board's HTTP 200 cannot turn a failed official
    fetch into OPEN, and a followed apply form that fails or shows no cited
    entry does not erase the official posting page that did.
    """
    current: dict[str, PageDossier] = {}
    for obs in observations:
        current.pop(obs.requested_url, None)
        current[obs.source_url] = obs
    results: dict[int, tuple[JobStatus, str]] = {}
    for obs in current.values():
        entry = (
            apply_entry
            if apply_entry is not None and apply_entry.observation == obs.observation
            else None
        )
        results[obs.observation] = classify_from_evidence(
            http_status=obs.http_status,
            apply_entry_kind=entry.kind if entry else None,
            closed_notice_found=any(
                e.observation == obs.observation and e.kind == EvidenceKind.CLOSED_NOTICE
                for e in evidence
            ),
            valid_through=obs.jsonld.valid_through if obs.jsonld else None,
            now=now,
            authority=obs.authority,
        )
    chosen = max(
        current.values(),
        key=lambda o: (
            is_official_authority(o.authority),
            results[o.observation][0] in (JobStatus.CLOSED, JobStatus.EXPIRED),
            apply_entry is not None and apply_entry.observation == o.observation,
            o.observation,
        ),
    )
    status, reason = results[chosen.observation]
    return chosen.observation, status, reason

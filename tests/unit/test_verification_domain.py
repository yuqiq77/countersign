"""The status classification decision table and the approval lifecycle gate."""

from datetime import UTC, datetime

from countersign.contracts.enums import ApplyEntryKind, ApprovalDecision, JobStatus, SourceAuthority
from countersign.contracts.verification import VerificationResult
from countersign.domain.gates import allowed_decisions_for
from countersign.domain.verification import classify_from_evidence


def classify(
    *,
    http_status: int = 200,
    apply_markers: bool = False,
    closed_markers: bool = False,
    authority: SourceAuthority = SourceAuthority.OFFICIAL_ATS,
) -> JobStatus:
    status, _ = classify_from_evidence(
        http_status=http_status,
        apply_entry_kind=ApplyEntryKind.APPLY_CONTROL if apply_markers else None,
        closed_notice_found=closed_markers,
        valid_through=None,
        now=datetime.now(UTC),
        authority=authority,
    )
    return status


def test_gone_or_closed_markers_mean_closed() -> None:
    assert classify(http_status=404) == JobStatus.CLOSED
    assert classify(http_status=410) == JobStatus.CLOSED
    assert classify(closed_markers=True) == JobStatus.CLOSED


def test_http_errors_are_unknown() -> None:
    assert classify(http_status=500) == JobStatus.UNVERIFIED
    assert classify(http_status=403) == JobStatus.UNVERIFIED


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
    assert classify() == JobStatus.UNVERIFIED


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


def test_approve_is_offered_on_any_pinned_verification_that_is_not_closed() -> None:
    """The lifecycle gate (ADR-013 as amended): the reviewer may approve a
    posting the system could not verify, and the approval then records their
    own verification. CLOSED and EXPIRED are guards the workflow ends before
    approval; a missing verification is never approvable."""

    for status in (JobStatus.OPEN, JobStatus.OPEN_THIRD_PARTY_ONLY, JobStatus.UNVERIFIED):
        allowed = allowed_decisions_for(_verification(status))
        assert ApprovalDecision.APPROVE in allowed, status
        assert ApprovalDecision.REJECT in allowed
        assert ApprovalDecision.REQUEST_EVIDENCE in allowed
    for status in (JobStatus.CLOSED, JobStatus.EXPIRED):
        assert ApprovalDecision.APPROVE not in allowed_decisions_for(_verification(status)), status
    assert ApprovalDecision.APPROVE not in allowed_decisions_for(None)


def test_request_evidence_is_offered_only_below_the_round_cap() -> None:
    from countersign.domain.gates import MAX_GENERATIONS

    open_ = _verification(JobStatus.OPEN)
    for generation in range(1, MAX_GENERATIONS):
        assert ApprovalDecision.REQUEST_EVIDENCE in allowed_decisions_for(
            open_, generation=generation
        )
    capped = allowed_decisions_for(open_, generation=MAX_GENERATIONS)
    assert ApprovalDecision.REQUEST_EVIDENCE not in capped
    assert capped == [ApprovalDecision.APPROVE, ApprovalDecision.REJECT]
    assert allowed_decisions_for(None, generation=MAX_GENERATIONS) == [ApprovalDecision.REJECT]


def test_evidence_table_ranks_closed_and_expiry_over_apply_entries() -> None:
    from datetime import UTC, datetime

    from countersign.contracts.enums import ApplyEntryKind
    from countersign.domain.verification import classify_from_evidence, parse_valid_through

    now = datetime(2026, 9, 20, tzinfo=UTC)

    def run(
        *,
        http_status: int = 200,
        entry: ApplyEntryKind | None = ApplyEntryKind.APPLY_CONTROL,
        closed: bool = False,
        valid_through: str | None = None,
        authority: SourceAuthority = SourceAuthority.OFFICIAL_ATS,
    ) -> JobStatus:
        return classify_from_evidence(
            http_status=http_status,
            apply_entry_kind=entry,
            closed_notice_found=closed,
            valid_through=valid_through,
            now=now,
            authority=authority,
        )[0]

    assert run() == JobStatus.OPEN
    assert run(authority=SourceAuthority.COMPANY_CONTROLLED) == JobStatus.OPEN_THIRD_PARTY_ONLY
    assert run(entry=None) == JobStatus.UNVERIFIED
    assert run(closed=True) == JobStatus.CLOSED
    assert run(http_status=410) == JobStatus.CLOSED
    assert run(http_status=503) == JobStatus.UNVERIFIED
    assert run(valid_through="2026-09-01") == JobStatus.EXPIRED
    assert run(valid_through="2026-10-28T00:00:00Z") == JobStatus.OPEN
    assert run(valid_through="not a date") == JobStatus.OPEN
    assert parse_valid_through("2026-10-28") is not None
    assert parse_valid_through("") is None


def test_multi_page_status_never_mixes_origins_or_reuses_an_old_render() -> None:
    from countersign.capabilities.page_evidence import observe
    from countersign.contracts.enums import EvidenceKind, ObservationHow
    from countersign.contracts.page_reading import ApplyEntry, PageEvidence
    from countersign.domain.verification import classify_reading

    board_url = "https://board.example/jobs/1"
    ats_url = "https://jobs.lever.co/acme/1"
    now = datetime(2026, 9, 20, tzinfo=UTC)
    board = observe(
        index=0,
        source_url=board_url,
        how=ObservationHow.STATIC,
        authority=SourceAuthority.COMPANY_CONTROLLED,
        http_status=200,
        body=(
            b'<script type="application/ld+json">{"@type":"JobPosting", '
            b'"validThrough":"2020-01-01"}</script><p>Position closed.</p>'
        ),
    ).dossier()
    ats = observe(
        index=1,
        source_url=ats_url,
        how=ObservationHow.FOLLOWED,
        authority=SourceAuthority.OFFICIAL_ATS,
        http_status=503,
        body=b"<button>Apply</button>",
    ).dossier()
    entry = ApplyEntry(kind=ApplyEntryKind.APPLY_CONTROL, cited_text="Apply", observation=1)
    closed_board = PageEvidence(
        kind=EvidenceKind.CLOSED_NOTICE, cited_text="Position closed.", observation=0
    )
    chosen, status, _ = classify_reading([board, ats], entry, [closed_board], now=now)
    assert (chosen, status) == (1, JobStatus.UNVERIFIED)
    ats = ats.model_copy(update={"http_status": 200})
    chosen, status, _ = classify_reading([board, ats], entry, [closed_board], now=now)
    assert (chosen, status) == (1, JobStatus.OPEN)
    rendered = ats.model_copy(
        update={
            "observation": 2,
            "how": ObservationHow.RENDERED,
            "http_status": 410,
            "requested_url": ats_url,
            "source_url": "https://board.example/unavailable",
            "authority": SourceAuthority.COMPANY_CONTROLLED,
        }
    )
    chosen, status, _ = classify_reading([board, ats, rendered], entry, [], now=now)
    assert (chosen, status) == (2, JobStatus.CLOSED)

    # A same-job application form must not erase the official posting's
    # expiry just because it is the newest successful page.
    expired_ats = ats.model_copy(update={"jsonld": board.jsonld})
    form = ats.model_copy(
        update={
            "observation": 2,
            "source_url": f"{ats_url}/apply",
            "requested_url": f"{ats_url}/apply",
        }
    )
    form_entry = entry.model_copy(update={"observation": 2})
    chosen, status, _ = classify_reading([expired_ats, form], form_entry, [], now=now)
    assert (chosen, status) == (1, JobStatus.EXPIRED)

    # A followed apply form that fails, or shows nothing the model cited,
    # does not erase the official posting page whose entry was verified.
    for failed_form in (
        form.model_copy(update={"http_status": 403}),
        form.model_copy(update={"http_status": 200}),
    ):
        chosen, status, _ = classify_reading([ats, failed_form], entry, [], now=now)
        assert (chosen, status) == (1, JobStatus.OPEN)
    gone_form = form.model_copy(update={"http_status": 410})
    chosen, status, _ = classify_reading([ats, gone_form], entry, [], now=now)
    assert (chosen, status) == (2, JobStatus.CLOSED)  # closed still outranks the entry

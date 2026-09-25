"""End-to-end: real Activities, real database, fake model, mocked network.

Proves the crash-recovery contract at the persistence layer: re-executed
Activities (at-least-once delivery) leave exactly one snapshot, one
evaluation, one approval, one decision, and no duplicate audit events.
Failure paths run against the real state machine — no mocked quarantine.
"""

import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import func, select
from support import JOB_URL, start_case, time_skipping_env, wait_for_pending_approval
from temporalio.worker import Worker

import countersign.activities.case_activities as case_activities
from countersign.activities import ALL_ACTIVITIES
from countersign.capabilities.http_fetch import FetchResult
from countersign.contracts.enums import (
    ApplyEntryKind,
    ApprovalDecision,
    ApprovalRequestStatus,
    CaseState,
    JobStatus,
    ReaderAction,
    SourceAuthority,
)
from countersign.contracts.page_reading import ApplyEntry, PageReading, PageStep
from countersign.contracts.workflow_io import (
    CaseStateNote,
    CaseWorkflowInput,
    DecisionSignal,
)
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.persistence import repo
from countersign.persistence.db import session_scope
from countersign.persistence.models import (
    AuditEventRow,
    EvaluationRunRow,
    HumanDecisionRow,
    SourceSnapshotRow,
)
from countersign.workflows.job_case import JobIntelligenceWorkflow

JOB_HTML = b"""
<html>
  <head><title>Platform Engineer - Acme</title>
        <meta property="og:site_name" content="Acme" /></head>
  <body>
    <h1>Platform Engineer</h1>
    <p>Build durable systems. Salary USD 100,000 - 130,000.</p>
    <a href="/apply">Apply for this job</a>
  </body>
</html>
"""


def _fetch_result(url: str) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        http_status=200,
        content_type="text/html",
        body=JOB_HTML,
        redirect_chain=[],
    )


@pytest.fixture
def fake_network(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    def fake_fetch(url: str, **_: object) -> FetchResult:
        return _fetch_result(url)

    def provider() -> FakeEvaluationProvider:
        return FakeEvaluationProvider(
            page_steps=[
                PageStep(
                    action=ReaderAction.DONE,
                    why="scripted test posting",
                    reading=PageReading(
                        body_observation=0,
                        apply_entry=ApplyEntry(
                            kind=ApplyEntryKind.APPLY_CONTROL,
                            cited_text="Apply for this job",
                            observation=0,
                            href="https://boards.greenhouse.io/apply",
                        ),
                    ),
                )
            ]
        )

    monkeypatch.setattr(case_activities, "resolve_provider", provider)
    monkeypatch.setattr(case_activities, "fetch_url", fake_fetch)
    monkeypatch.setenv("COUNTERSIGN_MODEL_PROVIDER", "fake")
    yield


@pytest.mark.usefixtures("db", "fake_network")
async def test_full_case_with_real_activities() -> None:
    queue = f"e2e-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=list(ALL_ACTIVITIES),
            activity_executor=ThreadPoolExecutor(4),
        ):
            handle = await start_case(env, queue)
            status = await wait_for_pending_approval(handle)
            case_id = status.case_id
            assert status.approval_request_id is not None

            signal = DecisionSignal(
                approval_request_id=status.approval_request_id,
                decision=ApprovalDecision.APPROVE,
                reason="end-to-end test approval",
                decision_version=1,
                actor="admin",
            )
            await handle.signal(JobIntelligenceWorkflow.submit_decision, signal)
            await handle.signal(JobIntelligenceWorkflow.submit_decision, signal)
            result = await handle.result()

    assert result.final_state == CaseState.APPROVED

    with session_scope() as session:
        case = repo.get_case(session, case_id)
        assert case is not None
        assert case.state == CaseState.APPROVED.value
        assert case.title == "Platform Engineer - Acme"

        # The verification re-observation deduped onto the first snapshot
        # (identical body), so exactly one snapshot exists.
        snapshots = session.scalar(
            select(func.count())
            .select_from(SourceSnapshotRow)
            .where(SourceSnapshotRow.case_id == case_id)
        )
        assert snapshots == 1

        verification = repo.latest_verification(session, case_id)
        assert verification is not None
        assert verification.content_changed is False
        assert verification.snapshot_id is not None

        approval = repo.approval_for_case(session, case_id)
        assert approval is not None
        # Artifacts under review are pinned at creation.
        assert approval.snapshot_id is not None
        assert approval.verification_id is not None
        assert approval.evaluation_id is not None

        evaluations = session.scalar(
            select(func.count())
            .select_from(EvaluationRunRow)
            .where(EvaluationRunRow.case_id == case_id)
        )
        assert evaluations == 1

        decisions = session.scalar(select(func.count()).select_from(HumanDecisionRow))
        assert decisions == 1

        audit_actions = list(
            session.scalars(
                select(AuditEventRow.action).where(AuditEventRow.resource_id == case_id)
            )
        )
        assert len(audit_actions) == len(set(audit_actions)), audit_actions


@pytest.mark.usefixtures("db", "fake_network")
async def test_contract_failure_quarantines_through_real_state_machine() -> None:
    """The ContractFailure -> QUARANTINED path with the real repo and states.

    Regression test: the workflow quarantines from OFFICIAL_SOURCE_VERIFIED
    (evaluation never persisted EVALUATED), which the state machine must
    permit — a mocked quarantine activity would hide this cross-layer break.
    """

    queue = f"e2e-{uuid.uuid4()}"

    def failing_provider() -> FakeEvaluationProvider:
        return FakeEvaluationProvider(fail_attempts=99)

    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=list(ALL_ACTIVITIES),
            activity_executor=ThreadPoolExecutor(4),
        ):
            import countersign.activities.case_activities as activities_module

            original = activities_module.resolve_provider
            activities_module.resolve_provider = failing_provider
            try:
                handle = await start_case(env, queue)
                result = await handle.result()
            finally:
                activities_module.resolve_provider = original

    assert result.final_state == CaseState.QUARANTINED
    with session_scope() as session:
        case = repo.get_case(session, result.case_id)
        assert case is not None
        assert case.state == CaseState.QUARANTINED.value


@pytest.mark.usefixtures("db", "fake_network")
async def test_transient_fetch_error_is_retried_not_recorded() -> None:
    """One network blip during verification retries instead of persisting
    a permanent UNVERIFIED verdict."""

    queue = f"e2e-{uuid.uuid4()}"
    calls = {"n": 0}

    def flaky_fetch(url: str, **_: object) -> FetchResult:
        calls["n"] += 1
        # Call 1: snapshot fetch. Call 2: first verify attempt — fail it.
        if calls["n"] == 2:
            raise httpx.ConnectError("induced transient failure")
        return _fetch_result(url)

    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=list(ALL_ACTIVITIES),
            activity_executor=ThreadPoolExecutor(4),
        ):
            import countersign.activities.case_activities as activities_module

            original = activities_module.fetch_url
            activities_module.fetch_url = flaky_fetch
            try:
                handle = await start_case(env, queue)
                status = await wait_for_pending_approval(handle)
            finally:
                activities_module.fetch_url = original

    assert calls["n"] >= 3  # snapshot + failed verify + retried verify
    with session_scope() as session:
        verification = repo.latest_verification(session, status.case_id)
        assert verification is not None
        assert verification.status == JobStatus.OPEN  # the retry re-observed


@pytest.mark.usefixtures("db", "fake_network")
def test_retried_activities_are_idempotent() -> None:
    """Simulate at-least-once delivery: run each Activity body twice."""

    case_id = str(uuid.uuid4())
    payload = CaseWorkflowInput(case_id=case_id, url=JOB_URL)

    case_activities.snapshot_source(payload)
    case_activities.snapshot_source(payload)
    case_activities.normalize_case(payload)
    case_activities.normalize_case(payload)
    case_activities.verify_status(payload)
    case_activities.verify_status(payload)
    case_activities.evaluate_case(payload)
    case_activities.evaluate_case(payload)
    first = case_activities.create_approval(payload)
    second = case_activities.create_approval(payload)
    assert first.approval_request_id == second.approval_request_id
    # The retried read comes back from SQLite (naive storage): the outcome
    # must still be timezone-aware or workflow date arithmetic would crash.
    assert second.expires_at.tzinfo is not None

    with session_scope() as session:
        snapshots = session.scalar(
            select(func.count())
            .select_from(SourceSnapshotRow)
            .where(SourceSnapshotRow.case_id == case_id)
        )
        assert snapshots == 1
        evaluations = session.scalar(
            select(func.count())
            .select_from(EvaluationRunRow)
            .where(EvaluationRunRow.case_id == case_id)
        )
        assert evaluations == 1
        audit_actions = list(
            session.scalars(
                select(AuditEventRow.action).where(AuditEventRow.resource_id == case_id)
            )
        )
        assert len(audit_actions) == len(set(audit_actions)), audit_actions
        case = repo.get_case(session, case_id)
        assert case is not None
        assert case.state == CaseState.PENDING_APPROVAL.value


@pytest.mark.usefixtures("db", "fake_network")
async def test_provider_outage_during_verify_is_retried_not_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model transport outage on the first verify attempt retries the
    Activity; it never persists an UNVERIFIED verification the outage made."""

    from countersign.contracts.base import ContractModel
    from countersign.gateway.provider import ModelProvider, ParsedResponse, ProviderUnavailable

    scripted = case_activities.resolve_provider  # the fake_network provider factory
    attempts = {"n": 0}

    def provider() -> ModelProvider:
        attempts["n"] += 1
        real = scripted()
        if attempts["n"] > 1:
            return real

        def outage[T: ContractModel](
            *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
        ) -> ParsedResponse[T]:
            raise ProviderUnavailable("induced 529")

        monkeypatch.setattr(real, "parse", outage)
        return real

    monkeypatch.setattr(case_activities, "resolve_provider", provider)
    queue = f"e2e-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=list(ALL_ACTIVITIES),
            activity_executor=ThreadPoolExecutor(4),
        ):
            handle = await start_case(env, queue)
            status = await wait_for_pending_approval(handle)

    assert attempts["n"] >= 3  # failed verify, retried verify, evaluate
    with session_scope() as session:
        verification = repo.latest_verification(session, status.case_id)
        assert verification is not None and verification.status == JobStatus.OPEN
        approval = repo.approval_for_case(session, status.case_id)
        assert approval is not None and "APPROVE" in list(approval.allowed_decisions)


@pytest.mark.usefixtures("db", "fake_network")
async def test_attested_approval_of_an_unverified_posting_is_recorded_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-013 end to end: the unscripted reader leaves the posting UNVERIFIED,
    the approval offers no plain APPROVE, and a reviewer's attested APPROVE
    lands as one decision plus one separate attestation audit event, even
    when the Signal is delivered twice."""

    monkeypatch.setattr(case_activities, "resolve_provider", lambda: FakeEvaluationProvider())
    queue = f"e2e-{uuid.uuid4()}"
    note = "application form loaded and accepted input today"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=list(ALL_ACTIVITIES),
            activity_executor=ThreadPoolExecutor(4),
        ):
            handle = await start_case(env, queue)
            status = await wait_for_pending_approval(handle)
            assert status.approval_request_id is not None
            with session_scope() as session:
                verification = repo.latest_verification(session, status.case_id)
                assert verification is not None
                assert verification.status == JobStatus.UNVERIFIED
                approval = repo.approval_for_case(session, status.case_id)
                assert approval is not None
                assert "APPROVE" not in list(approval.allowed_decisions)
            signal = DecisionSignal(
                approval_request_id=status.approval_request_id,
                decision=ApprovalDecision.APPROVE,
                reason="fits; applying anyway",
                decision_version=1,
                actor="admin",
                attested_open=True,
                attestation_note=note,
            )
            await handle.signal(JobIntelligenceWorkflow.submit_decision, signal)
            await handle.signal(JobIntelligenceWorkflow.submit_decision, signal)
            result = await handle.result()

    assert result.final_state == CaseState.APPROVED
    with session_scope() as session:
        decisions = list(session.scalars(select(HumanDecisionRow)))
        assert len(decisions) == 1
        assert decisions[0].attested_open is True
        assert decisions[0].attestation_note == note
        attested = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.resource_id == status.approval_request_id,
                    AuditEventRow.action == "human.verification_attested",
                )
            )
        )
        assert len(attested) == 1 and attested[0].reason == note


@pytest.mark.usefixtures("db", "fake_network")
def test_expired_approval_closes_the_case() -> None:
    """expire_approval flips the approval AND moves the case out of
    PENDING_APPROVAL — no zombie case behind a completed workflow."""

    case_id = str(uuid.uuid4())
    payload = CaseWorkflowInput(case_id=case_id, url=JOB_URL)
    case_activities.snapshot_source(payload)
    case_activities.normalize_case(payload)
    case_activities.verify_status(payload)
    case_activities.evaluate_case(payload)
    approval = case_activities.create_approval(payload)

    case_activities.expire_approval(
        CaseStateNote(
            case_id=case_id,
            reason="approval expired without a decision",
            approval_request_id=approval.approval_request_id,
        )
    )

    with session_scope() as session:
        request = repo.get_approval_request(session, approval.approval_request_id)
        assert request is not None
        assert request.status == ApprovalRequestStatus.EXPIRED.value
        case = repo.get_case(session, case_id)
        assert case is not None
        assert case.state == CaseState.CLOSED_OR_STALE.value


EMPTY_HTML = (
    b"<html><head><title>Member of Technical Staff</title>"
    b"<script>window.__APP__ = {};</script></head>"
    b'<body><div id="root"></div></body></html>'
)


@pytest.mark.usefixtures("db")
async def test_empty_page_stops_before_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client-rendered page with no text and no JSON-LD ends at
    NEEDS_MORE_EVIDENCE through the real Activities: verification recorded,
    no evaluation row, the reason in the audit trail."""

    def empty_fetch(url: str, **_: object) -> FetchResult:
        result = _fetch_result(url)
        return FetchResult(
            requested_url=result.requested_url,
            final_url=result.final_url,
            http_status=200,
            content_type="text/html",
            body=EMPTY_HTML,
            redirect_chain=[],
        )

    monkeypatch.setattr(case_activities, "fetch_url", empty_fetch)
    monkeypatch.setenv("COUNTERSIGN_MODEL_PROVIDER", "fake")

    queue = f"e2e-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=list(ALL_ACTIVITIES),
            activity_executor=ThreadPoolExecutor(4),
        ):
            handle = await start_case(env, queue)
            result = await handle.result()

    assert result.final_state == CaseState.NEEDS_MORE_EVIDENCE
    with session_scope() as session:
        case = repo.get_case(session, result.case_id)
        assert case is not None
        assert case.state == CaseState.NEEDS_MORE_EVIDENCE.value
        verification = repo.latest_verification(session, result.case_id)
        assert verification is not None
        assert verification.status == JobStatus.UNVERIFIED
        evaluations = session.scalar(
            select(func.count())
            .select_from(EvaluationRunRow)
            .where(EvaluationRunRow.case_id == result.case_id)
        )
        assert evaluations == 0
        reasons = list(
            session.scalars(
                select(AuditEventRow.reason).where(
                    AuditEventRow.resource_id == result.case_id,
                    AuditEventRow.action == "case.state.needs_more_evidence",
                )
            )
        )
        assert reasons and "empty posting body" in (reasons[0] or "")


COMPANY_URL = "https://acme.example/careers/platform-engineer"
COMPANY_HTML = b"""
<html><body>
  <h1>Platform Engineer</h1>
  <p>Read the full posting and apply on our applicant portal.</p>
  <a href="https://boards.greenhouse.io/acme/jobs/1">See full posting</a>
</body></html>
"""


@pytest.mark.usefixtures("db")
async def test_reader_follows_a_company_page_to_its_ats_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A company page with no apply entry links to its ATS posting: the
    reader follows the listed link, the apply entry is verified on the ATS
    page, the ATS page's authority governs OPEN, both observations are
    stored, and the evaluation reads the ATS page's body."""

    from countersign.contracts.enums import ApplyEntryKind, ReaderAction
    from countersign.contracts.page_reading import ApplyEntry, PageReading, PageStep
    from countersign.persistence.models import PageReadingRow

    pages = {COMPANY_URL: COMPANY_HTML, JOB_URL: JOB_HTML}

    def fetch(url: str, **_: object) -> FetchResult:
        body = pages[url]
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            content_type="text/html",
            body=body,
            redirect_chain=[],
        )

    scripted = FakeEvaluationProvider(
        page_steps=[
            PageStep(
                action=ReaderAction.FOLLOW,
                why="posting lives on the ATS",
                href=JOB_URL,
                reading=PageReading(
                    body_observation=0, apply_entry=ApplyEntry(kind=ApplyEntryKind.NONE)
                ),
            ),
            PageStep(
                action=ReaderAction.DONE,
                why="apply control on the ATS page",
                reading=PageReading(
                    body_observation=1,
                    apply_entry=ApplyEntry(
                        kind=ApplyEntryKind.APPLY_CONTROL,
                        cited_text="Apply for this job",
                        observation=1,
                        href="https://boards.greenhouse.io/apply",
                    ),
                ),
            ),
        ]
    )
    monkeypatch.setattr(case_activities, "fetch_url", fetch)
    monkeypatch.setattr(case_activities, "resolve_provider", lambda: scripted)
    monkeypatch.setenv("COUNTERSIGN_MODEL_PROVIDER", "fake")

    queue = f"e2e-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=list(ALL_ACTIVITIES),
            activity_executor=ThreadPoolExecutor(4),
        ):
            handle = await start_case(env, queue, url=COMPANY_URL)
            status = await wait_for_pending_approval(handle)

    with session_scope() as session:
        verification = repo.latest_verification(session, status.case_id)
        assert verification is not None
        assert verification.status == JobStatus.OPEN
        assert verification.authority == SourceAuthority.OFFICIAL_ATS
        assert verification.final_url == JOB_URL
        assert "APPLY_CONTROL" in verification.reason
        assert verification.reading_id is not None

        reading = repo.latest_page_reading(session, status.case_id)
        assert reading is not None
        assert [o.how.value for o in reading.observations] == ["STATIC", "FOLLOWED"]
        assert all(o.snapshot_id for o in reading.observations)
        assert reading.body_observation == 1
        assert verification.snapshot_id == reading.observations[1].snapshot_id
        assert reading.rejected_claims == []
        assert session.get(PageReadingRow, reading.reading_id) is not None

        snapshots = session.scalar(
            select(func.count())
            .select_from(SourceSnapshotRow)
            .where(SourceSnapshotRow.case_id == status.case_id)
        )
        assert snapshots == 2  # company page (re-observation deduped onto it) + ATS page

        evaluation = repo.latest_evaluation(session, status.case_id)
        assert evaluation is not None
        approval = repo.approval_for_case(session, status.case_id)
        assert approval is not None and "APPROVE" in list(approval.allowed_decisions)

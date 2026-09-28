"""End-to-end: real Activities, real database, fake model, mocked network.

Proves the crash-recovery contract at the persistence layer: re-executed
Activities (at-least-once delivery) leave exactly one snapshot, one
evaluation, one approval, one decision, and no duplicate audit events.
Failure paths run against the real state machine — no mocked quarantine.
"""

import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
    ApprovalRequestRow,
    AuditEventRow,
    EvaluationRunRow,
    HumanDecisionRow,
    SourceSnapshotRow,
    VerificationRow,
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
async def test_approval_of_an_unverified_posting_is_recorded_once_as_the_reviewers_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-013 as amended, end to end: the unscripted reader leaves the posting
    UNVERIFIED, the approval still offers APPROVE, and the reviewer's plain
    APPROVE lands as one decision flagged as approved without an official
    OPEN plus one `human.verification_attested` audit event carrying the
    decision reason, even when the Signal is delivered twice."""

    monkeypatch.setattr(case_activities, "resolve_provider", lambda: FakeEvaluationProvider())
    queue = f"e2e-{uuid.uuid4()}"
    reason = "fits; the application form accepted input today"
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
                assert "APPROVE" in list(approval.allowed_decisions)
            signal = DecisionSignal(
                approval_request_id=status.approval_request_id,
                decision=ApprovalDecision.APPROVE,
                reason=reason,
                decision_version=1,
                actor="admin",
            )
            await handle.signal(JobIntelligenceWorkflow.submit_decision, signal)
            await handle.signal(JobIntelligenceWorkflow.submit_decision, signal)
            result = await handle.result()

    assert result.final_state == CaseState.APPROVED
    with session_scope() as session:
        decisions = list(session.scalars(select(HumanDecisionRow)))
        assert len(decisions) == 1
        assert decisions[0].approved_without_official_open is True
        attested = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.resource_id == status.approval_request_id,
                    AuditEventRow.action == "human.verification_attested",
                )
            )
        )
        assert len(attested) == 1 and attested[0].reason == reason


@pytest.mark.usefixtures("db", "fake_network")
async def test_request_evidence_re_evaluates_with_the_reviewers_note() -> None:
    """ADR-014 end to end: the note becomes operator context for round 2, the
    round-2 evaluation cites the round-1 decision, every artifact exists once
    per round, and the case page's in-flight flag ends with the approval."""

    queue = f"e2e-{uuid.uuid4()}"
    note = "Completed a structured interview-preparation course this year."
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=list(ALL_ACTIVITIES),
            activity_executor=ThreadPoolExecutor(4),
        ):
            handle = await start_case(env, queue)
            first = await wait_for_pending_approval(handle)
            assert first.approval_request_id is not None
            await handle.signal(
                JobIntelligenceWorkflow.submit_decision,
                DecisionSignal(
                    approval_request_id=first.approval_request_id,
                    decision=ApprovalDecision.REQUEST_EVIDENCE,
                    reason=note,
                    decision_version=1,
                    actor="admin",
                ),
            )
            second = await wait_for_pending_approval(handle, generation=2)
            assert second.approval_request_id is not None
            with session_scope() as session:
                assert repo.in_flight(session, second.case_id) is True
                context = repo.operator_context(session, second.case_id)
                assert [c.text for c in context] == [note]
                evaluation = repo.latest_evaluation(session, second.case_id)
                assert evaluation is not None
                assert evaluation.operator_context_ids == [context[0].decision_id]
                history = repo.evaluations_for_case(session, second.case_id)
                assert [e.generation for e in history] == [1, 2]
                assert history[0].previous_evaluation_id is None
                assert evaluation.previous_evaluation_id == history[0].evaluation_id
                approval = repo.approval_for_case(session, second.case_id)
                assert approval is not None and approval.generation == 2
            await handle.signal(
                JobIntelligenceWorkflow.submit_decision,
                DecisionSignal(
                    approval_request_id=second.approval_request_id,
                    decision=ApprovalDecision.APPROVE,
                    reason="round two",
                    decision_version=1,
                    actor="admin",
                ),
            )
            result = await handle.result()

    assert result.final_state == CaseState.APPROVED
    with session_scope() as session:
        case_id = second.case_id
        assert repo.in_flight(session, case_id) is False
        counts = {
            "verifications": session.scalar(
                select(func.count())
                .select_from(VerificationRow)
                .where(VerificationRow.case_id == case_id)
            ),
            "evaluations": session.scalar(
                select(func.count())
                .select_from(EvaluationRunRow)
                .where(EvaluationRunRow.case_id == case_id)
            ),
            "approvals": session.scalar(
                select(func.count())
                .select_from(ApprovalRequestRow)
                .where(ApprovalRequestRow.case_id == case_id)
            ),
            "decisions": session.scalar(select(func.count()).select_from(HumanDecisionRow)),
        }
        assert counts == {"verifications": 2, "evaluations": 2, "approvals": 2, "decisions": 2}
        audit_actions = list(
            session.scalars(
                select(AuditEventRow.action).where(AuditEventRow.resource_id == case_id)
            )
        )
        assert audit_actions.count("case.state.official_source_verified") == 2
        assert audit_actions.count("case.state.needs_more_evidence") == 1
        assert audit_actions.count("case.round.continued") == 1
        history = repo.evaluations_for_case(session, case_id)

    # A retried round-2 evaluate Activity (completion lost after the record
    # was persisted) must still name round 1 as its predecessor, not the
    # round-2 record it finds as "latest".
    retried = case_activities.evaluate_case(
        CaseWorkflowInput(case_id=case_id, url=JOB_URL, generation=2)
    )
    assert retried.record.evaluation_id == history[1].evaluation_id
    assert retried.record.previous_evaluation_id == history[0].evaluation_id
    assert retried.record.generation == 2
    with session_scope() as session:
        assert [e.evaluation_id for e in repo.evaluations_for_case(session, case_id)] == [
            history[0].evaluation_id,
            history[1].evaluation_id,
        ]


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


# --- ticket 002: official company hosts, and the retry that returns the stored result ---

COMPANY_APPLY_URL = "https://careers.acme.example/roles/1"
COMPANY_APPLY_HTML = b"""
<html><body>
  <h1>Platform Engineer</h1>
  <p>Build the platform. Apply below.</p>
  <a href="/roles/1/apply">Apply now</a>
</body></html>
"""


def _company_provider() -> FakeEvaluationProvider:
    return FakeEvaluationProvider(
        page_steps=[
            PageStep(
                action=ReaderAction.DONE,
                why="apply control on the company page",
                reading=PageReading(
                    body_observation=0,
                    apply_entry=ApplyEntry(
                        kind=ApplyEntryKind.APPLY_CONTROL,
                        cited_text="Apply now",
                        observation=0,
                        href="https://careers.acme.example/roles/1/apply",
                    ),
                ),
            )
        ]
    )


@pytest.mark.usefixtures("db", "fake_network")
def test_retried_verify_after_commit_returns_the_stored_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry after the commit, before Temporal saw the completion, hands
    the workflow the record the database holds — text length included, so
    the empty-body branch is unchanged — and reads nothing again."""

    from countersign.domain import ids
    from countersign.persistence.models import PageReadingRow

    fetches: list[str] = []
    real_fetch = case_activities.fetch_url

    def counting_fetch(url: str, **kwargs: object) -> FetchResult:
        fetches.append(url)
        return real_fetch(url, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(case_activities, "fetch_url", counting_fetch)
    case_id = str(uuid.uuid4())
    payload = CaseWorkflowInput(case_id=case_id, url=JOB_URL)
    case_activities.snapshot_source(payload)
    case_activities.normalize_case(payload)
    first = case_activities.verify_status(payload)
    assert first.observed_text_chars is not None and first.observed_text_chars > 0
    fetched_once = len(fetches)

    again = case_activities.verify_status(payload)
    assert again == first
    assert len(fetches) == fetched_once
    with session_scope() as session:
        readings = list(
            session.scalars(select(PageReadingRow).where(PageReadingRow.case_id == case_id))
        )
        assert len(readings) == 1
        reading = repo.get_page_reading(session, ids.reading_id(case_id, 1))
        assert reading is not None and reading.body_text_chars == first.observed_text_chars
        # A reading written before the field existed: the length is
        # recomputed from the body snapshot rather than defaulting to None.
        readings[0].payload = {**readings[0].payload, "body_text_chars": None}
    third = case_activities.verify_status(payload)
    assert third.observed_text_chars == first.observed_text_chars
    assert third.verification == first.verification
    assert len(fetches) == fetched_once


def _render_unavailable(url: str, body: bytes, timeout: float) -> FetchResult | None:
    return None


@pytest.mark.usefixtures("db")
def test_retried_verify_of_an_empty_body_still_reports_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def empty_fetch(url: str, **_: object) -> FetchResult:
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            content_type="text/html",
            body=EMPTY_HTML,
            redirect_chain=[],
        )

    monkeypatch.setattr(case_activities, "fetch_url", empty_fetch)
    monkeypatch.setattr(case_activities, "render_url", _render_unavailable)
    monkeypatch.setenv("COUNTERSIGN_MODEL_PROVIDER", "fake")
    case_id = str(uuid.uuid4())
    payload = CaseWorkflowInput(case_id=case_id, url=JOB_URL)
    case_activities.snapshot_source(payload)
    case_activities.normalize_case(payload)
    first = case_activities.verify_status(payload)
    assert first.observed_text_chars == 0
    again = case_activities.verify_status(payload)
    assert again.observed_text_chars == 0
    assert again.verification == first.verification


@pytest.mark.usefixtures("db")
async def test_mapped_company_host_reaches_official_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ticket 002 end to end: a page on a host the operator declared official
    is OFFICIAL_COMPANY at intake and in the reading, OPEN with a cited apply
    control, and the reading records the mapping it used; the same page with
    no mapping stays OPEN_THIRD_PARTY_ONLY."""

    from countersign.domain import ids
    from countersign.policy.official_hosts import OFFICIAL_HOSTS_PATH_VAR

    mapping = tmp_path / "official-hosts.json"
    mapping.write_text(
        '{"version": "e2e-1", "entries": [{"host": "careers.acme.example", "company": "acme"}]}'
    )
    monkeypatch.setenv(OFFICIAL_HOSTS_PATH_VAR, str(mapping))

    def fetch(url: str, **_: object) -> FetchResult:
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            content_type="text/html",
            body=COMPANY_APPLY_HTML,
            redirect_chain=[],
        )

    monkeypatch.setattr(case_activities, "fetch_url", fetch)
    monkeypatch.setattr(case_activities, "resolve_provider", _company_provider)
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
            handle = await start_case(env, queue, url=COMPANY_APPLY_URL)
            status = await wait_for_pending_approval(handle)

    with session_scope() as session:
        verification = repo.latest_verification(session, status.case_id)
        assert verification is not None
        assert verification.status == JobStatus.OPEN
        assert verification.authority == SourceAuthority.OFFICIAL_COMPANY
        snapshot = repo.body_snapshot(session, status.case_id)
        assert snapshot is not None and snapshot.authority_level == SourceAuthority.OFFICIAL_COMPANY
        reading = repo.get_page_reading(session, ids.reading_id(status.case_id, 1))
        assert reading is not None
        assert reading.observations[0].authority == SourceAuthority.OFFICIAL_COMPANY
        assert reading.observations[0].official_host_entry == "acme"
        assert reading.authority_policy_version == "e2e-1"
        assert reading.authority_policy_fingerprint is not None
        assert len(reading.authority_policy_fingerprint) == 8
        assert reading.body_text_chars is not None and reading.body_text_chars > 0
        body_snapshot, body_ref = repo.body_observation(session, status.case_id)
        assert body_snapshot is not None and body_snapshot.snapshot_id == snapshot.snapshot_id
        assert body_ref is not None and body_ref.authority == SourceAuthority.OFFICIAL_COMPANY
        approval = repo.approval_for_case(session, status.case_id)
        assert approval is not None and "APPROVE" in list(approval.allowed_decisions)

    # The same page with no mapping configured, through the real Activities.
    monkeypatch.delenv(OFFICIAL_HOSTS_PATH_VAR)
    case_id = str(uuid.uuid4())
    payload = CaseWorkflowInput(case_id=case_id, url=COMPANY_APPLY_URL)
    case_activities.snapshot_source(payload)
    case_activities.normalize_case(payload)
    unmapped = case_activities.verify_status(payload)
    assert unmapped.verification.status == JobStatus.OPEN_THIRD_PARTY_ONLY
    assert unmapped.verification.authority == SourceAuthority.COMPANY_CONTROLLED
    with session_scope() as session:
        reading = repo.get_page_reading(session, ids.reading_id(case_id, 1))
        assert reading is not None
        assert reading.observations[0].authority == SourceAuthority.COMPANY_CONTROLLED
        assert reading.observations[0].official_host_entry is None
        assert reading.authority_policy_version is None
        assert reading.authority_policy_fingerprint is None


@pytest.mark.usefixtures("db", "fake_network")
def test_a_mapping_that_stops_parsing_fails_the_case_without_retries_or_fetches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file edited into a broken state after worker start is a configuration
    failure: the Activity raises a non-retryable error before touching the
    network, so Temporal neither retries nor re-fetches the posting."""

    from temporalio.exceptions import ApplicationError

    from countersign.policy.official_hosts import OFFICIAL_HOSTS_PATH_VAR

    broken = tmp_path / "official-hosts.json"
    broken.write_text('{"version": "v", "entries": [')
    monkeypatch.setenv(OFFICIAL_HOSTS_PATH_VAR, str(broken))
    fetches: list[str] = []

    def counting_fetch(url: str, **_: object) -> FetchResult:
        fetches.append(url)
        return _fetch_result(url)

    monkeypatch.setattr(case_activities, "fetch_url", counting_fetch)
    payload = CaseWorkflowInput(case_id=str(uuid.uuid4()), url=JOB_URL)
    with pytest.raises(ApplicationError) as excinfo:
        case_activities.snapshot_source(payload)
    assert excinfo.value.type == "PolicyError" and excinfo.value.non_retryable
    assert fetches == []


@pytest.mark.usefixtures("db", "fake_network")
def test_verify_returns_what_a_concurrent_attempt_committed_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out attempt Temporal already replaced can still commit while
    the retry is running. The database keeps the first rows written; the
    retry must return those, not its own computation, or the workflow and
    the database would each believe a different status."""

    from countersign.contracts.enums import ObservationHow, ReaderStop
    from countersign.contracts.page_reading import ObservationRef, PageReadingRecord
    from countersign.contracts.verification import VerificationResult
    from countersign.domain import ids

    case_id = str(uuid.uuid4())
    payload = CaseWorkflowInput(case_id=case_id, url=JOB_URL)
    case_activities.snapshot_source(payload)
    case_activities.normalize_case(payload)
    reading_id = ids.reading_id(case_id, 1)
    verification_id = ids.verification_id(case_id, 1)
    real_body_snapshot = repo.body_snapshot

    def other_attempt_commits_first(session: object, cid: str) -> object:
        # Runs inside this attempt's session block, after its entry check and
        # before its writes: the earlier attempt lands its rows now.
        with session_scope() as other:
            intake = repo.latest_snapshot(other, cid)
            assert intake is not None
            repo.record_page_reading(
                other,
                PageReadingRecord(
                    reading_id=reading_id,
                    case_id=cid,
                    url=JOB_URL,
                    observations=[
                        ObservationRef(
                            index=0,
                            source_url=JOB_URL,
                            how=ObservationHow.STATIC,
                            http_status=200,
                            snapshot_id=intake.snapshot_id,
                            authority=SourceAuthority.COMPANY_CONTROLLED,
                        )
                    ],
                    body_observation=0,
                    apply_entry=None,
                    evidence=[],
                    rejected_claims=[],
                    steps=[],
                    stop_reason=ReaderStop.DONE,
                    prompt_version="page-reader/0.5.0",
                    usage=None,
                    read_at=repo.utcnow(),
                    body_text_chars=7,
                ),
            )
            repo.record_verification(
                other,
                verification_id=verification_id,
                result=VerificationResult(
                    case_id=cid,
                    status=JobStatus.OPEN_THIRD_PARTY_ONLY,
                    authority=SourceAuthority.COMPANY_CONTROLLED,
                    checked_at=repo.utcnow(),
                    http_status=200,
                    final_url=JOB_URL,
                    reason="the earlier attempt's reading",
                    apply_markers_found=True,
                    closed_markers_found=False,
                    reading_id=reading_id,
                ),
            )
        return real_body_snapshot(session, cid)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "body_snapshot", other_attempt_commits_first)
    outcome = case_activities.verify_status(payload)
    # This attempt read the page as OPEN on an official ATS; the database
    # holds the other attempt's OPEN_THIRD_PARTY_ONLY, and that is returned.
    assert outcome.verification.status == JobStatus.OPEN_THIRD_PARTY_ONLY
    assert outcome.verification.reason == "the earlier attempt's reading"
    assert outcome.observed_text_chars == 7
    with session_scope() as session:
        assert repo.latest_verification(session, case_id) == outcome.verification

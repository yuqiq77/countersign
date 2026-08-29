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
    ApprovalDecision,
    ApprovalRequestStatus,
    CaseState,
    JobStatus,
)
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
    a permanent UNKNOWN_STALE_RISK verdict."""

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

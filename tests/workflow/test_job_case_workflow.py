"""Workflow logic under Temporal's time-skipping test environment.

Activities are mocked here; the durable ordering, Signal dedup, approval
expiry, failure landing paths, and quarantine routing are the code under
test. Cross-layer behavior (real repo + real state machine) lives in
test_end_to_end_recovery.py.
"""

import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

from support import start_case, time_skipping_env, wait_for_pending_approval
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from countersign.contracts.enums import (
    ApprovalDecision,
    CaseState,
    JobStatus,
    SourceAuthority,
)
from countersign.contracts.provenance import SnapshotRecord
from countersign.contracts.verification import VerificationResult
from countersign.contracts.workflow_io import (
    ApprovalRequestOutcome,
    CaseStateNote,
    CaseWorkflowInput,
    DecisionSignal,
    EvaluateOutcome,
    NormalizeOutcome,
    SnapshotOutcome,
    VerifyOutcome,
)
from countersign.gateway.evaluator import run_evaluation
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.policy.defaults import salary_bands
from countersign.workflows.job_case import JobIntelligenceWorkflow

ActivityFn = Callable[..., Coroutine[Any, Any, Any]]


class Recorder:
    """Counts mock-activity invocations for assertions."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def hit(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1


def make_activities(
    recorder: Recorder,
    *,
    status: JobStatus = JobStatus.OPEN,
    evaluate_contract_failure: bool = False,
    snapshot_policy_failure: bool = False,
    approval_ttl: timedelta = timedelta(days=7),
) -> list[ActivityFn]:
    def snapshot_record(case_id: str) -> SnapshotRecord:
        return SnapshotRecord(
            snapshot_id="snap-1",
            case_id=case_id,
            canonical_url="https://boards.greenhouse.io/acme/jobs/1",
            final_url="https://boards.greenhouse.io/acme/jobs/1",
            source_type="pasted_url",
            authority_level=SourceAuthority.OFFICIAL_ATS,
            retrieved_at=datetime.now(UTC),
            http_status=200,
            content_type="text/html",
            content_hash="deadbeef" * 8,
            raw_object_key="blob/sha256/deadbeef",
            extractor_name="html-text",
            extractor_version="1.0.0",
            redirect_chain=[],
        )

    @activity.defn(name="snapshot_source")
    async def snapshot_source(payload: CaseWorkflowInput) -> SnapshotOutcome:
        recorder.hit("snapshot_source")
        if snapshot_policy_failure:
            raise ApplicationError(
                "destination is not a public address",
                type="FetchPolicyError",
                non_retryable=True,
            )
        return SnapshotOutcome(snapshot=snapshot_record(payload.case_id))

    @activity.defn(name="normalize_case")
    async def normalize_case(payload: CaseWorkflowInput) -> NormalizeOutcome:
        recorder.hit("normalize_case")
        return NormalizeOutcome(
            title="Engineer",
            company_hint="Acme",
            content_hash="deadbeef" * 8,
            extractor_version="1.0.0",
        )

    @activity.defn(name="verify_status")
    async def verify_status(payload: CaseWorkflowInput) -> VerifyOutcome:
        recorder.hit("verify_status")
        return VerifyOutcome(
            verification=VerificationResult(
                case_id=payload.case_id,
                status=status,
                authority=SourceAuthority.OFFICIAL_ATS,
                checked_at=datetime.now(UTC),
                http_status=200,
                final_url="https://boards.greenhouse.io/acme/jobs/1",
                reason="mock",
                apply_markers_found=status == JobStatus.OPEN,
                closed_markers_found=status == JobStatus.CLOSED,
            )
        )

    @activity.defn(name="evaluate_case")
    async def evaluate_case(payload: CaseWorkflowInput) -> EvaluateOutcome:
        recorder.hit("evaluate_case")
        if evaluate_contract_failure:
            raise ApplicationError(
                "induced contract failure", type="ContractFailure", non_retryable=True
            )
        record = run_evaluation(
            FakeEvaluationProvider(),
            case_id=payload.case_id,
            content_hash="deadbeef" * 8,
            url=payload.url,
            title="Engineer",
            text="Apply now",
            verification=None,
            bands=salary_bands(),
        )
        return EvaluateOutcome(record=record)

    @activity.defn(name="create_approval")
    async def create_approval(payload: CaseWorkflowInput) -> ApprovalRequestOutcome:
        recorder.hit("create_approval")
        return ApprovalRequestOutcome(
            approval_request_id=f"appr-{payload.case_id}-g1",
            expires_at=datetime.now(UTC) + approval_ttl,
        )

    @activity.defn(name="finalize_decision")
    async def finalize_decision(signal: DecisionSignal) -> None:
        recorder.hit("finalize_decision")

    @activity.defn(name="mark_closed")
    async def mark_closed(note: CaseStateNote) -> None:
        recorder.hit("mark_closed")

    @activity.defn(name="quarantine_case")
    async def quarantine_case(note: CaseStateNote) -> None:
        recorder.hit("quarantine_case")

    @activity.defn(name="expire_approval")
    async def expire_approval(note: CaseStateNote) -> None:
        recorder.hit("expire_approval")

    return [
        snapshot_source,
        normalize_case,
        verify_status,
        evaluate_case,
        create_approval,
        finalize_decision,
        mark_closed,
        quarantine_case,
        expire_approval,
    ]


async def test_happy_path_with_duplicate_signals() -> None:
    recorder = Recorder()
    queue = f"test-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=make_activities(recorder),
        ):
            handle = await start_case(env, queue)
            status = await wait_for_pending_approval(handle)
            assert status.approval_request_id is not None

            signal = DecisionSignal(
                approval_request_id=status.approval_request_id,
                decision=ApprovalDecision.APPROVE,
                reason="verified and worth applying",
                decision_version=1,
                actor="admin",
            )
            await handle.signal(JobIntelligenceWorkflow.submit_decision, signal)
            await handle.signal(JobIntelligenceWorkflow.submit_decision, signal)

            result = await handle.result()
            assert result.final_state == CaseState.APPROVED
            assert result.decision == ApprovalDecision.APPROVE
    assert recorder.calls["finalize_decision"] == 1
    assert recorder.calls["evaluate_case"] == 1


async def test_closed_source_short_circuits() -> None:
    recorder = Recorder()
    queue = f"test-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=make_activities(recorder, status=JobStatus.CLOSED),
        ):
            handle = await start_case(env, queue)
            result = await handle.result()
    assert result.final_state == CaseState.CLOSED_OR_STALE
    assert "evaluate_case" not in recorder.calls
    assert recorder.calls["mark_closed"] == 1


async def test_contract_failure_quarantines() -> None:
    recorder = Recorder()
    queue = f"test-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=make_activities(recorder, evaluate_contract_failure=True),
        ):
            handle = await start_case(env, queue)
            result = await handle.result()
    assert result.final_state == CaseState.QUARANTINED
    assert recorder.calls["quarantine_case"] == 1
    assert recorder.calls["evaluate_case"] == 1  # non-retryable: exactly one attempt


async def test_pre_evaluation_failure_lands_in_quarantine() -> None:
    """A non-retryable pipeline failure quarantines instead of failing the run."""

    recorder = Recorder()
    queue = f"test-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=make_activities(recorder, snapshot_policy_failure=True),
        ):
            handle = await start_case(env, queue)
            result = await handle.result()
    assert result.final_state == CaseState.QUARANTINED
    assert recorder.calls["quarantine_case"] == 1
    assert recorder.calls["snapshot_source"] == 1  # non-retryable: one attempt
    assert "verify_status" not in recorder.calls


async def test_approval_expiry_never_implicitly_approves() -> None:
    recorder = Recorder()
    queue = f"test-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=make_activities(recorder),
        ):
            handle = await start_case(env, queue)
            # No decision arrives; time-skipping advances past the 7-day expiry.
            result = await handle.result()
    # The case leaves PENDING_APPROVAL — an expired approval must not leave
    # a zombie behind a completed workflow, and never implies approval.
    assert result.final_state == CaseState.CLOSED_OR_STALE
    assert result.decision is None
    assert recorder.calls["expire_approval"] == 1
    assert "finalize_decision" not in recorder.calls

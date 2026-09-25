"""Workflow logic under Temporal's time-skipping test environment.

Activities are mocked here; the durable ordering, Signal dedup, approval
expiry, failure landing paths, and quarantine routing are the code under
test. Cross-layer behavior (real repo + real state machine) lives in
test_end_to_end_recovery.py.
"""

import asyncio
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
from countersign.domain.gates import MAX_GENERATIONS
from countersign.domain.ids import approval_request_id
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
    observed_text_chars: int = 1200,
    round_two_barrier: tuple[asyncio.Event, asyncio.Event] | None = None,
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
        if round_two_barrier is not None and payload.generation == 2:
            # Hold round 2 here, after it started and before its approval
            # exists, so a test can deliver Signals into that window.
            entered, release = round_two_barrier
            entered.set()
            await release.wait()
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
            ),
            observed_text_chars=observed_text_chars,
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
            approval_request_id=approval_request_id(payload.case_id, payload.generation),
            expires_at=datetime.now(UTC) + approval_ttl,
        )

    @activity.defn(name="finalize_decision")
    async def finalize_decision(signal: DecisionSignal) -> None:
        recorder.hit("finalize_decision")

    @activity.defn(name="mark_closed")
    async def mark_closed(note: CaseStateNote) -> None:
        recorder.hit("mark_closed")

    @activity.defn(name="mark_needs_evidence")
    async def mark_needs_evidence(note: CaseStateNote) -> None:
        recorder.hit("mark_needs_evidence")

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
        mark_needs_evidence,
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


async def test_empty_posting_body_stops_before_the_model() -> None:
    """A verified page with no posting text ends at NEEDS_MORE_EVIDENCE:
    no model call, no approval request, the reason recorded by an Activity."""

    recorder = Recorder()
    queue = f"test-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=make_activities(recorder, observed_text_chars=0),
        ):
            handle = await start_case(env, queue)
            result = await handle.result()
    assert result.final_state == CaseState.NEEDS_MORE_EVIDENCE
    assert result.decision is None
    assert recorder.calls["verify_status"] == 1
    assert recorder.calls["mark_needs_evidence"] == 1
    assert "evaluate_case" not in recorder.calls
    assert "create_approval" not in recorder.calls


def _signal(approval_id: str, decision: ApprovalDecision, reason: str) -> DecisionSignal:
    return DecisionSignal(
        approval_request_id=approval_id,
        decision=decision,
        reason=reason,
        decision_version=1,
        actor="admin",
    )


async def test_request_evidence_continues_into_the_next_round_and_ignores_late_signals() -> None:
    """ADR-014: REQUEST_EVIDENCE continues the same workflow id as round 2,
    which skips snapshot/normalize and re-verifies. Round 2 is held inside
    its first Activity, after the run started and before its approval
    exists, and Signals for round 1's request — a duplicate and a late
    APPROVE — are delivered into exactly that window; none decides round 2."""

    recorder = Recorder()
    queue = f"test-{uuid.uuid4()}"
    entered, release = asyncio.Event(), asyncio.Event()
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=make_activities(recorder, round_two_barrier=(entered, release)),
        ):
            handle = await start_case(env, queue)
            first = await wait_for_pending_approval(handle)
            assert first.approval_request_id is not None
            assert first.generation == 1

            await handle.signal(
                JobIntelligenceWorkflow.submit_decision,
                _signal(first.approval_request_id, ApprovalDecision.REQUEST_EVIDENCE, "note"),
            )
            await asyncio.wait_for(entered.wait(), timeout=30)
            held = await handle.query(JobIntelligenceWorkflow.status)
            assert held.generation == 2
            assert held.state == CaseState.NEEDS_MORE_EVIDENCE  # verifying, no approval yet
            assert held.decided is False

            for late in (
                _signal(first.approval_request_id, ApprovalDecision.REQUEST_EVIDENCE, "note"),
                _signal(first.approval_request_id, ApprovalDecision.APPROVE, "late approve"),
            ):
                await handle.signal(JobIntelligenceWorkflow.submit_decision, late)
            still_held = await handle.query(JobIntelligenceWorkflow.status)
            assert still_held.generation == 2 and still_held.decided is False
            release.set()

            second = await wait_for_pending_approval(handle, generation=2)
            assert second.approval_request_id is not None
            assert second.approval_request_id == approval_request_id(second.case_id, 2)
            assert second.decided is False
            assert second.approval_request_id != first.approval_request_id

            await handle.signal(
                JobIntelligenceWorkflow.submit_decision,
                _signal(second.approval_request_id, ApprovalDecision.APPROVE, "round two"),
            )
            result = await handle.result()

    assert result.final_state == CaseState.APPROVED
    assert result.decision == ApprovalDecision.APPROVE
    assert recorder.calls["snapshot_source"] == 1
    assert recorder.calls["normalize_case"] == 1
    assert recorder.calls["verify_status"] == 2
    assert recorder.calls["evaluate_case"] == 2
    assert recorder.calls["create_approval"] == 2
    assert recorder.calls["finalize_decision"] == 2


async def test_request_evidence_at_the_round_cap_ends_the_case() -> None:
    recorder = Recorder()
    queue = f"test-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[JobIntelligenceWorkflow],
            activities=make_activities(recorder),
        ):
            handle = await start_case(env, queue, generation=MAX_GENERATIONS)
            status = await wait_for_pending_approval(handle, generation=MAX_GENERATIONS)
            assert status.approval_request_id is not None
            await handle.signal(
                JobIntelligenceWorkflow.submit_decision,
                _signal(status.approval_request_id, ApprovalDecision.REQUEST_EVIDENCE, "more"),
            )
            result = await handle.result()

    assert result.final_state == CaseState.NEEDS_MORE_EVIDENCE
    assert result.decision == ApprovalDecision.REQUEST_EVIDENCE
    assert "snapshot_source" not in recorder.calls  # a later round re-observes via verify
    assert recorder.calls["verify_status"] == 1
    assert recorder.calls["finalize_decision"] == 1

"""JobIntelligenceWorkflow: the durable owner of one case's lifecycle.

Deterministic by construction: imports only `contracts` and `domain`
(enforced by import-linter and Temporal's sandbox); every side effect
lives in an Activity referenced by name. Every pre-approval failure has a
landing path: exhausted or non-retryable pipeline errors quarantine the
case visibly instead of leaving a zombie behind a failed workflow.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from countersign.contracts.enums import ApprovalDecision, CaseState, JobStatus
    from countersign.contracts.workflow_io import (
        ApprovalRequestOutcome,
        CaseStateNote,
        CaseWorkflowInput,
        CaseWorkflowResult,
        CaseWorkflowStatus,
        DecisionSignal,
        EvaluateOutcome,
        NormalizeOutcome,
        SnapshotOutcome,
        VerifyOutcome,
    )
    from countersign.domain.gates import MAX_GENERATIONS
    from countersign.domain.ids import approval_request_id
    from countersign.domain.states import DECISION_TO_STATE

_RETRY = RetryPolicy(
    maximum_attempts=4,
    non_retryable_error_types=[
        "FetchPolicyError",
        "ContractFailure",
        "MissingApproval",
        "DecisionConflict",
    ],
)

_ACTIVITY_TIMEOUT = timedelta(minutes=3)


def _error_type(error: ActivityError) -> str | None:
    cause = error.cause
    if isinstance(cause, ApplicationError):
        return cause.type
    return None


@workflow.defn(name="JobIntelligenceWorkflow")
class JobIntelligenceWorkflow:
    """snapshot -> normalize -> verify -> evaluate -> durable approval wait.

    A REQUEST_EVIDENCE decision continues the same workflow id as a new run
    with the next generation (ADR-014), which re-verifies and re-evaluates
    with the reviewer's reason as operator context, up to MAX_GENERATIONS.
    """

    def __init__(self) -> None:
        self._state: CaseState = CaseState.DISCOVERED
        self._case_id: str = ""
        self._generation: int = 1
        self._approval_request_id: str | None = None
        self._decision: DecisionSignal | None = None

    async def _quarantine(self, payload: CaseWorkflowInput, reason: str) -> CaseWorkflowResult:
        await workflow.execute_activity(
            "quarantine_case",
            CaseStateNote(case_id=payload.case_id, reason=reason),
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )
        self._state = CaseState.QUARANTINED
        return CaseWorkflowResult(
            case_id=payload.case_id,
            final_state=CaseState.QUARANTINED,
            decision=None,
        )

    @workflow.run
    async def run(self, payload: CaseWorkflowInput) -> CaseWorkflowResult:
        self._case_id = payload.case_id
        self._generation = payload.generation
        # Known before any Activity: this run's approval request id is
        # deterministic, so a Signal for another round is dropped from the
        # first event on, including a late one from the previous round.
        self._approval_request_id = approval_request_id(payload.case_id, payload.generation)

        try:
            if payload.generation == 1:
                await workflow.execute_activity(
                    "snapshot_source",
                    payload,
                    result_type=SnapshotOutcome,
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    retry_policy=_RETRY,
                )
                self._state = CaseState.SNAPSHOTTED

                await workflow.execute_activity(
                    "normalize_case",
                    payload,
                    result_type=NormalizeOutcome,
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    retry_policy=_RETRY,
                )
                self._state = CaseState.DEDUPLICATED
            else:
                # A later round re-observes through verification itself; the
                # intake snapshot and its normalization are not repeated.
                self._state = CaseState.NEEDS_MORE_EVIDENCE

            verify = await workflow.execute_activity(
                "verify_status",
                payload,
                result_type=VerifyOutcome,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_RETRY,
            )
            self._state = CaseState.OFFICIAL_SOURCE_VERIFIED
        except ActivityError as error:
            kind = _error_type(error) or "unknown"
            return await self._quarantine(payload, f"pipeline failure before evaluation: {kind}")

        if verify.verification.status in (JobStatus.CLOSED, JobStatus.EXPIRED):
            await workflow.execute_activity(
                "mark_closed",
                CaseStateNote(
                    case_id=payload.case_id,
                    reason=f"verification status {verify.verification.status.value}",
                ),
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_RETRY,
            )
            self._state = CaseState.CLOSED_OR_STALE
            return CaseWorkflowResult(
                case_id=payload.case_id,
                final_state=CaseState.CLOSED_OR_STALE,
                decision=None,
            )

        if verify.observed_text_chars == 0:
            # Nothing to evaluate: no visible text and no JSON-LD posting in
            # the verified observation. Stop here rather than pay for a model
            # call that can only answer "the posting is empty".
            await workflow.execute_activity(
                "mark_needs_evidence",
                CaseStateNote(
                    case_id=payload.case_id,
                    reason="empty posting body: no visible text and no JSON-LD "
                    "JobPosting description in the verified observation",
                ),
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_RETRY,
            )
            self._state = CaseState.NEEDS_MORE_EVIDENCE
            return CaseWorkflowResult(
                case_id=payload.case_id,
                final_state=CaseState.NEEDS_MORE_EVIDENCE,
                decision=None,
            )

        try:
            await workflow.execute_activity(
                "evaluate_case",
                payload,
                result_type=EvaluateOutcome,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_RETRY,
            )
        except ActivityError as error:
            kind = _error_type(error) or "unknown"
            reason = (
                "evaluation contract failure after repair budget"
                if kind == "ContractFailure"
                else f"evaluation failure: {kind}"
            )
            return await self._quarantine(payload, reason)
        self._state = CaseState.EVALUATED

        approval = await workflow.execute_activity(
            "create_approval",
            payload,
            result_type=ApprovalRequestOutcome,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )
        self._approval_request_id = approval.approval_request_id
        self._state = CaseState.PENDING_APPROVAL

        remaining = approval.expires_at - workflow.now()
        timeout = max(remaining, timedelta(seconds=1))
        try:
            await workflow.wait_condition(lambda: self._decision is not None, timeout=timeout)
        except TimeoutError:
            await workflow.execute_activity(
                "expire_approval",
                CaseStateNote(
                    case_id=payload.case_id,
                    reason="approval expired without a decision",
                    approval_request_id=approval.approval_request_id,
                ),
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_RETRY,
            )
            self._state = CaseState.CLOSED_OR_STALE
            return CaseWorkflowResult(
                case_id=payload.case_id,
                final_state=CaseState.CLOSED_OR_STALE,
                decision=None,
            )

        decision = self._decision
        assert decision is not None
        await workflow.execute_activity(
            "finalize_decision",
            decision,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )
        final_state = DECISION_TO_STATE[decision.decision]
        self._state = final_state
        if (
            decision.decision == ApprovalDecision.REQUEST_EVIDENCE
            and payload.generation < MAX_GENERATIONS
        ):
            # The reviewer's reason is now stored as their decision; the next
            # round reads it back as operator context. Same workflow id, new
            # run: verify -> evaluate -> approve again.
            workflow.continue_as_new(
                CaseWorkflowInput(
                    case_id=payload.case_id,
                    url=payload.url,
                    generation=payload.generation + 1,
                    contract_version=payload.contract_version,
                )
            )
        return CaseWorkflowResult(
            case_id=payload.case_id,
            final_state=final_state,
            decision=decision.decision,
        )

    @workflow.signal(name="submit_decision")
    def submit_decision(self, signal: DecisionSignal) -> None:
        """Record the first decision for this round's approval request.

        Duplicates are harmless. A Signal for another approval request —
        a late one from the previous round after continue-as-new, or any
        Signal before this run knows its own request id — is dropped.
        """

        if self._decision is not None:
            return
        if signal.approval_request_id != self._approval_request_id:
            return
        self._decision = signal

    @workflow.query(name="status")
    def status(self) -> CaseWorkflowStatus:
        """Expose progress without changing it."""

        return CaseWorkflowStatus(
            case_id=self._case_id,
            state=self._state,
            approval_request_id=self._approval_request_id,
            decided=self._decision is not None,
            generation=self._generation,
        )

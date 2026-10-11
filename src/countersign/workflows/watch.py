"""WatchWorkflow: one scheduled run of the watch (ticket 004, ADR-016).

Deterministic like the case workflow: imports only `contracts` and
`domain`, and every side effect is an Activity referenced by name. A run
rechecks the cases waiting for a decision under its model-call budget,
then reads the watched boards and starts a case workflow for each
admitted posting. The Temporal Schedule decides when a run starts.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError, WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from countersign.contracts.enums import JobStatus
    from countersign.contracts.workflow_io import (
        CaseWorkflowInput,
        DiscoverInput,
        DiscoveryOutcome,
        NewCases,
        RecheckOutcome,
        SourceClosedSignal,
        WatchPlan,
        WatchRunStart,
        WatchRunSummary,
    )
    from countersign.domain.ids import case_workflow_id
    from countersign.domain.watch import NEW_CASE_MODEL_CALLS, RECHECK_MODEL_CALLS

_RETRY = RetryPolicy(
    maximum_attempts=4, non_retryable_error_types=["FetchPolicyError", "PolicyError"]
)

_ACTIVITY_TIMEOUT = timedelta(minutes=3)


@workflow.defn(name="WatchWorkflow")
class WatchWorkflow:
    """plan -> rechecks within their budget -> discovery -> new case workflows -> trail."""

    @workflow.run
    async def run(self) -> WatchRunSummary:
        run_key = workflow.info().run_id[:8]
        started_at = workflow.now()
        plan = await workflow.execute_activity(
            "plan_watch_run",
            WatchRunStart(run_key=run_key, started_at=started_at),
            result_type=WatchPlan,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )

        # A recheck starts only while the rest of the budget covers its worst
        # case; what it did not use is credited back. The rest wait.
        budget = plan.watchlist.max_recheck_model_calls_per_run
        remaining = budget
        rechecked = without_model = closed = failures = 0
        for case in plan.rechecks:
            if remaining < RECHECK_MODEL_CALLS:
                break
            remaining -= RECHECK_MODEL_CALLS
            try:
                outcome = await workflow.execute_activity(
                    "recheck_case",
                    case,
                    result_type=RecheckOutcome,
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    retry_policy=_RETRY,
                )
            except ActivityError:
                # Its worst case stays charged; the case waits for the next run.
                failures += 1
                continue
            remaining += RECHECK_MODEL_CALLS - outcome.model_calls
            if not outcome.recorded:
                continue
            rechecked += 1
            without_model += outcome.model_calls == 0
            if outcome.status not in (JobStatus.CLOSED, JobStatus.EXPIRED):
                continue
            try:
                await workflow.get_external_workflow_handle(case_workflow_id(case.case_id)).signal(
                    "source_closed",
                    SourceClosedSignal(
                        approval_request_id=case.approval_request_id, reason=outcome.reason
                    ),
                )
            except ApplicationError:
                # The case's workflow has ended since: a decision came first.
                continue
            closed += 1

        discovery = await workflow.execute_activity(
            "discover_postings",
            DiscoverInput(watchlist=plan.watchlist, started_at=started_at),
            result_type=DiscoveryOutcome,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )
        if discovery.admitted:
            await workflow.execute_activity(
                "create_watch_cases",
                NewCases(run_key=run_key, postings=discovery.admitted),
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_RETRY,
            )
        for posting in discovery.admitted:
            try:
                await workflow.start_child_workflow(
                    "JobIntelligenceWorkflow",
                    CaseWorkflowInput(case_id=posting.case_id, url=posting.url),
                    id=case_workflow_id(posting.case_id),
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                )
            except WorkflowAlreadyStartedError:
                pass  # an earlier or overlapping run started it

        summary = WatchRunSummary(
            run_key=run_key,
            watchlist_version=plan.watchlist.version,
            watchlist_fingerprint=plan.fingerprint,
            boards=discovery.boards,
            seen=discovery.seen,
            filtered=discovery.filtered,
            without_location=discovery.without_location,
            known=discovery.known,
            outside_window=discovery.outside_window,
            reappeared=discovery.reappeared,
            created=len(discovery.admitted),
            rechecked=rechecked,
            rechecked_without_model=without_model,
            closed=closed,
            recheck_failures=failures,
            recheck_model_calls=budget - remaining,
            new_case_model_calls=NEW_CASE_MODEL_CALLS * len(discovery.admitted),
        )
        await workflow.execute_activity(
            "record_watch_run",
            summary,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )
        return summary

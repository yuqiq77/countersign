"""WatchWorkflow: one scheduled run of the watch (ticket 004, ADR-016, ADR-017).

Deterministic like the case workflow: imports only `contracts` and
`domain`, and every side effect is an Activity referenced by name. A run
rechecks the cases waiting for a decision under its model-call budget,
derives the search brief when the profile changed, searches, reads the
watched boards, screens both sources' candidates in turns, admits the
passed ones in turns and starts a case workflow for each. A search failure
never stops the boards, and a failed run still tries to write its trail.
The Temporal Schedule decides when a run starts.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from itertools import islice

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError, WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from countersign.contracts.enums import CandidateSource, JobStatus, ScreenDecision
    from countersign.contracts.workflow_io import (
        BriefInput,
        BriefOutcome,
        CaseWorkflowInput,
        DiscoverInput,
        DiscoveryOutcome,
        DiscoveryQueues,
        NewCases,
        NewPosting,
        QueueInput,
        RecheckOutcome,
        ScreenInput,
        ScreenOutcome,
        SearchInput,
        SearchOutcome,
        SourceClosedSignal,
        WatchPlan,
        WatchRunStart,
        WatchRunSummary,
    )
    from countersign.domain.discovery import take_turns
    from countersign.domain.ids import case_workflow_id
    from countersign.domain.watch import (
        BRIEF_MODEL_CALLS,
        NEW_CASE_MODEL_CALLS,
        RECHECK_MODEL_CALLS,
        SCREEN_MODEL_CALLS,
        SEARCH_MAX_REQUESTS,
    )

_RETRY = RetryPolicy(
    maximum_attempts=4, non_retryable_error_types=["FetchPolicyError", "PolicyError"]
)
# A retried search would search again under a fresh allowance; one attempt
# keeps the run's search cap exact, and a failure waits for the next run.
_ONCE = RetryPolicy(maximum_attempts=1)

_ACTIVITY_TIMEOUT = timedelta(minutes=3)
_SEARCH_TIMEOUT = timedelta(minutes=10)


def _cause(error: ActivityError) -> str:
    cause = error.cause
    return str(cause)[:300] if cause is not None else "activity failed"


@dataclass
class _Tally:
    """What the run did, for its trail; filled as the steps go."""

    first_source: CandidateSource
    discovery_remaining: int
    discovery: DiscoveryOutcome | None = None
    recheck_spent: int = 0
    rechecked: int = 0
    without_model: int = 0
    closed: int = 0
    recheck_failures: int = 0
    searches: int = 0
    search_found: int = 0
    search_rejected: int = 0
    leads: int = 0
    passed: int = 0
    dropped: int = 0
    failed_open: int = 0
    screen_failures: int = 0
    board_waiting: int = 0
    search_waiting: int = 0
    created: int = 0
    created_from_search: int = 0
    failures: list[str] = field(default_factory=lambda: [])


@workflow.defn(name="WatchWorkflow")
class WatchWorkflow:
    """plan -> rechecks -> brief and search -> boards -> screening -> admission -> trail."""

    @workflow.run
    async def run(self) -> WatchRunSummary:
        run_key = workflow.info().run_id[:8]
        started_at = workflow.now()
        # One moment for the whole run: board sightings, search sightings and
        # the queues that select "listed this run" all use it.
        self._started_at = started_at
        plan = await workflow.execute_activity(
            "plan_watch_run",
            WatchRunStart(run_key=run_key, started_at=started_at),
            result_type=WatchPlan,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )
        tally = _Tally(
            first_source=plan.first_source,
            discovery_remaining=plan.watchlist.max_discovery_model_calls_per_run,
        )
        step = "rechecks"
        try:
            await self._rechecks(plan, tally)
            step = "search"
            await self._search(plan, run_key, tally)
            step = "boards"
            tally.discovery = await workflow.execute_activity(
                "discover_postings",
                DiscoverInput(watchlist=plan.watchlist, started_at=started_at),
                result_type=DiscoveryOutcome,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_RETRY,
            )
            step = "screening"
            await self._screen(plan, run_key, tally)
            step = "admission"
            await self._admit(plan, run_key, tally)
        except BaseException:
            tally.failures.append(f"{step}: failed")
            raise
        finally:
            summary = self._summary(plan, run_key, tally)
            await workflow.execute_activity(
                "record_watch_run",
                summary,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_RETRY,
            )
        return summary

    async def _rechecks(self, plan: WatchPlan, tally: _Tally) -> None:
        # A recheck starts only while the rest of the budget covers its worst
        # case; what it did not use is credited back. The rest wait.
        remaining = plan.watchlist.max_recheck_model_calls_per_run
        for case in plan.rechecks:
            if remaining < RECHECK_MODEL_CALLS:
                break
            remaining -= RECHECK_MODEL_CALLS
            tally.recheck_spent += RECHECK_MODEL_CALLS
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
                tally.recheck_failures += 1
                continue
            remaining += RECHECK_MODEL_CALLS - outcome.model_calls
            tally.recheck_spent -= RECHECK_MODEL_CALLS - outcome.model_calls
            if not outcome.recorded:
                continue
            tally.rechecked += 1
            tally.without_model += outcome.model_calls == 0
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
            tally.closed += 1

    async def _search(self, plan: WatchPlan, run_key: str, tally: _Tally) -> None:
        """The brief when due, then the search; every failure is recorded, none raised."""

        watchlist = plan.watchlist
        if watchlist.max_searches_per_run == 0:
            return
        if plan.profile_fingerprint == "none":
            tally.failures.append("search: no operator profile")
            return
        brief = plan.brief
        if brief is None:
            if tally.discovery_remaining < BRIEF_MODEL_CALLS:
                tally.failures.append("brief: discovery budget too small")
                return
            tally.discovery_remaining -= BRIEF_MODEL_CALLS
            try:
                derived = await workflow.execute_activity(
                    "derive_brief",
                    BriefInput(run_key=run_key, profile_fingerprint=plan.profile_fingerprint),
                    result_type=BriefOutcome,
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    retry_policy=_RETRY,
                )
            except ActivityError as exc:
                tally.failures.append(f"brief: {_cause(exc)}")
                return
            tally.discovery_remaining += BRIEF_MODEL_CALLS - derived.model_calls
            if derived.brief is None:
                tally.failures.append(f"brief: {derived.failure}")
                return
            brief = derived.brief
        requests = min(SEARCH_MAX_REQUESTS, tally.discovery_remaining)
        if requests < 1:
            tally.failures.append("search: discovery budget used up")
            return
        tally.discovery_remaining -= requests
        try:
            outcome = await workflow.execute_activity(
                "search_postings",
                SearchInput(
                    run_key=run_key,
                    started_at=self._started_at,
                    brief=brief,
                    regions=list(watchlist.locations),
                    max_searches=watchlist.max_searches_per_run,
                    max_requests=requests,
                ),
                result_type=SearchOutcome,
                start_to_close_timeout=_SEARCH_TIMEOUT,
                retry_policy=_ONCE,
            )
        except ActivityError as exc:
            # Its requests and the whole search cap stay charged: the step
            # may have searched before it failed. What it searched is stored.
            tally.searches = watchlist.max_searches_per_run
            tally.failures.append(f"search: {_cause(exc)}")
            return
        tally.discovery_remaining += requests - outcome.model_calls
        tally.searches = outcome.searches
        tally.search_found = outcome.found
        tally.search_rejected = outcome.rejected
        tally.leads = outcome.leads
        if outcome.failure is not None:
            tally.failures.append(f"search: {outcome.failure}")

    async def _queues(self, plan: WatchPlan, run_key: str) -> DiscoveryQueues:
        return await workflow.execute_activity(
            "discovery_queues",
            QueueInput(
                run_key=run_key,
                started_at=self._started_at,
                profile_fingerprint=plan.profile_fingerprint,
                published_within_days=plan.watchlist.published_within_days,
            ),
            result_type=DiscoveryQueues,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )

    async def _screen(self, plan: WatchPlan, run_key: str, tally: _Tally) -> None:
        """Both sources in turns, each screen charged its worst case first."""

        queues = await self._queues(plan, run_key)
        for _, key in take_turns(
            plan.first_source, queues.board_to_screen, queues.search_to_screen
        ):
            if tally.discovery_remaining < SCREEN_MODEL_CALLS:
                break
            tally.discovery_remaining -= SCREEN_MODEL_CALLS
            try:
                outcome = await workflow.execute_activity(
                    "screen_candidate",
                    ScreenInput(
                        run_key=run_key, key=key, profile_fingerprint=plan.profile_fingerprint
                    ),
                    result_type=ScreenOutcome,
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    retry_policy=_RETRY,
                )
            except ActivityError:
                # Unscreened until the next run; its worst case stays charged.
                tally.screen_failures += 1
                continue
            tally.discovery_remaining += SCREEN_MODEL_CALLS - outcome.model_calls
            if outcome.failed_open:
                tally.failed_open += 1
            elif outcome.decision == ScreenDecision.PASS:
                tally.passed += 1
            else:
                tally.dropped += 1

    async def _admit(self, plan: WatchPlan, run_key: str, tally: _Tally) -> None:
        """Passed candidates in turns, up to the run's new cases."""

        queues = await self._queues(plan, run_key)
        chosen = list(
            islice(
                take_turns(plan.first_source, queues.board_passed, queues.search_passed),
                plan.watchlist.max_new_cases_per_run,
            )
        )
        postings = [
            NewPosting(case_id=c.case_id, url=c.url, candidate_key=c.key) for _, c in chosen
        ]
        from_search = sum(source == CandidateSource.SEARCH for source, _ in chosen)
        tally.created = len(postings)
        tally.created_from_search = from_search
        tally.board_waiting = (
            len(queues.board_to_screen) + len(queues.board_passed) - (len(chosen) - from_search)
        )
        tally.search_waiting = (
            len(queues.search_to_screen) + len(queues.search_passed) - from_search
        )
        if postings:
            await workflow.execute_activity(
                "create_watch_cases",
                NewCases(run_key=run_key, postings=postings),
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_RETRY,
            )
        for posting in postings:
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

    def _summary(self, plan: WatchPlan, run_key: str, tally: _Tally) -> WatchRunSummary:
        found = tally.discovery
        return WatchRunSummary(
            run_key=run_key,
            watchlist_version=plan.watchlist.version,
            watchlist_fingerprint=plan.fingerprint,
            boards=found.boards if found else [],
            seen=found.seen if found else 0,
            filtered=found.filtered if found else 0,
            without_location=found.without_location if found else 0,
            known=found.known if found else 0,
            outside_window=found.outside_window if found else 0,
            reappeared=found.reappeared if found else 0,
            created=tally.created,
            rechecked=tally.rechecked,
            rechecked_without_model=tally.without_model,
            closed=tally.closed,
            recheck_failures=tally.recheck_failures,
            recheck_model_calls=tally.recheck_spent,
            new_case_model_calls=NEW_CASE_MODEL_CALLS * tally.created,
            searches=tally.searches,
            search_found=tally.search_found,
            search_rejected=tally.search_rejected,
            leads=tally.leads,
            screened_pass=tally.passed,
            screened_drop=tally.dropped,
            screened_failed_open=tally.failed_open,
            screen_failures=tally.screen_failures,
            board_waiting=tally.board_waiting,
            search_waiting=tally.search_waiting,
            created_from_search=tally.created_from_search,
            discovery_model_calls=(
                plan.watchlist.max_discovery_model_calls_per_run - tally.discovery_remaining
            ),
            first_source=tally.first_source,
            failures=tally.failures,
        )

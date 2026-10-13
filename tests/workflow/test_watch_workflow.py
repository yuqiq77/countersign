"""The scheduled watch's run under Temporal's time-skipping environment (ADR-016).

Mocked Activities drive the run's own logic: the recheck budget, closures
sent to the case's workflow, one case workflow per admitted posting, and
the trail. The last test runs the real Activities and both workflows on
the database: a watched Ashby posting becomes a pending case, and the next
run rechecks it without the model.
"""

import json
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from support import client_rendered_posting, time_skipping_env, wait_for_pending_approval
from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

import countersign.activities.case_activities as case_activities
import countersign.activities.watch_activities as watch_activities
from countersign.activities import ALL_ACTIVITIES
from countersign.capabilities.http_fetch import FetchResult
from countersign.contracts.enums import JobStatus
from countersign.contracts.watchlist import WatchBoard, Watchlist, WatchSchedule
from countersign.contracts.workflow_io import (
    CaseWorkflowInput,
    DiscoverInput,
    DiscoveryOutcome,
    NewCases,
    NewPosting,
    RecheckInput,
    RecheckOutcome,
    SourceClosedSignal,
    WatchPlan,
    WatchRunStart,
    WatchRunSummary,
)
from countersign.domain.ids import approval_request_id, case_workflow_id
from countersign.domain.watch import NEW_CASE_MODEL_CALLS, RECHECK_MODEL_CALLS
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.policy.watchlist import WATCHLIST_PATH_VAR
from countersign.workflows.job_case import JobIntelligenceWorkflow
from countersign.workflows.watch import WatchWorkflow

ActivityFn = Callable[..., Coroutine[Any, Any, Any]]

WATCHLIST = Watchlist(
    version="test-1",
    schedule=WatchSchedule(cron=["0 7 * * 1"], time_zone="UTC"),
    boards=[WatchBoard(adapter="ashby", board="acme")],
    locations=["Toronto"],
    max_new_cases_per_run=5,
    max_recheck_model_calls_per_run=2 * RECHECK_MODEL_CALLS,
)


@workflow.defn(name="JobIntelligenceWorkflow", sandboxed=False)
class WaitingCase:
    """Stands in for a pending case's workflow: ends on its first closure."""

    def __init__(self) -> None:
        self._closure: SourceClosedSignal | None = None

    @workflow.run
    async def run(self, payload: CaseWorkflowInput) -> str:
        await workflow.wait_condition(lambda: self._closure is not None)
        assert self._closure is not None
        return self._closure.approval_request_id

    @workflow.signal(name="source_closed")
    def source_closed(self, signal: SourceClosedSignal) -> None:
        self._closure = signal


@workflow.defn(name="JobIntelligenceWorkflow", sandboxed=False)
class FinishedCase:
    """Stands in for a new case's workflow."""

    @workflow.run
    async def run(self, payload: CaseWorkflowInput) -> None:
        return None


class WatchTrail:
    """What the mocked Activities were handed."""

    def __init__(self) -> None:
        self.rechecked: list[str] = []
        self.created: list[NewCases] = []
        self.recorded: list[WatchRunSummary] = []


def make_watch_activities(
    trail: WatchTrail,
    *,
    rechecks: dict[str, RecheckOutcome | ApplicationError] | None = None,
    admitted: list[NewPosting] | None = None,
) -> list[ActivityFn]:
    """The plan lists `rechecks` oldest first, each answering its outcome or
    raising its error; discovery admits `admitted`."""

    outcomes = rechecks or {}
    postings = admitted or []

    @activity.defn(name="plan_watch_run")
    async def plan_watch_run(start: WatchRunStart) -> WatchPlan:
        return WatchPlan(
            watchlist=WATCHLIST,
            fingerprint="0badf00d",
            rechecks=[
                RecheckInput(
                    case_id=case_id,
                    url=f"https://jobs.ashbyhq.com/acme/{case_id}",
                    generation=1,
                    approval_request_id=approval_request_id(case_id, 1),
                    run_key=start.run_key,
                )
                for case_id in outcomes
            ],
        )

    @activity.defn(name="recheck_case")
    async def recheck_case(payload: RecheckInput) -> RecheckOutcome:
        trail.rechecked.append(payload.case_id)
        outcome = outcomes[payload.case_id]
        if isinstance(outcome, ApplicationError):
            raise outcome
        return outcome

    @activity.defn(name="discover_postings")
    async def discover_postings(payload: DiscoverInput) -> DiscoveryOutcome:
        return DiscoveryOutcome(
            boards=["ashby/acme: http 200"],
            seen=len(postings),
            filtered=0,
            without_location=0,
            known=0,
            outside_window=0,
            reappeared=0,
            admitted=postings,
        )

    @activity.defn(name="create_watch_cases")
    async def create_watch_cases(payload: NewCases) -> None:
        trail.created.append(payload)

    @activity.defn(name="record_watch_run")
    async def record_watch_run(summary: WatchRunSummary) -> None:
        trail.recorded.append(summary)

    return [plan_watch_run, recheck_case, discover_postings, create_watch_cases, record_watch_run]


def _open(model_calls: int) -> RecheckOutcome:
    return RecheckOutcome(recorded=True, model_calls=model_calls, status=JobStatus.OPEN)


def _posting(case_id: str) -> NewPosting:
    return NewPosting(case_id=case_id, url=f"https://jobs.ashbyhq.com/acme/{case_id}")


async def _run_watch(activities: list[ActivityFn]) -> WatchRunSummary:
    queue = f"watch-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[WatchWorkflow, FinishedCase],
            activities=activities,
        ):
            return await env.client.execute_workflow(
                WatchWorkflow.run, id=f"watch-{uuid.uuid4()}", task_queue=queue
            )


async def test_rechecks_stop_when_no_worst_case_fits_and_unused_calls_are_credited() -> None:
    """The first recheck's unused worst case comes back, so a third starts
    within a budget of two; after it none fits and the fourth waits."""

    trail = WatchTrail()
    rechecks: dict[str, RecheckOutcome | ApplicationError] = {
        "a": _open(0),
        "b": _open(RECHECK_MODEL_CALLS),
        "c": _open(RECHECK_MODEL_CALLS),
        "d": _open(0),
    }
    summary = await _run_watch(make_watch_activities(trail, rechecks=rechecks))
    assert trail.rechecked == ["a", "b", "c"]
    assert (summary.rechecked, summary.rechecked_without_model) == (3, 1)
    assert summary.recheck_model_calls == 2 * RECHECK_MODEL_CALLS


async def test_a_failed_recheck_keeps_its_worst_case_charged_and_the_run_goes_on() -> None:
    trail = WatchTrail()
    failure = ApplicationError("induced", type="FetchPolicyError", non_retryable=True)
    summary = await _run_watch(make_watch_activities(trail, rechecks={"a": failure, "b": _open(0)}))
    assert trail.rechecked == ["a", "b"]
    assert (summary.recheck_failures, summary.rechecked) == (1, 1)
    assert summary.recheck_model_calls == RECHECK_MODEL_CALLS


async def test_a_closed_recheck_signals_the_waiting_case_and_skips_a_missing_workflow() -> None:
    trail = WatchTrail()
    closed = RecheckOutcome(
        recorded=True, model_calls=0, status=JobStatus.CLOSED, reason="posting gone"
    )
    activities = make_watch_activities(trail, rechecks={"ended": closed, "waiting": closed})
    queue = f"watch-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[WatchWorkflow, WaitingCase],
            activities=activities,
        ):
            case = await env.client.start_workflow(
                WaitingCase.run,
                CaseWorkflowInput(case_id="waiting", url="https://jobs.ashbyhq.com/acme/waiting"),
                id=case_workflow_id("waiting"),
                task_queue=queue,
            )
            summary = await env.client.execute_workflow(
                WatchWorkflow.run, id=f"watch-{uuid.uuid4()}", task_queue=queue
            )
            assert await case.result() == approval_request_id("waiting", 1)
    assert (summary.rechecked, summary.closed) == (2, 1)


async def test_each_admitted_posting_starts_one_case_workflow_across_runs() -> None:
    """A second run admitting the same postings (as overlapping runs could)
    finds their workflow ids taken and starts nothing."""

    trail = WatchTrail()
    admitted = [_posting("one"), _posting("two")]
    queue = f"watch-{uuid.uuid4()}"
    first_id = f"watch-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[WatchWorkflow, FinishedCase],
            activities=make_watch_activities(trail, admitted=admitted),
        ):
            first = await env.client.execute_workflow(
                WatchWorkflow.run, id=first_id, task_queue=queue
            )
            started = [
                await env.client.get_workflow_handle(case_workflow_id(p.case_id)).describe()
                for p in admitted
            ]
            second = await env.client.execute_workflow(
                WatchWorkflow.run, id=f"watch-{uuid.uuid4()}", task_queue=queue
            )
            after = [
                await env.client.get_workflow_handle(case_workflow_id(p.case_id)).describe()
                for p in admitted
            ]
    assert trail.created == [
        NewCases(run_key=first.run_key, postings=admitted),
        NewCases(run_key=second.run_key, postings=admitted),
    ]
    assert [c.parent_id for c in started] == [first_id, first_id]
    assert [c.run_id for c in after] == [c.run_id for c in started]


async def test_the_run_records_the_summary_it_returns() -> None:
    trail = WatchTrail()
    summary = await _run_watch(
        make_watch_activities(trail, rechecks={"a": _open(0)}, admitted=[_posting("one")])
    )
    assert trail.recorded == [summary]
    assert (summary.watchlist_version, summary.watchlist_fingerprint) == ("test-1", "0badf00d")
    assert (summary.rechecked, summary.created) == (1, 1)
    assert summary.new_case_model_calls == NEW_CASE_MODEL_CALLS


JOB_ID = "4c1d7e2a-5b6f-4a8e-9d3c-2b1a0f9e8d7c"
POSTING_URL = f"https://jobs.ashbyhq.com/acme/{JOB_ID}"
BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/acme"


def _board() -> bytes:
    job = {
        "id": JOB_ID,
        "title": "Platform Engineer",
        "location": "Toronto, Canada",
        "publishedAt": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "jobUrl": POSTING_URL,
        "applyUrl": f"{POSTING_URL}/application",
        "isListed": True,
        "descriptionPlain": "Build durable systems on Temporal.",
    }
    return json.dumps({"apiVersion": "1", "jobs": [job]}).encode()


def _fetch(url: str, **_: object) -> FetchResult:
    assert url in (BOARD_URL, POSTING_URL), url
    board = url == BOARD_URL
    return FetchResult(
        requested_url=url,
        final_url=url,
        http_status=200,
        content_type="application/json" if board else "text/html",
        body=_board() if board else client_rendered_posting(),
        redirect_chain=[],
    )


@pytest.mark.usefixtures("db")
async def test_a_watched_posting_reaches_approval_and_the_next_run_rechecks_it_without_the_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(WATCHLIST.model_dump_json())
    monkeypatch.setenv(WATCHLIST_PATH_VAR, str(watchlist))
    provider = FakeEvaluationProvider()
    monkeypatch.setattr(case_activities, "fetch_url", _fetch)
    monkeypatch.setattr(watch_activities, "fetch_url", _fetch)
    monkeypatch.setattr(case_activities, "resolve_provider", lambda: provider)
    monkeypatch.setenv("COUNTERSIGN_MODEL_PROVIDER", "fake")
    key = watch_activities.posting_key(POSTING_URL)
    assert key is not None

    queue = f"e2e-{uuid.uuid4()}"
    async with await time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[WatchWorkflow, JobIntelligenceWorkflow],
            activities=list(ALL_ACTIVITIES),
            activity_executor=ThreadPoolExecutor(4),
        ):
            first = await env.client.execute_workflow(
                WatchWorkflow.run, id=f"watch-{uuid.uuid4()}", task_queue=queue
            )
            case = env.client.get_workflow_handle_for(
                JobIntelligenceWorkflow.run, case_workflow_id(watch_activities.case_id_for(key))
            )
            await wait_for_pending_approval(case)
            assert provider.calls == 1  # the evaluation; verification asked no model
            second = await env.client.execute_workflow(
                WatchWorkflow.run, id=f"watch-{uuid.uuid4()}", task_queue=queue
            )

    assert (first.seen, first.created) == (1, 1)
    assert (second.created, second.known) == (0, 1)
    assert (second.rechecked, second.rechecked_without_model, second.closed) == (1, 1, 0)
    assert provider.calls == 1

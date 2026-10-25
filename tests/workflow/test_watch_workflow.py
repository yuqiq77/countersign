"""The scheduled watch's run under Temporal's time-skipping environment
(ADR-016, ADR-017).

Mocked Activities drive the run's own logic: the recheck budget, closures
sent to the case's workflow, the turns of screening and admission under
the discovery budget, a failed brief or search leaving the boards alone,
one case workflow per admitted posting, and the trail, written even when a
step fails. The last test runs the real Activities and both workflows on
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
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

import countersign.activities.case_activities as case_activities
import countersign.activities.watch_activities as watch_activities
from countersign.activities import ALL_ACTIVITIES
from countersign.capabilities.http_fetch import FetchResult
from countersign.contracts.discovery import SearchBrief
from countersign.contracts.enums import CandidateSource, JobStatus, ScreenDecision
from countersign.contracts.watchlist import WatchBoard, Watchlist, WatchSchedule
from countersign.contracts.workflow_io import (
    BriefInput,
    BriefOutcome,
    CaseWorkflowInput,
    DiscoverInput,
    DiscoveryOutcome,
    DiscoveryQueues,
    NewCases,
    NewPosting,
    QueuedCandidate,
    QueueInput,
    RecheckInput,
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
from countersign.domain.ids import approval_request_id, case_workflow_id
from countersign.domain.watch import (
    NEW_CASE_MODEL_CALLS,
    RECHECK_MODEL_CALLS,
    SCREEN_MODEL_CALLS,
    SEARCH_MAX_REQUESTS,
)
from countersign.gateway.fake_provider import FAKE_BRIEF, FakeEvaluationProvider
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
    max_searches_per_run=0,
    max_discovery_model_calls_per_run=20,
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
        self.screened: list[str] = []
        self.briefs: list[BriefInput] = []
        self.searches: list[SearchInput] = []


def make_watch_activities(
    trail: WatchTrail,
    *,
    rechecks: dict[str, RecheckOutcome | ApplicationError] | None = None,
    admitted: list[NewPosting] | None = None,
    watchlist: Watchlist = WATCHLIST,
    first_source: CandidateSource = CandidateSource.BOARD,
    profile_fingerprint: str = "none",
    brief: SearchBrief | None = None,
    derived: BriefOutcome | ApplicationError | None = None,
    search: SearchOutcome | ApplicationError | None = None,
    to_screen: tuple[list[str], list[str]] = ([], []),
    passed_search: list[NewPosting] | None = None,
    verdicts: dict[str, ScreenOutcome] | None = None,
    boards_error: ApplicationError | None = None,
) -> list[ActivityFn]:
    """The plan lists `rechecks` oldest first, each answering its outcome or
    raising its error. `admitted` are board candidates that already passed
    and `passed_search` search ones; `to_screen` holds the board and search
    keys to screen, each passing unless `verdicts` says otherwise."""

    outcomes = rechecks or {}
    board_passed = [_queued(p) for p in admitted or []]
    search_passed = [_queued(p) for p in passed_search or []]
    answers = verdicts or {}

    @activity.defn(name="plan_watch_run")
    async def plan_watch_run(start: WatchRunStart) -> WatchPlan:
        return WatchPlan(
            watchlist=watchlist,
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
            profile_fingerprint=profile_fingerprint,
            brief=brief,
            first_source=first_source,
        )

    @activity.defn(name="derive_brief")
    async def derive_brief(payload: BriefInput) -> BriefOutcome:
        trail.briefs.append(payload)
        if isinstance(derived, ApplicationError):
            raise derived
        return derived or BriefOutcome(brief=FAKE_BRIEF, failure=None, model_calls=1)

    @activity.defn(name="search_postings")
    async def search_postings(payload: SearchInput) -> SearchOutcome:
        trail.searches.append(payload)
        if isinstance(search, ApplicationError):
            raise search
        return search or SearchOutcome(
            found=0, new=0, leads=0, rejected=0, searches=0, model_calls=1, failure=None
        )

    @activity.defn(name="discovery_queues")
    async def discovery_queues(payload: QueueInput) -> DiscoveryQueues:
        # Before screening: what is to screen. After: those keys have passed.
        if not trail.screened and (to_screen[0] or to_screen[1]):
            return DiscoveryQueues(
                board_to_screen=to_screen[0],
                search_to_screen=to_screen[1],
                board_passed=board_passed,
                search_passed=search_passed,
            )
        unscreened_board = [k for k in to_screen[0] if k not in trail.screened]
        unscreened_search = [k for k in to_screen[1] if k not in trail.screened]
        passed = [
            k for k in trail.screened if answers.get(k, _PASS).decision == ScreenDecision.PASS
        ]
        return DiscoveryQueues(
            board_to_screen=unscreened_board,
            search_to_screen=unscreened_search,
            board_passed=board_passed + [_queued_key(k) for k in passed if k in to_screen[0]],
            search_passed=search_passed + [_queued_key(k) for k in passed if k in to_screen[1]],
        )

    @activity.defn(name="screen_candidate")
    async def screen_candidate(payload: ScreenInput) -> ScreenOutcome:
        trail.screened.append(payload.key)
        return answers.get(payload.key, _PASS)

    @activity.defn(name="recheck_case")
    async def recheck_case(payload: RecheckInput) -> RecheckOutcome:
        trail.rechecked.append(payload.case_id)
        outcome = outcomes[payload.case_id]
        if isinstance(outcome, ApplicationError):
            raise outcome
        return outcome

    @activity.defn(name="discover_postings")
    async def discover_postings(payload: DiscoverInput) -> DiscoveryOutcome:
        if boards_error is not None:
            raise boards_error
        return DiscoveryOutcome(
            boards=["ashby/acme: http 200"],
            seen=len(board_passed) + len(to_screen[0]),
            filtered=0,
            without_location=0,
            known=0,
            outside_window=0,
            reappeared=0,
            recorded=len(to_screen[0]),
        )

    @activity.defn(name="create_watch_cases")
    async def create_watch_cases(payload: NewCases) -> None:
        trail.created.append(payload)

    @activity.defn(name="record_watch_run")
    async def record_watch_run(summary: WatchRunSummary) -> None:
        trail.recorded.append(summary)

    return [
        plan_watch_run,
        recheck_case,
        derive_brief,
        search_postings,
        discover_postings,
        discovery_queues,
        screen_candidate,
        create_watch_cases,
        record_watch_run,
    ]


_PASS = ScreenOutcome(decision=ScreenDecision.PASS, failed_open=False, model_calls=1)


def _open(model_calls: int) -> RecheckOutcome:
    return RecheckOutcome(recorded=True, model_calls=model_calls, status=JobStatus.OPEN)


def _posting(case_id: str) -> NewPosting:
    return NewPosting(
        case_id=case_id,
        url=f"https://jobs.ashbyhq.com/acme/{case_id}",
        candidate_key=f"ashby:acme:{case_id}",
    )


def _queued(posting: NewPosting) -> QueuedCandidate:
    assert posting.candidate_key is not None
    return QueuedCandidate(key=posting.candidate_key, url=posting.url, case_id=posting.case_id)


def _queued_key(key: str) -> QueuedCandidate:
    return _queued(_posting(key))


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


def _with(**changes: object) -> Watchlist:
    return WATCHLIST.model_copy(update=changes)


async def test_screening_takes_turns_within_the_discovery_budget() -> None:
    """Board and search alternate, the board first this run; when no worst
    case fits any more the rest wait, and admission alternates too."""

    trail = WatchTrail()
    costly = ScreenOutcome(
        decision=ScreenDecision.PASS, failed_open=False, model_calls=SCREEN_MODEL_CALLS
    )
    summary = await _run_watch(
        make_watch_activities(
            trail,
            watchlist=_with(max_discovery_model_calls_per_run=3 * SCREEN_MODEL_CALLS),
            to_screen=(["b1", "b2", "b3"], ["s1"]),
            verdicts={key: costly for key in ("b1", "b2", "b3", "s1")},
        )
    )
    assert trail.screened == ["b1", "s1", "b2"]
    assert summary.discovery_model_calls == 3 * SCREEN_MODEL_CALLS
    assert [p.case_id for p in trail.created[0].postings] == ["b1", "s1", "b2"]
    assert (summary.screened_pass, summary.created, summary.created_from_search) == (3, 3, 1)
    assert (summary.board_waiting, summary.search_waiting) == (1, 0)


async def test_admission_alternates_from_the_source_whose_turn_it_is() -> None:
    trail = WatchTrail()
    summary = await _run_watch(
        make_watch_activities(
            trail,
            watchlist=_with(max_new_cases_per_run=3),
            first_source=CandidateSource.SEARCH,
            admitted=[_posting("B1"), _posting("B2")],
            passed_search=[_posting("S1"), _posting("S2"), _posting("S3")],
        )
    )
    assert [p.case_id for p in trail.created[0].postings] == ["S1", "B1", "S2"]
    assert (summary.created, summary.created_from_search) == (3, 2)
    assert (summary.board_waiting, summary.search_waiting) == (1, 1)
    assert summary.first_source == CandidateSource.SEARCH


async def test_a_failed_search_leaves_the_boards_and_the_trail() -> None:
    trail = WatchTrail()
    failure = ApplicationError("search backend down", non_retryable=True)
    summary = await _run_watch(
        make_watch_activities(
            trail,
            watchlist=_with(max_searches_per_run=4),
            profile_fingerprint="abcd1234",
            brief=FAKE_BRIEF,
            search=failure,
            admitted=[_posting("one")],
        )
    )
    assert [(s.max_searches, s.max_requests) for s in trail.searches] == [(4, SEARCH_MAX_REQUESTS)]
    assert summary.failures == ["search: search backend down"]
    # Its outcome was lost, so the whole search cap stays charged.
    assert summary.searches == 4
    assert summary.created == 1
    assert trail.recorded == [summary]


async def test_a_failed_brief_skips_the_search_and_the_run_goes_on() -> None:
    trail = WatchTrail()
    summary = await _run_watch(
        make_watch_activities(
            trail,
            watchlist=_with(max_searches_per_run=4),
            profile_fingerprint="abcd1234",
            derived=BriefOutcome(brief=None, failure="refused", model_calls=1),
            admitted=[_posting("one")],
        )
    )
    assert [b.profile_fingerprint for b in trail.briefs] == ["abcd1234"]
    assert trail.searches == []
    assert summary.failures == ["brief: refused"]
    assert summary.created == 1


async def test_without_a_profile_there_is_no_search() -> None:
    trail = WatchTrail()
    summary = await _run_watch(
        make_watch_activities(trail, watchlist=_with(max_searches_per_run=4))
    )
    assert (trail.briefs, trail.searches) == ([], [])
    assert summary.failures == ["search: no operator profile"]


async def test_a_failed_open_screen_is_counted_apart_and_still_admitted() -> None:
    trail = WatchTrail()
    failed_open = ScreenOutcome(
        decision=ScreenDecision.PASS, failed_open=True, model_calls=SCREEN_MODEL_CALLS
    )
    summary = await _run_watch(
        make_watch_activities(trail, to_screen=(["b1"], []), verdicts={"b1": failed_open})
    )
    assert (summary.screened_pass, summary.screened_failed_open, summary.created) == (0, 1, 1)


async def test_a_run_that_fails_still_writes_its_trail() -> None:
    trail = WatchTrail()
    activities = make_watch_activities(
        trail, boards_error=ApplicationError("board api down", non_retryable=True)
    )
    with pytest.raises(WorkflowFailureError):
        await _run_watch(activities)
    assert [r.failures for r in trail.recorded] == [["boards: failed"]]


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

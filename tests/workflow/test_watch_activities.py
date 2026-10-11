"""Ticket 004 through the real Activities: rechecks, discovery and case creation.

Real database and Activities, mocked network, fake model. A recheck decides
an unchanged page again without the model and runs the reader when a
claim no longer binds; it writes nothing once the case stopped waiting; an
Ashby posting absent from its healthy board closes on the second
consecutive recheck. Discovery filters, windows, dedupes and budgets the
listed postings, and records a closed case's posting listed again once.
"""

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import countersign.activities.case_activities as case_activities
import countersign.activities.watch_activities as watch_activities
from countersign.activities.case_activities import BOARD_ABSENCE, BOARD_ABSENCE_AGAIN
from countersign.activities.watch_activities import case_id_for
from countersign.capabilities.http_fetch import FetchResult
from countersign.contracts.enums import (
    ActorType,
    ApplyEntryKind,
    ApprovalDecision,
    CaseState,
    JobStatus,
    ReaderAction,
)
from countersign.contracts.page_reading import ApplyEntry, PageReading, PageStep
from countersign.contracts.watchlist import WatchBoard, Watchlist, WatchSchedule
from countersign.contracts.workflow_io import (
    CaseStateNote,
    CaseWorkflowInput,
    DecisionSignal,
    DiscoverInput,
    DiscoveryOutcome,
    NewCases,
    NewPosting,
    RecheckInput,
    RecheckOutcome,
)
from countersign.domain import ids
from countersign.domain.watch import RECHECK_MODEL_CALLS
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.persistence import repo
from countersign.persistence.db import session_scope
from countersign.persistence.models import AuditEventRow

NOW = datetime.now(UTC)
DAY = timedelta(days=1)
FUTURE = "2099-12-31T00:00:00Z"
GREENHOUSE_URL = "https://job-boards.greenhouse.io/globex/jobs/4001"
APPLY_URL = f"{GREENHOUSE_URL}#app"
JOB_ID = "4c1d7e2a-5b6f-4a8e-9d3c-2b1a0f9e8d7c"
ASHBY_URL = f"https://jobs.ashbyhq.com/acme/{JOB_ID}"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/acme"
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/globex/jobs"
SHELL = b'<html><head><title>Jobs</title></head><body><div id="root"></div></body></html>'
WATCHLIST = Watchlist(
    version="test-1",
    schedule=WatchSchedule(cron=["0 7 * * 1,3,5"], time_zone="UTC"),
    boards=[
        WatchBoard(adapter="ashby", board="acme"),
        WatchBoard(adapter="greenhouse", board="globex"),
    ],
    locations=["Remote - Canada", "Toronto", "Canada"],
    exclude_titles=["Intern"],
    published_within_days=14,
    max_new_cases_per_run=3,
    max_recheck_model_calls_per_run=30,
)


def _network(routes: dict[str, tuple[int, bytes]]) -> Callable[..., FetchResult]:
    def fetch(url: str, **_: object) -> FetchResult:
        status, body = routes[url]
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=status,
            content_type="text/html",
            body=body,
            redirect_chain=[],
        )

    return fetch


def _greenhouse_page(valid_through: str, apply_href: str = APPLY_URL) -> bytes:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "validThrough": valid_through,
    }
    return (
        f'<html><head><title>Platform Engineer</title><script type="application/ld+json">'
        f"{json.dumps(posting)}</script></head><body><h1>Platform Engineer</h1>"
        f'<p>Build durable systems.</p><a href="{apply_href}">Apply for this job</a></body></html>'
    ).encode()


def _ashby_page() -> bytes:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Platform Engineer",
        "description": "<p>Build durable systems on Temporal.</p>",
        "validThrough": FUTURE,
    }
    return (
        f'<html><head><script type="application/ld+json">{json.dumps(posting)}</script></head>'
        '<body><div id="root"></div></body></html>'
    ).encode()


def _ashby_board(*job_ids: str) -> tuple[int, bytes]:
    jobs = [
        {
            "jobUrl": f"https://jobs.ashbyhq.com/acme/{job_id}",
            "applyUrl": f"https://jobs.ashbyhq.com/acme/{job_id}/application",
        }
        for job_id in job_ids
    ]
    return 200, json.dumps({"jobs": jobs}).encode()


def _boards(
    ashby: Sequence[tuple[str, str, str | None, datetime]],
    greenhouse: Sequence[tuple[int, str, str | None, datetime]],
) -> Callable[..., FetchResult]:
    """Both watched boards' listings, from (id, title, location, published) per posting."""

    ashby_jobs = [
        {"id": job_id, "title": title, "location": location, "publishedAt": at.isoformat()}
        for job_id, title, location, at in ashby
    ]
    greenhouse_jobs = [
        {"id": job_id, "title": title, "location": {"name": location}, "updated_at": at.isoformat()}
        for job_id, title, location, at in greenhouse
    ]
    return _network(
        {
            ASHBY_API: (200, json.dumps({"jobs": ashby_jobs}).encode()),
            GREENHOUSE_API: (200, json.dumps({"jobs": greenhouse_jobs}).encode()),
        }
    )


def _reads_apply() -> FakeEvaluationProvider:
    reading = PageReading(
        body_observation=0,
        apply_entry=ApplyEntry(
            kind=ApplyEntryKind.APPLY_CONTROL,
            observation=0,
            cited_text="Apply for this job",
            href=APPLY_URL,
        ),
    )
    return FakeEvaluationProvider(
        page_steps=[PageStep(action=ReaderAction.DONE, why="scripted", reading=reading)]
    )


def _no_model() -> FakeEvaluationProvider:
    raise AssertionError("the recheck resolved a model provider")


def _render_unavailable(url: str, body: bytes, timeout: float) -> FetchResult | None:
    return None


def _pending(url: str) -> str:
    """A case waiting on its first approval request, through the real Activities."""

    payload = CaseWorkflowInput(case_id=str(uuid.uuid4()), url=url)
    case_activities.snapshot_source(payload)
    case_activities.normalize_case(payload)
    case_activities.verify_status(payload)
    case_activities.evaluate_case(payload)
    case_activities.create_approval(payload)
    return payload.case_id


def _greenhouse_case(monkeypatch: pytest.MonkeyPatch, valid_through: str) -> str:
    page = _greenhouse_page(valid_through)
    monkeypatch.setattr(case_activities, "fetch_url", _network({GREENHOUSE_URL: (200, page)}))
    monkeypatch.setattr(case_activities, "resolve_provider", _reads_apply)
    return _pending(GREENHOUSE_URL)


def _ashby_case(monkeypatch: pytest.MonkeyPatch) -> str:
    routes = {ASHBY_URL: (200, _ashby_page()), ASHBY_API: _ashby_board(JOB_ID)}
    monkeypatch.setattr(case_activities, "fetch_url", _network(routes))
    monkeypatch.setattr(case_activities, "resolve_provider", FakeEvaluationProvider)
    return _pending(ASHBY_URL)


def _recheck(case_id: str, url: str, run_key: str = "r1") -> RecheckInput:
    return RecheckInput(
        case_id=case_id,
        url=url,
        generation=1,
        approval_request_id=ids.approval_request_id(case_id, 1),
        run_key=run_key,
    )


@pytest.mark.usefixtures("db")
def test_an_unchanged_page_whose_deadline_passed_is_expired_without_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_through = (NOW + DAY).isoformat(timespec="seconds")
    case_id = _greenhouse_case(monkeypatch, valid_through)
    monkeypatch.setattr(repo, "utcnow", lambda: NOW + 2 * DAY)
    monkeypatch.setattr(case_activities, "resolve_provider", _no_model)

    assert case_activities.recheck_case(_recheck(case_id, GREENHOUSE_URL)) == RecheckOutcome(
        recorded=True,
        model_calls=0,
        status=JobStatus.EXPIRED,
        reason=f"JSON-LD validThrough {valid_through} has passed",
    )


@pytest.mark.usefixtures("db")
def test_a_changed_apply_target_under_unchanged_text_runs_the_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = _greenhouse_case(monkeypatch, FUTURE)
    moved = _greenhouse_page(FUTURE, apply_href=f"{GREENHOUSE_URL}/apply-elsewhere")
    monkeypatch.setattr(case_activities, "fetch_url", _network({GREENHOUSE_URL: (200, moved)}))
    reader = FakeEvaluationProvider()
    monkeypatch.setattr(case_activities, "resolve_provider", lambda: reader)

    outcome = case_activities.recheck_case(_recheck(case_id, GREENHOUSE_URL))
    assert (reader.calls, outcome.model_calls) == (1, 1)


@pytest.mark.usefixtures("db")
def test_a_retried_recheck_whose_reading_asked_a_model_is_charged_its_worst_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reading whose model calls all failed validation is stored without
    usage; the retry of its Activity must not credit those calls back."""

    case_id = _greenhouse_case(monkeypatch, FUTURE)
    moved = _greenhouse_page(FUTURE, apply_href=f"{GREENHOUSE_URL}/apply-elsewhere")
    monkeypatch.setattr(case_activities, "fetch_url", _network({GREENHOUSE_URL: (200, moved)}))
    reader = FakeEvaluationProvider(fail_attempts=99)
    monkeypatch.setattr(case_activities, "resolve_provider", lambda: reader)

    first = case_activities.recheck_case(_recheck(case_id, GREENHOUSE_URL))
    retried = case_activities.recheck_case(_recheck(case_id, GREENHOUSE_URL))
    assert (reader.calls, first.model_calls) == (2, 2)
    assert retried.model_calls == RECHECK_MODEL_CALLS


@pytest.mark.usefixtures("db")
def test_a_recheck_after_the_decision_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    case_id = _greenhouse_case(monkeypatch, FUTURE)
    case_activities.finalize_decision(
        DecisionSignal(
            approval_request_id=ids.approval_request_id(case_id, 1),
            decision=ApprovalDecision.REJECT,
            reason="not a fit",
            decision_version=1,
            actor="admin",
        )
    )

    outcome = case_activities.recheck_case(_recheck(case_id, GREENHOUSE_URL))
    assert outcome == RecheckOutcome(recorded=False, model_calls=0)
    with session_scope() as session:
        assert repo.get_verification(session, ids.recheck_verification_id(case_id, 1, "r1")) is None
        assert repo.get_page_reading(session, ids.recheck_reading_id(case_id, 1, "r1")) is None


ABSENT = _ashby_board("other")
UNAVAILABLE = (503, b"")


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize(
    ("boards", "expected"),
    [
        (
            [ABSENT, ABSENT],
            [(JobStatus.UNVERIFIED, BOARD_ABSENCE), (JobStatus.CLOSED, BOARD_ABSENCE_AGAIN)],
        ),
        (
            [ABSENT, UNAVAILABLE, ABSENT],
            [
                (JobStatus.UNVERIFIED, BOARD_ABSENCE),
                (JobStatus.UNVERIFIED, "no apply entry found; never inferred from http 200 alone"),
                (JobStatus.UNVERIFIED, BOARD_ABSENCE),
            ],
        ),
    ],
    ids=["absent-twice", "unavailable-between"],
)
def test_absence_from_a_healthy_board_closes_only_on_consecutive_rechecks(
    monkeypatch: pytest.MonkeyPatch,
    boards: list[tuple[int, bytes]],
    expected: list[tuple[JobStatus, str]],
) -> None:
    """The posting taken down: off the board, its page a shell without a JobPosting."""

    case_id = _ashby_case(monkeypatch)
    monkeypatch.setattr(case_activities, "render_url", _render_unavailable)
    outcomes: list[tuple[JobStatus | None, str]] = []
    for run, board in enumerate(boards, 1):
        routes = {ASHBY_URL: (200, SHELL), ASHBY_API: board}
        monkeypatch.setattr(case_activities, "fetch_url", _network(routes))
        outcome = case_activities.recheck_case(_recheck(case_id, ASHBY_URL, f"r{run}"))
        outcomes.append((outcome.status, outcome.reason))
    assert outcomes == expected


@pytest.mark.usefixtures("db")
def test_a_posting_the_api_still_publishes_is_rechecked_open_without_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = _ashby_case(monkeypatch)
    monkeypatch.setattr(case_activities, "resolve_provider", _no_model)

    first = case_activities.recheck_case(_recheck(case_id, ASHBY_URL))
    assert (first.status, first.model_calls) == (JobStatus.OPEN, 0)
    monkeypatch.setattr(case_activities, "fetch_url", _network({}))
    assert case_activities.recheck_case(_recheck(case_id, ASHBY_URL)) == first


@pytest.mark.usefixtures("db")
def test_discovery_admits_the_newest_unknown_postings_that_pass_the_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listings = _boards(
        [
            ("a1", "Platform Engineer", "Toronto", NOW - DAY),
            ("a2", "Engineering Intern", "Toronto", NOW - DAY),
            ("a3", "Account Executive", "New York", NOW - DAY),
            ("a4", "Data Engineer", None, NOW - 2 * DAY),
            ("a5", "Site Engineer", "Toronto", NOW - 30 * DAY),
        ],
        [
            (101, "Backend Engineer", "Remote - Canada", NOW - timedelta(hours=3)),
            (102, "Frontend Engineer", "Toronto", NOW - timedelta(hours=1)),
            (103, "Site Reliability Engineer", "Canada", NOW - 5 * DAY),
        ],
    )
    monkeypatch.setattr(watch_activities, "fetch_url", listings)
    with session_scope() as session:
        repo.create_case(
            session, case_id=str(uuid.uuid4()), url="https://boards.greenhouse.io/globex/jobs/102"
        )

    outcome = watch_activities.discover_postings(DiscoverInput(watchlist=WATCHLIST, started_at=NOW))
    assert outcome == DiscoveryOutcome(
        boards=["ashby/acme: http 200, 5 postings", "greenhouse/globex: http 200, 3 postings"],
        seen=8,
        filtered=2,
        without_location=1,
        known=1,
        outside_window=1,
        reappeared=0,
        admitted=[
            NewPosting(
                case_id=case_id_for("greenhouse:101"),
                url="https://job-boards.greenhouse.io/globex/jobs/101",
            ),
            NewPosting(
                case_id=case_id_for("ashby:acme:a1"), url="https://jobs.ashbyhq.com/acme/a1"
            ),
            NewPosting(
                case_id=case_id_for("ashby:acme:a4"), url="https://jobs.ashbyhq.com/acme/a4"
            ),
        ],
    )


@pytest.mark.usefixtures("db")
def test_a_closed_posting_listed_again_after_its_closure_is_recorded_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both cases closed on a 404. One posting is listed as published after
    the closure, then edited; the other was listed all along. The run starts
    a month on, so the return is older than the window."""

    returned, stayed = (f"https://job-boards.greenhouse.io/globex/jobs/{n}" for n in (201, 202))
    gone = (404, b"<html><body><h1>Job not found</h1></body></html>")
    monkeypatch.setattr(case_activities, "fetch_url", _network({returned: gone, stayed: gone}))
    monkeypatch.setattr(case_activities, "resolve_provider", FakeEvaluationProvider)
    case_ids: list[str] = []
    for url in (returned, stayed):
        payload = CaseWorkflowInput(case_id=str(uuid.uuid4()), url=url)
        case_activities.snapshot_source(payload)
        case_activities.normalize_case(payload)
        case_activities.verify_status(payload)
        case_activities.mark_closed(CaseStateNote(case_id=payload.case_id, reason="closed"))
        case_ids.append(payload.case_id)

    reappeared: list[int] = []
    for published in (NOW + 10 * DAY, NOW + 29 * DAY):
        listed = [(201, "Engineer", None, published), (202, "Engineer", None, NOW - DAY)]
        monkeypatch.setattr(watch_activities, "fetch_url", _boards([], listed))
        outcome = watch_activities.discover_postings(
            DiscoverInput(watchlist=WATCHLIST, started_at=NOW + 30 * DAY)
        )
        assert (outcome.known, outcome.admitted) == (2, [])
        reappeared.append(outcome.reappeared)

    assert reappeared == [1, 0]
    with session_scope() as session:
        events = session.scalars(
            select(AuditEventRow.resource_id).where(AuditEventRow.action == "case.reappeared")
        )
        assert list(events) == case_ids[:1]


@pytest.mark.usefixtures("db")
def test_watch_cases_are_created_once_by_the_watch() -> None:
    postings = [
        NewPosting(
            case_id=case_id_for(f"greenhouse:{n}"),
            url=f"https://job-boards.greenhouse.io/globex/jobs/{n}",
        )
        for n in (301, 302)
    ]
    for _ in range(2):
        watch_activities.create_watch_cases(NewCases(run_key="run-1", postings=postings))

    with session_scope() as session:
        assert sorted((row.id, row.state) for row in repo.list_cases(session)) == sorted(
            (p.case_id, CaseState.DISCOVERED.value) for p in postings
        )
        events = session.scalars(
            select(AuditEventRow).where(AuditEventRow.action == "case.created")
        )
        assert sorted((e.resource_id, e.actor_type, e.actor_id, e.request_id) for e in events) == (
            sorted((p.case_id, ActorType.SYSTEM.value, "watch", "run-1") for p in postings)
        )

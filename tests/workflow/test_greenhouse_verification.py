"""Ticket 006 through the real Activities: the Greenhouse Job Board API in verification.

Real database and Activities, mocked network, fake model. A job the API
serves is verified from the API without the model, and its description
stands in for a careers page that embeds it; an official page still wins
when it shows the job gone or expired; anything else leaves the reading to
the reader, with the API attempt recorded on it.
"""

import json
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from support import start_case, time_skipping_env, wait_for_pending_approval
from temporalio.worker import Worker

import countersign.activities.case_activities as case_activities
from countersign.activities import ALL_ACTIVITIES
from countersign.agents.page_reader import READER_PROMPT_VERSION
from countersign.capabilities.http_fetch import FetchResult
from countersign.contracts.enums import (
    ApplyEntryKind,
    EvidenceKind,
    JobStatus,
    ObservationHow,
    ReaderAction,
    ReaderStop,
    SourceAuthority,
)
from countersign.contracts.evaluation import EvaluationRecord
from countersign.contracts.page_reading import ApplyEntry
from countersign.contracts.workflow_io import CaseWorkflowInput, RecheckInput, RecheckOutcome
from countersign.diagnostics import replay_reading
from countersign.domain import ids
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.persistence import repo
from countersign.persistence.blobstore import DatabaseBlobStore
from countersign.persistence.db import session_scope
from countersign.workflows.job_case import JobIntelligenceWorkflow

POSTING_URL = "https://job-boards.greenhouse.io/acme/jobs/4012345"
JOB_API = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/4012345"
CAREERS_URL = "https://www.acme.example/careers?gh_jid=4012345"
# A careers page that embeds the board: its own text, no job in it.
CAREERS_PAGE = (
    b"<html><head><title>Careers | Acme</title></head><body>"
    b'<h1>Join us</h1><a href="https://www.acme.example/about">About</a></body></html>'
)
TEXT = "Toronto\nBuild durable systems on Temporal."
PAST = "2020-01-31T00:00:00Z"
FUTURE = "2099-12-31T00:00:00Z"
REASON_OPEN = "apply entry (ATS_LINK) on an official source"


def _job(**fields: Any) -> bytes:
    job: dict[str, Any] = {
        "id": 4012345,
        "internal_job_id": 2201,
        "title": "Platform Engineer",
        "company_name": "Acme",
        "location": {"name": "Toronto"},
        "content": "&lt;p&gt;Build durable systems on Temporal.&lt;/p&gt;",
        "absolute_url": CAREERS_URL,
        "application_deadline": None,
        **fields,
    }
    return json.dumps(job).encode()


def _page(*, title: str, text: str, valid_through: str | None = None) -> bytes:
    jsonld = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "JobPosting", "validThrough": valid_through})
        + "</script>"
        if valid_through
        else ""
    )
    return (
        f"<html><head><title>{title}</title>{jsonld}</head><body><p>{text}</p></body></html>"
    ).encode()


def _network(
    page: bytes,
    job: bytes,
    *,
    landing: str = CAREERS_URL,
    page_status: int = 200,
    job_status: int = 200,
    seen: list[str] | None = None,
) -> Callable[..., FetchResult]:
    """The hosted URL lands on `landing` (a careers page, or itself) and the
    API serves `job`."""

    def fetch(url: str, **kwargs: object) -> FetchResult:
        if seen is not None:
            seen.append(url)
        if url == JOB_API:
            return FetchResult(
                requested_url=url,
                final_url=url,
                http_status=job_status,
                content_type="application/json",
                body=job,
                redirect_chain=[],
            )
        assert url == POSTING_URL, url
        return FetchResult(
            requested_url=url,
            final_url=landing,
            http_status=page_status,
            content_type="text/html",
            body=page,
            redirect_chain=[url] if landing != url else [],
        )

    return fetch


def _no_model() -> FakeEvaluationProvider:
    raise AssertionError("verification resolved a model provider")


def _render_unavailable(url: str, body: bytes, timeout: float) -> FetchResult | None:
    return None


def _intake(case_id: str) -> CaseWorkflowInput:
    payload = CaseWorkflowInput(case_id=case_id, url=POSTING_URL)
    case_activities.snapshot_source(payload)
    case_activities.normalize_case(payload)
    return payload


@pytest.mark.usefixtures("db")
def test_a_job_behind_a_careers_page_is_verified_and_evaluated_from_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(case_activities, "fetch_url", _network(CAREERS_PAGE, _job()))
    monkeypatch.setattr(case_activities, "resolve_provider", _no_model)
    case_id = str(uuid.uuid4())
    payload = _intake(case_id)
    outcome = case_activities.verify_status(payload)

    verification = outcome.verification
    assert (verification.status, verification.authority, verification.reason) == (
        JobStatus.OPEN,
        SourceAuthority.OFFICIAL_ATS,
        REASON_OPEN,
    )
    assert verification.final_url == JOB_API
    # A careers page at intake and an API description now: not comparable.
    assert verification.content_changed is None
    assert outcome.observed_text_chars == len(TEXT)
    with session_scope() as session:
        reading = repo.get_page_reading(session, ids.reading_id(case_id, 1))
        assert reading is not None
        assert reading.adapter == "greenhouse: published"
        assert reading.prompt_version == "ats-adapter/greenhouse/0.1.0"
        assert (reading.stop_reason, reading.usage) == (ReaderStop.DONE, None)
        assert [(s.step, s.action, s.outcome) for s in reading.steps] == [
            (0, ReaderAction.DONE, "adapter greenhouse: published")
        ]
        assert [(o.how, o.source_url, o.authority) for o in reading.observations] == [
            (ObservationHow.STATIC, CAREERS_URL, SourceAuthority.COMPANY_CONTROLLED),
            (ObservationHow.API, JOB_API, SourceAuthority.OFFICIAL_ATS),
        ]
        assert reading.body_observation == 1
        assert reading.apply_entry == ApplyEntry(
            kind=ApplyEntryKind.ATS_LINK, observation=1, cited_text="Apply", href=CAREERS_URL
        )
        api = repo.get_snapshot(session, reading.observations[1].snapshot_id or "")
        assert api is not None and api.source_type == "ats_api"
        assert (api.extractor_name, api.extractor_version) == ("ats-adapter/greenhouse", "0.1.0")
        assert DatabaseBlobStore(session).get(api.raw_object_key) == _job()
        case = repo.get_case(session, case_id)
        assert case is not None and (case.title, case.company_hint) == (
            "Platform Engineer",
            "Acme",
        )

    seen: list[dict[str, Any]] = []
    run_evaluation = case_activities.run_evaluation

    def spy(*args: Any, **kwargs: Any) -> EvaluationRecord:
        seen.append(kwargs)
        return run_evaluation(*args, **kwargs)

    monkeypatch.setattr(case_activities, "run_evaluation", spy)
    monkeypatch.setattr(case_activities, "resolve_provider", FakeEvaluationProvider)
    case_activities.evaluate_case(payload)
    (evaluated,) = seen
    # The posting's own URL, not the API endpoint the body came from.
    assert (evaluated["text"], evaluated["title"], evaluated["url"]) == (
        TEXT,
        "Platform Engineer",
        POSTING_URL,
    )
    assert evaluated["source_authority"] == SourceAuthority.OFFICIAL_ATS


@pytest.mark.usefixtures("db")
def test_a_hosted_page_with_text_stays_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _page(title="Platform Engineer at Acme", text="Build durable systems. Apply below.")
    monkeypatch.setattr(case_activities, "fetch_url", _network(page, _job(), landing=POSTING_URL))
    monkeypatch.setattr(case_activities, "resolve_provider", _no_model)
    case_id = str(uuid.uuid4())
    verification = case_activities.verify_status(_intake(case_id)).verification

    assert (verification.status, verification.authority) == (
        JobStatus.OPEN,
        SourceAuthority.OFFICIAL_ATS,
    )
    assert verification.content_changed is False
    with session_scope() as session:
        reading = repo.get_page_reading(session, ids.reading_id(case_id, 1))
        assert reading is not None and reading.body_observation == 0
        case = repo.get_case(session, case_id)
        assert case is not None and case.title == "Platform Engineer at Acme"


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize(
    ("landing", "page_status", "valid_through", "status", "reason"),
    [
        (POSTING_URL, 404, None, JobStatus.CLOSED, "gone or closed notice observed"),
        (
            POSTING_URL,
            200,
            PAST,
            JobStatus.EXPIRED,
            f"JSON-LD validThrough {PAST} has passed",
        ),
        # An unmapped careers page is a lower tier: its date is recorded under OPEN.
        (CAREERS_URL, 200, PAST, JobStatus.OPEN, REASON_OPEN),
    ],
)
def test_an_official_page_still_wins_when_it_shows_the_job_gone_or_expired(
    monkeypatch: pytest.MonkeyPatch,
    landing: str,
    page_status: int,
    valid_through: str | None,
    status: JobStatus,
    reason: str,
) -> None:
    page = _page(
        title="Platform Engineer", text="Build durable systems.", valid_through=valid_through
    )
    monkeypatch.setattr(
        case_activities,
        "fetch_url",
        _network(page, _job(), landing=landing, page_status=page_status),
    )
    monkeypatch.setattr(case_activities, "resolve_provider", _no_model)
    case_id = str(uuid.uuid4())
    verification = case_activities.verify_status(_intake(case_id)).verification

    assert (verification.status, verification.reason) == (status, reason)
    with session_scope() as session:
        reading = repo.get_page_reading(session, ids.reading_id(case_id, 1))
        assert reading is not None and reading.adapter == "greenhouse: published"
        assert reading.apply_entry is not None and reading.apply_entry.observation == 1
        expected = [(EvidenceKind.VALID_THROUGH, 0)] if valid_through else []
        assert [(e.kind, e.observation) for e in reading.evidence] == expected


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize(
    ("job", "job_status", "note"),
    [
        (b'{"status": 404, "error": "Job not found"}', 404, "greenhouse: not served (http 404)"),
        (b"upstream error", 503, "greenhouse: unavailable (http 503)"),
        (_job(internal_job_id=None), 200, "greenhouse: prospect post"),
        (
            _job(application_deadline=PAST),
            200,
            "greenhouse: application deadline passed (2020-01-31T00:00:00+00:00)",
        ),
    ],
)
def test_otherwise_the_reader_reads_the_page_and_the_attempt_is_recorded(
    monkeypatch: pytest.MonkeyPatch, job: bytes, job_status: int, note: str
) -> None:
    """A job the API does not serve is not closed on that alone: the reader
    runs unchanged and the case goes on to evaluation as today."""

    reader = FakeEvaluationProvider()  # unscripted: an empty reading
    monkeypatch.setattr(
        case_activities, "fetch_url", _network(CAREERS_PAGE, job, job_status=job_status)
    )
    monkeypatch.setattr(case_activities, "resolve_provider", lambda: reader)
    monkeypatch.setattr(case_activities, "render_url", _render_unavailable)
    case_id = str(uuid.uuid4())
    verification = case_activities.verify_status(_intake(case_id)).verification

    assert reader.calls == 1
    assert verification.status == JobStatus.UNVERIFIED
    with session_scope() as session:
        reading = repo.get_page_reading(session, ids.reading_id(case_id, 1))
        assert reading is not None
        assert reading.adapter == note
        assert reading.prompt_version == READER_PROMPT_VERSION
        assert [o.how for o in reading.observations] == [ObservationHow.STATIC]
        case = repo.get_case(session, case_id)
        assert case is not None and case.title == "Careers | Acme"


def _pending(monkeypatch: pytest.MonkeyPatch) -> str:
    """A case waiting on its first approval, verified from the API."""

    monkeypatch.setattr(case_activities, "fetch_url", _network(CAREERS_PAGE, _job()))
    monkeypatch.setattr(case_activities, "resolve_provider", FakeEvaluationProvider)
    payload = _intake(str(uuid.uuid4()))
    case_activities.verify_status(payload)
    case_activities.evaluate_case(payload)
    case_activities.create_approval(payload)
    return payload.case_id


@pytest.mark.usefixtures("db")
def test_a_published_job_is_rechecked_without_the_model_and_a_retry_costs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = _pending(monkeypatch)
    monkeypatch.setattr(case_activities, "resolve_provider", _no_model)
    recheck = RecheckInput(
        case_id=case_id,
        url=POSTING_URL,
        generation=1,
        approval_request_id=ids.approval_request_id(case_id, 1),
        run_key="r1",
    )
    expected = RecheckOutcome(
        recorded=True, model_calls=0, status=JobStatus.OPEN, reason=REASON_OPEN
    )

    assert case_activities.recheck_case(recheck) == expected
    # Lost completion: the retry finds the stored record and charges nothing.
    assert case_activities.recheck_case(recheck) == expected


@pytest.mark.usefixtures("db")
def test_a_recheck_that_loses_the_api_body_records_a_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verified from the API, then not served: the reader's page becomes the
    body, and the posting is reported changed, not "nothing to compare"."""

    case_id = _pending(monkeypatch)
    not_served = b'{"status": 404, "error": "Job not found"}'
    monkeypatch.setattr(
        case_activities, "fetch_url", _network(CAREERS_PAGE, not_served, job_status=404)
    )
    monkeypatch.setattr(case_activities, "render_url", _render_unavailable)
    outcome = case_activities.recheck_case(
        RecheckInput(
            case_id=case_id,
            url=POSTING_URL,
            generation=1,
            approval_request_id=ids.approval_request_id(case_id, 1),
            run_key="r1",
        )
    )

    assert (outcome.recorded, outcome.status) == (True, JobStatus.UNVERIFIED)
    with session_scope() as session:
        verification = repo.get_verification(session, ids.recheck_verification_id(case_id, 1, "r1"))
        assert verification is not None and verification.content_changed is True
        reading = repo.latest_page_reading(session, case_id)
        assert reading is not None and reading.adapter == "greenhouse: not served (http 404)"


@pytest.mark.usefixtures("db")
def test_a_retried_verification_returns_the_stored_one_with_its_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(case_activities, "fetch_url", _network(CAREERS_PAGE, _job(), seen=seen))
    monkeypatch.setattr(case_activities, "resolve_provider", _no_model)
    payload = _intake(str(uuid.uuid4()))
    first = case_activities.verify_status(payload)
    fetched = len(seen)

    # Lost completion: the first attempt committed; its retry reads it back.
    assert case_activities.verify_status(payload) == first
    assert len(seen) == fetched
    with session_scope() as session:
        case = repo.get_case(session, payload.case_id)
        assert case is not None and case.title == "Platform Engineer"


@pytest.mark.usefixtures("db")
def test_a_replay_of_the_case_reads_the_page_not_the_api_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(case_activities, "fetch_url", _network(CAREERS_PAGE, _job()))
    monkeypatch.setattr(case_activities, "resolve_provider", _no_model)
    case_id = str(uuid.uuid4())
    case_activities.verify_status(_intake(case_id))

    replayed = replay_reading.replay(case_id, provider=FakeEvaluationProvider())
    with session_scope() as session:
        reading = repo.latest_page_reading(session, case_id)
        assert reading is not None
        assert replayed["snapshot_id"] == reading.observations[0].snapshot_id


@pytest.mark.usefixtures("db")
async def test_a_published_job_reaches_approval_with_only_the_evaluators_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeEvaluationProvider()
    monkeypatch.setattr(case_activities, "fetch_url", _network(CAREERS_PAGE, _job()))
    monkeypatch.setattr(case_activities, "resolve_provider", lambda: provider)
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
            handle = await start_case(env, queue, url=POSTING_URL)
            status = await wait_for_pending_approval(handle)

    assert provider.calls == 1  # the evaluation; verification asked no model
    with session_scope() as session:
        verification = repo.latest_verification(session, status.case_id)
        assert verification is not None
        assert (verification.status, verification.authority) == (
            JobStatus.OPEN,
            SourceAuthority.OFFICIAL_ATS,
        )
        case = repo.get_case(session, status.case_id)
        assert case is not None and case.title == "Platform Engineer"
        approval = repo.approval_for_case(session, status.case_id)
        assert approval is not None and "APPROVE" in list(approval.allowed_decisions)

"""Ticket 003 through the real Activities: the Ashby posting API in verification.

Real database and Activities, mocked network, fake model. A posting the
API publishes is verified from the API without the model; the page still
wins when it shows the posting gone or expired; anything else leaves the
reading to the reader, with the API attempt recorded on it.
"""

import json
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from support import start_case, time_skipping_env, wait_for_pending_approval
from temporalio.worker import Worker

import countersign.activities.case_activities as case_activities
from countersign.activities import ALL_ACTIVITIES
from countersign.agents.page_reader import READER_PROMPT_VERSION, ReaderConfig, ReaderResult
from countersign.capabilities.ats import ashby
from countersign.capabilities.http_fetch import FetchResult
from countersign.contracts.base import ContractModel
from countersign.contracts.enums import (
    ApplyEntryKind,
    EvidenceKind,
    JobStatus,
    ObservationHow,
    ReaderAction,
    ReaderStop,
    SourceAuthority,
)
from countersign.contracts.page_reading import ApplyEntry
from countersign.contracts.workflow_io import CaseWorkflowInput
from countersign.domain import ids
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.gateway.provider import ParsedResponse, ProviderUnavailable
from countersign.persistence import repo
from countersign.persistence.blobstore import DatabaseBlobStore
from countersign.persistence.db import session_scope
from countersign.persistence.models import SourceSnapshotRow, VerificationRow
from countersign.workflows.job_case import JobIntelligenceWorkflow

JOB_ID = "4c1d7e2a-5b6f-4a8e-9d3c-2b1a0f9e8d7c"
POSTING_URL = f"https://jobs.ashbyhq.com/acme/{JOB_ID}"
APPLY_URL = f"{POSTING_URL}/application"
BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/acme"
FUTURE = "2099-12-31T00:00:00Z"


def _page(valid_through: str = FUTURE) -> bytes:
    """A client-rendered posting: no visible text, the posting in JSON-LD."""

    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Platform Engineer",
        "description": "<p>Build durable systems on Temporal.</p>",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        "validThrough": valid_through,
    }
    return (
        b'<html><head><title>Platform Engineer @ Acme</title><script type="application/ld+json">'
        + json.dumps(posting).encode()
        + b'</script></head><body><div id="root"></div></body></html>'
    )


def _board(*, published: bool = True, listed: bool = True) -> bytes:
    jobs: list[dict[str, Any]] = [
        {
            "id": "other",
            "jobUrl": "https://jobs.ashbyhq.com/acme/other",
            "applyUrl": "https://jobs.ashbyhq.com/acme/other/application",
            "isListed": True,
            "descriptionPlain": "Another role.",
        }
    ]
    if published:
        jobs.append(
            {
                "id": JOB_ID,
                "title": "Platform Engineer",
                "jobUrl": POSTING_URL,
                "applyUrl": APPLY_URL,
                "isListed": listed,
                "descriptionPlain": "Build durable systems on Temporal.",
            }
        )
    return json.dumps({"apiVersion": "1", "jobs": jobs}).encode()


def _network(
    page: bytes,
    board: bytes | Exception,
    *,
    page_status: int = 200,
    seen: list[tuple[str, dict[str, object]]] | None = None,
) -> Callable[..., FetchResult]:
    def fetch(url: str, **kwargs: object) -> FetchResult:
        if seen is not None:
            seen.append((url, kwargs))
        if url == BOARD_URL:
            if isinstance(board, Exception):
                raise board
            status, body, content_type = 200, board, "application/json"
        else:
            assert url == POSTING_URL, url
            status, body, content_type = page_status, page, "text/html"
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=status,
            content_type=content_type,
            body=body,
            redirect_chain=[],
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
@pytest.mark.parametrize(
    ("listed", "note"), [(True, "ashby: published"), (False, "ashby: published, unlisted")]
)
def test_a_published_posting_is_verified_from_the_api_without_the_model(
    monkeypatch: pytest.MonkeyPatch, listed: bool, note: str
) -> None:
    seen: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        case_activities, "fetch_url", _network(_page(), _board(listed=listed), seen=seen)
    )
    monkeypatch.setattr(case_activities, "resolve_provider", _no_model)
    case_id = str(uuid.uuid4())
    outcome = case_activities.verify_status(_intake(case_id))

    verification = outcome.verification
    assert verification.status == JobStatus.OPEN
    assert verification.authority == SourceAuthority.OFFICIAL_ATS
    assert verification.reason == "apply entry (ATS_LINK) on an official source"
    assert verification.final_url == BOARD_URL
    assert verification.content_changed is False
    # The body is still the pasted page: its JSON-LD description.
    assert outcome.observed_text_chars == len("Build durable systems on Temporal.")
    # One API call, under the board's own body limit.
    assert [(url, kw.get("max_body_bytes")) for url, kw in seen if url == BOARD_URL] == [
        (BOARD_URL, ashby.MAX_BOARD_BYTES)
    ]
    with session_scope() as session:
        reading = repo.get_page_reading(session, ids.reading_id(case_id, 1))
        assert reading is not None
        assert reading.adapter == note
        assert reading.prompt_version == "ats-adapter/ashby/0.1.0"
        assert (reading.stop_reason, reading.usage, reading.rejected_claims) == (
            ReaderStop.DONE,
            None,
            [],
        )
        assert [(s.step, s.action, s.outcome) for s in reading.steps] == [
            (0, ReaderAction.DONE, f"adapter {note}")
        ]
        assert [(o.how, o.authority) for o in reading.observations] == [
            (ObservationHow.STATIC, SourceAuthority.OFFICIAL_ATS),
            (ObservationHow.API, SourceAuthority.OFFICIAL_ATS),
        ]
        assert reading.body_observation == 0
        assert reading.apply_entry == ApplyEntry(
            kind=ApplyEntryKind.ATS_LINK, observation=1, cited_text="Apply", href=APPLY_URL
        )
        assert [(e.kind, e.observation) for e in reading.evidence] == [
            (EvidenceKind.VALID_THROUGH, 0)
        ]
        page_ref, api_ref = reading.observations
        assert verification.snapshot_id == api_ref.snapshot_id
        api = repo.get_snapshot(session, api_ref.snapshot_id or "")
        assert api is not None
        assert (api.source_type, api.authority_level) == ("ats_api", SourceAuthority.OFFICIAL_ATS)
        assert (api.extractor_name, api.extractor_version) == ("ats-adapter/ashby", "0.1.0")
        assert DatabaseBlobStore(session).get(api.raw_object_key) == _board(listed=listed)
        body, body_ref = repo.body_observation(session, case_id)
        assert body is not None and body.snapshot_id == page_ref.snapshot_id
        assert body.final_url == POSTING_URL and body_ref == page_ref
        # The re-observed page deduped onto its intake snapshot; the API adds one.
        snapshots = session.scalar(
            select(func.count())
            .select_from(SourceSnapshotRow)
            .where(SourceSnapshotRow.case_id == case_id)
        )
        assert snapshots == 2


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize(
    ("page_status", "valid_through", "status", "reason"),
    [
        (404, FUTURE, JobStatus.CLOSED, "gone or closed notice observed"),
        (
            200,
            "2020-01-31T00:00:00Z",
            JobStatus.EXPIRED,
            "JSON-LD validThrough 2020-01-31T00:00:00Z has passed",
        ),
    ],
)
def test_the_page_still_wins_when_it_shows_the_posting_gone_or_expired(
    monkeypatch: pytest.MonkeyPatch,
    page_status: int,
    valid_through: str,
    status: JobStatus,
    reason: str,
) -> None:
    """The API's entry is recorded on the reading, and the existing table
    decides against it: the conflict stays visible, nothing is averaged."""

    monkeypatch.setattr(
        case_activities,
        "fetch_url",
        _network(_page(valid_through), _board(), page_status=page_status),
    )
    monkeypatch.setattr(case_activities, "resolve_provider", _no_model)
    case_id = str(uuid.uuid4())
    verification = case_activities.verify_status(_intake(case_id)).verification

    assert (verification.status, verification.reason) == (status, reason)
    assert verification.final_url == POSTING_URL
    assert verification.apply_markers_found is False
    with session_scope() as session:
        reading = repo.get_page_reading(session, ids.reading_id(case_id, 1))
        assert reading is not None and reading.adapter == "ashby: published"
        assert reading.apply_entry is not None and reading.apply_entry.observation == 1
        assert verification.snapshot_id == reading.observations[0].snapshot_id


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize(
    ("board", "note"),
    [
        (_board(published=False), "ashby: not published"),
        (httpx.ConnectError("connection refused"), "ashby: unavailable (connection refused)"),
    ],
)
def test_otherwise_the_reader_reads_the_page_and_the_attempt_is_recorded(
    monkeypatch: pytest.MonkeyPatch, board: bytes | Exception, note: str
) -> None:
    """A posting absent from the board is not closed on that alone, and an
    unavailable API is no worse than today: the reader runs unchanged."""

    reader = FakeEvaluationProvider()  # unscripted: an empty reading
    monkeypatch.setattr(case_activities, "fetch_url", _network(_page(), board))
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
        assert [s.outcome for s in reading.steps] == [
            "pre-render: render unavailable",
            "reading proposed",
        ]


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize(
    ("pasted", "landed", "adapter", "status"),
    [
        (
            "https://careers.acme.example/roles/platform",
            POSTING_URL,
            "ashby: published",
            JobStatus.OPEN,
        ),
        (POSTING_URL, "https://jobs.ashbyhq.com/acme", None, JobStatus.UNVERIFIED),
    ],
)
def test_the_lookup_follows_the_page_to_where_it_landed(
    monkeypatch: pytest.MonkeyPatch,
    pasted: str,
    landed: str,
    adapter: str | None,
    status: JobStatus,
) -> None:
    """The adapter matches the fetched page's final URL, the one that also
    classifies observation 0: a link redirecting onto an Ashby posting is
    looked up; an Ashby link redirecting off its posting is not."""

    served = _network(_page(), _board())

    def fetch(url: str, **kwargs: object) -> FetchResult:
        if url != pasted:
            return served(url, **kwargs)
        return FetchResult(
            requested_url=url,
            final_url=landed,
            http_status=200,
            content_type="text/html",
            body=_page(),
            redirect_chain=[url],
        )

    monkeypatch.setattr(case_activities, "fetch_url", fetch)
    monkeypatch.setattr(case_activities, "resolve_provider", FakeEvaluationProvider)
    monkeypatch.setattr(case_activities, "render_url", _render_unavailable)
    case_id = str(uuid.uuid4())
    payload = CaseWorkflowInput(case_id=case_id, url=pasted)
    case_activities.snapshot_source(payload)
    case_activities.normalize_case(payload)

    assert case_activities.verify_status(payload).verification.status == status
    with session_scope() as session:
        reading = repo.get_page_reading(session, ids.reading_id(case_id, 1))
        assert reading is not None
        assert (reading.url, reading.observations[0].source_url) == (pasted, landed)
        assert reading.adapter == adapter


@pytest.mark.usefixtures("db")
def test_a_provider_outage_in_the_reader_path_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = FakeEvaluationProvider()

    def outage[T: ContractModel](
        *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
    ) -> ParsedResponse[T]:
        raise ProviderUnavailable("induced 529")

    monkeypatch.setattr(reader, "parse", outage)
    monkeypatch.setattr(case_activities, "fetch_url", _network(_page(), _board(published=False)))
    monkeypatch.setattr(case_activities, "resolve_provider", lambda: reader)
    monkeypatch.setattr(case_activities, "render_url", _render_unavailable)
    case_id = str(uuid.uuid4())
    payload = _intake(case_id)
    with pytest.raises(ProviderUnavailable):
        case_activities.verify_status(payload)
    with session_scope() as session:
        assert session.get(VerificationRow, ids.verification_id(case_id, 1)) is None


@pytest.mark.usefixtures("db")
def test_the_api_attempt_spends_the_reading_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-012 sizes the Activity for one first fetch plus one 120-second
    reading; time spent on the API comes out of that reading."""

    clock = iter([100.0, 107.5])  # before the API call, then before the reader
    monkeypatch.setattr(case_activities, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    budgets: list[float] = []
    read_page = case_activities.read_page

    def spy(*args: Any, **kwargs: Any) -> ReaderResult:
        budgets.append(kwargs["config"].wall_clock_seconds)
        return read_page(*args, **kwargs)

    monkeypatch.setattr(case_activities, "read_page", spy)
    monkeypatch.setattr(case_activities, "fetch_url", _network(_page(), _board(published=False)))
    monkeypatch.setattr(case_activities, "resolve_provider", FakeEvaluationProvider)
    monkeypatch.setattr(case_activities, "render_url", _render_unavailable)
    case_activities.verify_status(_intake(str(uuid.uuid4())))
    assert budgets == [ReaderConfig().wall_clock_seconds - 7.5]


@pytest.mark.usefixtures("db")
async def test_a_published_posting_reaches_approval_with_only_the_evaluators_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeEvaluationProvider()
    monkeypatch.setattr(case_activities, "fetch_url", _network(_page(), _board()))
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
        assert verification.status == JobStatus.OPEN
        assert verification.authority == SourceAuthority.OFFICIAL_ATS
        reading = repo.latest_page_reading(session, status.case_id)
        assert reading is not None and reading.adapter == "ashby: published"
        evaluation = repo.latest_evaluation(session, status.case_id)
        assert evaluation is not None
        approval = repo.approval_for_case(session, status.case_id)
        assert approval is not None and "APPROVE" in list(approval.allowed_decisions)

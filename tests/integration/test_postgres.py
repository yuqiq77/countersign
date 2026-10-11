"""PostgreSQL-backed integration: the behaviors SQLite silently masks.

SQLite ignores VARCHAR lengths and drops timezone information, so id-width
and datetime-awareness bugs pass every SQLite test and detonate on the
deployment database. This module runs only when COUNTERSIGN_TEST_POSTGRES_URL
is set (CI provides a service container; locally, point it at a scratch
database).
"""

import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from countersign.contracts.enums import ActorType, ApprovalDecision, CaseState
from countersign.persistence import repo
from countersign.persistence.db import reset_engine, session_scope

REPO_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_URL = os.environ.get("COUNTERSIGN_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    POSTGRES_URL is None, reason="COUNTERSIGN_TEST_POSTGRES_URL not set"
)


@pytest.fixture
def postgres_db(monkeypatch: pytest.MonkeyPatch):
    assert POSTGRES_URL is not None
    monkeypatch.setenv("COUNTERSIGN_DATABASE_URL", POSTGRES_URL)
    reset_engine()
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(config, "head")
    yield
    reset_engine()


@pytest.mark.usefixtures("postgres_db")
def test_full_repo_cycle_with_real_column_widths() -> None:
    """Prefixed ids, the re-entry audit, approvals, and decisions must all
    insert on PostgreSQL — VARCHAR lengths are enforced here."""

    case_id = str(uuid.uuid4())
    with session_scope() as session:
        repo.create_case(session, case_id=case_id, url="https://example.com/jobs/1")
        for state in (
            CaseState.SNAPSHOTTED,
            CaseState.NORMALIZED,
            CaseState.DEDUPLICATED,
            CaseState.OFFICIAL_SOURCE_VERIFIED,
            CaseState.EVALUATED,
            CaseState.PENDING_APPROVAL,
            CaseState.NEEDS_MORE_EVIDENCE,
            CaseState.OFFICIAL_SOURCE_VERIFIED,  # legal re-entry: second audit row
        ):
            repo.transition_case(
                session,
                case_id=case_id,
                new_state=state,
                actor_type=ActorType.SERVICE,
                actor_id="worker",
            )
        row = repo.create_approval_request(
            session,
            case_id=case_id,
            generation=1,
            allowed=[ApprovalDecision.APPROVE],
            expires_at=repo.utcnow() + timedelta(days=7),
        )
        approval_id = row.id
    with session_scope() as session:
        applied = repo.record_decision(
            session,
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVE,
            reason="postgres integration",
            actor="admin",
            decision_version=1,
        )
        assert applied is True
    with session_scope() as session:
        request = repo.get_approval_request(session, approval_id)
        assert request is not None
        # PostgreSQL round-trips timezone-aware datetimes.
        view = repo.approval_view(request)
        assert view.expires_at.tzinfo is not None


@pytest.mark.usefixtures("postgres_db")
def test_identical_bodies_preserve_each_sources_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import func, select

    from countersign.activities.case_activities import snapshot_source
    from countersign.capabilities.http_fetch import FetchResult
    from countersign.contracts.workflow_io import CaseWorkflowInput
    from countersign.persistence.models import SourceSnapshotRow

    body = b'<p>Engineer</p><a href="/apply">Apply</a>'
    response_status = [200]

    def fetch(url: str, **_: object) -> FetchResult:
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=response_status[0],
            content_type="text/html",
            body=body,
            redirect_chain=[],
        )

    monkeypatch.setattr("countersign.activities.case_activities.fetch_url", fetch)
    case_id = str(uuid.uuid4())
    board = CaseWorkflowInput(case_id=case_id, url="https://board.example/jobs/1")
    official = board.model_copy(update={"url": "https://jobs.lever.co/acme/1"})
    first = snapshot_source(board).snapshot
    second = snapshot_source(official).snapshot
    response_status[0] = 503
    third = snapshot_source(official).snapshot
    repeat = snapshot_source(official).snapshot
    assert len({s.snapshot_id for s in (first, second, third)}) == 3
    assert repeat.snapshot_id == third.snapshot_id
    with session_scope() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(SourceSnapshotRow)
                .where(SourceSnapshotRow.case_id == case_id)
            )
            == 3
        )
        stored = repo.get_snapshot(session, third.snapshot_id)
        assert stored is not None and stored.http_status == 503
        assert stored.final_url == official.url


@pytest.mark.usefixtures("postgres_db")
def test_an_ashby_api_reading_persists_within_column_widths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ticket 003 on the deployment engine: the API observation's snapshot
    (source type, adapter extractor, JSON body) and the reading's adapter
    note round-trip within PostgreSQL's column widths."""

    import json

    from countersign.activities.case_activities import (
        normalize_case,
        snapshot_source,
        verify_status,
    )
    from countersign.capabilities.http_fetch import FetchResult
    from countersign.contracts.enums import JobStatus, ObservationHow
    from countersign.contracts.workflow_io import CaseWorkflowInput
    from countersign.domain import ids

    posting_url = "https://jobs.ashbyhq.com/acme/4c1d7e2a-5b6f-4a8e-9d3c-2b1a0f9e8d7c"
    board_url = "https://api.ashbyhq.com/posting-api/job-board/acme"
    posting = {
        "jobUrl": posting_url,
        "applyUrl": f"{posting_url}/application",
        "isListed": True,
        "descriptionPlain": "Build durable systems.",
    }
    board = json.dumps({"apiVersion": "1", "jobs": [posting]}).encode()

    def fetch(url: str, **_: object) -> FetchResult:
        api = url == board_url
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            content_type="application/json" if api else "text/html",
            body=board if api else b"<h1>Platform Engineer</h1><p>Build durable systems.</p>",
            redirect_chain=[],
        )

    monkeypatch.setattr("countersign.activities.case_activities.fetch_url", fetch)
    case_id = str(uuid.uuid4())
    payload = CaseWorkflowInput(case_id=case_id, url=posting_url)
    snapshot_source(payload)
    normalize_case(payload)
    assert verify_status(payload).verification.status == JobStatus.OPEN
    with session_scope() as session:
        reading = repo.get_page_reading(session, ids.reading_id(case_id, 1))
        assert reading is not None and reading.adapter == "ashby: published"
        api_ref = reading.observations[1]
        assert api_ref.how == ObservationHow.API
        api = repo.get_snapshot(session, api_ref.snapshot_id or "")
        assert api is not None
        assert (api.source_type, api.extractor_name) == ("ats_api", "ats-adapter/ashby")


@pytest.mark.usefixtures("postgres_db")
def test_the_watchs_records_persist_within_column_widths() -> None:
    """Ticket 004 on the deployment engine: a watch-created case, a recheck's
    run-derived verification and reading ids and the run's audit events fit
    PostgreSQL's widths, and the recheck queries compare aware timestamps."""

    from countersign.activities.case_activities import BOARD_ABSENCE
    from countersign.capabilities.ats import ashby
    from countersign.contracts.enums import JobStatus, ObservationHow, ReaderStop, SourceAuthority
    from countersign.contracts.page_reading import ObservationRef, PageReadingRecord
    from countersign.contracts.verification import VerificationResult
    from countersign.contracts.workflow_io import WatchRunSummary
    from countersign.domain import ids
    from countersign.persistence.models import AuditEventRow

    run_key = "abcd1234"
    url = "https://jobs.ashbyhq.com/acme/4c1d7e2a-5b6f-4a8e-9d3c-2b1a0f9e8d7c"
    case_id = str(uuid.uuid4())
    reading_id = ids.recheck_reading_id(case_id, 999, run_key)
    verification_id = ids.recheck_verification_id(case_id, 999, run_key)
    verification = VerificationResult(
        case_id=case_id,
        status=JobStatus.UNVERIFIED,
        authority=SourceAuthority.OFFICIAL_ATS,
        checked_at=repo.utcnow(),
        http_status=200,
        final_url=url,
        reason=BOARD_ABSENCE,
        apply_markers_found=False,
        closed_markers_found=False,
        reading_id=reading_id,
    )
    summary = WatchRunSummary(
        run_key=run_key,
        watchlist_version="2026-10-01",
        watchlist_fingerprint="0123abcd",
        boards=["ashby/acme: published", "greenhouse/acme: published"],
        seen=42,
        filtered=30,
        without_location=2,
        known=9,
        outside_window=1,
        reappeared=1,
        created=2,
        rechecked=1,
        rechecked_without_model=1,
        closed=0,
        recheck_failures=0,
        recheck_model_calls=0,
        new_case_model_calls=8,
    )
    with session_scope() as session:
        repo.create_case(
            session,
            case_id=case_id,
            url=url,
            request_id=run_key,
            actor_type=ActorType.SYSTEM,
            actor_id="watch",
        )
        for state in (
            CaseState.SNAPSHOTTED,
            CaseState.NORMALIZED,
            CaseState.DEDUPLICATED,
            CaseState.OFFICIAL_SOURCE_VERIFIED,
            CaseState.EVALUATED,
            CaseState.PENDING_APPROVAL,
        ):
            repo.transition_case(
                session,
                case_id=case_id,
                new_state=state,
                actor_type=ActorType.SERVICE,
                actor_id="worker",
            )
        approval_id = repo.create_approval_request(
            session,
            case_id=case_id,
            generation=1,
            allowed=[ApprovalDecision.APPROVE],
            expires_at=repo.utcnow() + timedelta(days=7),
        ).id
        repo.record_page_reading(
            session,
            PageReadingRecord(
                reading_id=reading_id,
                case_id=case_id,
                url=url,
                observations=[
                    ObservationRef(
                        index=0,
                        source_url=url,
                        how=ObservationHow.STATIC,
                        http_status=200,
                        authority=SourceAuthority.OFFICIAL_ATS,
                    )
                ],
                body_observation=0,
                apply_entry=None,
                evidence=[],
                rejected_claims=[],
                steps=[],
                stop_reason=ReaderStop.DONE,
                prompt_version="page-reader/0.5.0",
                usage=None,
                read_at=repo.utcnow(),
                adapter=ashby.NOT_PUBLISHED,
            ),
        )
        repo.record_verification(session, verification_id=verification_id, result=verification)
        repo.append_audit(
            session,
            audit_id=f"aud-case-reappeared-{case_id}",
            actor_type=ActorType.SYSTEM,
            actor_id="watch",
            action="case.reappeared",
            resource_type="job_case",
            resource_id=case_id,
            reason="listed again, published 2026-10-11T07:00:00+00:00",
            workflow_id="countersign-watch-2026-10-11T07:00:00Z",
        )
        repo.append_audit(
            session,
            audit_id=f"aud-watch-run-{run_key}",
            actor_type=ActorType.SYSTEM,
            actor_id="watch",
            action="watch.run",
            resource_type="watch_run",
            resource_id=run_key,
            reason=summary.model_dump_json(),
            workflow_id="countersign-watch-2026-10-11T07:00:00Z",
            request_id=run_key,
        )
    with session_scope() as session:
        assert repo.get_verification(session, verification_id) == verification
        run = session.get(AuditEventRow, f"aud-watch-run-{run_key}")
        assert run is not None and WatchRunSummary.model_validate_json(run.reason or "") == summary
        due = repo.recheck_candidates(session, before=repo.utcnow() + timedelta(minutes=1))
        assert (case_id, approval_id) in [(row.id, approval.id) for row, approval in due]
        assert repo.awaits_decision(session, case_id, approval_id)
        assert not repo.awaits_decision(session, case_id, ids.approval_request_id(case_id, 2))

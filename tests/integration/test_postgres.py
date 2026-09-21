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

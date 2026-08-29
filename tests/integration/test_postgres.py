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

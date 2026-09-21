"""Repository idempotency: retries must not duplicate business effects."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from countersign.contracts.enums import ActorType, ApprovalDecision, CaseState
from countersign.persistence import repo
from countersign.persistence.db import session_scope
from countersign.persistence.models import AuditEventRow

pytestmark = pytest.mark.usefixtures("db")


def test_create_case_is_idempotent() -> None:
    with session_scope() as session:
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
    with session_scope() as session:
        assert len(repo.list_cases(session)) == 1


def test_reapplied_transition_adds_no_duplicate_audit() -> None:
    with session_scope() as session:
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
        repo.transition_case(
            session,
            case_id="c1",
            new_state=CaseState.SNAPSHOTTED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
        )
        # A retried Activity applies the same transition again: no-op.
        repo.transition_case(
            session,
            case_id="c1",
            new_state=CaseState.SNAPSHOTTED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
        )
    with session_scope() as session:
        audits = list(
            session.scalars(select(AuditEventRow).where(AuditEventRow.resource_id == "c1"))
        )
        actions = [a.action for a in audits]
        assert actions.count("case.state.snapshotted") == 1


def test_duplicate_decision_delivery_is_noop() -> None:
    with session_scope() as session:
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
        row = repo.create_approval_request(
            session,
            case_id="c1",
            generation=1,
            allowed=[ApprovalDecision.APPROVE],
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        approval_id = row.id
    with session_scope() as session:
        first = repo.record_decision(
            session,
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVE,
            reason="looks good",
            actor="admin",
            decision_version=1,
        )
        duplicate = repo.record_decision(
            session,
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVE,
            reason="looks good",
            actor="admin",
            decision_version=1,
        )
    assert first is True
    assert duplicate is False


def test_decision_replay_with_different_content_is_a_conflict() -> None:
    with session_scope() as session:
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
        row = repo.create_approval_request(
            session,
            case_id="c1",
            generation=1,
            allowed=[ApprovalDecision.APPROVE, ApprovalDecision.REJECT],
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        approval_id = row.id
    with session_scope() as session:
        repo.record_decision(
            session,
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVE,
            reason="looks good",
            actor="admin",
            decision_version=1,
        )
        with pytest.raises(repo.DecisionConflictError):
            repo.record_decision(
                session,
                approval_request_id=approval_id,
                decision=ApprovalDecision.REJECT,
                reason="changed my mind",
                actor="admin",
                decision_version=1,
            )


def test_legal_reentry_into_a_visited_state_still_audits() -> None:
    """The append-only log records every applied transition, including a
    revisit (evidence loop) — the audit id must not collide by state."""

    chain = [
        CaseState.SNAPSHOTTED,
        CaseState.NORMALIZED,
        CaseState.DEDUPLICATED,
        CaseState.OFFICIAL_SOURCE_VERIFIED,
        CaseState.EVALUATED,
        CaseState.PENDING_APPROVAL,
        CaseState.NEEDS_MORE_EVIDENCE,
        CaseState.OFFICIAL_SOURCE_VERIFIED,  # legal re-entry
    ]
    with session_scope() as session:
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
        for state in chain:
            repo.transition_case(
                session,
                case_id="c1",
                new_state=state,
                actor_type=ActorType.SERVICE,
                actor_id="worker",
            )
    with session_scope() as session:
        audits = list(
            session.scalars(select(AuditEventRow).where(AuditEventRow.resource_id == "c1"))
        )
        actions = [a.action for a in audits]
        assert actions.count("case.state.official_source_verified") == 2


def test_approval_request_is_idempotent_per_generation() -> None:
    expires = datetime.now(UTC) + timedelta(days=7)
    with session_scope() as session:
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
        first = repo.create_approval_request(
            session,
            case_id="c1",
            generation=1,
            allowed=[ApprovalDecision.APPROVE],
            expires_at=expires,
        )
        second = repo.create_approval_request(
            session,
            case_id="c1",
            generation=1,
            allowed=[ApprovalDecision.APPROVE],
            expires_at=expires,
        )
        assert first.id == second.id


def test_attested_decision_writes_its_own_audit_event_in_the_same_transaction() -> None:
    """ADR-013: signing for an unverified posting is a searchable human event,
    and an unattested replay of the same version is a different decision."""

    with session_scope() as session:
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
        row = repo.create_approval_request(
            session,
            case_id="c1",
            generation=1,
            allowed=[ApprovalDecision.REJECT, ApprovalDecision.REQUEST_EVIDENCE],
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        approval_id = row.id
    with session_scope() as session:
        applied = repo.record_decision(
            session,
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVE,
            reason="fits the profile",
            actor="admin",
            decision_version=1,
            attested_open=True,
            attestation_note="application form loaded and accepted input today",
        )
    assert applied is True
    with session_scope() as session:
        audits = {
            a.action: a
            for a in session.scalars(
                select(AuditEventRow).where(AuditEventRow.resource_id == approval_id)
            )
        }
        attested = audits["human.verification_attested"]
        assert attested.reason == "application form loaded and accepted input today"
        assert attested.actor_type == ActorType.HUMAN.value
        assert attested.actor_id == "admin"
        assert audits["approval.approve"].reason == "fits the profile"
        decision = repo.get_decision(session, approval_id, 1)
        assert decision is not None and decision.attested_open is True
        with pytest.raises(repo.DecisionConflictError):
            repo.record_decision(
                session,
                approval_request_id=approval_id,
                decision=ApprovalDecision.APPROVE,
                reason="fits the profile",
                actor="admin",
                decision_version=1,
            )

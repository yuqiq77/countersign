"""Repository idempotency: retries must not duplicate business effects."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

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


def _walk_to(session: Session, case_id: str, states: list[CaseState]) -> None:
    for state in states:
        repo.transition_case(
            session,
            case_id=case_id,
            new_state=state,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
        )


_TO_PENDING = [
    CaseState.SNAPSHOTTED,
    CaseState.NORMALIZED,
    CaseState.DEDUPLICATED,
    CaseState.OFFICIAL_SOURCE_VERIFIED,
    CaseState.EVALUATED,
    CaseState.PENDING_APPROVAL,
]


def test_operator_context_is_the_request_evidence_reasons_in_order() -> None:
    expires = datetime.now(UTC) + timedelta(days=7)
    with session_scope() as session:
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
        for generation, decision, reason in (
            (1, ApprovalDecision.REQUEST_EVIDENCE, "first note"),
            (2, ApprovalDecision.REQUEST_EVIDENCE, "second note"),
            (3, ApprovalDecision.REJECT, "not context"),
        ):
            row = repo.create_approval_request(
                session,
                case_id="c1",
                generation=generation,
                allowed=[ApprovalDecision.REJECT, ApprovalDecision.REQUEST_EVIDENCE],
                expires_at=expires,
            )
            repo.record_decision(
                session,
                approval_request_id=row.id,
                decision=decision,
                reason=reason,
                actor="admin",
                decision_version=1,
            )
    with session_scope() as session:
        context = repo.operator_context(session, "c1")
        assert [c.text for c in context] == ["first note", "second note"]
        assert [c.decision_id for c in context] == ["dec-appr-c1-g1-v1", "dec-appr-c1-g2-v1"]
        assert all(c.supplied_at.tzinfo is not None for c in context)
        assert repo.operator_context(session, "other") == []


def test_in_flight_covers_the_re_round_but_not_a_dead_end() -> None:
    """ADR-014: NEEDS_MORE_EVIDENCE counts as in flight only while the next
    round is due — decided REQUEST_EVIDENCE below the cap and no verification
    for the next generation yet."""

    from countersign.contracts.enums import JobStatus, SourceAuthority
    from countersign.contracts.verification import VerificationResult
    from countersign.domain.gates import MAX_GENERATIONS
    from countersign.domain.ids import verification_id

    expires = datetime.now(UTC) + timedelta(days=7)
    with session_scope() as session:
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
        assert repo.in_flight(session, "c1") is True  # DISCOVERED
        _walk_to(session, "c1", _TO_PENDING)
        assert repo.in_flight(session, "c1") is True  # PENDING_APPROVAL
        row = repo.create_approval_request(
            session,
            case_id="c1",
            generation=1,
            allowed=[ApprovalDecision.REJECT, ApprovalDecision.REQUEST_EVIDENCE],
            expires_at=expires,
        )
        repo.record_decision(
            session,
            approval_request_id=row.id,
            decision=ApprovalDecision.REQUEST_EVIDENCE,
            reason="a note",
            actor="admin",
            decision_version=1,
        )
        _walk_to(session, "c1", [CaseState.NEEDS_MORE_EVIDENCE])
        # The decision alone is not enough: a case decided under earlier code
        # ended here and has no continued-round record.
        assert repo.in_flight(session, "c1") is False
        repo.mark_round_continued(session, case_id="c1", next_generation=2)
        repo.mark_round_continued(session, case_id="c1", next_generation=2)  # idempotent
        assert repo.round_continued(session, "c1", 2) is True
        assert repo.in_flight(session, "c1") is True  # round 2 is due
        repo.record_verification(
            session,
            verification_id=verification_id("c1", 2),
            result=VerificationResult(
                case_id="c1",
                status=JobStatus.UNVERIFIED,
                authority=SourceAuthority.OFFICIAL_ATS,
                checked_at=datetime.now(UTC),
                http_status=200,
                final_url="https://example.com/j/1",
                reason="empty body",
                apply_markers_found=False,
                closed_markers_found=False,
            ),
        )
        assert repo.in_flight(session, "c1") is False  # round 2 ran and ended here

    with session_scope() as session:
        repo.create_case(session, case_id="c2", url="https://example.com/j/2")
        _walk_to(session, "c2", _TO_PENDING)
        row = repo.create_approval_request(
            session,
            case_id="c2",
            generation=MAX_GENERATIONS,
            allowed=[ApprovalDecision.REJECT],
            expires_at=expires,
        )
        repo.record_decision(
            session,
            approval_request_id=row.id,
            decision=ApprovalDecision.REJECT,
            reason="no",
            actor="admin",
            decision_version=1,
        )
        _walk_to(session, "c2", [CaseState.REJECTED])
        assert repo.in_flight(session, "c2") is False
        assert repo.in_flight(session, "missing") is False

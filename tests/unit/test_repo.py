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


def test_approval_without_official_open_is_recorded_as_the_reviewers_own() -> None:
    """ADR-013 as amended: approving a posting the system did not verify OPEN
    is kept on the row and as a searchable human event in the same
    transaction; a replay of the same content is a no-op and a different
    reason is a conflict."""

    reason = "fits the profile; the application form accepted input today"
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
        applied = repo.record_decision(
            session,
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVE,
            reason=reason,
            actor="admin",
            decision_version=1,
            approved_without_official_open=True,
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
        assert attested.reason == reason
        assert attested.actor_type == ActorType.HUMAN.value
        assert attested.actor_id == "admin"
        assert audits["approval.approve"].reason == reason
        decision = repo.get_decision(session, approval_id, 1)
        assert decision is not None and decision.approved_without_official_open is True
        replay = repo.record_decision(
            session,
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVE,
            reason=reason,
            actor="admin",
            decision_version=1,
        )
        assert replay is False
        with pytest.raises(repo.DecisionConflictError):
            repo.record_decision(
                session,
                approval_request_id=approval_id,
                decision=ApprovalDecision.APPROVE,
                reason="another reason",
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


def test_evaluation_rounds_are_recovered_for_records_written_without_one() -> None:
    """Records from `evaluator/0.3.0` carry no `generation`; the round comes
    from the approval that pinned the evaluation, else from its position."""

    from countersign.gateway.evaluator import run_evaluation
    from countersign.gateway.fake_provider import FakeEvaluationProvider
    from countersign.persistence.models import EvaluationRunRow
    from countersign.policy.defaults import salary_bands

    def evaluate(generation: int, text: str):
        return run_evaluation(
            FakeEvaluationProvider(),
            case_id="c1",
            content_hash="abc",
            url="https://example.com/j/1",
            title="Engineer",
            text=text,
            verification=None,
            bands=salary_bands(),
            generation=generation,
        )

    with session_scope() as session:
        repo.create_case(session, case_id="c1", url="https://example.com/j/1")
        first = evaluate(1, "round one")
        second = evaluate(2, "round two")
        repo.record_evaluation(session, first)
        repo.record_evaluation(session, second)
        for row in session.scalars(select(EvaluationRunRow)):
            row.payload = {
                k: v
                for k, v in row.payload.items()
                if k not in ("generation", "previous_evaluation_id")
            }
    with session_scope() as session:
        assert [r.generation for r in repo.evaluations_for_case(session, "c1")] == [1, 2]
        latest = repo.latest_evaluation(session, "c1")
        assert latest is not None and latest.generation == 2
        row = repo.create_approval_request(
            session,
            case_id="c1",
            generation=1,
            allowed=[ApprovalDecision.REJECT],
            expires_at=datetime.now(UTC) + timedelta(days=7),
            evaluation_id=first.evaluation_id,
        )
        pinned = repo.pinned_evaluation(session, row.id)
        assert pinned is not None
        assert (pinned.evaluation_id, pinned.generation) == (first.evaluation_id, 1)
        assert repo.pinned_evaluation(session, "appr-c1-g9") is None


def test_body_observation_pairs_a_snapshot_only_with_the_reference_that_names_it() -> None:
    """Ticket 002: a reading's authority is never stamped onto a snapshot it
    did not classify. When the body reference's snapshot is missing, the
    fallback is the latest snapshot alone, with no reference."""

    from countersign.contracts.enums import ObservationHow, ReaderStop, SourceAuthority
    from countersign.contracts.page_reading import ObservationRef, PageReadingRecord
    from countersign.contracts.provenance import SnapshotRecord

    url = "https://careers.acme.example/roles/1"

    def snapshot(snapshot_id: str) -> SnapshotRecord:
        return SnapshotRecord(
            snapshot_id=snapshot_id,
            case_id="c1",
            canonical_url=url,
            final_url=url,
            source_type="pasted_url",
            authority_level=SourceAuthority.COMPANY_CONTROLLED,
            retrieved_at=datetime.now(UTC),
            http_status=200,
            content_type="text/html",
            content_hash="abc",
            raw_object_key="blob-1",
            extractor_name="html-text",
            extractor_version="1.2.0",
            redirect_chain=[],
        )

    def reading(reading_id: str, snapshot_id: str | None) -> PageReadingRecord:
        return PageReadingRecord(
            reading_id=reading_id,
            case_id="c1",
            url=url,
            observations=[
                ObservationRef(
                    index=0,
                    source_url=url,
                    how=ObservationHow.STATIC,
                    http_status=200,
                    snapshot_id=snapshot_id,
                    authority=SourceAuthority.OFFICIAL_COMPANY,
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
            read_at=datetime.now(UTC),
        )

    with session_scope() as session:
        repo.create_case(session, case_id="c1", url=url)
        assert repo.body_observation(session, "c1") == (None, None)
        repo.record_snapshot(session, snapshot("snap-1"))
        repo.record_page_reading(session, reading("read-c1-g1", "snap-1"))
        chosen, ref = repo.body_observation(session, "c1")
        assert chosen is not None and chosen.snapshot_id == "snap-1"
        assert ref is not None and ref.authority == SourceAuthority.OFFICIAL_COMPANY
    with session_scope() as session:
        repo.record_page_reading(session, reading("read-c1-g2", "snap-missing"))
        chosen, ref = repo.body_observation(session, "c1")
        assert chosen is not None and chosen.snapshot_id == "snap-1"
        assert ref is None

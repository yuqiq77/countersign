"""Repository functions: idempotent writes and audited state transitions.

Deterministic IDs make Activity retries naturally idempotent: a repeated
write with the same inputs finds its earlier row instead of duplicating it
(v4 §20 — at-least-once execution, effectively-once effects).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from countersign.contracts.cases import ApprovalRequestView, CaseSummary, OperatorContext
from countersign.contracts.enums import (
    ActorType,
    ApprovalDecision,
    ApprovalRequestStatus,
    CaseState,
    JobStatus,
    SourceAuthority,
)
from countersign.contracts.evaluation import EvaluationRecord
from countersign.contracts.page_reading import PageReadingRecord
from countersign.contracts.provenance import SnapshotRecord
from countersign.contracts.verification import VerificationResult
from countersign.domain.gates import MAX_GENERATIONS
from countersign.domain.ids import approval_request_id, verification_id
from countersign.domain.states import (
    IN_FLIGHT_STATES,
    assert_transition,
    can_transition,
    is_retry_noop,
)
from countersign.persistence.models import (
    ApprovalRequestRow,
    AuditEventRow,
    EvaluationRunRow,
    HumanDecisionRow,
    IdempotencyRow,
    JobCaseRow,
    PageReadingRow,
    SourceSnapshotRow,
    VerificationRow,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Normalize a possibly-naive stored datetime to timezone-aware UTC.

    SQLite ignores `DateTime(timezone=True)` and returns naive datetimes;
    every value we store is UTC, so a missing tzinfo means UTC. Aware
    values pass through unchanged (PostgreSQL path).
    """

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class DecisionConflictError(ValueError):
    """A decision replay carried different content than the recorded one."""


# --- audit -----------------------------------------------------------------


def append_audit(
    session: Session,
    *,
    audit_id: str,
    actor_type: ActorType,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    reason: str | None = None,
    workflow_id: str | None = None,
    request_id: str | None = None,
) -> bool:
    """Append an audit event; deterministic IDs keep retries duplicate-free."""

    if session.get(AuditEventRow, audit_id) is not None:
        return False
    session.add(
        AuditEventRow(
            id=audit_id,
            occurred_at=utcnow(),
            actor_type=actor_type.value,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            reason=reason,
            workflow_id=workflow_id,
            request_id=request_id,
            payload_hash=None,
        )
    )
    return True


# --- cases -----------------------------------------------------------------


def create_case(
    session: Session, *, case_id: str, url: str, request_id: str | None = None
) -> JobCaseRow:
    """Create a case in DISCOVERED, or return the existing row (idempotent)."""

    existing = session.get(JobCaseRow, case_id)
    if existing is not None:
        return existing
    now = utcnow()
    row = JobCaseRow(
        id=case_id,
        state=CaseState.DISCOVERED.value,
        url=url,
        title=None,
        company_hint=None,
        created_at=now,
        updated_at=now,
        version=1,
    )
    session.add(row)
    append_audit(
        session,
        audit_id=f"aud-case-created-{case_id}",
        actor_type=ActorType.HUMAN,
        actor_id="admin",
        action="case.created",
        resource_type="job_case",
        resource_id=case_id,
        request_id=request_id,
    )
    return row


def get_case(session: Session, case_id: str) -> JobCaseRow | None:
    return session.get(JobCaseRow, case_id)


def list_cases(session: Session) -> list[JobCaseRow]:
    stmt = select(JobCaseRow).order_by(JobCaseRow.created_at.desc())
    return list(session.scalars(stmt))


def transition_case(
    session: Session,
    *,
    case_id: str,
    new_state: CaseState,
    actor_type: ActorType,
    actor_id: str,
    workflow_id: str | None = None,
    reason: str | None = None,
) -> JobCaseRow:
    """Move a case to `new_state`, appending audit in the same transaction.

    Re-applying the transition the case is already in is a no-op, so a
    retried Activity converges instead of failing.
    """

    row = session.get(JobCaseRow, case_id)
    if row is None:
        raise ValueError(f"unknown case {case_id}")
    current = CaseState(row.state)
    if current == new_state:
        return row
    if not can_transition(current, new_state):
        if is_retry_noop(current, new_state):
            return row
        assert_transition(current, new_state)
    row.state = new_state.value
    row.updated_at = utcnow()
    row.version += 1
    # The version counter makes the audit id unique per applied transition,
    # so a legal re-entry into a previously visited state still audits;
    # converged retries return above and never reach this point.
    append_audit(
        session,
        audit_id=f"aud-case-{case_id}-v{row.version}-{new_state.value}",
        actor_type=actor_type,
        actor_id=actor_id,
        action=f"case.state.{new_state.value.lower()}",
        resource_type="job_case",
        resource_id=case_id,
        reason=reason,
        workflow_id=workflow_id,
    )
    return row


def set_case_facts(
    session: Session, *, case_id: str, title: str | None, company_hint: str | None
) -> None:
    row = session.get(JobCaseRow, case_id)
    if row is None:
        raise ValueError(f"unknown case {case_id}")
    row.title = title
    row.company_hint = company_hint
    row.updated_at = utcnow()


def case_summary(row: JobCaseRow) -> CaseSummary:
    return CaseSummary(
        case_id=row.id,
        state=CaseState(row.state),
        url=row.url,
        title=row.title,
        company_hint=row.company_hint,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# --- snapshots -------------------------------------------------------------


def record_snapshot(session: Session, record: SnapshotRecord) -> str:
    """Persist a snapshot; its ID binds response provenance as well as content."""

    existing = session.get(SourceSnapshotRow, record.snapshot_id)
    if existing is not None:
        return existing.id
    session.add(
        SourceSnapshotRow(
            id=record.snapshot_id,
            case_id=record.case_id,
            canonical_url=record.canonical_url,
            final_url=record.final_url,
            source_type=record.source_type,
            authority_level=record.authority_level.value,
            retrieved_at=record.retrieved_at,
            http_status=record.http_status,
            content_type=record.content_type,
            content_hash=record.content_hash,
            raw_object_key=record.raw_object_key,
            extractor_name=record.extractor_name,
            extractor_version=record.extractor_version,
            redirect_chain=list(record.redirect_chain),
            request_trace_id=record.request_trace_id,
        )
    )
    return record.snapshot_id


def latest_snapshot(session: Session, case_id: str) -> SnapshotRecord | None:
    stmt = (
        select(SourceSnapshotRow)
        .where(SourceSnapshotRow.case_id == case_id)
        .order_by(SourceSnapshotRow.retrieved_at.desc())
    )
    row = session.scalars(stmt).first()
    return None if row is None else _snapshot_record(row)


def get_snapshot(session: Session, snapshot_id: str) -> SnapshotRecord | None:
    row = session.get(SourceSnapshotRow, snapshot_id)
    return None if row is None else _snapshot_record(row)


def body_snapshot(session: Session, case_id: str) -> SnapshotRecord | None:
    """The observation the latest reading identified as the posting body.

    Falls back to the latest snapshot before any reading exists. Every
    consumer of "the posting" (evaluation, approval pinning, the case
    detail) goes through here, so none of them shows a followed apply page
    or a rendered copy that merely happened to be stored last.
    """

    reading = latest_page_reading(session, case_id)
    if reading is not None:
        ref = next((r for r in reading.observations if r.index == reading.body_observation), None)
        if ref is not None and ref.snapshot_id is not None:
            chosen = get_snapshot(session, ref.snapshot_id)
            if chosen is not None:
                return chosen
    return latest_snapshot(session, case_id)


def _snapshot_record(row: SourceSnapshotRow) -> SnapshotRecord:
    return SnapshotRecord(
        snapshot_id=row.id,
        case_id=row.case_id,
        canonical_url=row.canonical_url,
        final_url=row.final_url,
        source_type=row.source_type,
        authority_level=SourceAuthority(row.authority_level),
        retrieved_at=row.retrieved_at,
        http_status=row.http_status,
        content_type=row.content_type,
        content_hash=row.content_hash,
        raw_object_key=row.raw_object_key,
        extractor_name=row.extractor_name,
        extractor_version=row.extractor_version,
        redirect_chain=list(row.redirect_chain),
        request_trace_id=row.request_trace_id,
    )


# --- page readings (ADR-012) ----------------------------------------------


def record_page_reading(session: Session, record: PageReadingRecord) -> str:
    """Persist a reading; a repeat of the same reading id is a no-op."""

    if session.get(PageReadingRow, record.reading_id) is not None:
        return record.reading_id
    session.add(
        PageReadingRow(
            id=record.reading_id,
            case_id=record.case_id,
            payload=record.model_dump(mode="json"),
            prompt_version=record.prompt_version,
            stop_reason=record.stop_reason.value,
            created_at=utcnow(),
        )
    )
    return record.reading_id


def latest_page_reading(session: Session, case_id: str) -> PageReadingRecord | None:
    stmt = (
        select(PageReadingRow)
        .where(PageReadingRow.case_id == case_id)
        .order_by(PageReadingRow.created_at.desc())
    )
    row = session.scalars(stmt).first()
    if row is None:
        return None
    # Self-produced JSON boundary: enums and datetimes come back as strings.
    return PageReadingRecord.model_validate(row.payload, strict=False)


# --- verification ----------------------------------------------------------


def record_verification(
    session: Session, *, verification_id: str, result: VerificationResult
) -> str:
    if session.get(VerificationRow, verification_id) is not None:
        return verification_id
    session.add(
        VerificationRow(
            id=verification_id,
            case_id=result.case_id,
            status=result.status.value,
            authority=result.authority.value,
            checked_at=result.checked_at,
            http_status=result.http_status,
            final_url=result.final_url,
            reason=result.reason,
            apply_markers_found=result.apply_markers_found,
            closed_markers_found=result.closed_markers_found,
            observed_content_hash=result.observed_content_hash,
            content_changed=result.content_changed,
            snapshot_id=result.snapshot_id,
            reading_id=result.reading_id,
        )
    )
    return verification_id


def _verification_result(row: VerificationRow) -> VerificationResult:
    return VerificationResult(
        case_id=row.case_id,
        status=JobStatus(row.status),
        authority=SourceAuthority(row.authority),
        checked_at=row.checked_at,
        http_status=row.http_status,
        final_url=row.final_url,
        reason=row.reason,
        apply_markers_found=row.apply_markers_found,
        closed_markers_found=row.closed_markers_found,
        observed_content_hash=row.observed_content_hash,
        content_changed=row.content_changed,
        snapshot_id=row.snapshot_id,
        reading_id=row.reading_id,
    )


def _latest_verification_row(session: Session, case_id: str) -> VerificationRow | None:
    stmt = (
        select(VerificationRow)
        .where(VerificationRow.case_id == case_id)
        .order_by(VerificationRow.checked_at.desc())
    )
    return session.scalars(stmt).first()


def latest_verification(session: Session, case_id: str) -> VerificationResult | None:
    row = _latest_verification_row(session, case_id)
    return None if row is None else _verification_result(row)


def get_verification(session: Session, verification_id: str) -> VerificationResult | None:
    row = session.get(VerificationRow, verification_id)
    return None if row is None else _verification_result(row)


def latest_verification_id(session: Session, case_id: str) -> str | None:
    row = _latest_verification_row(session, case_id)
    return None if row is None else row.id


# --- evaluations -----------------------------------------------------------


def record_evaluation(session: Session, record: EvaluationRecord) -> str:
    if session.get(EvaluationRunRow, record.evaluation_id) is not None:
        return record.evaluation_id
    session.add(
        EvaluationRunRow(
            id=record.evaluation_id,
            case_id=record.case_id,
            payload=record.model_dump(mode="json"),
            policy_version=record.policy_version,
            prompt_version=record.prompt_version,
            model_route=record.model_route,
            created_at=utcnow(),
        )
    )
    return record.evaluation_id


def _latest_evaluation_row(session: Session, case_id: str) -> EvaluationRunRow | None:
    stmt = (
        select(EvaluationRunRow)
        .where(EvaluationRunRow.case_id == case_id)
        .order_by(EvaluationRunRow.created_at.desc())
    )
    return session.scalars(stmt).first()


def _evaluation_record(row: EvaluationRunRow, generation: int) -> EvaluationRecord:
    # Self-produced JSON payload: validated laxly at this one boundary
    # because it was dumped in JSON mode (enums/datetimes as strings).
    record = EvaluationRecord.model_validate(row.payload, strict=False)
    if "generation" not in row.payload:
        # Written before records carried their round: recover it from the
        # approval that pinned this evaluation, else from its position
        # (one evaluation per round, oldest first).
        record = record.model_copy(update={"generation": generation})
    return record


def evaluations_for_case(session: Session, case_id: str) -> list[EvaluationRecord]:
    """Every round's evaluation, oldest first (ADR-014 history)."""

    stmt = (
        select(EvaluationRunRow)
        .where(EvaluationRunRow.case_id == case_id)
        .order_by(EvaluationRunRow.created_at.asc(), EvaluationRunRow.id.asc())
    )
    pinned = {
        approval.evaluation_id: approval.generation
        for approval in session.scalars(
            select(ApprovalRequestRow).where(ApprovalRequestRow.case_id == case_id)
        )
        if approval.evaluation_id is not None
    }
    return [
        _evaluation_record(row, pinned.get(row.id, position))
        for position, row in enumerate(session.scalars(stmt), start=1)
    ]


def latest_evaluation(session: Session, case_id: str) -> EvaluationRecord | None:
    records = evaluations_for_case(session, case_id)
    return records[-1] if records else None


def pinned_evaluation(session: Session, approval_request_id: str) -> EvaluationRecord | None:
    """The evaluation an approval request pinned: the judgment of that round.

    The previous round's input for the evaluator comes from here, not from
    "the latest evaluation": a retried evaluate Activity has already
    persisted this round's own record, and "latest" would hand the round
    its own judgment as its predecessor.
    """

    approval = session.get(ApprovalRequestRow, approval_request_id)
    if approval is None or approval.evaluation_id is None:
        return None
    row = session.get(EvaluationRunRow, approval.evaluation_id)
    return None if row is None else _evaluation_record(row, approval.generation)


def latest_evaluation_id(session: Session, case_id: str) -> str | None:
    row = _latest_evaluation_row(session, case_id)
    return None if row is None else row.id


# --- approvals -------------------------------------------------------------


def create_approval_request(
    session: Session,
    *,
    case_id: str,
    generation: int,
    allowed: list[ApprovalDecision],
    expires_at: datetime,
    snapshot_id: str | None = None,
    verification_id: str | None = None,
    evaluation_id: str | None = None,
    workflow_id: str | None = None,
) -> ApprovalRequestRow:
    """Create the durable approval request for a case generation (idempotent)."""

    approval_id = approval_request_id(case_id, generation)
    existing = session.get(ApprovalRequestRow, approval_id)
    if existing is not None:
        return existing
    row = ApprovalRequestRow(
        id=approval_id,
        case_id=case_id,
        generation=generation,
        status=ApprovalRequestStatus.PENDING.value,
        allowed_decisions=[d.value for d in allowed],
        created_at=utcnow(),
        expires_at=expires_at,
        decided_at=None,
        snapshot_id=snapshot_id,
        verification_id=verification_id,
        evaluation_id=evaluation_id,
    )
    session.add(row)
    append_audit(
        session,
        audit_id=f"aud-appr-created-{approval_id}",
        actor_type=ActorType.SERVICE,
        actor_id="worker",
        action="approval.requested",
        resource_type="approval_request",
        resource_id=approval_id,
        workflow_id=workflow_id,
    )
    return row


def get_approval_request(session: Session, approval_request_id: str) -> ApprovalRequestRow | None:
    return session.get(ApprovalRequestRow, approval_request_id)


def approval_for_case(session: Session, case_id: str) -> ApprovalRequestRow | None:
    stmt = (
        select(ApprovalRequestRow)
        .where(ApprovalRequestRow.case_id == case_id)
        .order_by(ApprovalRequestRow.generation.desc())
    )
    return session.scalars(stmt).first()


def approval_view(row: ApprovalRequestRow) -> ApprovalRequestView:
    return ApprovalRequestView(
        approval_request_id=row.id,
        case_id=row.case_id,
        status=ApprovalRequestStatus(row.status),
        allowed_decisions=[ApprovalDecision(d) for d in row.allowed_decisions],
        created_at=ensure_utc(row.created_at),
        expires_at=ensure_utc(row.expires_at),
        decided_at=None if row.decided_at is None else ensure_utc(row.decided_at),
        snapshot_id=row.snapshot_id,
        verification_id=row.verification_id,
        evaluation_id=row.evaluation_id,
        generation=row.generation,
        max_generations=MAX_GENERATIONS,
    )


def _round_audit_id(case_id: str, generation: int) -> str:
    return f"aud-round-{case_id}-g{generation}"


def mark_round_continued(
    session: Session, *, case_id: str, next_generation: int, workflow_id: str | None = None
) -> None:
    """Record that a REQUEST_EVIDENCE decision opens the next round (ADR-014).

    Written by the Activity that persists the decision, before the workflow
    continues; `in_flight` relies on it rather than inferring a re-round
    from the decision alone, so cases decided under earlier code (whose
    workflows ended there) are not reported as waiting for a round that
    never comes.
    """

    append_audit(
        session,
        audit_id=_round_audit_id(case_id, next_generation),
        actor_type=ActorType.SERVICE,
        actor_id="worker",
        action="case.round.continued",
        resource_type="job_case",
        resource_id=case_id,
        reason=f"REQUEST_EVIDENCE opens round {next_generation}",
        workflow_id=workflow_id,
    )


def round_continued(session: Session, case_id: str, generation: int) -> bool:
    return session.get(AuditEventRow, _round_audit_id(case_id, generation)) is not None


def operator_context(session: Session, case_id: str) -> list[OperatorContext]:
    """The reasons of REQUEST_EVIDENCE decisions on this case, oldest first (ADR-014)."""

    stmt = (
        select(HumanDecisionRow)
        .join(ApprovalRequestRow, HumanDecisionRow.approval_request_id == ApprovalRequestRow.id)
        .where(
            ApprovalRequestRow.case_id == case_id,
            HumanDecisionRow.decision == ApprovalDecision.REQUEST_EVIDENCE.value,
        )
        .order_by(HumanDecisionRow.created_at.asc(), HumanDecisionRow.id.asc())
    )
    return [
        OperatorContext(decision_id=row.id, supplied_at=ensure_utc(row.created_at), text=row.reason)
        for row in session.scalars(stmt)
    ]


def in_flight(session: Session, case_id: str) -> bool:
    """Whether the workflow may still move this case, from stored records only.

    The pipeline states count; so does NEEDS_MORE_EVIDENCE while an
    evidence-loop re-round is due: the next round was recorded as continued
    (`case.round.continued`) and has not verified yet. Once that
    verification row exists the round has either moved on (and the state
    shows it) or ended at an empty body. A case that reached
    NEEDS_MORE_EVIDENCE without that record — an empty body, or a
    REQUEST_EVIDENCE decided before the loop existed — is final.
    """

    row = session.get(JobCaseRow, case_id)
    if row is None:
        return False
    state = CaseState(row.state)
    if state in IN_FLIGHT_STATES:
        return True
    if state != CaseState.NEEDS_MORE_EVIDENCE:
        return False
    approval = approval_for_case(session, case_id)
    if approval is None or approval.generation >= MAX_GENERATIONS:
        return False
    next_generation = approval.generation + 1
    return (
        round_continued(session, case_id, next_generation)
        and session.get(VerificationRow, verification_id(case_id, next_generation)) is None
    )


def get_decision(
    session: Session, approval_request_id: str, decision_version: int
) -> HumanDecisionRow | None:
    return session.get(HumanDecisionRow, f"dec-{approval_request_id}-v{decision_version}")


def record_decision(
    session: Session,
    *,
    approval_request_id: str,
    decision: ApprovalDecision,
    reason: str,
    actor: str,
    decision_version: int,
    attested_open: bool = False,
    attestation_note: str | None = None,
) -> bool:
    """Record a human decision; an identical (request, version) replay no-ops.

    Returns True when the decision was applied, False when it was a
    duplicate delivery. A replay whose content differs from the recorded
    decision raises DecisionConflictError — it must never be silently
    reported as accepted. An attested decision (ADR-013) also appends its
    own audit event in the same transaction, so "a human signed for an
    unverified posting" is a searchable fact, not a column to remember.
    """

    request = session.get(ApprovalRequestRow, approval_request_id)
    if request is None:
        raise ValueError(f"unknown approval request {approval_request_id}")
    decision_id = f"dec-{approval_request_id}-v{decision_version}"
    existing = session.get(HumanDecisionRow, decision_id)
    if existing is not None:
        if (
            existing.decision == decision.value
            and existing.reason == reason
            and existing.actor == actor
            and existing.attested_open == attested_open
            and existing.attestation_note == attestation_note
        ):
            return False
        raise DecisionConflictError(
            f"decision {decision_id} already recorded with different content"
        )
    session.add(
        HumanDecisionRow(
            id=decision_id,
            approval_request_id=approval_request_id,
            decision=decision.value,
            reason=reason,
            actor=actor,
            decision_version=decision_version,
            attested_open=attested_open,
            attestation_note=attestation_note,
            created_at=utcnow(),
        )
    )
    request.status = ApprovalRequestStatus.DECIDED.value
    request.decided_at = utcnow()
    if attested_open:
        append_audit(
            session,
            audit_id=f"aud-attest-{decision_id}",
            actor_type=ActorType.HUMAN,
            actor_id=actor,
            action="human.verification_attested",
            resource_type="approval_request",
            resource_id=approval_request_id,
            reason=attestation_note,
        )
    append_audit(
        session,
        audit_id=f"aud-{decision_id}",
        actor_type=ActorType.HUMAN,
        actor_id=actor,
        action=f"approval.{decision.value.lower()}",
        resource_type="approval_request",
        resource_id=approval_request_id,
        reason=reason,
    )
    return True


# --- idempotency -----------------------------------------------------------


def find_idempotent(session: Session, *, key: str, operation: str) -> IdempotencyRow | None:
    return session.get(IdempotencyRow, (key, operation))


def save_idempotent(
    session: Session, *, key: str, operation: str, resource_id: str, fingerprint: str
) -> None:
    if session.get(IdempotencyRow, (key, operation)) is None:
        session.add(
            IdempotencyRow(
                key=key,
                operation=operation,
                resource_id=resource_id,
                fingerprint=fingerprint,
                created_at=utcnow(),
            )
        )


def new_uuid() -> str:
    return str(uuid.uuid4())

"""Temporal Activities: all non-deterministic I/O for the case workflow.

Every write is idempotent (deterministic IDs, dedup constraints) so
at-least-once Activity execution yields effectively-once business effects.
Policy violations and contract failures raise non-retryable errors;
transient network errors propagate so Temporal's retry policy fires.
"""

from datetime import timedelta

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from countersign.capabilities.http_fetch import FetchPolicyError, FetchResult, fetch_url
from countersign.capabilities.normalize import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    classify_authority,
    normalize_html,
)
from countersign.contracts.enums import (
    ActorType,
    ApprovalRequestStatus,
    CaseState,
)
from countersign.contracts.provenance import SnapshotRecord
from countersign.contracts.verification import VerificationResult
from countersign.contracts.workflow_io import (
    ApprovalRequestOutcome,
    CaseStateNote,
    CaseWorkflowInput,
    DecisionSignal,
    EvaluateOutcome,
    NormalizeOutcome,
    SnapshotOutcome,
    VerifyOutcome,
)
from countersign.domain.gates import allowed_decisions_for
from countersign.domain.states import DECISION_TO_STATE
from countersign.domain.verification import classify_job_status
from countersign.gateway.evaluator import resolve_provider, run_evaluation
from countersign.gateway.provider import ContractFailure, ProviderRefusal
from countersign.persistence import repo
from countersign.persistence.blobstore import DatabaseBlobStore, sha256_hex
from countersign.persistence.db import session_scope
from countersign.policy.defaults import salary_bands

APPROVAL_TTL_DAYS = 7


def _workflow_id() -> str | None:
    try:
        return activity.info().workflow_id
    except RuntimeError:  # outside an activity context (unit tests)
        return None


def _fetch(url: str) -> FetchResult:
    try:
        return fetch_url(url)
    except FetchPolicyError as exc:
        raise ApplicationError(str(exc), type="FetchPolicyError", non_retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ApplicationError(f"fetch failed: {exc}", type="TransientFetchError") from exc


@activity.defn(name="snapshot_source")
def snapshot_source(payload: CaseWorkflowInput) -> SnapshotOutcome:
    """Fetch the pasted URL, store the raw body, and record provenance."""

    result = _fetch(payload.url)
    content_hash = sha256_hex(result.body)
    snapshot_id = f"snap-{payload.case_id[:8]}-{content_hash[:16]}"
    with session_scope() as session:
        repo.create_case(session, case_id=payload.case_id, url=payload.url)
        blob_key = DatabaseBlobStore(session).put(
            result.body, result.content_type, sha256=content_hash
        )
        record = SnapshotRecord(
            snapshot_id=snapshot_id,
            case_id=payload.case_id,
            canonical_url=payload.url,
            final_url=result.final_url,
            source_type="pasted_url",
            authority_level=classify_authority(result.final_url),
            retrieved_at=repo.utcnow(),
            http_status=result.http_status,
            content_type=result.content_type,
            content_hash=content_hash,
            raw_object_key=blob_key,
            extractor_name=EXTRACTOR_NAME,
            extractor_version=EXTRACTOR_VERSION,
            redirect_chain=result.redirect_chain,
            request_trace_id=None,
        )
        repo.record_snapshot(session, record)
        repo.transition_case(
            session,
            case_id=payload.case_id,
            new_state=CaseState.SNAPSHOTTED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
            workflow_id=_workflow_id(),
        )
        return SnapshotOutcome(snapshot=record)


@activity.defn(name="normalize_case")
def normalize_case(payload: CaseWorkflowInput) -> NormalizeOutcome:
    """Deterministically extract text facts and pass phase-1 deduplication.

    Returns scalar facts only; the full text stays behind the blob store
    (Temporal payloads carry references, never raw bodies).
    """

    with session_scope() as session:
        snapshot = repo.latest_snapshot(session, payload.case_id)
        if snapshot is None:
            raise ApplicationError("no snapshot for case", type="MissingSnapshot")
        body = DatabaseBlobStore(session).get(snapshot.raw_object_key)
        if body is None:
            raise ApplicationError("blob missing", type="MissingBlob")
        normalized = normalize_html(body)
        repo.set_case_facts(
            session,
            case_id=payload.case_id,
            title=normalized.title,
            company_hint=normalized.company_hint,
        )
        repo.transition_case(
            session,
            case_id=payload.case_id,
            new_state=CaseState.NORMALIZED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
            workflow_id=_workflow_id(),
        )
        # Phase-1 dedup is exact (case, content-hash) — enforced by the
        # snapshot unique constraint. Cross-case canonical merge is Slice 2.
        repo.transition_case(
            session,
            case_id=payload.case_id,
            new_state=CaseState.DEDUPLICATED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
            workflow_id=_workflow_id(),
        )
        return NormalizeOutcome(
            title=normalized.title,
            company_hint=normalized.company_hint,
            content_hash=normalized.content_hash,
            extractor_version=EXTRACTOR_VERSION,
        )


@activity.defn(name="verify_status")
def verify_status(payload: CaseWorkflowInput) -> VerifyOutcome:
    """Second observation of the source: persisted, compared, and bound.

    Distinct from snapshot_source by intent: this re-observes whether the
    posting is still open and where it finally redirects, immediately
    before evaluation. The observation is stored with full provenance,
    its normalized text is compared against the first snapshot, and the
    verification record binds to the observation snapshot — so an approval
    can never silently pair old content with new status.

    Transient fetch errors propagate so Temporal's retry policy fires;
    exhaustion quarantines the case in the workflow (visible, with reason).
    """

    result = _fetch(payload.url)
    normalized = normalize_html(result.body)
    authority = classify_authority(result.final_url)
    status, reason = classify_job_status(
        http_status=result.http_status,
        apply_markers_found=normalized.apply_markers_found,
        closed_markers_found=normalized.closed_markers_found,
        authority=authority,
    )
    content_hash = sha256_hex(result.body)
    observation_id = f"snap-{payload.case_id[:8]}-{content_hash[:16]}"
    verification_id = f"ver-{payload.case_id}-g{payload.generation}"

    with session_scope() as session:
        first = repo.latest_snapshot(session, payload.case_id)
        first_normalized_hash: str | None = None
        if first is not None:
            first_body = DatabaseBlobStore(session).get(first.raw_object_key)
            if first_body is not None:
                first_normalized_hash = normalize_html(first_body).content_hash

        blob_key = DatabaseBlobStore(session).put(
            result.body, result.content_type, sha256=content_hash
        )
        observation = SnapshotRecord(
            snapshot_id=observation_id,
            case_id=payload.case_id,
            canonical_url=payload.url,
            final_url=result.final_url,
            source_type="verification_observation",
            authority_level=authority,
            retrieved_at=repo.utcnow(),
            http_status=result.http_status,
            content_type=result.content_type,
            content_hash=content_hash,
            raw_object_key=blob_key,
            extractor_name=EXTRACTOR_NAME,
            extractor_version=EXTRACTOR_VERSION,
            redirect_chain=result.redirect_chain,
            request_trace_id=None,
        )
        stored_snapshot_id = repo.record_snapshot(session, observation)

        content_changed = (
            None
            if first_normalized_hash is None
            else normalized.content_hash != first_normalized_hash
        )
        verification = VerificationResult(
            case_id=payload.case_id,
            status=status,
            authority=authority,
            checked_at=repo.utcnow(),
            http_status=result.http_status,
            final_url=result.final_url,
            reason=reason,
            apply_markers_found=normalized.apply_markers_found,
            closed_markers_found=normalized.closed_markers_found,
            observed_content_hash=normalized.content_hash,
            content_changed=content_changed,
            snapshot_id=stored_snapshot_id,
        )
        repo.record_verification(
            session, verification_id=verification_id, result=verification
        )
        repo.transition_case(
            session,
            case_id=payload.case_id,
            new_state=CaseState.OFFICIAL_SOURCE_VERIFIED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
            workflow_id=_workflow_id(),
        )
    return VerifyOutcome(verification=verification)


@activity.defn(name="evaluate_case")
def evaluate_case(payload: CaseWorkflowInput) -> EvaluateOutcome:
    """Run the governed model evaluation and persist the versioned record."""

    with session_scope() as session:
        snapshot = repo.latest_snapshot(session, payload.case_id)
        if snapshot is None:
            raise ApplicationError("no snapshot for case", type="MissingSnapshot")
        body = DatabaseBlobStore(session).get(snapshot.raw_object_key)
        if body is None:
            raise ApplicationError("blob missing", type="MissingBlob")
        verification = repo.latest_verification(session, payload.case_id)
        case = repo.get_case(session, payload.case_id)
        title = case.title if case is not None else None
    normalized = normalize_html(body)

    # Marks the activity attempt only; the provider-boundary lines live in
    # the gateway, and the request id below is the evidence a request landed.
    activity.logger.info("evaluate: start for %s", payload.case_id)
    try:
        record = run_evaluation(
            resolve_provider(),
            case_id=payload.case_id,
            content_hash=normalized.content_hash,
            url=payload.url,
            title=title,
            text=normalized.text,
            verification=verification,
            bands=salary_bands(),
            generation=payload.generation,
        )
    except (ContractFailure, ProviderRefusal) as exc:
        raise ApplicationError(str(exc), type="ContractFailure", non_retryable=True) from exc
    usage = record.usage
    activity.logger.info(
        "evaluate: %s request_id=%s model=%s in=%d out=%d cache_read=%d "
        "latency_ms=%d repairs=%d",
        record.evaluation_id,
        usage.provider_request_id,
        usage.model_id,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens,
        usage.latency_ms,
        usage.repair_attempts,
    )

    with session_scope() as session:
        repo.record_evaluation(session, record)
        repo.transition_case(
            session,
            case_id=payload.case_id,
            new_state=CaseState.EVALUATED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
            workflow_id=_workflow_id(),
        )
    return EvaluateOutcome(record=record)


@activity.defn(name="create_approval")
def create_approval(payload: CaseWorkflowInput) -> ApprovalRequestOutcome:
    """Create the durable approval request and enter PENDING_APPROVAL.

    The allowed decisions come from the lifecycle gate (plain APPROVE only
    on a verified official OPEN), and the artifacts under review are pinned
    to the request at creation.
    """

    expires_at = repo.utcnow() + timedelta(days=APPROVAL_TTL_DAYS)
    with session_scope() as session:
        verification = repo.latest_verification(session, payload.case_id)
        row = repo.create_approval_request(
            session,
            case_id=payload.case_id,
            generation=payload.generation,
            allowed=allowed_decisions_for(verification),
            expires_at=expires_at,
            snapshot_id=repo.latest_snapshot_id(session, payload.case_id),
            verification_id=repo.latest_verification_id(session, payload.case_id),
            evaluation_id=repo.latest_evaluation_id(session, payload.case_id),
            workflow_id=_workflow_id(),
        )
        repo.transition_case(
            session,
            case_id=payload.case_id,
            new_state=CaseState.PENDING_APPROVAL,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
            workflow_id=_workflow_id(),
        )
        return ApprovalRequestOutcome(
            approval_request_id=row.id,
            expires_at=repo.ensure_utc(row.expires_at),
        )


@activity.defn(name="finalize_decision")
def finalize_decision(signal: DecisionSignal) -> None:
    """Persist the human decision and move the case; duplicates are no-ops."""

    with session_scope() as session:
        request = repo.get_approval_request(session, signal.approval_request_id)
        if request is None:
            raise ApplicationError(
                "unknown approval request", type="MissingApproval", non_retryable=True
            )
        try:
            repo.record_decision(
                session,
                approval_request_id=signal.approval_request_id,
                decision=signal.decision,
                reason=signal.reason,
                actor=signal.actor,
                decision_version=signal.decision_version,
            )
        except repo.DecisionConflictError as exc:
            raise ApplicationError(
                str(exc), type="DecisionConflict", non_retryable=True
            ) from exc
        repo.transition_case(
            session,
            case_id=request.case_id,
            new_state=DECISION_TO_STATE[signal.decision],
            actor_type=ActorType.HUMAN,
            actor_id=signal.actor,
            reason=signal.reason,
            workflow_id=_workflow_id(),
        )


@activity.defn(name="mark_closed")
def mark_closed(note: CaseStateNote) -> None:
    """Move a pre-submission case to CLOSED_OR_STALE."""

    with session_scope() as session:
        repo.transition_case(
            session,
            case_id=note.case_id,
            new_state=CaseState.CLOSED_OR_STALE,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
            reason=note.reason,
            workflow_id=_workflow_id(),
        )


@activity.defn(name="quarantine_case")
def quarantine_case(note: CaseStateNote) -> None:
    """Quarantine a case after a non-recoverable operational failure."""

    with session_scope() as session:
        repo.transition_case(
            session,
            case_id=note.case_id,
            new_state=CaseState.QUARANTINED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
            reason=note.reason,
            workflow_id=_workflow_id(),
        )


@activity.defn(name="expire_approval")
def expire_approval(note: CaseStateNote) -> None:
    """Expire an undecided approval and close the case — never implicit approval.

    The approval row flips to EXPIRED and the case leaves PENDING_APPROVAL
    for CLOSED_OR_STALE, so no zombie case outlives its completed workflow.
    """

    with session_scope() as session:
        if note.approval_request_id is None:
            return
        request = repo.get_approval_request(session, note.approval_request_id)
        if request is not None and request.status == ApprovalRequestStatus.PENDING.value:
            request.status = ApprovalRequestStatus.EXPIRED.value
            repo.append_audit(
                session,
                audit_id=f"aud-appr-expired-{request.id}",
                actor_type=ActorType.SYSTEM,
                actor_id="workflow",
                action="approval.expired",
                resource_type="approval_request",
                resource_id=request.id,
                reason=note.reason,
                workflow_id=_workflow_id(),
            )
        repo.transition_case(
            session,
            case_id=note.case_id,
            new_state=CaseState.CLOSED_OR_STALE,
            actor_type=ActorType.SYSTEM,
            actor_id="workflow",
            reason=note.reason,
            workflow_id=_workflow_id(),
        )


ALL_ACTIVITIES = [
    snapshot_source,
    normalize_case,
    verify_status,
    evaluate_case,
    create_approval,
    finalize_decision,
    mark_closed,
    quarantine_case,
    expire_approval,
]

"""Temporal Activities: all non-deterministic I/O for the case workflow.

Every write is idempotent (deterministic IDs, dedup constraints) so
at-least-once Activity execution yields effectively-once business effects.
Policy violations and contract failures raise non-retryable errors;
transient network errors propagate so Temporal's retry policy fires.
"""

import json
from datetime import timedelta

import httpx
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from countersign.agents.page_reader import ReaderResult, read_page
from countersign.capabilities.http_fetch import FetchPolicyError, FetchResult, fetch_url
from countersign.capabilities.normalize import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    classify_authority,
    normalize_html,
)
from countersign.capabilities.page_evidence import Observation
from countersign.capabilities.render import render_url
from countersign.contracts.enums import (
    ActorType,
    ApprovalRequestStatus,
    CaseState,
    EvidenceKind,
    ObservationHow,
)
from countersign.contracts.page_reading import ObservationRef, PageReadingRecord
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
from countersign.domain.verification import classify_reading
from countersign.gateway.evaluator import resolve_provider, run_evaluation
from countersign.gateway.provider import ContractFailure, ProviderRefusal
from countersign.persistence import repo
from countersign.persistence.blobstore import DatabaseBlobStore, sha256_hex
from countersign.persistence.db import session_scope
from countersign.policy.defaults import salary_bands
from countersign.policy.operator_profile import load_operator_profile

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
    snapshot_id = _snapshot_id(
        payload.case_id, result.requested_url, result.final_url, result.http_status, content_hash
    )
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
        # Snapshot identity includes the case, response source/status and body.
        # Cross-case canonical merge is Slice 2.
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


_OBSERVATION_SOURCE = {
    ObservationHow.STATIC: "verification_observation",
    ObservationHow.RENDERED: "reader_rendered",
    ObservationHow.FOLLOWED: "reader_followed",
}


def _snapshot_id(case_id: str, requested: str, final: str, status: int, content_hash: str) -> str:
    # Identical bytes from different sources/statuses are different evidence.
    digest = sha256_hex(json.dumps([case_id, requested, final, status, content_hash]).encode())
    return f"snap-{case_id[:8]}-{digest[:16]}"


def _store_observation(session: Session, case_id: str, obs: Observation) -> str:
    """Persist one reader observation as a snapshot; returns its (deduped) id."""

    content_hash = sha256_hex(obs.body)
    blob_key = DatabaseBlobStore(session).put(obs.body, obs.content_type, sha256=content_hash)
    record = SnapshotRecord(
        snapshot_id=_snapshot_id(
            case_id, obs.requested_url, obs.source_url, obs.http_status, content_hash
        ),
        case_id=case_id,
        canonical_url=obs.requested_url,
        final_url=obs.source_url,
        source_type=_OBSERVATION_SOURCE[obs.how],
        authority_level=obs.authority,
        retrieved_at=repo.utcnow(),
        http_status=obs.http_status,
        content_type=obs.content_type,
        content_hash=content_hash,
        raw_object_key=blob_key,
        extractor_name=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        redirect_chain=obs.redirect_chain,
        request_trace_id=None,
    )
    return repo.record_snapshot(session, record)


@activity.defn(name="verify_status")
def verify_status(payload: CaseWorkflowInput) -> VerifyOutcome:
    """Second observation of the source, read by the bounded page reader.

    Distinct from snapshot_source by intent: this re-observes whether the
    posting is still open and where one applies, immediately before
    evaluation. The reader may render the page or follow a link that is on
    it, within code-enforced budgets; every page it sees is stored with
    provenance, every claim it makes is checked against those stores, and
    the status is decided by the domain decision table over the verified
    evidence. The verification record binds to the governing observation and
    to the reading, so an approval never pairs old content with new status.

    Transient fetch errors on the primary observation and provider outages
    propagate so Temporal's retry policy fires; failures on followed links
    and schema failures are recorded in the reading's step log instead.
    """

    first = _fetch(payload.url)
    result: ReaderResult = read_page(
        payload.url,
        first=first,
        authority_of=classify_authority,
        fetch=lambda url, timeout: fetch_url(url, timeout_seconds=timeout),
        render=render_url,
        provider=resolve_provider(),
    )
    chosen_index, status, reason = classify_reading(
        [o.dossier() for o in result.observations],
        result.apply_entry,
        result.evidence,
        now=repo.utcnow(),
    )
    chosen = result.observations[chosen_index]
    closed_found = any(
        e.observation == chosen_index and e.kind == EvidenceKind.CLOSED_NOTICE
        for e in result.evidence
    )
    reading_id = f"read-{payload.case_id}-g{payload.generation}"
    verification_id = f"ver-{payload.case_id}-g{payload.generation}"

    with session_scope() as session:
        first_snapshot = repo.body_snapshot(session, payload.case_id)
        first_normalized_hash: str | None = None
        if first_snapshot is not None:
            first_body = DatabaseBlobStore(session).get(first_snapshot.raw_object_key)
            if first_body is not None:
                first_normalized_hash = normalize_html(first_body).content_hash

        refs: list[ObservationRef] = []
        for obs in result.observations:
            refs.append(
                ObservationRef(
                    index=obs.index,
                    source_url=obs.source_url,
                    how=obs.how,
                    http_status=obs.http_status,
                    snapshot_id=_store_observation(session, payload.case_id, obs),
                )
            )
        reading = PageReadingRecord(
            reading_id=reading_id,
            case_id=payload.case_id,
            url=payload.url,
            observations=refs,
            body_observation=result.body_observation,
            apply_entry=result.apply_entry,
            evidence=result.evidence,
            rejected_claims=result.rejected_claims,
            steps=result.steps,
            stop_reason=result.stop_reason,
            prompt_version=result.prompt_version,
            usage=result.usage,
            read_at=repo.utcnow(),
        )
        repo.record_page_reading(session, reading)

        content_changed = (
            None
            if first_normalized_hash is None
            else result.observations[result.body_observation].normalized_hash
            != first_normalized_hash
        )
        verification = VerificationResult(
            case_id=payload.case_id,
            status=status,
            authority=chosen.authority,
            checked_at=repo.utcnow(),
            http_status=chosen.http_status,
            final_url=chosen.source_url,
            reason=reason,
            apply_markers_found=(
                result.apply_entry is not None and result.apply_entry.observation == chosen_index
            ),
            closed_markers_found=closed_found,
            observed_content_hash=chosen.normalized_hash,
            content_changed=content_changed,
            snapshot_id=refs[chosen_index].snapshot_id,
            reading_id=reading_id,
        )
        repo.record_verification(session, verification_id=verification_id, result=verification)
        repo.transition_case(
            session,
            case_id=payload.case_id,
            new_state=CaseState.OFFICIAL_SOURCE_VERIFIED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
            workflow_id=_workflow_id(),
        )
    body_text = result.observations[result.body_observation].text
    activity.logger.info(
        "verify: %s status=%s entry=%s observations=%d stop=%s rejected=%d",
        payload.case_id,
        status.value,
        result.apply_entry.kind.value if result.apply_entry else None,
        len(result.observations),
        result.stop_reason.value,
        len(result.rejected_claims),
    )
    return VerifyOutcome(verification=verification, observed_text_chars=len(body_text))


@activity.defn(name="evaluate_case")
def evaluate_case(payload: CaseWorkflowInput) -> EvaluateOutcome:
    """Run the governed model evaluation and persist the versioned record."""

    with session_scope() as session:
        snapshot = repo.body_snapshot(session, payload.case_id)
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
            url=snapshot.final_url,
            title=normalized.title or title,
            text=normalized.text,
            verification=verification,
            source_authority=snapshot.authority_level,
            bands=salary_bands(),
            profile=load_operator_profile(),
            generation=payload.generation,
        )
    except (ContractFailure, ProviderRefusal) as exc:
        raise ApplicationError(str(exc), type="ContractFailure", non_retryable=True) from exc
    usage = record.usage
    activity.logger.info(
        "evaluate: %s request_id=%s model=%s in=%d out=%d cache_read=%d latency_ms=%d repairs=%d",
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
        snapshot = repo.body_snapshot(session, payload.case_id)
        row = repo.create_approval_request(
            session,
            case_id=payload.case_id,
            generation=payload.generation,
            allowed=allowed_decisions_for(verification),
            expires_at=expires_at,
            snapshot_id=snapshot.snapshot_id if snapshot else None,
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
                attested_open=signal.attested_open,
                attestation_note=signal.attestation_note,
            )
        except repo.DecisionConflictError as exc:
            raise ApplicationError(str(exc), type="DecisionConflict", non_retryable=True) from exc
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


@activity.defn(name="mark_needs_evidence")
def mark_needs_evidence(note: CaseStateNote) -> None:
    """Stop a verified case before the model: there is nothing to evaluate."""

    with session_scope() as session:
        repo.transition_case(
            session,
            case_id=note.case_id,
            new_state=CaseState.NEEDS_MORE_EVIDENCE,
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
    mark_needs_evidence,
    quarantine_case,
    expire_approval,
]

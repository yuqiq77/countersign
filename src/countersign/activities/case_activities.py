"""Temporal Activities: all non-deterministic I/O for the case workflow.

Every write is idempotent (deterministic IDs, dedup constraints) so
at-least-once Activity execution yields effectively-once business effects.
Policy violations and contract failures raise non-retryable errors;
transient network errors propagate so Temporal's retry policy fires.
"""

import json
import time
from datetime import timedelta

import httpx
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from countersign.agents.page_reader import (
    AuthorityFn,
    ReaderConfig,
    ReaderResult,
    add_source_dates,
    observe_response,
    read_page,
    reread_unchanged,
)
from countersign.capabilities.ats import ashby
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
    ApplyEntryKind,
    ApprovalDecision,
    ApprovalRequestStatus,
    CaseState,
    EvidenceKind,
    JobStatus,
    ObservationHow,
    ReaderAction,
    ReaderStop,
)
from countersign.contracts.page_reading import (
    ApplyEntry,
    ObservationRef,
    PageEvidence,
    PageReadingRecord,
    StepLog,
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
    RecheckInput,
    RecheckOutcome,
    SnapshotOutcome,
    VerifyOutcome,
)
from countersign.domain import ids
from countersign.domain.gates import MAX_GENERATIONS, allowed_decisions_for
from countersign.domain.states import DECISION_TO_STATE
from countersign.domain.verification import classify_reading
from countersign.domain.watch import RECHECK_MODEL_CALLS
from countersign.gateway.evaluator import resolve_provider, run_evaluation
from countersign.gateway.provider import ContractFailure, ProviderRefusal
from countersign.persistence import repo
from countersign.persistence.blobstore import DatabaseBlobStore, sha256_hex
from countersign.persistence.db import session_scope
from countersign.policy.defaults import salary_bands
from countersign.policy.official_hosts import OfficialHostPolicy, load_official_hosts
from countersign.policy.operator_profile import load_operator_profile

APPROVAL_TTL_DAYS = 7


def _authority_policy() -> tuple[OfficialHostPolicy | None, AuthorityFn]:
    """The official-host mapping for this Activity attempt, loaded once (ticket 002).

    Every observation of the attempt is classified by the same revision; a
    later attempt may load a newer file, and the reading records which
    revision it used. Unset means the classification that existed before
    the mapping.
    """

    try:
        policy = load_official_hosts()
    except ValueError as exc:
        # A mapping that stopped parsing after worker start is a
        # configuration failure, not a transient one: no retries, and a
        # reason the case's audit trail can name.
        raise ApplicationError(str(exc), type="PolicyError", non_retryable=True) from exc
    return policy, (policy.classify if policy is not None else classify_authority)


def _stored_body_text_chars(session: Session, verification: VerificationResult) -> int | None:
    """The body text length a stored verification's reading recorded, or recomputed.

    A reading written before `body_text_chars` existed is re-normalized
    from its body snapshot; only a missing snapshot leaves None, which the
    workflow treats as the old-history path.
    """

    reading = (
        repo.get_page_reading(session, verification.reading_id)
        if verification.reading_id is not None
        else None
    )
    if reading is None:
        return None
    if reading.body_text_chars is not None:
        return reading.body_text_chars
    ref = reading.body_ref()
    snapshot = (
        repo.get_snapshot(session, ref.snapshot_id)
        if ref is not None and ref.snapshot_id is not None
        else None
    )
    body = DatabaseBlobStore(session).get(snapshot.raw_object_key) if snapshot else None
    return None if body is None else len(normalize_html(body).text)


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

    _, authority_of = _authority_policy()
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
            authority_level=authority_of(result.final_url),
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
    ObservationHow.API: "ats_api",
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
        extractor_name=obs.extractor_name,
        extractor_version=obs.extractor_version,
        redirect_chain=obs.redirect_chain,
        request_trace_id=None,
    )
    return repo.record_snapshot(session, record)


def _adapter_reading(
    first: FetchResult, api: Observation, note: str, authority_of: AuthorityFn
) -> ReaderResult:
    """The reading an official ATS API observation makes without the model (ticket 003).

    Observation 0 is the fetched page as the reader observes it and stays
    the posting body; the apply entry is the API's apply link on
    observation 1; page dates are added as the reader adds them. The
    decision table then lets a 404/410 or a past validThrough on the page
    win over that entry, as it would over a reader's.
    """

    page = observe_response(0, first, ObservationHow.STATIC, authority_of)
    observations = [page, api]
    evidence: list[PageEvidence] = []
    add_source_dates(observations, evidence)
    (control,) = api.controls
    return ReaderResult(
        observations=observations,
        body_observation=page.index,
        apply_entry=ApplyEntry(
            kind=ApplyEntryKind.ATS_LINK,
            observation=api.index,
            cited_text=control.text,
            href=control.href,
        ),
        evidence=evidence,
        rejected_claims=[],
        steps=[StepLog(step=0, action=ReaderAction.DONE, outcome=f"adapter {note}")],
        stop_reason=ReaderStop.DONE,
        usage=None,
        prompt_version=ashby.READING_VERSION,
    )


def _read(
    url: str,
    first: FetchResult,
    authority_of: AuthorityFn,
    previous: tuple[PageReadingRecord, str | None] | None = None,
) -> tuple[ReaderResult, str | None]:
    """The verify path's reading of a fetched page, with the adapter's note.

    The Ashby API first (ticket 003); on a recheck, the previous reading
    when the page is unchanged (ticket 004); otherwise the bounded reader.
    """

    started = time.monotonic()
    ats = ashby.lookup(
        first.final_url,
        fetch=lambda url: fetch_url(url, max_body_bytes=ashby.MAX_BOARD_BYTES),
    )
    adapter_note = ats.note if ats is not None else None
    if ats is not None and ats.observation is not None:
        return _adapter_reading(first, ats.observation, ats.note, authority_of), adapter_note
    if previous is not None:
        page = observe_response(0, first, ObservationHow.STATIC, authority_of)
        reused = reread_unchanged(*previous, page)
        if reused is not None:
            return reused, adapter_note
    # The API attempt spent part of the reading budget, not extra time:
    # ADR-012 sizes the Activity for one first fetch plus one reading.
    budget = ReaderConfig().wall_clock_seconds - (time.monotonic() - started)
    result = read_page(
        url,
        first=first,
        authority_of=authority_of,
        fetch=lambda url, timeout: fetch_url(url, timeout_seconds=timeout),
        render=render_url,
        provider=resolve_provider(),
        config=ReaderConfig(wall_clock_seconds=budget),
    )
    return result, adapter_note


def _record(
    session: Session,
    *,
    case_id: str,
    url: str,
    reading_id: str,
    verification_id: str,
    policy: OfficialHostPolicy | None,
    result: ReaderResult,
    adapter_note: str | None,
    chosen_index: int,
    status: JobStatus,
    reason: str,
) -> VerificationResult:
    """Persist a reading's observations, the reading and its verification."""

    chosen = result.observations[chosen_index]
    closed_found = any(
        e.observation == chosen_index and e.kind == EvidenceKind.CLOSED_NOTICE
        for e in result.evidence
    )
    body_text = result.observations[result.body_observation].text
    first_snapshot = repo.body_snapshot(session, case_id)
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
                snapshot_id=_store_observation(session, case_id, obs),
                authority=obs.authority,
                official_host_entry=(
                    policy.entry_for(obs.source_url) if policy is not None else None
                ),
            )
        )
    reading = PageReadingRecord(
        reading_id=reading_id,
        case_id=case_id,
        url=url,
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
        notes=result.notes,
        authority_policy_version=policy.version if policy is not None else None,
        authority_policy_fingerprint=policy.fingerprint if policy is not None else None,
        body_text_chars=len(body_text),
        adapter=adapter_note,
    )
    repo.record_page_reading(session, reading)

    content_changed = (
        None
        if first_normalized_hash is None
        else result.observations[result.body_observation].normalized_hash != first_normalized_hash
    )
    verification = VerificationResult(
        case_id=case_id,
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
    return verification


def _classify(result: ReaderResult) -> tuple[int, JobStatus, str]:
    return classify_reading(
        [o.dossier() for o in result.observations],
        result.apply_entry,
        result.evidence,
        now=repo.utcnow(),
    )


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

    When the fetched page is an Ashby posting (by its final URL), it is
    first looked up in its board's public API (ticket 003). When the API
    publishes it with an apply link, that observation carries the entry and
    code makes the reading without the model; otherwise the reader runs as
    before, within what remains of the same budget, and the reading records
    what the API attempt found.

    Transient fetch errors on the primary observation and provider outages
    propagate so Temporal's retry policy fires; failures on followed links
    and schema failures are recorded in the reading's step log instead.
    """

    reading_id = ids.reading_id(payload.case_id, payload.generation)
    verification_id = ids.verification_id(payload.case_id, payload.generation)
    with session_scope() as session:
        stored = repo.get_verification(session, verification_id)
        if stored is not None:
            # Committed by an earlier attempt whose completion Temporal did
            # not see. The stored record is the result, and the text length
            # the workflow branches on comes back with it: None would take
            # the old-history path and skip the empty-body stop.
            chars = _stored_body_text_chars(session, stored)
            activity.logger.info(
                "verify: %s already recorded as %s; returning the stored result",
                payload.case_id,
                stored.status.value,
            )
            return VerifyOutcome(verification=stored, observed_text_chars=chars)

    policy, authority_of = _authority_policy()
    first = _fetch(payload.url)
    result, adapter_note = _read(payload.url, first, authority_of)
    chosen_index, status, reason = _classify(result)
    with session_scope() as session:
        verification = _record(
            session,
            case_id=payload.case_id,
            url=payload.url,
            reading_id=reading_id,
            verification_id=verification_id,
            policy=policy,
            result=result,
            adapter_note=adapter_note,
            chosen_index=chosen_index,
            status=status,
            reason=reason,
        )
        repo.transition_case(
            session,
            case_id=payload.case_id,
            new_state=CaseState.OFFICIAL_SOURCE_VERIFIED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
            workflow_id=_workflow_id(),
        )
    activity.logger.info(
        "verify: %s status=%s entry=%s observations=%d stop=%s rejected=%d adapter=%s",
        payload.case_id,
        status.value,
        result.apply_entry.kind.value if result.apply_entry else None,
        len(result.observations),
        result.stop_reason.value,
        len(result.rejected_claims),
        adapter_note,
    )
    # The database holds one reading and one verification per id, and the
    # `record_*` calls keep the first row written. A timed-out attempt that
    # Temporal already replaced may still commit first while this one runs;
    # the workflow must then carry what the database holds, not what this
    # attempt computed, so the result is read back after the commit.
    with session_scope() as session:
        stored = repo.get_verification(session, verification_id)
        if stored is None:
            raise ApplicationError("verification not stored", type="MissingVerification")
        if stored != verification:
            activity.logger.info(
                "verify: %s recorded by another attempt as %s; returning the stored result",
                payload.case_id,
                stored.status.value,
            )
        return VerifyOutcome(
            verification=stored, observed_text_chars=_stored_body_text_chars(session, stored)
        )


# Stored as a verification's reason, and the next recheck compares against
# it: rewording it restarts the chain for cases already waiting on it.
BOARD_ABSENCE = "absent from its ATS board and no JobPosting on the page"
BOARD_ABSENCE_AGAIN = f"{BOARD_ABSENCE} on two consecutive rechecks"


@activity.defn(name="recheck_case")
def recheck_case(payload: RecheckInput) -> RecheckOutcome:
    """Verify a pending case again for the watch, without a state transition (ADR-016).

    The verify path under run-derived ids; a page whose text hash and
    cited claims are unchanged is decided again without the model. An
    Ashby posting absent from its healthy board while no observation shows
    a JobPosting is UNVERIFIED with that reason, or CLOSED when the case's
    preceding verification recorded the same absence. Nothing is written
    unless the case still waits on the same approval request; the watch
    run sends any closure to the case's workflow.
    """

    reading_id = ids.recheck_reading_id(payload.case_id, payload.generation, payload.run_key)
    verification_id = ids.recheck_verification_id(
        payload.case_id, payload.generation, payload.run_key
    )
    with session_scope() as session:
        stored = repo.get_verification(session, verification_id)
        if stored is not None:
            # The attempt that wrote it did not record its call count:
            # charge the worst case whenever it asked a model. Usage cannot
            # tell, since calls that all failed validation leave none; every
            # model step does leave a step from 1 on (adapter and unchanged
            # readings have only step 0).
            reading = repo.get_page_reading(session, reading_id)
            asked = reading is not None and any(s.step > 0 for s in reading.steps)
            calls = RECHECK_MODEL_CALLS if asked else 0
            return RecheckOutcome(
                recorded=True, model_calls=calls, status=stored.status, reason=stored.reason
            )
        previous = repo.latest_verification(session, payload.case_id)
        previous_reading = (
            repo.get_page_reading(session, previous.reading_id)
            if previous is not None and previous.reading_id is not None
            else None
        )

    policy, authority_of = _authority_policy()
    first = _fetch(payload.url)
    result, adapter_note = _read(
        payload.url,
        first,
        authority_of,
        previous=(
            (previous_reading, previous.observed_content_hash)
            if previous is not None and previous_reading is not None
            else None
        ),
    )
    chosen_index, status, reason = _classify(result)
    if (
        status not in (JobStatus.CLOSED, JobStatus.EXPIRED)
        and adapter_note == ashby.NOT_PUBLISHED
        and all(o.jsonld is None for o in result.observations)
    ):
        if previous is not None and previous.reason == BOARD_ABSENCE:
            status, reason = JobStatus.CLOSED, BOARD_ABSENCE_AGAIN
        else:
            status, reason = JobStatus.UNVERIFIED, BOARD_ABSENCE
    with session_scope() as session:
        if not repo.awaits_decision(session, payload.case_id, payload.approval_request_id):
            return RecheckOutcome(recorded=False, model_calls=result.model_calls)
        _record(
            session,
            case_id=payload.case_id,
            url=payload.url,
            reading_id=reading_id,
            verification_id=verification_id,
            policy=policy,
            result=result,
            adapter_note=adapter_note,
            chosen_index=chosen_index,
            status=status,
            reason=reason,
        )
        # Another attempt may have committed this id first; its record stands.
        stored = repo.get_verification(session, verification_id)
        assert stored is not None
    activity.logger.info(
        "recheck: %s status=%s calls=%d adapter=%s",
        payload.case_id,
        stored.status.value,
        result.model_calls,
        adapter_note,
    )
    return RecheckOutcome(
        recorded=True, model_calls=result.model_calls, status=stored.status, reason=stored.reason
    )


@activity.defn(name="evaluate_case")
def evaluate_case(payload: CaseWorkflowInput) -> EvaluateOutcome:
    """Run the governed model evaluation and persist the versioned record."""

    with session_scope() as session:
        snapshot, body_ref = repo.body_observation(session, payload.case_id)
        if snapshot is None:
            raise ApplicationError("no snapshot for case", type="MissingSnapshot")
        body = DatabaseBlobStore(session).get(snapshot.raw_object_key)
        if body is None:
            raise ApplicationError("blob missing", type="MissingBlob")
        verification = repo.latest_verification(session, payload.case_id)
        # The reading's own authority for the body observation, when
        # recorded; a reused snapshot's stamp can predate the official-host
        # mapping (ticket 002).
        body_authority = (
            body_ref.authority
            if body_ref is not None and body_ref.authority is not None
            else snapshot.authority_level
        )
        case = repo.get_case(session, payload.case_id)
        title = case.title if case is not None else None
        # Reviewer notes from earlier rounds ride along as human-asserted
        # context, and the previous round's assessment as a revisable
        # judgment (ADR-014); the record names both. The previous round is
        # the evaluation its approval pinned — never "the latest", which on
        # a retried Activity is this round's own, already persisted record.
        context = repo.operator_context(session, payload.case_id)
        previous = (
            repo.pinned_evaluation(
                session, ids.approval_request_id(payload.case_id, payload.generation - 1)
            )
            if payload.generation > 1
            else None
        )
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
            source_authority=body_authority,
            bands=salary_bands(),
            profile=load_operator_profile(),
            operator_context=context,
            previous=previous,
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

    The allowed decisions come from the lifecycle gate (APPROVE needs a
    verification that is not CLOSED or EXPIRED), and the artifacts under
    review are pinned to the request at creation.
    """

    expires_at = repo.utcnow() + timedelta(days=APPROVAL_TTL_DAYS)
    with session_scope() as session:
        verification = repo.latest_verification(session, payload.case_id)
        snapshot = repo.body_snapshot(session, payload.case_id)
        row = repo.create_approval_request(
            session,
            case_id=payload.case_id,
            generation=payload.generation,
            allowed=allowed_decisions_for(verification, generation=payload.generation),
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
        # ADR-013 as amended: approving a posting the system did not verify
        # OPEN is the reviewer's own verification. The fact is derived here
        # from the verification pinned to the request, not typed by the
        # reviewer and not carried by the Signal, so the decision row, the
        # audit event and the verification cannot disagree.
        pinned = (
            repo.get_verification(session, request.verification_id)
            if request.verification_id is not None
            else None
        )
        approved_without_official_open = signal.decision == ApprovalDecision.APPROVE and (
            pinned is None or pinned.status != JobStatus.OPEN
        )
        try:
            repo.record_decision(
                session,
                approval_request_id=signal.approval_request_id,
                decision=signal.decision,
                reason=signal.reason,
                actor=signal.actor,
                decision_version=signal.decision_version,
                approved_without_official_open=approved_without_official_open,
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
        if (
            signal.decision == ApprovalDecision.REQUEST_EVIDENCE
            and request.generation < MAX_GENERATIONS
        ):
            # The same rule the workflow applies before continue-as-new,
            # recorded here so the next round is a stored fact (ADR-014).
            repo.mark_round_continued(
                session,
                case_id=request.case_id,
                next_generation=request.generation + 1,
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
    recheck_case,
]

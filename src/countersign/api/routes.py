"""Case intake, review, and approval-decision routes.

Mutations require an Idempotency-Key bound to a payload fingerprint and
append audit events; long-running work returns 202 and continues in the
durable workflow. A replay re-issues the (idempotent) workflow start only
for a case still in DISCOVERED, so an intake whose first start failed
heals on retry without a second workflow for a case that already ran.
"""

import hashlib
from typing import Annotated

import anyio.to_thread
from fastapi import APIRouter, Header, HTTPException
from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import TemporalError, WorkflowAlreadyStartedError

from countersign.capabilities.http_fetch import FetchPolicyError, assert_url_allowed
from countersign.contracts.cases import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    CaseDetail,
    CaseSummary,
    CreateCaseRequest,
    CreateCaseResponse,
)
from countersign.contracts.enums import ApprovalDecision, ApprovalRequestStatus, CaseState
from countersign.contracts.workflow_io import CaseWorkflowInput, DecisionSignal
from countersign.domain.gates import allowed_decisions_for, gated_decision
from countersign.persistence import repo
from countersign.persistence.db import session_scope
from countersign.runtime import TASK_QUEUE, WORKFLOW_NAME, connect_client, workflow_id_for

router = APIRouter(prefix="/v1")

_client: Client | None = None


async def _temporal() -> Client:
    global _client
    if _client is None:
        _client = await connect_client()
    return _client


def _create_fingerprint(payload: CreateCaseRequest) -> str:
    return hashlib.sha256(payload.url.encode("utf-8")).hexdigest()


async def _start_case_workflow(case_id: str, url: str) -> str:
    """Start the case workflow; an existing run for this id is success.

    REJECT_DUPLICATE means a completed workflow is never silently re-run by
    an idempotent replay, while an orphaned case (start failed after the DB
    commit) gets its workflow started on retry.
    """

    client = await _temporal()
    workflow_id = workflow_id_for(case_id)
    try:
        await client.start_workflow(
            WORKFLOW_NAME,
            CaseWorkflowInput(case_id=case_id, url=url),
            id=workflow_id,
            task_queue=TASK_QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        pass  # running or already completed: the intake stays idempotent
    return workflow_id


@router.post("/cases", response_model=CreateCaseResponse, status_code=202)
async def create_case(
    payload: CreateCaseRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CreateCaseResponse:
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    try:
        await anyio.to_thread.run_sync(assert_url_allowed, payload.url)
    except FetchPolicyError as exc:
        raise HTTPException(status_code=422, detail=f"url rejected: {exc}") from exc

    fingerprint = _create_fingerprint(payload)
    with session_scope() as session:
        existing = repo.find_idempotent(session, key=idempotency_key, operation="create_case")
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used with a different payload",
                )
            case_id = existing.resource_id
            row = repo.get_case(session, case_id)
            state = CaseState(row.state) if row is not None else CaseState.DISCOVERED
        else:
            case_id = repo.new_uuid()
            repo.create_case(session, case_id=case_id, url=payload.url, request_id=idempotency_key)
            repo.save_idempotent(
                session,
                key=idempotency_key,
                operation="create_case",
                resource_id=case_id,
                fingerprint=fingerprint,
            )
            state = CaseState.DISCOVERED

    # Only an intake that never left DISCOVERED may lack its workflow. A case
    # further along has one, running or completed — under this id format or
    # the earlier per-generation one — and a replay must not start another.
    if state == CaseState.DISCOVERED:
        await _start_case_workflow(case_id, payload.url)
    return CreateCaseResponse(case_id=case_id, workflow_id=workflow_id_for(case_id), state=state)


@router.get("/cases", response_model=list[CaseSummary])
def list_cases() -> list[CaseSummary]:
    with session_scope() as session:
        return [repo.case_summary(row) for row in repo.list_cases(session)]


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: str) -> CaseDetail:
    with session_scope() as session:
        row = repo.get_case(session, case_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown case")
        approval = repo.approval_for_case(session, case_id)
        verification = repo.latest_verification(session, case_id)
        evaluations = repo.evaluations_for_case(session, case_id)
        evaluation = evaluations[-1] if evaluations else None
        gated = (
            gated_decision(evaluation.evaluation, verification)
            if evaluation is not None and verification is not None
            else None
        )
        return CaseDetail(
            case_id=row.id,
            state=CaseState(row.state),
            url=row.url,
            created_at=row.created_at,
            updated_at=row.updated_at,
            snapshot=repo.body_snapshot(session, case_id),
            verification=verification,
            evaluation=evaluation,
            gated_decision=gated,
            approval=repo.approval_view(approval) if approval is not None else None,
            evaluations=evaluations,
            operator_context=repo.operator_context(session, case_id),
            in_flight=repo.in_flight(session, case_id),
        )


@router.post(
    "/approvals/{approval_request_id}/decisions",
    response_model=ApprovalDecisionResponse,
)
async def submit_decision(
    approval_request_id: str, payload: ApprovalDecisionRequest
) -> ApprovalDecisionResponse:
    with session_scope() as session:
        request = repo.get_approval_request(session, approval_request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="unknown approval request")

        attested_open = payload.attestation is not None
        attestation_note = payload.attestation.note if payload.attestation else None
        existing = repo.get_decision(session, approval_request_id, payload.decision_version)
        if existing is not None:
            if (
                existing.decision == payload.decision.value
                and existing.reason == payload.reason
                and existing.actor == payload.actor
                and existing.attested_open == attested_open
                and existing.attestation_note == attestation_note
            ):
                # Idempotent replay of an already-recorded decision.
                return ApprovalDecisionResponse(
                    approval_request_id=approval_request_id,
                    case_id=request.case_id,
                    accepted=True,
                )
            raise HTTPException(
                status_code=409,
                detail="decision version already recorded with different content",
            )

        status = ApprovalRequestStatus(request.status)
        if status is ApprovalRequestStatus.EXPIRED:
            raise HTTPException(status_code=410, detail="approval request has expired")
        if status is ApprovalRequestStatus.DECIDED:
            raise HTTPException(status_code=409, detail="approval already decided")
        if payload.decision.value in request.allowed_decisions:
            if payload.attestation is not None:
                # Recording it would let the audit log claim a human signed
                # for an unverified posting that the system had verified.
                raise HTTPException(
                    status_code=400,
                    detail="attestation not applicable: the decision is allowed without one",
                )
        else:
            # The stored set is the no-attestation gate. A reviewer's own
            # attestation is re-checked here against the verification the
            # request pinned, never against whatever is latest (ADR-013).
            pinned = (
                repo.get_verification(session, request.verification_id)
                if request.verification_id is not None
                else None
            )
            if payload.decision not in allowed_decisions_for(
                pinned, payload.attestation, generation=request.generation
            ):
                raise HTTPException(status_code=400, detail="decision not allowed")
        case_id = request.case_id

    client = await _temporal()
    handle: WorkflowHandle[object, object] = client.get_workflow_handle(workflow_id_for(case_id))
    try:
        await handle.signal(
            "submit_decision",
            DecisionSignal(
                approval_request_id=approval_request_id,
                decision=ApprovalDecision(payload.decision),
                reason=payload.reason,
                decision_version=payload.decision_version,
                actor=payload.actor,
                attested_open=attested_open,
                attestation_note=attestation_note,
            ),
        )
    except TemporalError as exc:
        raise HTTPException(
            status_code=409,
            detail="workflow is no longer accepting decisions",
        ) from exc
    return ApprovalDecisionResponse(
        approval_request_id=approval_request_id, case_id=case_id, accepted=True
    )

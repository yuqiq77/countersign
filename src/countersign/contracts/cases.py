"""API contracts for case intake, listing, and approval decisions."""

from datetime import datetime

from pydantic import Field

from countersign.contracts.base import ContractModel, JsonBoundaryModel
from countersign.contracts.enums import (
    ApprovalDecision,
    ApprovalRequestStatus,
    CaseState,
    EvalDecision,
)
from countersign.contracts.evaluation import EvaluationRecord
from countersign.contracts.provenance import SnapshotRecord
from countersign.contracts.verification import VerificationResult


class CreateCaseRequest(ContractModel):
    """Paste-a-URL intake: the permanent first-class discovery input."""

    url: str = Field(min_length=1, max_length=2048)


class CreateCaseResponse(ContractModel):
    """202 response: the case exists and its workflow has been started."""

    case_id: str
    workflow_id: str
    state: CaseState


class CaseSummary(ContractModel):
    """One row in the case list."""

    case_id: str
    state: CaseState
    url: str
    title: str | None
    company_hint: str | None
    created_at: datetime
    updated_at: datetime


class ApprovalRequestView(ContractModel):
    """A pending or decided approval request as shown to the reviewer.

    The pinned ids identify exactly which artifacts were under review when
    the request was created; the reviewer decides against these, not against
    whatever is latest at render time.
    """

    approval_request_id: str
    case_id: str
    status: ApprovalRequestStatus
    allowed_decisions: list[ApprovalDecision]
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    snapshot_id: str | None
    verification_id: str | None
    evaluation_id: str | None


class CaseDetail(ContractModel):
    """Everything the reviewer sees before deciding (v4 §26 step 4)."""

    case_id: str
    state: CaseState
    url: str
    created_at: datetime
    updated_at: datetime
    snapshot: SnapshotRecord | None
    verification: VerificationResult | None
    evaluation: EvaluationRecord | None
    gated_decision: EvalDecision | None
    approval: ApprovalRequestView | None


class ApprovalDecisionRequest(JsonBoundaryModel):
    """A durable human decision submitted against an approval request.

    JSON-boundary model: FastAPI validates the parsed body in Python mode,
    so the enum stays lax while `decision_version` is strict — a quoted
    number like "1" is rejected, never coerced.
    """

    decision: ApprovalDecision
    reason: str = Field(min_length=1, max_length=4000)
    decision_version: int = Field(ge=1, strict=True)
    actor: str = Field(default="admin", min_length=1, max_length=128)


class ApprovalDecisionResponse(ContractModel):
    """Acknowledgement that the decision Signal was delivered."""

    approval_request_id: str
    case_id: str
    accepted: bool

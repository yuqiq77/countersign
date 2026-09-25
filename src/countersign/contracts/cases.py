"""API contracts for case intake, listing, and approval decisions."""

from datetime import datetime

from pydantic import Field, model_validator

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
    # Evidence-loop round this request belongs to, and the cap (ADR-014).
    generation: int
    max_generations: int


class OperatorContext(ContractModel):
    """Context the reviewer supplied with a REQUEST_EVIDENCE decision (ADR-014).

    Human-asserted, case-scoped, and given to later evaluations as such;
    the decision id is what the evaluation record cites.
    """

    decision_id: str
    supplied_at: datetime
    text: str


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
    # Every round's evaluation, oldest first; `evaluation` is the last one.
    evaluations: list[EvaluationRecord]
    operator_context: list[OperatorContext]
    # Derived from stored records: the workflow may still move this case,
    # including the evidence-loop re-round that passes through
    # NEEDS_MORE_EVIDENCE. The client polls while this is true.
    in_flight: bool


class Attestation(JsonBoundaryModel):
    """The reviewer's own observation that the posting accepts applications.

    It lets APPROVE pass the lifecycle gate on a posting the system could
    not verify (ADR-013). It is a human decision, recorded and audited on
    its own; it never overrides a CLOSED or EXPIRED verification, and the
    note is what the reviewer saw, not a free-form reason.
    """

    observed_open: bool = Field(strict=True)
    note: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _states_open(self) -> "Attestation":
        if not self.observed_open:
            raise ValueError("an attestation must state observed_open: true")
        return self


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
    attestation: Attestation | None = None

    @model_validator(mode="after")
    def _attestation_only_with_approve(self) -> "ApprovalDecisionRequest":
        if self.attestation is not None and self.decision != ApprovalDecision.APPROVE:
            raise ValueError("an attestation only accompanies APPROVE")
        return self


class ApprovalDecisionResponse(ContractModel):
    """Acknowledgement that the decision Signal was delivered."""

    approval_request_id: str
    case_id: str
    accepted: bool

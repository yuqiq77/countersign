"""Deterministic policy gates around probabilistic model output (v4 §13.4).

The model proposes; these gates decide. No `APPLY_NOW` without a verified
`OPEN` status, and hard blockers are surfaced, never silently dropped.
"""

from countersign.contracts.enums import ApprovalDecision, EvalDecision, JobStatus
from countersign.contracts.evaluation import ModelEvaluation
from countersign.contracts.verification import VerificationResult

_NEVER_APPROVABLE = (JobStatus.CLOSED, JobStatus.EXPIRED)

# Evidence-loop rounds per case (ADR-014). At the cap the approval offers no
# REQUEST_EVIDENCE.
MAX_GENERATIONS = 3


def apply_now_permitted(verification: VerificationResult) -> bool:
    """Only a verified official `OPEN` status may be framed as apply-now."""

    return verification.status == JobStatus.OPEN


def allowed_decisions_for(
    verification: VerificationResult | None,
    *,
    generation: int = 1,
) -> list[ApprovalDecision]:
    """The lifecycle gate: which decisions an approval request accepts.

    APPROVE is offered on any pinned verification that is not CLOSED or
    EXPIRED (ADR-013 as amended); the apply-now framing is gated separately
    (`gated_decision`). REQUEST_EVIDENCE is offered while evidence-loop
    rounds remain (ADR-014).

    The set is computed once, against the verification pinned to the
    request, and stored on it; the API checks decisions against that set.
    In practice every pending approval accepts APPROVE: the workflow ends
    CLOSED/EXPIRED cases before approval and always pins a verification,
    so the two checks below are guards, not a path.
    """

    approve = verification is not None and verification.status not in _NEVER_APPROVABLE
    allowed = [ApprovalDecision.APPROVE] if approve else []
    allowed.append(ApprovalDecision.REJECT)
    if generation < MAX_GENERATIONS:
        allowed.append(ApprovalDecision.REQUEST_EVIDENCE)
    return allowed


def gated_decision(evaluation: ModelEvaluation, verification: VerificationResult) -> EvalDecision:
    """Downgrade the model's decision when the status gate forbids it.

    The model's proposal is preserved in the evaluation record; the gated
    value is what the product displays and acts on.
    """

    if evaluation.decision == EvalDecision.APPLY_NOW and not apply_now_permitted(verification):
        return EvalDecision.WAIT
    return evaluation.decision

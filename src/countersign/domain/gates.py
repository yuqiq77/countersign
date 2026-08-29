"""Deterministic policy gates around probabilistic model output (v4 §13.4).

The model proposes; these gates decide. No `APPLY_NOW` without a verified
`OPEN` status, and hard blockers are surfaced, never silently dropped.
"""

from countersign.contracts.enums import ApprovalDecision, EvalDecision, JobStatus
from countersign.contracts.evaluation import ModelEvaluation
from countersign.contracts.verification import VerificationResult


def apply_now_permitted(verification: VerificationResult) -> bool:
    """Only a verified official `OPEN` status may be framed as apply-now."""

    return verification.status == JobStatus.OPEN


def allowed_decisions_for(
    verification: VerificationResult | None,
) -> list[ApprovalDecision]:
    """The lifecycle gate: plain APPROVE requires a verified official OPEN.

    This constrains what the approval request accepts — not merely what the
    UI displays. Overriding a non-OPEN status is a distinct, explicitly
    audited decision path (Slice 3); it never shares the plain-APPROVE path.
    """

    if verification is not None and apply_now_permitted(verification):
        return [
            ApprovalDecision.APPROVE,
            ApprovalDecision.REJECT,
            ApprovalDecision.REQUEST_EVIDENCE,
        ]
    return [ApprovalDecision.REJECT, ApprovalDecision.REQUEST_EVIDENCE]


def gated_decision(
    evaluation: ModelEvaluation, verification: VerificationResult
) -> EvalDecision:
    """Downgrade the model's decision when the status gate forbids it.

    The model's proposal is preserved in the evaluation record; the gated
    value is what the product displays and acts on.
    """

    if evaluation.decision == EvalDecision.APPLY_NOW and not apply_now_permitted(
        verification
    ):
        return EvalDecision.WAIT
    return evaluation.decision

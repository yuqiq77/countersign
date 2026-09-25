"""Deterministic policy gates around probabilistic model output (v4 §13.4).

The model proposes; these gates decide. No `APPLY_NOW` without a verified
`OPEN` status, and hard blockers are surfaced, never silently dropped.
"""

from countersign.contracts.cases import Attestation
from countersign.contracts.enums import ApprovalDecision, EvalDecision, JobStatus
from countersign.contracts.evaluation import ModelEvaluation
from countersign.contracts.verification import VerificationResult

_NEVER_ATTESTABLE = (JobStatus.CLOSED, JobStatus.EXPIRED)

# Evidence-loop rounds per case (ADR-014). At the cap the approval offers no
# REQUEST_EVIDENCE: three rounds of context is a conversation; more is a
# profile that should be edited.
MAX_GENERATIONS = 3


def apply_now_permitted(verification: VerificationResult) -> bool:
    """Only a verified official `OPEN` status may be framed as apply-now."""

    return verification.status == JobStatus.OPEN


def allowed_decisions_for(
    verification: VerificationResult | None,
    attestation: Attestation | None = None,
    *,
    generation: int = 1,
) -> list[ApprovalDecision]:
    """The lifecycle gate: APPROVE needs a verified official OPEN, or a human
    attestation on a posting the system could not verify (ADR-013);
    REQUEST_EVIDENCE is offered while evidence-loop rounds remain (ADR-014).

    This constrains what the approval request accepts — not merely what the
    UI displays. The stored allowed set on a request is the no-attestation
    list; the API recomputes with the submitted attestation at decision
    time. An attestation is a separately audited human decision: it never
    reopens a CLOSED or EXPIRED verification and cannot stand in for a
    missing one. "No evidence" means a human must sign for it, not that
    nobody may act.
    """

    approve = verification is not None and (
        apply_now_permitted(verification)
        or (
            attestation is not None
            and attestation.observed_open
            and verification.status not in _NEVER_ATTESTABLE
        )
    )
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

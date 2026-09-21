"""Typed inputs/outputs crossing the Temporal workflow/Activity boundary.

Payloads carry references and scalar facts, never raw bodies (v4 §8.4):
everything here is serialized into workflow history.
"""

from datetime import datetime

from countersign.contracts.base import ContractModel
from countersign.contracts.enums import ApprovalDecision, CaseState
from countersign.contracts.evaluation import EvaluationRecord
from countersign.contracts.provenance import SnapshotRecord
from countersign.contracts.verification import VerificationResult


class CaseWorkflowInput(ContractModel):
    """Input to JobIntelligenceWorkflow; stable across contract versions."""

    case_id: str
    url: str
    generation: int = 1
    contract_version: str = "1"


class SnapshotOutcome(ContractModel):
    """Result of the snapshot Activity (provenance metadata, no body)."""

    snapshot: SnapshotRecord


class NormalizeOutcome(ContractModel):
    """Result of the normalize Activity: scalar facts, never the full text."""

    title: str | None
    company_hint: str | None
    content_hash: str
    extractor_version: str


class VerifyOutcome(ContractModel):
    """Result of the verify Activity."""

    verification: VerificationResult
    # Length of the normalized posting text in the verified observation;
    # zero means there is nothing to evaluate and the workflow stops before
    # the model (NEEDS_MORE_EVIDENCE), never spending a call on an empty body.
    # Old Temporal histories lack this field; None preserves their original path.
    observed_text_chars: int | None = None


class EvaluateOutcome(ContractModel):
    """Result of the evaluate Activity."""

    record: EvaluationRecord


class ApprovalRequestOutcome(ContractModel):
    """Result of the create-approval-request Activity.

    `expires_at` is always timezone-aware UTC; the Temporal pydantic
    converter round-trips it natively (no manual ISO stringification).
    """

    approval_request_id: str
    expires_at: datetime


class DecisionSignal(ContractModel):
    """The approval Signal payload; idempotent by request id + version."""

    approval_request_id: str
    decision: ApprovalDecision
    reason: str
    decision_version: int
    actor: str
    # Human attestation (ADR-013): the reviewer observed the posting open
    # themselves; the API has already re-checked the gate with it. Old
    # histories lack these fields.
    attested_open: bool = False
    attestation_note: str | None = None


class CaseStateNote(ContractModel):
    """Input for terminal bookkeeping Activities (quarantine, close, expire)."""

    case_id: str
    reason: str
    approval_request_id: str | None = None


class CaseWorkflowStatus(ContractModel):
    """Query response exposing workflow progress without changing it."""

    case_id: str
    state: CaseState
    approval_request_id: str | None
    decided: bool


class CaseWorkflowResult(ContractModel):
    """Terminal summary returned by the workflow."""

    case_id: str
    final_state: CaseState
    decision: ApprovalDecision | None

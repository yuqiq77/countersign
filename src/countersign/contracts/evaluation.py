"""Evaluation contracts (v4 §14): typed, cited claims; honest naming."""

from pydantic import Field

from countersign.contracts.base import ContractModel
from countersign.contracts.enums import (
    CompensationSignal,
    EvalDecision,
    JobStatus,
    Relevance,
    SeniorityFit,
    Severity,
    SourceAuthority,
)


class SalaryExtraction(ContractModel):
    """Salary data extracted from the posting, labeled by source authority.

    The model only extracts; the compensation signal is computed
    deterministically by domain code (never by the model).
    """

    official: bool
    currency: str | None = None
    min_amount: int | None = None
    max_amount: int | None = None
    source_authority: SourceAuthority
    cited_text: str | None = None


class HardBlocker(ContractModel):
    """A requirement the operator cannot currently satisfy, with citation."""

    category: str
    severity: Severity
    cited_text: str | None = None


class DistinctiveAsset(ContractModel):
    """An operator asset that is scarce within this role's applicant pool."""

    asset_id: str
    category: str
    relevance: Relevance


class RiskFlag(ContractModel):
    """A cited concern the reviewer should see before deciding."""

    category: str
    severity: Severity
    cited_text: str | None = None


class ModelEvaluation(ContractModel):
    """The model's structured evaluation output.

    Score dimensions come from the active rubric; `transition_feasibility`
    is an ordinal 1-10 judgment, not a probability. `compensation_signal`
    is deliberately absent — it is computed by deterministic code.
    """

    decision: EvalDecision
    application_status: JobStatus
    skill_capital: int = Field(ge=1, le=10)
    strategic_alignment: int = Field(ge=1, le=10)
    learning_velocity: int = Field(ge=1, le=10)
    drift_risk: int = Field(ge=1, le=10)
    domain_lock_in_risk: int = Field(ge=1, le=10)
    transition_feasibility: int = Field(ge=1, le=10)
    seniority_fit: SeniorityFit
    salary_extraction: SalaryExtraction | None = None
    hard_blockers: list[HardBlocker] = Field(default_factory=lambda: [])
    missing_evidence: list[str] = Field(default_factory=lambda: [])
    distinctive_assets: list[DistinctiveAsset] = Field(default_factory=lambda: [])
    risk_flags: list[RiskFlag] = Field(default_factory=lambda: [])
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(
        description=(
            "For the reviewer, who knows how this system works: the decision and "
            "its main reasons in three to six plain sentences. When operator context "
            "or a previous assessment changed the reading, say what it resolved and "
            "what remains open. No description of process, no restated rules."
        )
    )


class ModelUsage(ContractModel):
    """Token, cost, and latency metadata captured on every invocation."""

    model_id: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    latency_ms: int
    repair_attempts: int
    # Vendor-side request id of the accepted response, when the SDK exposes
    # one; the evidence that this evaluation's request reached the provider.
    provider_request_id: str | None = None


class EvaluationRecord(ContractModel):
    """A persisted, versioned evaluation: model output + deterministic fields."""

    evaluation_id: str
    case_id: str
    evaluation: ModelEvaluation
    compensation_signal: CompensationSignal
    policy_version: str
    prompt_version: str
    model_route: str
    usage: ModelUsage
    # Decision ids of the operator context this evaluation was given
    # (ADR-014); empty before the evidence loop existed or when none was.
    operator_context_ids: list[str] = Field(default_factory=lambda: [])
    # Evidence-loop round (ADR-014); 1 for records written before rounds.
    generation: int = 1
    # The previous round's evaluation shown to the model as a revisable
    # judgment; None on the first round or for records written before this.
    previous_evaluation_id: str | None = None

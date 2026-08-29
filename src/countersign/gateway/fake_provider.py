"""Deterministic fake provider for tests and model-free local runs."""

import hashlib

from countersign.contracts.enums import EvalDecision, JobStatus, SeniorityFit
from countersign.contracts.evaluation import ModelEvaluation
from countersign.gateway.provider import ContractFailure, ProviderResponse


def _score(seed: bytes, salt: str) -> int:
    digest = hashlib.sha256(seed + salt.encode()).digest()
    return digest[0] % 10 + 1


class FakeEvaluationProvider:
    """Derives a stable, contract-valid evaluation from the input text.

    `fail_attempts` makes the first N calls raise ContractFailure so the
    gateway's repair path is testable without a live model.
    """

    def __init__(
        self,
        *,
        fail_attempts: int = 0,
        canned: ModelEvaluation | None = None,
    ) -> None:
        self._remaining_failures = fail_attempts
        self._canned = canned
        self.calls = 0

    def evaluate(self, *, system: str, user: str) -> ProviderResponse:
        self.calls += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise ContractFailure("fake provider: induced contract failure")
        evaluation = self._canned if self._canned is not None else self._derive(user)
        return ProviderResponse(
            evaluation=evaluation,
            model_id="fake-model",
            input_tokens=len(system) // 4 + len(user) // 4,
            output_tokens=256,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )

    def _derive(self, user: str) -> ModelEvaluation:
        seed = hashlib.sha256(user.encode()).digest()
        return ModelEvaluation(
            decision=EvalDecision.STRETCH,
            application_status=JobStatus.UNKNOWN_STALE_RISK,
            skill_capital=_score(seed, "skill"),
            strategic_alignment=_score(seed, "alignment"),
            learning_velocity=_score(seed, "velocity"),
            drift_risk=_score(seed, "drift"),
            domain_lock_in_risk=_score(seed, "lockin"),
            transition_feasibility=_score(seed, "feasibility"),
            seniority_fit=SeniorityFit.PLAUSIBLE_STRETCH,
            salary_extraction=None,
            hard_blockers=[],
            missing_evidence=["deterministic fake evaluation"],
            distinctive_assets=[],
            risk_flags=[],
            confidence=0.5,
            summary="Deterministic fake evaluation derived from input hash.",
        )

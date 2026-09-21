"""Deterministic fake provider for tests and model-free local runs."""

import hashlib
from typing import cast

from countersign.contracts.base import ContractModel
from countersign.contracts.enums import EvalDecision, JobStatus, ReaderAction, SeniorityFit
from countersign.contracts.evaluation import ModelEvaluation
from countersign.contracts.page_reading import PageReading, PageStep
from countersign.gateway.provider import ContractFailure, ParsedResponse, ProviderResponse


def _score(seed: bytes, salt: str) -> int:
    digest = hashlib.sha256(seed + salt.encode()).digest()
    return digest[0] % 10 + 1


class FakeEvaluationProvider:
    """Derives a stable, contract-valid evaluation from the input text.

    `fail_attempts` makes the first N calls raise ContractFailure so the
    gateway's repair path is testable without a live model. `page_steps`
    scripts the page reader: each call for a `PageStep` pops the next one;
    with none left the fake answers DONE with an empty reading (unassessed).
    """

    def __init__(
        self,
        *,
        fail_attempts: int = 0,
        canned: ModelEvaluation | None = None,
        page_steps: list[PageStep] | None = None,
    ) -> None:
        self._remaining_failures = fail_attempts
        self._canned = canned
        self._page_steps = list(page_steps or [])
        self.calls = 0

    def parse[T: ContractModel](
        self, *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
    ) -> ParsedResponse[T]:
        self.calls += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise ContractFailure("fake provider: induced contract failure")
        parsed: ContractModel
        if output_type is ModelEvaluation:
            parsed = self._canned if self._canned is not None else self._derive(user)
        elif output_type is PageStep:
            parsed = (
                self._page_steps.pop(0)
                if self._page_steps
                else PageStep(
                    action=ReaderAction.DONE,
                    why="fake: no scripted assessment",
                    reading=PageReading(body_observation=0),
                )
            )
        else:
            raise ContractFailure(f"fake provider cannot produce {output_type.__name__}")
        return ParsedResponse(
            parsed=cast(T, parsed),
            model_id="fake-model",
            input_tokens=len(system) // 4 + len(user) // 4,
            output_tokens=256,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )

    def evaluate(self, *, system: str, user: str) -> ProviderResponse:
        response = self.parse(system=system, user=user, output_type=ModelEvaluation)
        return ProviderResponse(
            evaluation=response.parsed,
            model_id=response.model_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_input_tokens=response.cache_read_input_tokens,
            cache_creation_input_tokens=response.cache_creation_input_tokens,
        )

    def _derive(self, user: str) -> ModelEvaluation:
        seed = hashlib.sha256(user.encode()).digest()
        return ModelEvaluation(
            decision=EvalDecision.STRETCH,
            application_status=JobStatus.UNVERIFIED,
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

"""Thin model gateway: routes, structured output, repair, version capture."""

from countersign.gateway.evaluator import (
    DEFAULT_MODEL_ROUTE,
    POLICY_VERSION,
    PROMPT_VERSION,
    EvaluatorConfig,
    build_system_prompt,
    build_user_prompt,
    resolve_provider,
    run_evaluation,
)
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.gateway.provider import (
    ContractFailure,
    EvaluationProvider,
    GatewayError,
    ProviderRefusal,
    ProviderResponse,
)

__all__ = [
    "DEFAULT_MODEL_ROUTE",
    "POLICY_VERSION",
    "PROMPT_VERSION",
    "ContractFailure",
    "EvaluationProvider",
    "EvaluatorConfig",
    "FakeEvaluationProvider",
    "GatewayError",
    "ProviderRefusal",
    "ProviderResponse",
    "build_system_prompt",
    "build_user_prompt",
    "resolve_provider",
    "run_evaluation",
]

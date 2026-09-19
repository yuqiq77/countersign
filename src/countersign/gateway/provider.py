"""Provider protocol and errors for the thin model gateway."""

from dataclasses import dataclass
from typing import Protocol

from countersign.contracts.evaluation import ModelEvaluation


class GatewayError(Exception):
    """Base class for gateway failures."""


class ContractFailure(GatewayError):
    """The provider could not produce a contract-valid evaluation.

    Raised after the repair budget is exhausted; routes the run to
    quarantine, never to a business-state change.
    """


class ProviderRefusal(GatewayError):
    """The model declined the request (stop_reason == refusal)."""


@dataclass(frozen=True)
class ProviderResponse:
    """One contract-valid model response plus its usage metadata."""

    evaluation: ModelEvaluation
    model_id: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    # The vendor's server-side request id when the SDK exposes one: the
    # only evidence that a request reached the provider (None: fake backend).
    request_id: str | None = None


class EvaluationProvider(Protocol):
    """A model backend able to produce a typed evaluation."""

    def evaluate(self, *, system: str, user: str) -> ProviderResponse:
        """Run one evaluation; raise GatewayError subclasses on failure."""
        ...

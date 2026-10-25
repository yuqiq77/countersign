"""Provider protocol and errors for the thin model gateway."""

from dataclasses import dataclass, field
from typing import Protocol

from countersign.contracts.base import ContractModel
from countersign.contracts.discovery import SearchCandidates
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


class ProviderUnavailable(GatewayError):
    """A transport or service failure, not malformed model output."""


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


@dataclass(frozen=True)
class ParsedResponse[T: ContractModel]:
    """One contract-valid structured response of any contract type."""

    parsed: T
    model_id: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    request_id: str | None = None


@dataclass(frozen=True)
class SearchResponse:
    """One request of the search step (ADR-017) with what code checks.

    `parsed` is None while the model is paused (`stop_reason` "pause_turn");
    `content` then carries the turn so far, opaque, for the continuation.
    `results` pairs every result URL with the query that returned it.
    """

    parsed: SearchCandidates | None
    stop_reason: str | None
    queries: list[str]
    results: list[tuple[str, str]]
    searches: int
    model_id: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    request_id: str | None = None
    content: list[object] = field(default_factory=lambda: [])


class EvaluationProvider(Protocol):
    """A model backend able to produce a typed evaluation."""

    def evaluate(self, *, system: str, user: str) -> ProviderResponse:
        """Run one evaluation; raise GatewayError subclasses on failure."""
        ...


class StructuredProvider(Protocol):
    """A model backend able to fill any contract via structured output."""

    def parse[T: ContractModel](
        self, *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
    ) -> ParsedResponse[T]:
        """Return one instance of `output_type`; raise GatewayError subclasses on failure."""
        ...


class SearchProvider(Protocol):
    """A model backend able to run one request of the web search step."""

    def search(
        self, *, system: str, messages: list[dict[str, object]], max_uses: int
    ) -> SearchResponse:
        """Search with at most `max_uses` searches; 0 means no search at all."""
        ...


class ModelProvider(EvaluationProvider, StructuredProvider, SearchProvider, Protocol):
    """What the routes return: evaluation, the generic structured call and search."""

"""Provider protocol and errors for the thin model gateway."""

import os
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


def optional_env(name: str) -> str | None:
    """Read an environment variable, treating blank as unset.

    Compose passes `${VAR:-}` through as an empty string, and vendor SDKs
    treat an empty string as a value (an empty base URL fails every
    request). Providers read their optional settings through this helper;
    for a setting the SDK also resolves itself, they pair it with
    `clear_blank_env` so the SDK's own chain (environment, profile,
    default) still runs instead of seeing the blank.
    """

    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def clear_blank_env(name: str) -> None:
    """Remove `name` from the process environment when its value is blank.

    For a setting the vendor SDK resolves itself (environment, then its own
    profile files, then a default), a blank value would count as explicit
    and short-circuit that chain. Clearing it lets the SDK run the chain.
    """

    if name in os.environ and not os.environ[name].strip():
        del os.environ[name]

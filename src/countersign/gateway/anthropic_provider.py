"""Anthropic-backed evaluation provider.

Structured output via `client.messages.parse` with the Pydantic contract:
the API constrains generation to the schema, and the SDK validates the
parsed result client-side. The stable system prompt is cached (v4 §13.2).
"""

from countersign.contracts.evaluation import ModelEvaluation
from countersign.env import clear_blank_env
from countersign.gateway.provider import ContractFailure, ProviderRefusal, ProviderResponse

_MAX_TOKENS = 16000


def request_id_of(response: object) -> str | None:
    """The SDK's `_request_id` (documented on every response), or None."""

    value = getattr(response, "_request_id", None)
    return value if isinstance(value, str) else None


class AnthropicProvider:
    """Evaluation provider on the Anthropic API (one route, one model).

    The base URL follows the SDK's own chain — `base_url` argument, then
    `ANTHROPIC_BASE_URL`, then the CLI profile, then the default — with one
    correction: a blank `ANTHROPIC_BASE_URL` (Compose's `${VAR:-}`) would
    count as explicit inside the SDK and become an empty base URL, so it
    is cleared first. Credentials stay entirely with the SDK (API key,
    auth token, CLI profile).
    """

    def __init__(self, model_id: str, *, base_url: str | None = None) -> None:
        import anthropic

        clear_blank_env("ANTHROPIC_BASE_URL")
        self._client = anthropic.Anthropic(base_url=base_url)
        self._model_id = model_id

    @property
    def base_url(self) -> str:
        return str(self._client.base_url)

    def evaluate(self, *, system: str, user: str) -> ProviderResponse:
        response = self._client.messages.parse(
            model=self._model_id,
            max_tokens=_MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
            output_format=ModelEvaluation,
        )
        if response.stop_reason == "refusal":
            raise ProviderRefusal("model declined the evaluation request")
        parsed = response.parsed_output
        if parsed is None:
            raise ContractFailure(f"no contract-valid output (stop_reason={response.stop_reason})")
        usage = response.usage
        return ProviderResponse(
            evaluation=parsed,
            model_id=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            request_id=request_id_of(response),
        )

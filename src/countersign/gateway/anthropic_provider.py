"""Anthropic-backed evaluation provider.

Structured output via `client.messages.parse` with the Pydantic contract:
the API constrains generation to the schema, and the SDK validates the
parsed result client-side. The stable system prompt is cached (v4 §13.2).
"""

from countersign.contracts.evaluation import ModelEvaluation
from countersign.gateway.provider import ContractFailure, ProviderRefusal, ProviderResponse

_MAX_TOKENS = 16000


class AnthropicProvider:
    """Evaluation provider on the Anthropic API (one route, one model)."""

    def __init__(self, model_id: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic()
        self._model_id = model_id

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
            raise ContractFailure(
                f"no contract-valid output (stop_reason={response.stop_reason})"
            )
        usage = response.usage
        return ProviderResponse(
            evaluation=parsed,
            model_id=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        )

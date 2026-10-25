"""Anthropic-backed evaluation provider.

Structured output via `client.messages.parse` with the Pydantic contract:
the API constrains generation to the schema, and the SDK validates the
parsed result client-side. The stable system prompt is cached (v4 §13.2).
"""

from collections.abc import Sequence
from typing import Any, cast

from anthropic import APIError
from pydantic import ValidationError

from countersign.contracts.base import ContractModel
from countersign.contracts.discovery import SearchCandidates
from countersign.contracts.evaluation import ModelEvaluation
from countersign.env import clear_blank_env
from countersign.gateway.provider import (
    ContractFailure,
    ParsedResponse,
    ProviderRefusal,
    ProviderResponse,
    ProviderUnavailable,
    SearchResponse,
)

_MAX_TOKENS = 16000
# The provider's web search tool with dynamic result filtering (ADR-017).
_SEARCH_TOOL = "web_search_20260209"


def search_blocks(content: Sequence[object]) -> tuple[list[str], list[tuple[str, str]]]:
    """The queries a turn issued and each result URL with the query that returned it."""

    query_of: dict[str, str] = {}
    queries: list[str] = []
    results: list[tuple[str, str]] = []
    for block in content:
        kind = getattr(block, "type", None)
        if kind == "server_tool_use" and getattr(block, "name", None) == "web_search":
            raw = getattr(block, "input", None)
            query = str(cast(dict[str, Any], raw).get("query", "")) if isinstance(raw, dict) else ""
            query_of[str(getattr(block, "id", ""))] = query
            queries.append(query)
        elif kind == "web_search_tool_result":
            found = getattr(block, "content", None)
            if isinstance(found, list):
                query = query_of.get(str(getattr(block, "tool_use_id", "")), "")
                for item in cast(list[object], found):
                    url = getattr(item, "url", None)
                    if isinstance(url, str):
                        results.append((query, url))
    return queries, results


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

    def parse[T: ContractModel](
        self, *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
    ) -> ParsedResponse[T]:
        # The SDK validates the JSON against the contract inside parse();
        # a client-side validation error is a contract failure the caller
        # may repair once, not a transport error.
        client = (
            self._client
            if timeout_seconds is None
            else self._client.with_options(timeout=timeout_seconds, max_retries=0)
        )
        try:
            response = client.messages.parse(
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
                output_format=output_type,
            )
        except ValidationError as exc:
            raise ContractFailure(f"output failed contract validation: {exc}") from exc
        except APIError as exc:
            raise ProviderUnavailable(f"provider request failed: {exc}") from exc
        if response.stop_reason == "refusal":
            raise ProviderRefusal("model declined the request")
        parsed = response.parsed_output
        if parsed is None:
            raise ContractFailure(f"no contract-valid output (stop_reason={response.stop_reason})")
        usage = response.usage
        return ParsedResponse(
            parsed=parsed,
            model_id=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            request_id=request_id_of(response),
        )

    def search(
        self, *, system: str, messages: list[dict[str, object]], max_uses: int
    ) -> SearchResponse:
        # `max_uses` must be positive; with nothing left, tool choice `none`
        # keeps the tool defined (the turn may hold its blocks) and stops it.
        extra: dict[str, Any] = {} if max_uses > 0 else {"tool_choice": {"type": "none"}}
        # Sent once: an SDK retry after a timeout would send the same
        # `max_uses` again, though the provider may already have searched,
        # and nothing would count it (ticket 005).
        client = self._client.with_options(max_retries=0)
        try:
            response = client.messages.parse(
                model=self._model_id,
                max_tokens=_MAX_TOKENS,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=cast(Any, messages),
                tools=[{"type": _SEARCH_TOOL, "name": "web_search", "max_uses": max(max_uses, 1)}],
                output_format=SearchCandidates,
                **extra,
            )
        except ValidationError as exc:
            raise ContractFailure(f"output failed contract validation: {exc}") from exc
        except APIError as exc:
            raise ProviderUnavailable(f"provider request failed: {exc}") from exc
        if response.stop_reason == "refusal":
            raise ProviderRefusal("model declined the request")
        content = list(response.content)
        queries, results = search_blocks(content)
        usage = response.usage
        server = getattr(usage, "server_tool_use", None)
        # The count is absent on a response that made no search.
        searches = getattr(server, "web_search_requests", None) or 0
        # Paused, or ended without output: `parsed` is None and the caller
        # decides; either way the queries reach the caller to be stored. A
        # validation error inside `parse` loses them (ContractFailure above).
        parsed = None if response.stop_reason == "pause_turn" else response.parsed_output
        return SearchResponse(
            parsed=parsed,
            stop_reason=response.stop_reason,
            queries=queries,
            results=results,
            searches=int(searches),
            model_id=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            request_id=request_id_of(response),
            content=cast(list[object], content),
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
            request_id=response.request_id,
        )

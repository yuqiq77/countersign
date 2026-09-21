"""Provider configuration read from the environment (gateway boundary)."""

import json
from pathlib import Path

import anthropic
import pytest

from countersign.env import clear_blank_env, optional_env
from countersign.gateway.anthropic_provider import AnthropicProvider, request_id_of
from countersign.gateway.evaluator import resolve_provider
from countersign.gateway.fake_provider import FakeEvaluationProvider

_SDK_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")


def test_optional_env_treats_blank_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_OPTIONAL", raising=False)
    assert optional_env("X_OPTIONAL") is None
    monkeypatch.setenv("X_OPTIONAL", "")
    assert optional_env("X_OPTIONAL") is None
    monkeypatch.setenv("X_OPTIONAL", "  ")
    assert optional_env("X_OPTIONAL") is None
    monkeypatch.setenv("X_OPTIONAL", " value ")
    assert optional_env("X_OPTIONAL") == "value"


def test_clear_blank_env_only_removes_blanks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BLANK", " ")
    monkeypatch.setenv("X_SET", "v")
    clear_blank_env("X_BLANK")
    clear_blank_env("X_SET")
    clear_blank_env("X_ABSENT")
    assert optional_env("X_BLANK") is None
    assert optional_env("X_SET") == "v"


def test_blank_base_url_behaves_like_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose passes `${ANTHROPIC_BASE_URL:-}` through as an empty string.

    Inside the SDK an empty string counts as an explicit base URL and every
    request fails with a connection error. The provider must resolve exactly
    what the SDK resolves with the variable absent.
    """

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    expected = str(anthropic.Anthropic().base_url)

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    blank = AnthropicProvider("m", base_url=optional_env("ANTHROPIC_BASE_URL"))
    assert blank.base_url == expected

    relay = AnthropicProvider("m", base_url="http://relay.local:8081")
    assert relay.base_url.startswith("http://relay.local:8081")


def test_cli_profile_base_url_survives_blank_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CLI profile's base URL is the SDK's fallback when no explicit URL
    is given; the blank-env correction must not replace it with the default."""

    for name in _SDK_ENV:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "relay.json").write_text(
        json.dumps(
            {
                "authentication": {"type": "user_oauth"},
                "base_url": "https://profile-relay.example.test",
            }
        )
    )
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_PROFILE", "relay")

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    from_profile = AnthropicProvider("m", base_url=optional_env("ANTHROPIC_BASE_URL"))
    assert from_profile.base_url.startswith("https://profile-relay.example.test")

    # An explicit URL still wins over the profile. (Bound to a user_oauth
    # profile, the SDK insists on https or localhost for the token exchange.)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:8081")
    from_env = AnthropicProvider("m", base_url=optional_env("ANTHROPIC_BASE_URL"))
    assert from_env.base_url.startswith("http://localhost:8081")


def test_resolve_provider_reads_base_url_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COUNTERSIGN_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("COUNTERSIGN_MODEL_ID", "m")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://relay.local:8081")
    relay = resolve_provider()
    assert isinstance(relay, AnthropicProvider)
    assert relay.base_url.startswith("http://relay.local:8081")

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    assert resolve_provider() is not relay

    monkeypatch.setenv("COUNTERSIGN_MODEL_PROVIDER", "fake")
    assert isinstance(resolve_provider(), FakeEvaluationProvider)


def test_request_id_is_read_from_the_sdk_response() -> None:
    class _Response:
        _request_id = "req_123"

    class _NoId:
        pass

    assert request_id_of(_Response()) == "req_123"
    assert request_id_of(_NoId()) is None


def test_sdk_transport_failure_is_unavailable_and_bounded_without_hidden_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from countersign.contracts.page_reading import PageStep
    from countersign.gateway.provider import ProviderUnavailable

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    requests: list[httpx.Request] = []

    def fail(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ConnectError("offline", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as http_client:
        client = anthropic.Anthropic(api_key="test-key", http_client=http_client)
        provider = AnthropicProvider("test-model")
        monkeypatch.setattr(provider, "_client", client)
        with pytest.raises(ProviderUnavailable):
            provider.parse(system="test", user="test", output_type=PageStep, timeout_seconds=0.25)
    assert len(requests) == 1
    assert requests[0].extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 0.25
    )

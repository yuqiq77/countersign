"""Fetch hardening: DNS-rebinding pinning and streaming size limits."""

import ipaddress

import httpx
import pytest

import countersign.capabilities.http_fetch as http_fetch
from countersign.capabilities.http_fetch import FetchPolicyError, fetch_url

PUBLIC_IP = "93.184.216.34"
HTML_HEADERS = {"content-type": "text/html"}

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _static_resolver(ip: str):
    def resolve(host: str) -> list[_IPAddress]:
        return [ipaddress.ip_address(ip)]

    return resolve


def _map_resolver(ips: dict[str, str]):
    def resolve(host: str) -> list[_IPAddress]:
        return [ipaddress.ip_address(ips[host])]

    return resolve


def test_connection_is_pinned_to_the_vetted_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request must go to the address that passed validation — a resolver
    that answers differently on a second lookup can never redirect the
    connection, because there is no second lookup."""

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers=HTML_HEADERS, content=b"<p>Apply now</p>")

    monkeypatch.setattr(http_fetch, "_resolved_addresses", _static_resolver(PUBLIC_IP))
    result = fetch_url(
        "http://example.com/jobs/1", transport=httpx.MockTransport(handler)
    )
    assert result.http_status == 200
    assert seen[0].url.host == PUBLIC_IP
    assert seen[0].headers["host"] == "example.com"


def test_resolution_to_private_address_is_rejected_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests_made: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_made.append(request)
        return httpx.Response(200, headers=HTML_HEADERS, content=b"")

    monkeypatch.setattr(http_fetch, "_resolved_addresses", _static_resolver("10.0.0.8"))
    with pytest.raises(FetchPolicyError):
        fetch_url("http://example.com/jobs/1", transport=httpx.MockTransport(handler))
    assert requests_made == []


def test_redirect_hop_to_private_destination_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_fetch,
        "_resolved_addresses",
        _map_resolver({"example.com": PUBLIC_IP, "internal.example": "10.0.0.8"}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://internal.example/secret"}
        )

    with pytest.raises(FetchPolicyError):
        fetch_url("http://example.com/jobs/1", transport=httpx.MockTransport(handler))


def test_declared_oversize_content_length_aborts_before_reading() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html",
                "content-length": str(http_fetch.MAX_BODY_BYTES + 1),
            },
            stream=httpx.ByteStream(b"x"),
        )

    with pytest.raises(FetchPolicyError):
        fetch_url(
            "http://example.com/jobs/1",
            transport=httpx.MockTransport(handler),
            enforce_network_policy=False,
        )


class _ChunkStream(httpx.SyncByteStream):
    """A chunked body with no Content-Length header."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def __iter__(self):  # type: ignore[no-untyped-def]
        yield from self._chunks


def test_streamed_body_over_limit_aborts_mid_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http_fetch, "MAX_BODY_BYTES", 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=_ChunkStream([b"x" * 60, b"x" * 60, b"x" * 60]),
        )

    with pytest.raises(FetchPolicyError):
        fetch_url(
            "http://example.com/jobs/1",
            transport=httpx.MockTransport(handler),
            enforce_network_policy=False,
        )

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
    result = fetch_url("http://example.com/jobs/1", transport=httpx.MockTransport(handler))
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
        return httpx.Response(302, headers={"location": "http://internal.example/secret"})

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


def test_one_call_can_set_its_own_body_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ticket 003: an API with no per-job endpoint reads a whole board under
    its own limit; every other fetch keeps the module's."""

    monkeypatch.setattr(http_fetch, "MAX_BODY_BYTES", 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_ChunkStream([b"x" * 60, b"x" * 60, b"x" * 60]),
        )

    transport = httpx.MockTransport(handler)
    result = fetch_url(
        "http://example.com/board",
        transport=transport,
        enforce_network_policy=False,
        max_body_bytes=200,
    )
    assert len(result.body) == 180
    with pytest.raises(FetchPolicyError):
        fetch_url("http://example.com/board", transport=transport, enforce_network_policy=False)


def _cookie_site(seen: list[httpx.Request]):
    """A site whose application form sits behind a cookie-setting 302."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/entry":
            return httpx.Response(
                302,
                headers={
                    "location": "/token",
                    "set-cookie": "sess=abc; Domain=example.com; Path=/",
                },
            )
        if request.url.path == "/token":
            if "sess=abc" in request.headers.get("cookie", ""):
                return httpx.Response(200, headers=HTML_HEADERS, content=b"<form>apply</form>")
            return httpx.Response(200, headers=HTML_HEADERS, content=b"<p>not found</p>")
        return httpx.Response(
            200, headers=HTML_HEADERS, content=request.headers.get("cookie", "").encode()
        )

    return handler


def test_session_cookie_survives_a_pinned_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live finding: an ATS answered a cookie-setting 302 with a token URL
    that only works with the cookie. The connection stays pinned to the
    vetted address; the cookie is keyed by the hostname the hop is about."""

    seen: list[httpx.Request] = []
    monkeypatch.setattr(http_fetch, "_resolved_addresses", _static_resolver(PUBLIC_IP))
    result = fetch_url(
        "http://example.com/entry", transport=httpx.MockTransport(_cookie_site(seen))
    )
    assert result.body == b"<form>apply</form>"
    assert result.redirect_chain == ["http://example.com/entry"]
    assert [r.url.host for r in seen] == [PUBLIC_IP, PUBLIC_IP]
    assert seen[1].headers["host"] == "example.com"
    assert seen[1].headers["cookie"] == "sess=abc"


def test_cookie_for_another_domain_is_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/entry":
            return httpx.Response(
                302,
                headers={"location": "/token", "set-cookie": "sess=abc; Domain=other.example"},
            )
        return httpx.Response(
            200, headers=HTML_HEADERS, content=request.headers.get("cookie", "").encode()
        )

    monkeypatch.setattr(http_fetch, "_resolved_addresses", _static_resolver(PUBLIC_IP))
    result = fetch_url("http://example.com/entry", transport=httpx.MockTransport(handler))
    assert result.body == b""


def test_cookies_do_not_carry_across_fetches_on_the_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The long-lived client's own jar must never become a session store:
    it would key cookies by the pinned address and outlive the fetch."""

    seen: list[httpx.Request] = []
    shared = http_fetch.client_for(httpx.MockTransport(_cookie_site(seen)))

    def client_for(transport: httpx.BaseTransport | None) -> httpx.Client:
        return shared

    monkeypatch.setattr(http_fetch, "client_for", client_for)
    monkeypatch.setattr(http_fetch, "_resolved_addresses", _static_resolver(PUBLIC_IP))
    first = fetch_url("http://example.com/entry")
    second = fetch_url("http://example.com/echo")
    assert first.body == b"<form>apply</form>"
    assert second.body == b""  # nothing from the first fetch was sent
    assert len(shared.cookies) == 0


def test_concurrent_fetches_to_one_address_never_share_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reproduction, made deterministic: two sites on one pinned
    address, fetched from two threads through the shared client. Case A's
    302 sets a session cookie; case B's request is forced into the moment
    right after A's response was received and before A's own jar stored
    it. Whatever the client's jar did with A's cookie, B must never see it."""

    import threading

    seen: dict[str, str | None] = {}
    a_response_received = threading.Event()
    b_requested = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        if host == "a.example" and request.url.path == "/entry":
            return httpx.Response(
                302, headers={"location": "/token", "set-cookie": "session=case_a; Path=/"}
            )
        seen[host] = request.headers.get("cookie")
        return httpx.Response(200, headers=HTML_HEADERS, content=host.encode())

    shared = http_fetch.client_for(httpx.MockTransport(handler))

    def shared_client(transport: httpx.BaseTransport | None) -> httpx.Client:
        return shared

    monkeypatch.setattr(http_fetch, "client_for", shared_client)
    monkeypatch.setattr(
        http_fetch,
        "_resolved_addresses",
        _map_resolver({"a.example": PUBLIC_IP, "b.example": PUBLIC_IP}),
    )
    original_store = http_fetch.FetchCookies.store

    def store_then_wait(
        self: http_fetch.FetchCookies, url: httpx.URL, headers: httpx.Headers
    ) -> None:
        original_store(self, url, headers)
        if url.host == "a.example" and not a_response_received.is_set():
            a_response_received.set()
            assert b_requested.wait(timeout=10)

    monkeypatch.setattr(http_fetch.FetchCookies, "store", store_then_wait)

    results: dict[str, bytes] = {}

    def fetch(name: str, url: str) -> None:
        results[name] = fetch_url(url).body

    thread_a = threading.Thread(target=fetch, args=("a", "http://a.example/entry"))
    thread_a.start()
    assert a_response_received.wait(timeout=10)
    fetch("b", "http://b.example/page")
    b_requested.set()
    thread_a.join(timeout=10)

    assert seen["b.example"] is None  # never case A's session
    assert seen["a.example"] == "session=case_a"  # A's own hop still carries it
    assert results == {"a": b"a.example", "b": b"b.example"}

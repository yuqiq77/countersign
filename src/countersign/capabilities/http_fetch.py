"""SSRF-guarded HTTP fetch (v4 §15 network controls).

Blocks private, loopback, link-local, and metadata destinations; pins each
connection to the address that passed validation, closing the DNS-rebinding
TOCTOU between validation and connection; streams responses so the size
limit aborts early; validates every redirect hop. External content is data,
never instructions.
"""

import ipaddress
import socket
import time
from dataclasses import dataclass

import httpx

MAX_BODY_BYTES = 2_000_000
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 15.0
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_CONTENT_PREFIXES = ("text/html", "text/plain", "application/json", "application/xhtml")

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class FetchPolicyError(Exception):
    """A policy violation (SSRF, scheme, size). Non-retryable by design."""


@dataclass(frozen=True)
class FetchResult:
    """The observed response for one fetch, including its redirect chain."""

    requested_url: str
    final_url: str
    http_status: int
    content_type: str
    body: bytes
    redirect_chain: list[str]


_shared_client: httpx.Client | None = None


def _client_for(transport: httpx.BaseTransport | None) -> httpx.Client:
    """One long-lived client per worker process; ephemeral when tests inject
    a transport (httpx transports are client-level, not per-request)."""

    if transport is not None:
        return httpx.Client(follow_redirects=False, timeout=TIMEOUT_SECONDS, transport=transport)
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.Client(follow_redirects=False, timeout=TIMEOUT_SECONDS)
    return _shared_client


def _resolved_addresses(host: str) -> list[_IPAddress]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return [literal]
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchPolicyError(f"cannot resolve host {host!r}") from exc
    addresses: list[_IPAddress] = []
    for info in infos:
        raw = info[4][0]
        if isinstance(raw, str):
            addresses.append(ipaddress.ip_address(raw))
    return addresses


def _validate_address(address: _IPAddress) -> None:
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise FetchPolicyError(f"destination {address} is not a public address")


def _vetted_address(url: httpx.URL) -> _IPAddress:
    """Resolve the URL's host once and validate every returned address."""

    if url.scheme not in _ALLOWED_SCHEMES:
        raise FetchPolicyError(f"scheme {url.scheme!r} is not allowed")
    if not url.host:
        raise FetchPolicyError("URL has no host")
    addresses = _resolved_addresses(url.host)
    if not addresses:
        raise FetchPolicyError(f"host {url.host!r} resolved to no addresses")
    for address in addresses:
        _validate_address(address)
    return addresses[0]


def vetted_addresses(url: str) -> list[str]:
    """Every address the host resolves to, all validated, IPv4 first.

    Chromium's host-resolver rules accept IPv4 literals reliably; callers
    that build such rules take the first entry.
    """

    host = httpx.URL(url).host
    addresses = _resolved_addresses(host)
    for address in addresses:
        _validate_address(address)
    ordered = sorted(addresses, key=lambda a: a.version)  # 4 before 6
    return [str(a) for a in ordered]


def assert_url_allowed(url: str) -> None:
    """Reject URLs whose scheme or destination violates network policy."""

    _vetted_address(httpx.URL(url))


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise httpx.TimeoutException("fetch budget exhausted")
    return min(TIMEOUT_SECONDS, remaining)


def _read_limited(response: httpx.Response, deadline: float) -> bytes:
    """Stream the body, aborting as soon as the size limit is exceeded."""

    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise FetchPolicyError(f"declared content-length exceeds {MAX_BODY_BYTES} bytes")
        except ValueError:
            pass
    total = 0
    chunks: list[bytes] = []
    for chunk in response.iter_bytes():
        _remaining(deadline)
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise FetchPolicyError(f"response body exceeds {MAX_BODY_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_url(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    enforce_network_policy: bool = True,
    timeout_seconds: float = TIMEOUT_SECONDS,
) -> FetchResult:
    """Fetch a URL with per-hop validation pinned to the vetted address.

    Each hop resolves the hostname once, validates all returned addresses,
    then connects to the vetted address directly — the original hostname
    travels in the Host header and TLS SNI, so a resolver that answers
    differently on a second lookup (DNS rebinding) never redirects the
    connection. `transport` and `enforce_network_policy` exist for tests.
    """

    deadline = time.monotonic() + timeout_seconds
    chain: list[str] = []
    current = url
    client = _client_for(transport)
    for _ in range(MAX_REDIRECTS + 1):
        logical = httpx.URL(current)
        if enforce_network_policy:
            pinned = _vetted_address(logical)
            pinned_host = f"[{pinned}]" if pinned.version == 6 else str(pinned)
            target = logical.copy_with(host=pinned_host)
            host_header = logical.netloc.decode("ascii")
            headers = {"User-Agent": "countersign/0.1", "Host": host_header}
            extensions: dict[str, object] = (
                {"sni_hostname": logical.host} if logical.scheme == "https" else {}
            )
        else:
            target = logical
            headers = {"User-Agent": "countersign/0.1"}
            extensions = {}
        with client.stream(
            "GET", target, headers=headers, extensions=extensions, timeout=_remaining(deadline)
        ) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise FetchPolicyError("redirect without a Location header")
                chain.append(current)
                current = str(logical.join(location))
                continue
            body = _read_limited(response, deadline)
            content_type = response.headers.get("content-type", "application/octet-stream")
            base_type = content_type.split(";", 1)[0].strip().lower()
            if response.status_code < 400 and not base_type.startswith(_ALLOWED_CONTENT_PREFIXES):
                raise FetchPolicyError(f"content type {base_type!r} is not allowed")
            return FetchResult(
                requested_url=url,
                final_url=current,
                http_status=response.status_code,
                content_type=base_type,
                body=body,
                redirect_chain=chain,
            )
    raise FetchPolicyError(f"more than {MAX_REDIRECTS} redirects")

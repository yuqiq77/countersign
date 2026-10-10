"""SSRF-guarded HTTP fetch (v4 §15 network controls).

Blocks private, loopback, link-local, and metadata destinations; pins each
connection to the address that passed validation, closing the DNS-rebinding
TOCTOU between validation and connection; streams responses so the size
limit aborts early; validates every redirect hop. External content is data,
never instructions.
"""

import http.cookiejar
import ipaddress
import socket
import time
import urllib.request
from dataclasses import dataclass
from email.message import Message

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


class _NoCookieJar(http.cookiejar.CookieJar):
    """A jar the shared client cannot use: it neither stores nor sends.

    httpx stores every response's cookies in the client's jar and adds
    them to every later request, keyed by the transport host — here a
    pinned address that several sites may share — and it does so from
    whichever thread is fetching. With Activities running concurrently,
    one case's session cookie could be sent to another case's site on the
    same address. Clearing the jar around each hop only narrows that
    window; a jar that does nothing closes it.
    """

    def add_cookie_header(self, request: object) -> None:
        return None

    def extract_cookies(self, response: object, request: object) -> None:
        return None


class _CookieHeaders:
    """The response-side view `http.cookiejar` needs: just the headers."""

    def __init__(self, headers: httpx.Headers) -> None:
        self._message = Message()
        for name, value in headers.multi_items():
            self._message.add_header(name, value)

    def info(self) -> Message:
        return self._message


class FetchCookies:
    """A cookie jar scoped to one fetch and keyed by the *logical* URL.

    Connections are pinned to a vetted address, so the transport-level
    request names an IP literal; a jar keyed by that would reject a
    `Domain=` cookie and could hand one site's cookie to another site on
    the same address. Keying by the hostname the redirect chain is really
    about keeps a session across hops (an application form behind a
    cookie-setting 302, for example) without any of that, and a jar per
    fetch means nothing carries over between cases or threads.

    Known laxness, accepted for now: Python's cookiejar sends a host-only
    cookie (no `Domain=`) to subdomains of the setting host as well.
    """

    def __init__(self) -> None:
        self._jar = http.cookiejar.CookieJar()

    def header_for(self, url: httpx.URL) -> str | None:
        request = urllib.request.Request(str(url))
        self._jar.add_cookie_header(request)
        return request.get_header("Cookie")

    def store(self, url: httpx.URL, headers: httpx.Headers) -> None:
        request = urllib.request.Request(str(url))
        self._jar.extract_cookies(_CookieHeaders(headers), request)  # type: ignore[arg-type]


def client_for(transport: httpx.BaseTransport | None) -> httpx.Client:
    """One long-lived client per worker process; ephemeral when tests inject
    a transport (httpx transports are client-level, not per-request)."""

    if transport is not None:
        return httpx.Client(
            follow_redirects=False,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            cookies=_NoCookieJar(),
        )
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.Client(
            follow_redirects=False, timeout=TIMEOUT_SECONDS, cookies=_NoCookieJar()
        )
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


def _read_limited(response: httpx.Response, deadline: float, limit: int) -> bytes:
    """Stream the body, aborting as soon as the size limit is exceeded."""

    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise FetchPolicyError(f"declared content-length exceeds {limit} bytes")
        except ValueError:
            pass
    total = 0
    chunks: list[bytes] = []
    for chunk in response.iter_bytes():
        _remaining(deadline)
        total += len(chunk)
        if total > limit:
            raise FetchPolicyError(f"response body exceeds {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_url(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    enforce_network_policy: bool = True,
    timeout_seconds: float = TIMEOUT_SECONDS,
    max_body_bytes: int | None = None,
) -> FetchResult:
    """Fetch a URL with per-hop validation pinned to the vetted address.

    Each hop resolves the hostname once, validates all returned addresses,
    then connects to the vetted address directly — the original hostname
    travels in the Host header and TLS SNI, so a resolver that answers
    differently on a second lookup (DNS rebinding) never redirects the
    connection. `max_body_bytes` sets the size limit for one call (an ATS
    API with no per-job endpoint, ticket 003); None is `MAX_BODY_BYTES`.
    `transport` and `enforce_network_policy` exist for tests.
    """

    deadline = time.monotonic() + timeout_seconds
    chain: list[str] = []
    current = url
    client = client_for(transport)
    cookies = FetchCookies()
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
        cookie_header = cookies.header_for(logical)
        if cookie_header:
            headers["Cookie"] = cookie_header
        with client.stream(
            "GET", target, headers=headers, extensions=extensions, timeout=_remaining(deadline)
        ) as response:
            cookies.store(logical, response.headers)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise FetchPolicyError("redirect without a Location header")
                chain.append(current)
                current = str(logical.join(location))
                continue
            body = _read_limited(
                response, deadline, MAX_BODY_BYTES if max_body_bytes is None else max_body_bytes
            )
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

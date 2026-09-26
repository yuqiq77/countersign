"""Optional read-only Chromium observation, with real navigation metadata.

The main document may land only on the page host and its subdomains;
resources (scripts, data, images, sub-frames) may load from the hosts the
fetched HTML itself names (ADR-015). Every host resolves to a vetted
public address, GET/HEAD only. A missing browser or blocked dependency
leaves rendering unavailable; it never becomes an invented HTTP 200.
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Response, Route, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from countersign.capabilities.http_fetch import (
    MAX_BODY_BYTES,
    FetchPolicyError,
    FetchResult,
    assert_url_allowed,
    vetted_addresses,
)
from countersign.env import optional_env

_MAX_NAMED_HOSTS = 10
# Hosts the fetched HTML names, in attributes or in JSON-escaped inline
# configuration ("https:\/\/cdn.example").
_HOST_IN_BODY = re.compile(rb"https?:(?:\\/|/){2}([a-z0-9.-]+\.[a-z]{2,})", re.I)
CHROMIUM_PATH_VAR = "COUNTERSIGN_CHROMIUM_PATH"
RENDER_TIMEOUT_SECONDS = 30.0


class RenderError(Exception):
    """A browser/navigation failure that the reader can record and stop exploring."""


def chromium_binary() -> Path | None:
    configured = optional_env(CHROMIUM_PATH_VAR)
    if configured is None:
        return None
    path = Path(configured)
    return path if path.is_file() else None


@dataclass(frozen=True)
class RenderHosts:
    """Where one render may go: a pinned address for every permitted host,
    and the subset the main document may navigate to."""

    addresses: dict[str, str]
    navigation: frozenset[str]


def render_hosts(url: str, body: bytes, deadline: float) -> RenderHosts:
    """Navigation: the page host and its subdomains. Resources: those plus
    every host the fetched HTML names, in document order up to a cap.

    The page's own references are the allowlist: no registrable-domain
    guess and no vendor list. Resolution is synchronous and has no timeout
    of its own, so named hosts are resolved only while budget remains; the
    page host is always tried.
    """
    base = httpx.URL(url).host.lower()
    named = [base]
    for match in _HOST_IN_BODY.finditer(body):
        host = match.group(1).decode("ascii").lower()
        if host not in named:
            named.append(host)
        if len(named) > _MAX_NAMED_HOSTS:
            break
    addresses: dict[str, str] = {}
    for host in named:
        if host != base and time.monotonic() >= deadline:
            break
        try:
            found = vetted_addresses(f"https://{host}/")
            if found:
                addresses[host] = found[0] if ":" not in found[0] else f"[{found[0]}]"
        except FetchPolicyError:
            if host == base:
                raise
    navigation = frozenset(h for h in addresses if h == base or h.endswith(f".{base}"))
    return RenderHosts(addresses=addresses, navigation=navigation)


def guard_route(route: Route, hosts: RenderHosts) -> None:
    """Main-document navigations stay on the navigation hosts; sub-frames and
    subresources may use any permitted host. GET/HEAD only, http(s) only."""
    request = route.request
    target = httpx.URL(request.url)
    main_document = request.is_navigation_request()
    if main_document:
        try:
            main_document = request.frame.parent_frame is None
        except PlaywrightError:
            pass  # frame unknown: keep the stricter rule
    permitted = hosts.navigation if main_document else hosts.addresses
    if (
        target.scheme in ("http", "https")
        and target.host in permitted
        and request.method in ("GET", "HEAD")
    ):
        route.continue_()
    else:
        route.abort()


def render_url(
    url: str, body: bytes = b"", timeout_seconds: float = RENDER_TIMEOUT_SECONDS
) -> FetchResult | None:
    binary = chromium_binary()
    if binary is None:
        return None
    deadline = time.monotonic() + min(timeout_seconds, RENDER_TIMEOUT_SECONDS)

    def remaining_ms() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RenderError("render budget exhausted")
        return remaining * 1000

    assert_url_allowed(url)
    hosts = render_hosts(url, body, deadline)
    rules = ", ".join([*(f"MAP {h} {ip}" for h, ip in hosts.addresses.items()), "MAP * ~NOTFOUND"])

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(binary),
                timeout=remaining_ms(),
                args=[f"--host-resolver-rules={rules}", "--no-proxy-server"],
            )
            try:
                context = browser.new_context(service_workers="block", accept_downloads=False)
                context.route("**/*", lambda route: guard_route(route, hosts))
                context.route_web_socket("**/*", lambda socket: socket.close())
                page = context.new_page()
                responses: list[Response] = []

                def observed(response: Response) -> None:
                    if (
                        response.request.is_navigation_request()
                        and response.frame == page.main_frame
                    ):
                        responses.append(response)

                page.on("response", observed)
                page.goto(url, wait_until="domcontentloaded", timeout=remaining_ms())
                try:
                    page.wait_for_load_state("networkidle", timeout=min(2_000, remaining_ms()))
                except PlaywrightTimeout:
                    pass  # Background polling is not a reason to lose an observed DOM.
                remaining_ms()
                rendered = page.content().encode("utf-8")
                final_url = page.url
                remaining_ms()
                final_host = httpx.URL(final_url).host.lower()
                if final_host not in hosts.navigation:
                    # A script or redirect tried to move the main document
                    # off the page host family; the guard aborted it and the
                    # browser shows an error page, which is no observation.
                    raise RenderError(f"main document left the page host: {final_host}")
                if (
                    not responses
                    or responses[-1].url.split("#", 1)[0] != final_url.split("#", 1)[0]
                ):
                    raise RenderError("no response metadata for the final document")
                if len(rendered) > MAX_BODY_BYTES:
                    raise RenderError("rendered document exceeds the body limit")
                return FetchResult(
                    requested_url=url,
                    final_url=final_url,
                    http_status=responses[-1].status,
                    content_type="text/html",
                    body=rendered,
                    redirect_chain=[r.url for r in responses[:-1]],
                )
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise RenderError(f"browser failed: {exc}") from exc

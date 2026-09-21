"""Optional read-only Chromium observation, with real navigation metadata.

Only the page host and its referenced subdomains may resolve, pinned to
vetted public addresses. A missing browser or blocked dependency leaves
rendering unavailable; it never becomes an invented HTTP 200.
"""

import re
import time
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

_MAX_SUBDOMAINS = 10
_HOST_IN_BODY = re.compile(rb"https?://([a-z0-9.-]+\.[a-z]{2,})", re.I)
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


def _allowed_hosts(url: str, body: bytes, deadline: float) -> dict[str, str]:
    """Exact host plus observed subdomains; no guessed registrable-domain boundary.

    Resolution is synchronous and has no timeout of its own, so subdomains
    are resolved only while budget remains; the page host is always tried.
    """
    base = httpx.URL(url).host.lower()
    hosts = [base]
    for match in _HOST_IN_BODY.finditer(body):
        host = match.group(1).decode("ascii").lower()
        if host.endswith(f".{base}") and host not in hosts:
            hosts.append(host)
        if len(hosts) > _MAX_SUBDOMAINS:
            break
    allowed: dict[str, str] = {}
    for host in hosts:
        if host != base and time.monotonic() >= deadline:
            break
        try:
            addresses = vetted_addresses(f"https://{host}/")
            if addresses:
                address = addresses[0]
                allowed[host] = address if ":" not in address else f"[{address}]"
        except FetchPolicyError:
            if host == base:
                raise
    return allowed


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
    allowed = _allowed_hosts(url, body, deadline)
    rules = ", ".join([*(f"MAP {h} {ip}" for h, ip in allowed.items()), "MAP * ~NOTFOUND"])

    def guard(route: Route) -> None:
        request = route.request
        target = httpx.URL(request.url)
        if (
            target.scheme in ("http", "https")
            and target.host in allowed
            and request.method in ("GET", "HEAD")
        ):
            route.continue_()
        else:
            route.abort()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(binary),
                timeout=remaining_ms(),
                args=[f"--host-resolver-rules={rules}", "--no-proxy-server"],
            )
            try:
                context = browser.new_context(service_workers="block", accept_downloads=False)
                context.route("**/*", guard)
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

"""Render network policy (ADR-015): the main document stays on the page host
and its subdomains; resources may load from hosts the fetched HTML names."""

import time
from typing import cast

import pytest
from playwright.sync_api import Route

import countersign.capabilities.render as renderer
from countersign.capabilities.http_fetch import FetchPolicyError

PAGE = "https://jobs.page.example/j/1"


def _resolver(monkeypatch: pytest.MonkeyPatch, private: frozenset[str] = frozenset()) -> None:
    def vetted(url: str) -> list[str]:
        host = url.split("/")[2]
        if host in private:
            raise FetchPolicyError(f"{host} is not public")
        return ["93.184.216.34"]

    monkeypatch.setattr(renderer, "vetted_addresses", vetted)


def test_named_hosts_may_serve_resources_but_only_the_page_host_family_navigates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolver(monkeypatch)
    body = (
        b'<link rel="icon" href="https://cdn.vendor.example/icon.svg">'
        b'<meta property="og:image" content="https://app.page.example/img.png">'
        b'<script src="https://assets.jobs.page.example/app.js"></script>'
        b'<script>window.cfg = {"api": "https:\\/\\/api.vendor.example\\/v1"};</script>'
        b'<a href="https://other.example/x">x</a>'
    )
    hosts = renderer.render_hosts(PAGE, body, time.monotonic() + 10)
    assert set(hosts.addresses) == {
        "jobs.page.example",
        "cdn.vendor.example",
        "app.page.example",  # a sibling of the page host: resource only
        "assets.jobs.page.example",
        "api.vendor.example",  # named in JSON-escaped inline configuration
        "other.example",
    }
    assert hosts.navigation == frozenset({"jobs.page.example", "assets.jobs.page.example"})


def test_named_hosts_are_capped_in_document_order_and_private_ones_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolver(monkeypatch, private=frozenset({"h3.example"}))
    body = b"".join(f'<img src="https://h{i}.example/p.png">'.encode() for i in range(14))
    hosts = renderer.render_hosts(PAGE, body, time.monotonic() + 10)
    assert "jobs.page.example" in hosts.addresses
    named = [h for h in hosts.addresses if h != "jobs.page.example"]
    assert named == [f"h{i}.example" for i in range(10) if i != 3]
    with pytest.raises(FetchPolicyError):  # the page host itself must be public
        renderer.render_hosts("https://h3.example/j", b"", time.monotonic() + 10)


def test_named_hosts_are_not_resolved_past_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolver(monkeypatch)
    body = b'<script src="https://cdn.vendor.example/app.js"></script>'
    hosts = renderer.render_hosts(PAGE, body, time.monotonic() - 1)
    assert set(hosts.addresses) == {"jobs.page.example"}


class _Frame:
    def __init__(self, parent: object | None) -> None:
        self.parent_frame = parent


class _Request:
    def __init__(
        self, url: str, *, method: str = "GET", navigation: bool = False, top: bool = True
    ) -> None:
        self.url = url
        self.method = method
        self._navigation = navigation
        self.frame = _Frame(None if top else object())

    def is_navigation_request(self) -> bool:
        return self._navigation


class _Route:
    def __init__(self, request: _Request) -> None:
        self.request = request
        self.decision = ""

    def continue_(self) -> None:
        self.decision = "continue"

    def abort(self) -> None:
        self.decision = "abort"


@pytest.mark.parametrize(
    ("request_", "decision"),
    [
        (_Request("https://jobs.page.example/j/1", navigation=True), "continue"),
        (_Request("https://assets.jobs.page.example/next", navigation=True), "continue"),
        (_Request("https://cdn.vendor.example/landing", navigation=True), "abort"),
        (_Request("https://cdn.vendor.example/app.js"), "continue"),
        (_Request("https://cdn.vendor.example/frame", navigation=True, top=False), "continue"),
        (_Request("https://unnamed.example/beacon.gif"), "abort"),
        (_Request("https://cdn.vendor.example/api", method="POST"), "abort"),
        (_Request("ftp://cdn.vendor.example/x"), "abort"),
    ],
)
def test_guard_separates_main_document_navigation_from_resource_loading(
    request_: _Request, decision: str
) -> None:
    hosts = renderer.RenderHosts(
        addresses={
            "jobs.page.example": "93.184.216.34",
            "assets.jobs.page.example": "93.184.216.34",
            "cdn.vendor.example": "93.184.216.35",
        },
        navigation=frozenset({"jobs.page.example", "assets.jobs.page.example"}),
    )
    route = _Route(request_)
    renderer.guard_route(cast(Route, route), hosts)
    assert route.decision == decision, request_.url

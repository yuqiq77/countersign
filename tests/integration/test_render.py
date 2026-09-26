"""Real browser regression, using an isolated local server and no external network."""

from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

import countersign.capabilities.render as renderer

pytestmark = pytest.mark.skipif(
    renderer.chromium_binary() is None, reason="no test Chromium configured"
)


@pytest.fixture
def render_server(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[str, list[str]]]:
    visited: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            visited.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/error")
                self.end_headers()
                return
            port = self.headers.get("Host", "").rsplit(":", 1)[-1]
            if self.path == "/inject.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                self.wfile.write(
                    b"document.body.insertAdjacentHTML('beforeend',"
                    b" '<a href=\"/apply\">Apply now</a>');"
                )
                return
            self.send_response(503 if self.path == "/error" else 200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if self.path in ("/spa", "/hop"):
                self.wfile.write(_page(self.path, port))
                return
            self.wfile.write(b"""<h1>Engineer</h1><button>Apply</button><script>
              fetch('/write', {method: 'POST'}).catch(() => {});
              fetch('http://127.0.0.1:' + location.port + '/private').catch(() => {});
            </script>""")

        def do_POST(self) -> None:
            visited.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Only this test transport may reach loopback. Production address vetting
    # remains covered by the fetch security tests; browser route checks stay on.
    def allow_local(url: str) -> None:
        pass

    def local_address(url: str) -> list[str]:
        return ["127.0.0.1"]

    monkeypatch.setattr(renderer, "assert_url_allowed", allow_local)
    monkeypatch.setattr(renderer, "vetted_addresses", local_address)
    try:
        yield f"http://render.example.test:{server.server_port}", visited
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


ASSETS = "assets.example.test"  # a different host, not a subdomain of the page host


def _page(path: str, port: str) -> bytes:
    """The two ADR-015 pages: one loads its apply control from a script on
    another named host; one tries to move the main document there."""

    if path == "/spa":
        return f'<h1>Engineer</h1><script src="http://{ASSETS}:{port}/inject.js"></script>'.encode()
    return (
        f"<h1>Engineer</h1><script>location.href = 'http://{ASSETS}:{port}/landed';</script>"
    ).encode()


def test_resources_may_load_from_a_host_the_page_names(
    render_server: tuple[str, list[str]],
) -> None:
    base, visited = render_server
    port = base.rsplit(":", 1)[-1]
    result = renderer.render_url(f"{base}/spa", body=_page("/spa", port), timeout_seconds=10)
    assert result is not None
    assert b"Apply now" in result.body
    assert visited == ["/spa", "/inject.js"]


def test_main_document_may_not_navigate_to_a_resource_host(
    render_server: tuple[str, list[str]],
) -> None:
    base, visited = render_server
    port = base.rsplit(":", 1)[-1]
    with pytest.raises(renderer.RenderError, match="left the page host"):
        renderer.render_url(f"{base}/hop", body=_page("/hop", port), timeout_seconds=10)
    assert "/landed" not in visited  # aborted by the guard before any request left


def test_render_preserves_redirect_status_and_blocks_unapproved_requests(
    render_server: tuple[str, list[str]],
) -> None:
    base, visited = render_server
    result = renderer.render_url(f"{base}/start", timeout_seconds=10)
    assert result is not None
    assert result.requested_url == f"{base}/start"
    assert result.final_url == f"{base}/error"
    assert result.http_status == 503
    assert result.redirect_chain == [f"{base}/start"]
    assert b"<button>Apply</button>" in result.body
    assert visited == ["/start", "/error"]

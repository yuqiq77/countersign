"""Deterministic HTML normalization and authority classification."""

from countersign.capabilities.normalize import classify_authority, normalize_html
from countersign.contracts.enums import SourceAuthority

HTML = b"""
<html>
  <head>
    <title>Senior Engineer - ExampleCorp</title>
    <meta property="og:site_name" content="ExampleCorp" />
    <style>body { color: red; }</style>
    <script>alert("ignore me")</script>
  </head>
  <body>
    <h1>Senior Engineer</h1>
    <p>Build reliable systems.</p>
    <a href="/apply">Apply now</a>
  </body>
</html>
"""


def test_extracts_title_site_and_text() -> None:
    result = normalize_html(HTML)
    assert result.title == "Senior Engineer - ExampleCorp"
    assert result.company_hint == "ExampleCorp"
    assert "Build reliable systems." in result.text
    assert "alert" not in result.text
    assert "color: red" not in result.text


def test_marker_detection() -> None:
    result = normalize_html(HTML)
    assert result.apply_markers_found
    assert not result.closed_markers_found

    closed = normalize_html(b"<p>This job is no longer accepting applications.</p>")
    assert closed.closed_markers_found


def test_normalization_is_deterministic() -> None:
    assert normalize_html(HTML).content_hash == normalize_html(HTML).content_hash


def test_authority_classification() -> None:
    assert (
        classify_authority("https://boards.greenhouse.io/acme/jobs/1")
        == SourceAuthority.OFFICIAL_ATS
    )
    assert (
        classify_authority("https://jobs.lever.co/acme/1") == SourceAuthority.OFFICIAL_ATS
    )
    assert (
        classify_authority("https://www.linkedin.com/jobs/view/1")
        == SourceAuthority.DISCOVERY_ONLY
    )
    assert classify_authority("https://acme.com/about") == SourceAuthority.COMPANY_CONTROLLED


def test_unknown_hosts_never_reach_official_authority() -> None:
    """A careers-looking path or subdomain proves nothing about the host.

    OFFICIAL_* gates the apply-now framing, so an unknown host must never
    be elevated by its own URL shape — that would let any third-party page
    with '/jobs/...' and an apply marker bypass the anti-fraud gate.
    """

    for url in (
        "https://random-blog.example/jobs/fake-posting",
        "https://random-blog.example/careers/1",
        "https://careers.random-blog.example/openings/1",
        "https://jobs.random-blog.example/1",
        "https://acme.com/careers/roles/1",
    ):
        assert classify_authority(url) == SourceAuthority.COMPANY_CONTROLLED, url

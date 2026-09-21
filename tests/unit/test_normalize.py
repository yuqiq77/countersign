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


def test_normalization_is_deterministic() -> None:
    assert normalize_html(HTML).content_hash == normalize_html(HTML).content_hash


def test_authority_classification() -> None:
    assert (
        classify_authority("https://boards.greenhouse.io/acme/jobs/1")
        == SourceAuthority.OFFICIAL_ATS
    )
    assert classify_authority("https://jobs.lever.co/acme/1") == SourceAuthority.OFFICIAL_ATS
    assert (
        classify_authority("https://www.linkedin.com/jobs/view/1") == SourceAuthority.DISCOVERY_ONLY
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


def test_og_title_wins_over_placeholder_title_tag() -> None:
    """An ATS page was observed serving `<title>page_title</title>` on one
    response and the real title a second later; `og:title` was right both
    times, so it takes precedence. The text hash is unaffected either way."""

    placeholder = b"""
    <html><head>
      <title>page_title</title>
      <meta property="og:title" content="Research Engineer" />
    </head><body><p>Build reliable systems.</p></body></html>
    """
    real = placeholder.replace(b"page_title", b"Job Application for Research Engineer")
    assert normalize_html(placeholder).title == "Research Engineer"
    assert normalize_html(real).title == "Research Engineer"
    assert normalize_html(placeholder).content_hash == normalize_html(real).content_hash
    assert normalize_html(HTML).title == "Senior Engineer - ExampleCorp"


_LDJSON_PAGE = b"""
<html><head>
  <title>Member of Technical Staff</title>
  <script type="application/ld+json">
  {"@context": "https://schema.org", "@type": "JobPosting",
   "title": "Member of Technical Staff",
   "hiringOrganization": {"@type": "Organization", "name": "Example Labs"},
   "description": "<p>Build evaluation infrastructure.</p><ul><li>Python</li></ul>\
<p>Apply now.</p>",
   "directApply": true}
  </script>
  <script>window.__APP__ = {};</script>
</head><body><div id="root"></div></body></html>
"""


def _ldjson_page(script: str) -> bytes:
    return (
        b'<html><head><script type="application/ld+json">'
        + script.encode()
        + b"</script></head><body></body></html>"
    )


def test_json_ld_job_posting_is_the_fallback_for_client_rendered_pages() -> None:
    result = normalize_html(_LDJSON_PAGE)
    assert result.text_source == "json-ld"
    assert "Build evaluation infrastructure." in result.text
    assert "Python" in result.text
    assert "window.__APP__" not in result.text
    assert result.title == "Member of Technical Staff"
    assert result.company_hint == "Example Labs"
    assert normalize_html(_LDJSON_PAGE).content_hash == result.content_hash


def test_visible_text_wins_over_json_ld_and_is_never_merged() -> None:
    page = _LDJSON_PAGE.replace(b'<div id="root"></div>', b"<p>Visible body only.</p>")
    result = normalize_html(page)
    assert result.text_source == "html"
    assert result.text == "Visible body only."


def test_json_ld_edge_cases_leave_the_text_empty() -> None:
    malformed = normalize_html(_ldjson_page("{not json"))
    assert malformed.text_source == "none" and malformed.text == ""
    other_type = normalize_html(_ldjson_page('{"@type": "Organization", "name": "X"}'))
    assert other_type.text == ""
    nested = normalize_html(
        _ldjson_page(
            '{"@graph": [{"@type": "WebPage"},'
            ' {"@type": ["JobPosting"], "description": "Nested posting."}]}'
        )
    )
    assert nested.text == "Nested posting." and nested.text_source == "json-ld"

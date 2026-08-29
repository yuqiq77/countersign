"""SSRF network policy: private, loopback, and metadata destinations."""

import pytest

from countersign.capabilities.http_fetch import FetchPolicyError, assert_url_allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.0.0.8/internal",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
    ],
)
def test_blocked_destinations(url: str) -> None:
    with pytest.raises(FetchPolicyError):
        assert_url_allowed(url)


def test_public_literal_address_is_allowed() -> None:
    assert_url_allowed("https://8.8.8.8/jobs")


def test_url_without_host_is_blocked() -> None:
    with pytest.raises(FetchPolicyError):
        assert_url_allowed("https:///nohost")

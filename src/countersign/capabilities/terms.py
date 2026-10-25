"""Hosts whose terms forbid automated access (ADR-017 decision 5).

Code, like the ATS host list, and apart from authority classification: an
entry says nothing about how official a page is, only that the system does
not start a case there on its own and that the reader neither renders nor
follows on or onto it. Matched on the host and its subdomains.
"""

from countersign.capabilities.normalize import host_of

# Host -> why it is listed.
RESTRICTED_HOSTS: dict[str, str] = {
    "linkedin.com": "its user agreement prohibits bots and other automated access",
}


def restricted_host(url: str) -> str | None:
    """The listed host a URL is on, or None."""

    host = host_of(url)
    for listed in RESTRICTED_HOSTS:
        if host == listed or host.endswith("." + listed):
            return listed
    return None


def refusal(url: str) -> str | None:
    """Why the system does not act on this URL on its own, or None."""

    listed = restricted_host(url)
    return None if listed is None else f"{listed}: {RESTRICTED_HOSTS[listed]}"

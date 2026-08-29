"""Capability registry seed: governed external calls (fetch, normalize).

Every external call passes through this package — one chokepoint for
network policy today; the full registry (schemas, budgets, audit policy)
lands in Slice 2.
"""

from countersign.capabilities.http_fetch import (
    FetchPolicyError,
    FetchResult,
    assert_url_allowed,
    fetch_url,
)
from countersign.capabilities.normalize import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    Normalized,
    classify_authority,
    normalize_html,
)

__all__ = [
    "EXTRACTOR_NAME",
    "EXTRACTOR_VERSION",
    "FetchPolicyError",
    "FetchResult",
    "Normalized",
    "assert_url_allowed",
    "classify_authority",
    "fetch_url",
    "normalize_html",
]

"""ATS adapters: an application system's own API as an official observation (ticket 003),
and its board listing as the watch's source of new postings (ticket 004)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx

from countersign.capabilities.http_fetch import FetchPolicyError, FetchResult
from countersign.capabilities.normalize import host_of


@dataclass(frozen=True)
class ListedPosting:
    """One posting as a board's API lists it: what the watch filters and orders by."""

    url: str
    title: str
    # Non-empty structured location texts; empty when the posting names none.
    locations: tuple[str, ...]
    # The vendor's latest publication time (Ashby `publishedAt`, Greenhouse `updated_at`).
    published_at: datetime


@dataclass(frozen=True)
class BoardListing:
    """One read of a board: its outcome note and, when it was read, the postings."""

    note: str
    postings: list[ListedPosting] | None = None


def fetch_board(
    url: str, *, api_host: str, fetch: Callable[[str], FetchResult]
) -> FetchResult | str:
    """A board API's 200 response from its own host, or why it is unavailable."""

    try:
        response = fetch(url)
    except (FetchPolicyError, httpx.HTTPError) as exc:
        # A body over the cap is a policy error: unavailable, not truncated.
        return f"unavailable ({str(exc) or type(exc).__name__})"
    if response.http_status != 200:
        return f"unavailable (http {response.http_status})"
    landed = host_of(response.final_url)
    if landed != api_host:
        # What it says is the API host's, not a redirect target's.
        return f"unavailable (redirected to {landed})"
    return response

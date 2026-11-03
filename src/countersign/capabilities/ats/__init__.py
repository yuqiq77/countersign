"""ATS adapters: an application system's own API as an official observation (ticket 003),
and its board listing as the watch's source of new postings (ticket 004)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import unquote

import httpx

from countersign.capabilities.http_fetch import FetchPolicyError, FetchResult
from countersign.capabilities.normalize import host_of

# Every adapter's extractor name starts so.
ADAPTER_PREFIX = "ats-adapter/"


def is_api_body(extractor_name: str) -> bool:
    """Whether a stored body is an ATS API's response, made by an adapter,
    rather than a page (ticket 006)."""

    return extractor_name.startswith(ADAPTER_PREFIX)


def leaves_segment(board: str) -> bool:
    """Whether a board named in a posting URL would leave its one segment
    of an API path: a dot segment or a slash, encoded or not."""

    name = unquote(board)
    return name in (".", "..") or "/" in name or "\\" in name


@dataclass(frozen=True)
class ListedPosting:
    """One posting as a board's API lists it: what the watch filters and orders by."""

    url: str
    title: str
    # Non-empty structured location texts; empty when the posting names none.
    locations: tuple[str, ...]
    # The vendor's latest publication time (Ashby `publishedAt`, Greenhouse `updated_at`).
    published_at: datetime
    # Listing facts for the coarse screen (ADR-017), where the API gives them:
    # Ashby's department, team and the head of its plain description.
    department: str | None = None
    team: str | None = None
    excerpt: str | None = None


@dataclass(frozen=True)
class BoardListing:
    """One read of a board: its outcome note and, when it was read, the postings."""

    note: str
    postings: list[ListedPosting] | None = None


@dataclass(frozen=True)
class Unavailable:
    """Why a board API's answer cannot be used, and the HTTP status the API
    host itself answered, when that is why."""

    note: str
    status: int | None = None


def fetch_board(
    url: str, *, api_host: str, fetch: Callable[[str], FetchResult]
) -> FetchResult | Unavailable:
    """A board API's 200 response from its own host, or why it is unavailable."""

    try:
        response = fetch(url)
    except (FetchPolicyError, httpx.HTTPError) as exc:
        # A body over the cap is a policy error: unavailable, not truncated.
        return Unavailable(f"unavailable ({str(exc) or type(exc).__name__})")
    landed = host_of(response.final_url)
    if response.http_status != 200:
        own = response.http_status if landed == api_host else None
        return Unavailable(f"unavailable (http {response.http_status})", own)
    if landed != api_host:
        # What it says is the API host's, not a redirect target's.
        return Unavailable(f"unavailable (redirected to {landed})")
    return response

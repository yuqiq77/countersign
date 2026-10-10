"""Ashby's public posting API as the official ATS observation (ticket 003).

A posting at `jobs.ashbyhq.com/<board>/<job-id>` is looked up among its
board's published postings with one GET; the API has no per-job endpoint,
so the whole board is read. When the board lists the job with an apply
link, that response becomes an OFFICIAL_ATS observation whose one control
is the link, and verification needs no model. Every other outcome is a
note on the reading, and the page reader runs as it always has. A posting
missing from a healthy board is closure evidence only with a second
observation (ADR-005); this adapter never closes on absence.

Terms policy (v4 §12, ADR-005): the job-board API is public and needs no
credentials; Ashby documents it for populating a careers page
(developers.ashbyhq.com/docs/public-job-posting-api), and that page states
no further terms or rate limits. One GET per verification through the
SSRF-guarded fetcher; nothing polls it.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from countersign.capabilities.http_fetch import FetchPolicyError, FetchResult
from countersign.capabilities.normalize import host_of
from countersign.capabilities.page_evidence import Observation
from countersign.contracts.enums import ObservationHow, SourceAuthority
from countersign.contracts.page_reading import PageControl

BOARD_HOST = "jobs.ashbyhq.com"
API_HOST = "api.ashbyhq.com"
# A board of 67 postings was 2.0 MB. A larger board is unavailable, never
# a truncated parse.
MAX_BOARD_BYTES = 8_000_000
ADAPTER_NAME = "ats-adapter/ashby"
ADAPTER_VERSION = "0.1.0"
# Stands where a reader's prompt version would on the reading it makes.
READING_VERSION = f"{ADAPTER_NAME}/{ADAPTER_VERSION}"


class _Posting(BaseModel):
    """The fields of one published posting this adapter uses.

    Ashby leaves out a field whose data is missing, so each is optional
    here and checked where it decides something.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    job_url: str | None = Field(default=None, alias="jobUrl")
    apply_url: str | None = Field(default=None, alias="applyUrl")
    is_listed: bool | None = Field(default=None, alias="isListed")
    description_plain: str | None = Field(default=None, alias="descriptionPlain")


class _Board(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    jobs: list[_Posting]


@dataclass(frozen=True)
class AshbyLookup:
    """One API attempt: the note the reading records and, when the posting
    is published with an apply link, the observation that carries it."""

    note: str
    observation: Observation | None = None


def posting_ref(url: str) -> tuple[str, str] | None:
    """The (board, job id) an Ashby posting URL names, or None.

    Further path segments and the query are ignored. Any other host,
    including the API's own, names no posting.
    """

    if host_of(url) != BOARD_HOST:
        return None
    segments = [s for s in urlparse(url).path.split("/") if s]
    # The board becomes a segment of the API path; a dot segment, encoded
    # or not, would leave it.
    if len(segments) < 2 or unquote(segments[0]) in (".", ".."):
        return None
    return segments[0], segments[1]


def lookup(page_url: str, *, fetch: Callable[[str], FetchResult]) -> AshbyLookup | None:
    """Look the posting up among its board's published postings.

    None when `page_url` is not an Ashby posting. Otherwise the API never
    raises through here: unavailable, oversized and malformed responses are
    notes, and the caller reads the page instead.
    """

    ref = posting_ref(page_url)
    if ref is None:
        return None
    board, job_id = ref
    try:
        response = fetch(f"https://{API_HOST}/posting-api/job-board/{board}")
    except (FetchPolicyError, httpx.HTTPError) as exc:
        # A body over the cap is a policy error: unavailable, not truncated.
        return AshbyLookup(f"ashby: unavailable ({str(exc) or type(exc).__name__})")
    if response.http_status != 200:
        return AshbyLookup(f"ashby: unavailable (http {response.http_status})")
    landed = host_of(response.final_url)
    if landed != API_HOST:
        # The authority below is the API host's, not a redirect target's.
        return AshbyLookup(f"ashby: unavailable (redirected to {landed})")
    try:
        postings = _Board.model_validate_json(response.body).jobs
    except ValidationError:
        return AshbyLookup("ashby: unavailable (malformed response)")
    wanted = (board.lower(), job_id.lower())
    posting = next((p for p in postings if _names(p.job_url, wanted)), None)
    if posting is None:
        return AshbyLookup("ashby: not published")
    apply_url = posting.apply_url or ""
    if not apply_url.startswith(("http://", "https://")):
        return AshbyLookup("ashby: published without an apply link")
    # Unlisted means reachable by direct link only, which a pasted link is:
    # recorded, not judged.
    note = "ashby: published, unlisted" if posting.is_listed is False else "ashby: published"
    return AshbyLookup(note, _observation(response, apply_url, posting.description_plain or ""))


def _names(job_url: str | None, wanted: tuple[str, str]) -> bool:
    ref = posting_ref(job_url) if job_url else None
    return ref is not None and (ref[0].lower(), ref[1].lower()) == wanted


def _observation(response: FetchResult, apply_url: str, description: str) -> Observation:
    """Observation 1, after the fetched page: the board response as stored,
    with the posting's plain description as its text and its apply link as
    its one control."""

    return Observation(
        index=1,
        source_url=response.final_url,
        requested_url=response.requested_url,
        how=ObservationHow.API,
        authority=SourceAuthority.OFFICIAL_ATS,
        http_status=response.http_status,
        body=response.body,
        text=description,
        text_source="api",
        normalized_hash=hashlib.sha256(description.encode("utf-8")).hexdigest(),
        title=None,
        company_hint=None,
        controls=[PageControl(text="Apply", href=apply_url)],
        content_type=response.content_type,
        redirect_chain=response.redirect_chain,
        extractor_name=ADAPTER_NAME,
        extractor_version=ADAPTER_VERSION,
    )

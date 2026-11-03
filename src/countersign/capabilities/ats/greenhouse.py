"""Greenhouse's public Job Board API: the official ATS observation of a
hosted posting (ticket 006) and the watch's listing of a board (ticket 004).

A hosted posting URL anywhere on a fetch's way (requested, redirect hop or
final) is looked up with one GET of the per-job endpoint: a board with its
own careers page redirects its hosted URLs there and embeds the job in a
frame no render observes, so where the fetch started counts too. When the
API serves the job as an opening with a link, the response becomes an
OFFICIAL_ATS observation whose one control is that link, and verification
needs no model. Every other outcome is a note on the reading, and the page
reader runs as it always has. A 404 is noted apart, as "not served", but
nothing closes on it (ticket 006, decision 5).

`list_board` reads the list endpoint without descriptions for the watch,
skipping prospect posts.

Terms policy (v4 §12, ADR-005): the Job Board API's GET endpoints are
public and need no credentials; Greenhouse documents them for building a
custom careers page (docs.greenhouse.io/job-board.html). One GET per
verification or recheck, and one per watched board per scheduled run,
through the SSRF-guarded fetcher; nothing is written.
"""

import hashlib
import html
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, unquote, urlparse

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from countersign.capabilities.ats import (
    ADAPTER_PREFIX,
    BoardListing,
    ListedPosting,
    Unavailable,
    fetch_board,
    leaves_segment,
)
from countersign.capabilities.http_fetch import FetchResult
from countersign.capabilities.normalize import Normalized, host_of, normalize_html
from countersign.capabilities.page_evidence import Observation
from countersign.contracts.enums import ObservationHow, SourceAuthority
from countersign.contracts.page_reading import PageControl

API_HOST = "boards-api.greenhouse.io"
# The hosted posting page under its current and its legacy spelling; the
# legacy host redirects to the current one.
POSTING_HOST = "job-boards.greenhouse.io"
LEGACY_POSTING_HOST = "boards.greenhouse.io"
ADAPTER_NAME = f"{ADAPTER_PREFIX}greenhouse"
ADAPTER_VERSION = "0.1.0"
# Stands where a reader's prompt version would on the reading it makes.
READING_VERSION = f"{ADAPTER_NAME}/{ADAPTER_VERSION}"
PUBLISHED = "greenhouse: published"
NOT_SERVED = "greenhouse: not served (http 404)"


class _Location(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    name: str | None = None


class _Job(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: int
    title: str
    # Moved by republication and by edits; `first_published` never moves
    # on republication, so a posting back after a long gap would stay hidden.
    updated_at: AwareDatetime
    location: _Location | None = None
    # Null on a prospect post, a general-interest post with no job behind it.
    internal_job_id: int | None = None


class _Jobs(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    jobs: list[_Job]


class _Posting(BaseModel):
    """The fields of one job post this adapter uses.

    `internal_job_id` must be present, null on a prospect post; the rest
    may be left out and are checked where they decide something.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    id: int
    internal_job_id: int | None
    title: str
    company_name: str | None = None
    location: _Location | None = None
    content: str | None = None
    absolute_url: str | None = None
    application_deadline: AwareDatetime | None = None


@dataclass(frozen=True)
class GreenhouseLookup:
    """One API attempt: the note the reading records and, when the job is
    served as an opening with a link, the observation that carries it."""

    note: str
    observation: Observation | None = None


def posting_ref(url: str) -> tuple[str, str] | None:
    """The (board, job id) a hosted Greenhouse posting URL names, or None.

    `<host>/<board>/jobs/<id>` under either hosted spelling; the query and
    any further segments are ignored.
    """

    if host_of(url) not in (POSTING_HOST, LEGACY_POSTING_HOST):
        return None
    segments = [s for s in urlparse(url).path.split("/") if s]
    if len(segments) < 3 or segments[1] != "jobs":
        return None
    board, job_id = segments[0], segments[2]
    if leaves_segment(board) or not (job_id.isascii() and job_id.isdigit()):
        return None
    return board, job_id


def lookup(
    first: FetchResult, *, fetch: Callable[[str], FetchResult], now: datetime
) -> GreenhouseLookup | None:
    """Look the posting up in its board's Job Board API.

    None when no URL on the fetch's way is a hosted posting. Otherwise the
    API never raises through here: anything but a served opening is a note,
    and the caller reads the page instead.
    """

    ref = next(
        (
            found
            for url in (first.requested_url, *first.redirect_chain, first.final_url)
            if (found := posting_ref(url)) is not None
        ),
        None,
    )
    if ref is None:
        return None
    board, job_id = ref
    # The board's own name, quoted afresh: the URL's encoding never reaches the path.
    path = f"/v1/boards/{quote(unquote(board), safe='')}/jobs/{job_id}"
    response = fetch_board(f"https://{API_HOST}{path}", api_host=API_HOST, fetch=fetch)
    if isinstance(response, Unavailable):
        # A 404 from the API itself: taken down, unpublished or never there,
        # kept apart from errors.
        if response.status == 404:
            return GreenhouseLookup(NOT_SERVED)
        return GreenhouseLookup(f"greenhouse: {response.note}")
    try:
        posting = _Posting.model_validate_json(response.body)
    except ValidationError:
        return GreenhouseLookup("greenhouse: unavailable (malformed response)")
    if posting.id != int(job_id):
        return GreenhouseLookup("greenhouse: unavailable (another job)")
    if posting.internal_job_id is None:
        return GreenhouseLookup("greenhouse: prospect post")
    absolute_url = posting.absolute_url or ""
    if not absolute_url.startswith(("http://", "https://")):
        return GreenhouseLookup("greenhouse: published without an absolute URL")
    deadline = posting.application_deadline
    if deadline is not None and deadline < now:
        return GreenhouseLookup(f"greenhouse: application deadline passed ({deadline.isoformat()})")
    return GreenhouseLookup(PUBLISHED, _observation(response, posting, absolute_url))


def describe(body: bytes) -> Normalized:
    """The posting text of a stored per-job response (see `_described`)."""

    return _described(_Posting.model_validate_json(body))


def list_board(board: str, *, fetch: Callable[[str], FetchResult]) -> BoardListing:
    """Every job the board publishes, as hosted posting URLs: one GET.

    Prospect posts are skipped and counted in the note.
    """

    slug = quote(board, safe="")
    response = fetch_board(
        f"https://{API_HOST}/v1/boards/{slug}/jobs", api_host=API_HOST, fetch=fetch
    )
    if isinstance(response, Unavailable):
        return BoardListing(response.note)
    try:
        jobs = _Jobs.model_validate_json(response.body).jobs
    except ValidationError:
        return BoardListing("unavailable (malformed response)")
    # Null, not absent: a listing without the field drops nothing.
    openings = [
        job
        for job in jobs
        if "internal_job_id" not in job.model_fields_set or job.internal_job_id is not None
    ]
    postings = [
        ListedPosting(
            url=f"https://{POSTING_HOST}/{slug}/jobs/{job.id}",
            title=job.title,
            locations=_location_texts(job.location),
            published_at=job.updated_at,
        )
        for job in openings
    ]
    note = f"http 200, {len(postings)} postings"
    if len(openings) < len(jobs):
        note += f", {len(jobs) - len(openings)} prospect posts skipped"
    return BoardListing(note, postings)


def _location_texts(location: _Location | None) -> tuple[str, ...]:
    name = (location.name or "").strip() if location is not None else ""
    return (name,) if name else ()


def _described(posting: _Posting) -> Normalized:
    """The location name, then the visible text of `content`; empty when
    the description is. The API serves `content` as HTML entities once
    (checked on a live response, ticket 006), so it is unescaped once."""

    description = normalize_html(html.unescape(posting.content or "").encode()).text
    text = "\n".join((*_location_texts(posting.location), description)) if description else ""
    return Normalized(
        title=posting.title.strip() or None,
        company_hint=(posting.company_name or "").strip() or None,
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text_source="api",
    )


def _observation(response: FetchResult, posting: _Posting, absolute_url: str) -> Observation:
    """Observation 1, after the fetched page: the response as stored, with
    the posting text and its absolute URL as the one control."""

    described = _described(posting)
    return Observation(
        index=1,
        source_url=response.final_url,
        requested_url=response.requested_url,
        how=ObservationHow.API,
        authority=SourceAuthority.OFFICIAL_ATS,
        http_status=response.http_status,
        body=response.body,
        text=described.text,
        text_source="api",
        normalized_hash=described.content_hash,
        title=described.title,
        company_hint=described.company_hint,
        controls=[PageControl(text="Apply", href=absolute_url)],
        content_type=response.content_type,
        redirect_chain=response.redirect_chain,
        extractor_name=ADAPTER_NAME,
        extractor_version=ADAPTER_VERSION,
    )

"""Greenhouse's public Job Board API as the watch's listing of a board (ticket 004).

Discovery only: the list endpoint without descriptions, one GET per
watched board per scheduled run. Verification of a Greenhouse posting
stays with the page reader; the hosted posting page is an ATS host.

Terms policy (v4 §12, ADR-005): the Job Board API's GET endpoints are
public and need no credentials; Greenhouse documents them for building a
custom careers page (developers.greenhouse.io/job-board.html). Read
through the SSRF-guarded fetcher; nothing is written.
"""

from collections.abc import Callable
from urllib.parse import quote, urlparse

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from countersign.capabilities.ats import BoardListing, ListedPosting, fetch_board
from countersign.capabilities.http_fetch import FetchResult
from countersign.capabilities.normalize import host_of

API_HOST = "boards-api.greenhouse.io"
# The hosted posting page under its current and its legacy spelling; the
# legacy host redirects to the current one.
POSTING_HOST = "job-boards.greenhouse.io"
LEGACY_POSTING_HOST = "boards.greenhouse.io"


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


class _Jobs(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    jobs: list[_Job]


def posting_ref(url: str) -> tuple[str, str] | None:
    """The (board, job id) a hosted Greenhouse posting URL names, or None.

    `<host>/<board>/jobs/<id>` under either hosted spelling; the query and
    any further segments are ignored.
    """

    if host_of(url) not in (POSTING_HOST, LEGACY_POSTING_HOST):
        return None
    segments = [s for s in urlparse(url).path.split("/") if s]
    if len(segments) < 3 or segments[1] != "jobs" or not segments[2].isdigit():
        return None
    return segments[0], segments[2]


def list_board(board: str, *, fetch: Callable[[str], FetchResult]) -> BoardListing:
    """Every posting the board publishes, as hosted posting URLs: one GET."""

    slug = quote(board, safe="")
    response = fetch_board(
        f"https://{API_HOST}/v1/boards/{slug}/jobs", api_host=API_HOST, fetch=fetch
    )
    if isinstance(response, str):
        return BoardListing(response)
    try:
        jobs = _Jobs.model_validate_json(response.body).jobs
    except ValidationError:
        return BoardListing("unavailable (malformed response)")
    postings = [
        ListedPosting(
            url=f"https://{POSTING_HOST}/{slug}/jobs/{job.id}",
            title=job.title,
            locations=_location_texts(job),
            published_at=job.updated_at,
        )
        for job in jobs
    ]
    return BoardListing(f"http 200, {len(postings)} postings", postings)


def _location_texts(job: _Job) -> tuple[str, ...]:
    name = (job.location.name or "").strip() if job.location is not None else ""
    return (name,) if name else ()

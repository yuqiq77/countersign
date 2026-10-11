"""Ticket 004: Ashby and Greenhouse board listings, and the watch's identity of a posting."""

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from countersign.activities.watch_activities import case_id_for, posting_key
from countersign.capabilities.ats import BoardListing, ListedPosting, ashby, greenhouse
from countersign.capabilities.http_fetch import FetchResult, fetch_url

ASHBY_JOB = "4c1d7e2a-5b6f-4a8e-9d3c-2b1a0f9e8d7c"
GREENHOUSE_JOB = 8860302002

Handler = Callable[[httpx.Request], httpx.Response]


def _serving(body: bytes, status: int = 200) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"content-type": "application/json"}, content=body)

    return handler


def _api(handler: Handler, seen: list[str] | None = None) -> Callable[[str], FetchResult]:
    """The real fetcher over a fake transport."""

    def fetch(url: str) -> FetchResult:
        if seen is not None:
            seen.append(url)
        return fetch_url(url, transport=httpx.MockTransport(handler), enforce_network_policy=False)

    return fetch


def test_an_ashby_board_lists_each_posting_with_its_location_texts() -> None:
    body = {
        "apiVersion": "1",
        "jobs": [
            {
                "id": ASHBY_JOB,
                "title": "Platform Engineer",
                "location": "Remote - European Union",
                "secondaryLocations": [
                    {
                        "location": "Spain",
                        "address": {
                            "postalAddress": {
                                "addressRegion": "Spain",
                                "addressCountry": "Spain",
                                "addressLocality": "Spain",
                            }
                        },
                    }
                ],
                "publishedAt": "2024-03-04T14:29:08.532+00:00",
                "isListed": True,
                "isRemote": True,
                "address": {
                    "postalAddress": {"addressCountry": "European Union", "addressLocality": ""}
                },
                "jobUrl": f"https://jobs.ashbyhq.com/acme/{ASHBY_JOB}",
                "applyUrl": f"https://jobs.ashbyhq.com/acme/{ASHBY_JOB}/application",
                "descriptionPlain": "Build durable systems.",
            },
            {"id": "other", "title": "Recruiter", "publishedAt": "2026-10-01T09:00:00Z"},
        ],
    }
    seen: list[str] = []
    listing = ashby.list_board("acme", fetch=_api(_serving(json.dumps(body).encode()), seen))

    assert seen == ["https://api.ashbyhq.com/posting-api/job-board/acme"]
    assert listing == BoardListing(
        "http 200, 2 postings",
        [
            ListedPosting(
                url=f"https://jobs.ashbyhq.com/acme/{ASHBY_JOB}",
                title="Platform Engineer",
                locations=("Remote - European Union", "Spain", "European Union"),
                published_at=datetime(2024, 3, 4, 14, 29, 8, 532000, tzinfo=UTC),
            ),
            ListedPosting(
                url="https://jobs.ashbyhq.com/acme/other",
                title="Recruiter",
                locations=(),
                published_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
            ),
        ],
    )


def test_a_greenhouse_board_lists_hosted_posting_urls_by_update_time() -> None:
    def job(job_id: int, **location: object) -> dict[str, object]:
        return {
            "absolute_url": f"https://job-boards.greenhouse.io/globex/jobs/{job_id}",
            "id": job_id,
            "updated_at": "2026-10-02T11:31:50-04:00",
            "title": "Backend Engineer",
            "first_published": "2025-01-15T10:00:00-05:00",
            **location,
        }

    jobs = [job(GREENHOUSE_JOB, location={"name": "Remote, France"}), job(2, location=None), job(3)]
    body = json.dumps({"jobs": jobs, "meta": {"total": 3}}).encode()
    seen: list[str] = []
    listing = greenhouse.list_board("globex", fetch=_api(_serving(body), seen))

    assert seen == ["https://boards-api.greenhouse.io/v1/boards/globex/jobs"]
    assert listing.note == "http 200, 3 postings"
    assert listing.postings is not None
    assert listing.postings[0] == ListedPosting(
        url=f"https://job-boards.greenhouse.io/globex/jobs/{GREENHOUSE_JOB}",
        title="Backend Engineer",
        locations=("Remote, France",),
        published_at=datetime(2026, 10, 2, 15, 31, 50, tzinfo=UTC),
    )
    assert [p.locations for p in listing.postings[1:]] == [(), ()]


@pytest.mark.parametrize("list_board", [ashby.list_board, greenhouse.list_board])
@pytest.mark.parametrize(
    ("status", "body", "note"),
    [
        (404, b'{"message": "not found"}', "unavailable (http 404)"),
        (200, b"<html>maintenance</html>", "unavailable (malformed response)"),
    ],
)
def test_an_unreadable_board_is_only_a_note(
    list_board: Callable[..., BoardListing], status: int, body: bytes, note: str
) -> None:
    assert list_board("acme", fetch=_api(_serving(body, status))) == BoardListing(note)


@pytest.mark.parametrize(
    ("url", "ref"),
    [
        (f"https://boards.greenhouse.io/globex/jobs/{GREENHOUSE_JOB}", ("globex", "8860302002")),
        (
            f"https://job-boards.greenhouse.io/globex/jobs/{GREENHOUSE_JOB}?gh_src=feed",
            ("globex", "8860302002"),
        ),
        ("https://job-boards.greenhouse.io/globex", None),
        ("https://job-boards.greenhouse.io/globex/jobs/apply", None),
    ],
)
def test_a_hosted_greenhouse_url_names_its_board_and_job(
    url: str, ref: tuple[str, str] | None
) -> None:
    assert greenhouse.posting_ref(url) == ref


def test_one_posting_has_one_key_and_one_case_id() -> None:
    legacy = posting_key(f"https://boards.greenhouse.io/globex/jobs/{GREENHOUSE_JOB}")
    current = posting_key(f"https://job-boards.greenhouse.io/globex/jobs/{GREENHOUSE_JOB}?x=1")
    assert legacy == current == f"greenhouse:{GREENHOUSE_JOB}"
    upper = f"https://jobs.ashbyhq.com/Acme/{ASHBY_JOB.upper()}"
    assert posting_key(upper) == f"ashby:acme:{ASHBY_JOB}"

    case_id = case_id_for(f"greenhouse:{GREENHOUSE_JOB}")
    assert case_id == case_id_for(f"greenhouse:{GREENHOUSE_JOB}") == str(uuid.UUID(case_id))

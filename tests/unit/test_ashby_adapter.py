"""Ticket 003: the Ashby posting API as an official ATS observation."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from countersign.capabilities.ats import ashby
from countersign.capabilities.http_fetch import FetchResult, fetch_url
from countersign.contracts.enums import ObservationHow, SourceAuthority
from countersign.contracts.page_reading import PageControl

JOB_ID = "4c1d7e2a-5b6f-4a8e-9d3c-2b1a0f9e8d7c"
POSTING_URL = f"https://jobs.ashbyhq.com/acme/{JOB_ID}"
APPLY_URL = f"{POSTING_URL}/application"
BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/acme"
JSON_HEADERS = {"content-type": "application/json"}

Handler = Callable[[httpx.Request], httpx.Response]


def _posting(**fields: Any) -> dict[str, Any]:
    return {
        "id": JOB_ID,
        "title": "Platform Engineer",
        "jobUrl": POSTING_URL,
        "applyUrl": APPLY_URL,
        "isListed": True,
        "publishedAt": "2026-09-01T09:00:00.000+00:00",
        "descriptionHtml": "<p>Build durable systems.</p>",
        "descriptionPlain": "Build durable systems.",
        **fields,
    }


def _board(*postings: dict[str, Any]) -> bytes:
    other = _posting(
        id="other",
        jobUrl="https://jobs.ashbyhq.com/acme/other",
        applyUrl="https://jobs.ashbyhq.com/acme/other/application",
        descriptionPlain="Another role.",
    )
    return json.dumps({"apiVersion": "1", "jobs": [other, *postings]}).encode()


def _serving(body: bytes, status: int = 200) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=JSON_HEADERS, content=body)

    return handler


def _api(
    handler: Handler, seen: list[str] | None = None, *, max_body_bytes: int = ashby.MAX_BOARD_BYTES
) -> Callable[[str], FetchResult]:
    """The real fetcher over a fake transport, as `verify_status` calls it."""

    def fetch(url: str) -> FetchResult:
        if seen is not None:
            seen.append(url)
        return fetch_url(
            url,
            transport=httpx.MockTransport(handler),
            enforce_network_policy=False,
            max_body_bytes=max_body_bytes,
        )

    return fetch


@pytest.mark.parametrize(
    "url",
    [
        POSTING_URL,
        f"{POSTING_URL}/application?utm_source=newsletter",
        f"{POSTING_URL}/?departmentId=7#apply",
        f"https://JOBS.ashbyhq.com./acme/{JOB_ID}",
    ],
)
def test_a_posting_url_names_its_board_and_job(url: str) -> None:
    assert ashby.posting_ref(url) == ("acme", JOB_ID)


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.ashbyhq.com/acme",
        BOARD_URL,
        "https://boards.greenhouse.io/acme/jobs/1",
        f"https://jobs.ashbyhq.com.example/acme/{JOB_ID}",
        f"https://careers.acme.example/jobs.ashbyhq.com/acme/{JOB_ID}",
        f"https://jobs.ashbyhq.com/../acme/{JOB_ID}",
        f"https://jobs.ashbyhq.com/%2E%2E/{JOB_ID}",
    ],
)
def test_other_urls_never_reach_the_api(url: str) -> None:
    def no_fetch(url: str) -> FetchResult:
        raise AssertionError(f"fetched {url}")

    assert ashby.posting_ref(url) is None
    assert ashby.lookup(url, fetch=no_fetch) is None


@pytest.mark.parametrize(
    ("listed", "note"), [(True, "ashby: published"), (False, "ashby: published, unlisted")]
)
def test_a_published_posting_is_an_official_api_observation(listed: bool, note: str) -> None:
    """Listed or not, the API's own entry for the posting is the observation:
    an unlisted posting is reachable by direct link, as a pasted one is."""

    body = _board(_posting(isListed=listed))
    seen: list[str] = []
    result = ashby.lookup(f"{POSTING_URL}/application", fetch=_api(_serving(body), seen))

    assert seen == [BOARD_URL]
    assert result is not None and result.note == note
    obs = result.observation
    assert obs is not None
    assert (obs.index, obs.how, obs.authority) == (
        1,
        ObservationHow.API,
        SourceAuthority.OFFICIAL_ATS,
    )
    assert (obs.source_url, obs.http_status, obs.content_type) == (
        BOARD_URL,
        200,
        "application/json",
    )
    assert obs.body == body  # the response as fetched, stored as the snapshot
    assert obs.text == "Build durable systems."
    assert obs.controls == [PageControl(text="Apply", href=APPLY_URL)]
    assert (obs.extractor_name, obs.extractor_version) == (
        ashby.ADAPTER_NAME,
        ashby.ADAPTER_VERSION,
    )


def test_the_posting_matches_regardless_of_case() -> None:
    body = _board(_posting(jobUrl=f"https://jobs.ashbyhq.com/Acme/{JOB_ID.upper()}"))
    result = ashby.lookup(POSTING_URL, fetch=_api(_serving(body)))
    assert result is not None and result.note == "ashby: published"


def _redirect_elsewhere(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.ashbyhq.com":
        return httpx.Response(302, headers={"location": "https://mirror.example/board"})
    return httpx.Response(200, headers=JSON_HEADERS, content=_board(_posting()))


def _transport_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


def _silent_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("")


@pytest.mark.parametrize(
    ("handler", "note"),
    [
        (_serving(_board()), "ashby: not published"),
        (_serving(b'{"message": "not found"}', 404), "ashby: unavailable (http 404)"),
        (_serving(b"<html>maintenance</html>"), "ashby: unavailable (malformed response)"),
        (_serving(b'{"jobs": [{"isListed": "yes"}]}'), "ashby: unavailable (malformed response)"),
        (_serving(_board(_posting(applyUrl=""))), "ashby: published without an apply link"),
        (_transport_error, "ashby: unavailable (connection refused)"),
        (_silent_timeout, "ashby: unavailable (ReadTimeout)"),
        (_redirect_elsewhere, "ashby: unavailable (redirected to mirror.example)"),
    ],
)
def test_every_other_outcome_is_only_a_note(handler: Handler, note: str) -> None:
    result = ashby.lookup(POSTING_URL, fetch=_api(handler))
    assert result is not None
    assert (result.note, result.observation) == (note, None)


def test_a_board_over_the_cap_is_unavailable_never_truncated() -> None:
    body = _board(_posting())
    cap = len(body) - 1
    result = ashby.lookup(POSTING_URL, fetch=_api(_serving(body), max_body_bytes=cap))
    assert result is not None and result.observation is None
    assert result.note == f"ashby: unavailable (declared content-length exceeds {cap} bytes)"

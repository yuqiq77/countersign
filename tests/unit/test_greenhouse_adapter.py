"""Ticket 006: Greenhouse's Job Board API as an official ATS observation."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

import countersign.activities.case_activities as case_activities
from countersign.capabilities import ats
from countersign.capabilities.ats import Unavailable, ashby, fetch_board, greenhouse
from countersign.capabilities.http_fetch import FetchResult, fetch_url
from countersign.capabilities.normalize import EXTRACTOR_NAME
from countersign.contracts.enums import ObservationHow, SourceAuthority
from countersign.contracts.page_reading import PageControl
from countersign.diagnostics import replay_reading

JOB_ID = 4012345
POSTING_URL = f"https://job-boards.greenhouse.io/acme/jobs/{JOB_ID}"
JOB_API = f"https://boards-api.greenhouse.io/v1/boards/acme/jobs/{JOB_ID}"
CAREERS_URL = f"https://www.acme.example/careers?gh_jid={JOB_ID}"
NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)
JSON_HEADERS = {"content-type": "application/json"}
# The description's HTML as entities, once, as a live response showed. The
# second paragraph's text is a literal "<b>", which a second unescape would
# turn into a tag and drop.
CONTENT = (
    "&lt;p&gt;Build durable systems on Temporal.&lt;/p&gt;"
    "&lt;p&gt;Escape &amp;lt;b&amp;gt; in templates.&lt;/p&gt;"
)
TEXT = "Toronto\nBuild durable systems on Temporal.\nEscape <b> in templates."

Handler = Callable[[httpx.Request], httpx.Response]


def _job(**fields: Any) -> bytes:
    job: dict[str, Any] = {
        "id": JOB_ID,
        "internal_job_id": 2201,
        "title": "Platform Engineer",
        "company_name": "Acme",
        "location": {"name": "Toronto"},
        "content": CONTENT,
        "absolute_url": CAREERS_URL,
        "application_deadline": None,
        "first_published": "2026-10-20T09:00:00-04:00",
        "updated_at": "2026-10-21T09:00:00-04:00",
        **fields,
    }
    return json.dumps(job).encode()


def _serving(body: bytes, status: int = 200) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=JSON_HEADERS, content=body)

    return handler


def _api(handler: Handler, seen: list[str] | None = None) -> Callable[[str], FetchResult]:
    """The real fetcher over a fake transport, as `verify_status` calls it."""

    def fetch(url: str) -> FetchResult:
        if seen is not None:
            seen.append(url)
        return fetch_url(url, transport=httpx.MockTransport(handler), enforce_network_policy=False)

    return fetch


def _fetched(*urls: str) -> FetchResult:
    """The page fetch a lookup starts from: requested URL, hops, final URL."""

    return FetchResult(
        requested_url=urls[0],
        final_url=urls[-1],
        http_status=200,
        content_type="text/html",
        body=b"<html><body><h1>Careers</h1></body></html>",
        redirect_chain=list(urls[:-1]),
    )


@pytest.mark.parametrize(
    "url",
    [
        POSTING_URL,
        f"https://boards.greenhouse.io/acme/jobs/{JOB_ID}",
        f"{POSTING_URL}?gh_jid={JOB_ID}",  # as the search returned one live
        f"{POSTING_URL}/apply#app",
        f"https://JOB-BOARDS.greenhouse.io./acme/jobs/{JOB_ID}",
    ],
)
def test_a_hosted_posting_url_names_its_board_and_job(url: str) -> None:
    assert greenhouse.posting_ref(url) == ("acme", str(JOB_ID))


@pytest.mark.parametrize(
    "url",
    [
        CAREERS_URL,
        f"https://boards.greenhouse.io/embed/job_app?for=acme&token={JOB_ID}",
        JOB_API,
        f"https://job-boards.greenhouse.io/../jobs/{JOB_ID}",
        f"https://job-boards.greenhouse.io/%2E%2E/jobs/{JOB_ID}",
        # An encoded slash would carry the API path elsewhere on its host.
        f"https://job-boards.greenhouse.io/x%2F..%2F..%2Fv1%2Fboards%2Fother/jobs/{JOB_ID}",
        f"https://job-boards.greenhouse.io/x%5C..%5Cother/jobs/{JOB_ID}",
        "https://job-boards.greenhouse.io/acme/jobs/٤٠١",  # not ASCII digits
    ],
)
def test_other_urls_never_reach_the_api(url: str) -> None:
    def no_fetch(url: str) -> FetchResult:
        raise AssertionError(f"fetched {url}")

    assert greenhouse.posting_ref(url) is None
    assert greenhouse.lookup(_fetched(url), fetch=no_fetch, now=NOW) is None


@pytest.mark.parametrize(
    "urls",
    [
        (POSTING_URL,),
        (POSTING_URL, CAREERS_URL),
        ("https://go.acme.example/r/42", f"https://boards.greenhouse.io/acme/jobs/{JOB_ID}"),
        ("https://go.acme.example/r/42", POSTING_URL, CAREERS_URL),
    ],
)
def test_a_served_opening_is_an_official_api_observation(urls: tuple[str, ...]) -> None:
    """Wherever the hosted URL sits on the fetch's way, the API's job is the
    observation, its text the location and the unescaped description."""

    body = _job()
    seen: list[str] = []
    result = greenhouse.lookup(_fetched(*urls), fetch=_api(_serving(body), seen), now=NOW)

    assert seen == [JOB_API]
    assert result is not None and result.note == "greenhouse: published"
    obs = result.observation
    assert obs is not None
    assert (obs.index, obs.how, obs.authority) == (
        1,
        ObservationHow.API,
        SourceAuthority.OFFICIAL_ATS,
    )
    assert (obs.source_url, obs.http_status, obs.content_type) == (
        JOB_API,
        200,
        "application/json",
    )
    assert obs.body == body  # the response as fetched, stored as the snapshot
    assert (obs.text, obs.title, obs.company_hint) == (TEXT, "Platform Engineer", "Acme")
    assert obs.controls == [PageControl(text="Apply", href=CAREERS_URL)]
    assert (obs.extractor_name, obs.extractor_version) == (
        greenhouse.ADAPTER_NAME,
        greenhouse.ADAPTER_VERSION,
    )
    assert greenhouse.describe(body).text == TEXT  # the stored bytes give the same text


def test_the_api_path_carries_the_board_quoted_afresh() -> None:
    seen: list[str] = []
    fetched = _fetched(f"https://job-boards.greenhouse.io/ac:me/jobs/{JOB_ID}")
    greenhouse.lookup(fetched, fetch=_api(_serving(_job()), seen), now=NOW)
    assert seen == [f"https://boards-api.greenhouse.io/v1/boards/ac%3Ame/jobs/{JOB_ID}"]


def test_an_empty_description_or_a_future_deadline_still_publishes() -> None:
    body = _job(content="", application_deadline="2026-12-01T00:00:00Z")
    result = greenhouse.lookup(_fetched(POSTING_URL), fetch=_api(_serving(body)), now=NOW)
    assert result is not None and result.note == "greenhouse: published"
    assert result.observation is not None and result.observation.text == ""


def _redirect_elsewhere(request: httpx.Request) -> httpx.Response:
    if request.url.host == greenhouse.API_HOST:
        return httpx.Response(302, headers={"location": "https://mirror.example/job"})
    return httpx.Response(200, headers=JSON_HEADERS, content=_job())


def _transport_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


_WITHOUT_INTERNAL_ID = json.dumps(
    {k: v for k, v in json.loads(_job()).items() if k != "internal_job_id"}
).encode()


@pytest.mark.parametrize(
    ("handler", "note"),
    [
        (_serving(b'{"status": 404, "error": "Job not found"}', 404), greenhouse.NOT_SERVED),
        (_serving(b"upstream error", 503), "greenhouse: unavailable (http 503)"),
        (_serving(b"<html>maintenance</html>"), "greenhouse: unavailable (malformed response)"),
        (_serving(_WITHOUT_INTERNAL_ID), "greenhouse: unavailable (malformed response)"),
        (_serving(_job(id=1)), "greenhouse: unavailable (another job)"),
        (_serving(_job(internal_job_id=None)), "greenhouse: prospect post"),
        (_serving(_job(absolute_url=None)), "greenhouse: published without an absolute URL"),
        (
            _serving(_job(absolute_url="mailto:jobs@acme.example")),
            "greenhouse: published without an absolute URL",
        ),
        (
            _serving(_job(application_deadline="2026-11-01T23:59:00Z")),
            "greenhouse: application deadline passed (2026-11-01T23:59:00+00:00)",
        ),
        (_transport_error, "greenhouse: unavailable (connection refused)"),
        (_redirect_elsewhere, "greenhouse: unavailable (redirected to mirror.example)"),
    ],
)
def test_every_other_outcome_is_only_a_note(handler: Handler, note: str) -> None:
    result = greenhouse.lookup(_fetched(POSTING_URL, CAREERS_URL), fetch=_api(handler), now=NOW)
    assert result is not None
    assert (result.note, result.observation) == (note, None)


def _gone_elsewhere(request: httpx.Request) -> httpx.Response:
    if request.url.host == greenhouse.API_HOST:
        return httpx.Response(302, headers={"location": "https://mirror.example/job"})
    return httpx.Response(404, headers=JSON_HEADERS, content=b"{}")


def test_a_404_is_not_served_only_when_the_api_host_answers_it() -> None:
    """The status, not the note's wording, says the API did not serve the job."""

    assert fetch_board(
        JOB_API, api_host=greenhouse.API_HOST, fetch=_api(_serving(b"{}", 404))
    ) == Unavailable("unavailable (http 404)", 404)
    assert fetch_board(
        JOB_API, api_host=greenhouse.API_HOST, fetch=_api(_gone_elsewhere)
    ) == Unavailable("unavailable (http 404)", None)
    result = greenhouse.lookup(_fetched(POSTING_URL), fetch=_api(_gone_elsewhere), now=NOW)
    assert result is not None and result.note == "greenhouse: unavailable (http 404)"


def test_replay_and_the_verify_path_share_one_api_body_predicate() -> None:
    assert replay_reading.is_api_body is case_activities.is_api_body is ats.is_api_body
    assert ats.is_api_body(greenhouse.ADAPTER_NAME) and ats.is_api_body(ashby.ADAPTER_NAME)
    assert not ats.is_api_body(EXTRACTOR_NAME)


def test_the_listing_skips_prospect_posts_and_counts_them() -> None:
    def job(job_id: int, **fields: object) -> dict[str, object]:
        return {
            "id": job_id,
            "title": "Backend Engineer",
            "updated_at": "2026-10-02T11:31:50-04:00",
            **fields,
        }

    jobs = [job(1, internal_job_id=11), job(2, internal_job_id=None), job(3)]
    body = json.dumps({"jobs": jobs}).encode()
    listing = greenhouse.list_board("acme", fetch=_api(_serving(body)))

    assert listing.note == "http 200, 2 postings, 1 prospect posts skipped"
    assert listing.postings is not None
    # A listing without the field has nothing to skip by.
    assert [p.url for p in listing.postings] == [
        "https://job-boards.greenhouse.io/acme/jobs/1",
        "https://job-boards.greenhouse.io/acme/jobs/3",
    ]

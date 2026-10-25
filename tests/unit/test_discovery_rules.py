"""Pure rules of profile-driven discovery (ADR-017): turns, staleness, URL
identity, the URL-in-results check, the terms list and the brief's limits."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from countersign.capabilities.terms import restricted_host
from countersign.contracts.discovery import SearchBrief
from countersign.contracts.enums import CandidateSource
from countersign.domain.discovery import (
    VerdictInputs,
    listing_hash,
    take_turns,
    url_in_results,
    url_key,
    verdict_is_stale,
)

BOARD, SEARCH = CandidateSource.BOARD, CandidateSource.SEARCH


def test_turns_alternate_from_the_first_source_and_the_rest_continue_alone() -> None:
    assert list(take_turns(BOARD, ["b1", "b2", "b3"], ["s1"])) == [
        (BOARD, "b1"),
        (SEARCH, "s1"),
        (BOARD, "b2"),
        (BOARD, "b3"),
    ]
    assert list(take_turns(SEARCH, ["b1"], ["s1", "s2"])) == [
        (SEARCH, "s1"),
        (BOARD, "b1"),
        (SEARCH, "s2"),
    ]
    none: list[str] = []
    assert list(take_turns(BOARD, none, ["s1", "s2"])) == [(SEARCH, "s1"), (SEARCH, "s2")]
    assert list(take_turns(BOARD, none, none)) == []


INPUTS = VerdictInputs("abcd1234", "discovery-screen/0.1.0", "fake", "0123456789abcdef")


@pytest.mark.parametrize(
    "changed",
    [
        INPUTS._replace(profile_fingerprint="ffff0000"),
        INPUTS._replace(prompt_version="discovery-screen/0.2.0"),
        INPUTS._replace(route="anthropic:claude-sonnet-5-5"),
        INPUTS._replace(listing_hash="fedcba9876543210"),
    ],
)
def test_a_verdict_is_stale_when_any_of_its_inputs_changes(changed: VerdictInputs) -> None:
    assert verdict_is_stale(INPUTS, changed)
    assert not verdict_is_stale(INPUTS, INPUTS)
    assert verdict_is_stale(None, INPUTS)


def test_the_listing_hash_reads_the_title_and_a_boards_publication_time() -> None:
    naive = datetime(2026, 10, 20, 7, 0)
    aware = naive.replace(tzinfo=UTC)
    assert listing_hash(" Platform Engineer ", aware) == listing_hash("platform engineer", naive)
    assert listing_hash("Platform Engineer", aware) != listing_hash("Platform Engineer", None)
    assert listing_hash("Platform Engineer", None) != listing_hash("Backend Engineer", None)


def test_a_url_key_drops_tracking_and_keeps_the_parameters_that_name_a_posting() -> None:
    assert (
        url_key("HTTPS://Careers.Example.com/jobs/?utm_source=x&gh_jid=42&b=2&gclid=y#apply")
        == "url:https://careers.example.com/jobs?b=2&gh_jid=42"
    )
    assert url_key("https://careers.example.com/jobs/7/") == url_key(
        "https://careers.example.com/jobs/7?utm_medium=feed"
    )
    assert url_key("https://careers.example.com/jobs?id=1") != url_key(
        "https://careers.example.com/jobs?id=2"
    )


def test_a_candidate_url_must_appear_among_the_results() -> None:
    results = ["https://jobs.ashbyhq.com/acme/1?utm_source=search", "https://example.com/a/"]
    assert url_in_results("https://JOBS.ashbyhq.com/acme/1", results)
    assert url_in_results("https://example.com/a", results)
    assert not url_in_results("https://jobs.ashbyhq.com/acme/2", results)
    assert not url_in_results("http://example.com/a", results)


@pytest.mark.parametrize(
    ("url", "listed"),
    [
        ("https://www.linkedin.com/jobs/view/1", "linkedin.com"),
        ("https://ch.linkedin.com/jobs/view/1", "linkedin.com"),
        ("https://linkedin.com/", "linkedin.com"),
        ("https://notlinkedin.com/jobs", None),
        ("https://linkedin.com.example.org/jobs", None),
    ],
)
def test_the_terms_list_matches_a_host_and_its_subdomains(url: str, listed: str | None) -> None:
    assert restricted_host(url) == listed


def test_the_brief_holds_short_entries_only() -> None:
    lists: dict[str, object] = {
        name: [] for name in ("role_families", "skills", "regions", "languages", "exclude_roles")
    }
    SearchBrief.model_validate({**lists, "skills": ["python"] * 12})
    with pytest.raises(ValidationError):
        SearchBrief.model_validate({**lists, "skills": ["python"] * 13})
    with pytest.raises(ValidationError):
        SearchBrief.model_validate({**lists, "skills": ["x" * 61]})
    with pytest.raises(ValidationError):
        SearchBrief.model_validate({**lists, "notes": "free text"})

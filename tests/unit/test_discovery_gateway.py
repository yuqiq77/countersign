"""Discovery's model calls (ADR-017) against the fake backend: the search
step's caps across continuations, its URL and terms checks, and the brief
and screen with their repairs and fail-open paths."""

from typing import TypeVar

from countersign.contracts.base import ContractModel
from countersign.contracts.discovery import ScreenVerdict, SearchCandidate, SearchCandidates
from countersign.contracts.enums import ScreenDecision
from countersign.gateway.discovery import (
    ListingFacts,
    derive_brief,
    run_search,
    screen_posting,
)
from countersign.gateway.fake_provider import (
    FAKE_BRIEF,
    FakeEvaluationProvider,
    fake_search_response,
)
from countersign.gateway.provider import (
    ParsedResponse,
    ProviderRefusal,
    ProviderUnavailable,
    SearchResponse,
)

T = TypeVar("T", bound=ContractModel)

ASHBY = "https://jobs.ashbyhq.com/acme/1"
LINKEDIN = "https://www.linkedin.com/jobs/view/9"


def _candidate(url: str) -> SearchCandidate:
    return SearchCandidate(
        url=url, title="Engineer", company="Acme", location="Toronto", reason="fits"
    )


def _run(provider: FakeEvaluationProvider, *, max_searches: int = 4, max_requests: int = 3):
    seen: list[tuple[int, SearchResponse]] = []
    run = run_search(
        provider,
        brief=FAKE_BRIEF,
        regions=["Toronto"],
        max_searches=max_searches,
        max_requests=max_requests,
        on_response=lambda index, response: seen.append((index, response)),
    )
    return run, seen


def test_searches_are_counted_across_continuations_and_never_granted_again() -> None:
    paused = fake_search_response(results=[("q1", ASHBY)], searches=2, stop_reason="pause_turn")
    final = fake_search_response(
        SearchCandidates(candidates=[_candidate(ASHBY)]),
        results=[("q2", "https://x.example/")],
        searches=1,
    )
    provider = FakeEvaluationProvider(searches=[paused, final])
    run, seen = _run(provider)
    assert provider.search_uses == [4, 2]
    assert (run.searches, run.model_calls, run.failure) == (3, 2, None)
    assert [index for index, _ in seen] == [1, 2]
    assert [(f.candidate.url, f.query) for f in run.accepted] == [(ASHBY, "q1")]


def test_a_cap_used_up_while_paused_continues_without_searching() -> None:
    paused = fake_search_response(results=[("q1", ASHBY)], searches=4, stop_reason="pause_turn")
    final = fake_search_response(SearchCandidates(candidates=[_candidate(ASHBY)]))
    provider = FakeEvaluationProvider(searches=[paused, final])
    run, _ = _run(provider)
    assert provider.search_uses == [4, 0]
    assert run.searches == 4 and len(run.accepted) == 1


def test_a_request_budget_used_up_while_paused_is_a_failure_outcome() -> None:
    paused = fake_search_response(searches=1, stop_reason="pause_turn")
    provider = FakeEvaluationProvider(searches=[paused, paused])
    run, seen = _run(provider, max_requests=2)
    assert run.failure == "request budget used up while the model was paused"
    assert (run.model_calls, run.searches, len(seen)) == (2, 2, 2)
    assert run.accepted == []


def test_model_level_failures_end_the_search_as_outcomes_and_keep_earlier_queries() -> None:
    paused = fake_search_response(results=[("q1", ASHBY)], searches=1, stop_reason="pause_turn")
    unavailable = FakeEvaluationProvider(searches=[paused, ProviderUnavailable("timeout")])
    run, seen = _run(unavailable)
    # The failed request reported no count: its whole allowance stays charged.
    assert unavailable.search_uses == [4, 3]
    assert (run.failure, run.model_calls, run.searches) == ("timeout", 2, 4)
    assert [r.queries for _, r in seen] == [["q1"]]
    refused, _ = _run(FakeEvaluationProvider(searches=[ProviderRefusal("no")]))
    assert (refused.failure, refused.model_calls, refused.searches) == ("refused", 1, 4)


def test_only_urls_from_the_results_count_and_restricted_hosts_become_leads() -> None:
    final = fake_search_response(
        SearchCandidates(
            candidates=[
                _candidate(ASHBY),
                _candidate("https://invented.example/job"),
                _candidate(LINKEDIN),
            ]
        ),
        results=[("site:ashby", ASHBY), ("linkedin", LINKEDIN)],
        searches=2,
    )
    run, _ = _run(FakeEvaluationProvider(searches=[final]))
    assert [f.candidate.url for f in run.accepted] == [ASHBY]
    assert [(f.candidate.url, f.query) for f in run.leads] == [(LINKEDIN, "linkedin")]
    assert run.rejected == 1


def test_the_brief_is_repaired_once_and_a_second_failure_is_an_outcome() -> None:
    repaired = derive_brief(FakeEvaluationProvider(fail_attempts=1), "profile text")
    assert (repaired.brief, repaired.failure, repaired.model_calls) == (FAKE_BRIEF, None, 2)
    failed = derive_brief(FakeEvaluationProvider(fail_attempts=2), "profile text")
    assert failed.brief is None and failed.model_calls == 2
    assert failed.failure is not None and failed.failure.startswith("contract failure")


FACTS = ListingFacts(
    title="Account Executive",
    company="Acme",
    locations=["Toronto"],
    department="Sales",
    team=None,
    excerpt=None,
)


class _Refusing:
    def parse(
        self, *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
    ) -> ParsedResponse[T]:
        raise ProviderRefusal("declined")


def test_the_screen_passes_on_every_failure_and_says_so() -> None:
    no_profile = screen_posting(FakeEvaluationProvider(), None, FACTS)
    assert (no_profile.decision, no_profile.failed_open, no_profile.model_calls) == (
        ScreenDecision.PASS,
        True,
        0,
    )
    broken = screen_posting(FakeEvaluationProvider(fail_attempts=2), "profile", FACTS)
    assert (broken.decision, broken.failed_open, broken.model_calls) == (
        ScreenDecision.PASS,
        True,
        2,
    )
    refused = screen_posting(_Refusing(), "profile", FACTS)
    assert (refused.failed_open, refused.reason) == (True, "screen failed: refused")


def test_a_drop_is_the_models_verdict_with_its_reason() -> None:
    drop = ScreenVerdict(verdict=ScreenDecision.DROP, reason="a sales role")
    result = screen_posting(FakeEvaluationProvider(verdicts=[drop]), "profile", FACTS)
    assert (result.decision, result.reason, result.failed_open, result.model_calls) == (
        ScreenDecision.DROP,
        "a sales role",
        False,
        1,
    )
    assert result.usage is not None and result.usage.repair_attempts == 0

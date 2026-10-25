"""Discovery's model calls (ADR-017, ticket 005): the brief, the search, the screen.

All three share one route, `COUNTERSIGN_DISCOVERY_MODEL_ID`. The prompts
are drafts the owner edits; their versions stamp every record. The search
sees the brief only: what it may send out through the provider's search.
"""

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from countersign.capabilities.terms import restricted_host
from countersign.contracts.discovery import ScreenVerdict, SearchBrief, SearchCandidate
from countersign.contracts.enums import ScreenDecision
from countersign.contracts.evaluation import ModelUsage
from countersign.domain.discovery import url_in_results
from countersign.domain.watch import BRIEF_MODEL_CALLS, SCREEN_MODEL_CALLS
from countersign.env import optional_env
from countersign.gateway.evaluator import provider_for
from countersign.gateway.provider import (
    ContractFailure,
    ModelProvider,
    ParsedResponse,
    ProviderRefusal,
    ProviderUnavailable,
    SearchProvider,
    SearchResponse,
    StructuredProvider,
)

BRIEF_PROMPT_VERSION = "discovery-brief/0.1.0"
SEARCH_PROMPT_VERSION = "discovery-search/0.1.0"
SCREEN_PROMPT_VERSION = "discovery-screen/0.1.0"
DEFAULT_DISCOVERY_MODEL = "claude-haiku-5-5"

_BRIEF_SYSTEM = """You turn a job seeker's profile into a search brief: the few \
facts a job search needs, and nothing else. Fill each list with short entries, at \
most 60 characters each and at most 12 per list:
- role_families: kinds of roles the profile seeks, as job titles a posting would use;
- skills: skills and tools the profile offers that postings name;
- regions: places where the profile accepts work;
- languages: working languages;
- exclude_roles: kinds of roles the profile rules out.
Use only what the profile states. Leave out names, employers, salary, personal \
circumstances and anything else a search does not need. An empty list is fine."""

_SEARCH_SYSTEM = """You find currently open job postings for a search brief, \
using the web search tool. Prefer the posting pages of applicant tracking systems, \
searched with site-restricted queries such as site:jobs.ashbyhq.com, \
site:job-boards.greenhouse.io or site:jobs.lever.co together with a role and a \
region from the brief, and companies' own careers pages. Return individual \
postings only, never listing, search or aggregator pages, and only URLs that \
appear in your search results; never construct a URL. Leave out roles the brief \
excludes and postings outside the regions given. For each posting give its title \
and company as the posting states them, its location text, and one sentence on \
why it matches the brief. Return at most 20 postings; an empty list is a valid \
answer."""

_SCREEN_SYSTEM = """You screen job postings for one job seeker before a full \
evaluation. Decide one thing: whether the role is the kind of work the profile \
below seeks. Do not judge seniority, feasibility, salary or fit; a later \
evaluation does that. Answer DROP only when the posting is clearly a different \
kind of work: another profession or function, or a role the profile rules out. \
When in doubt, answer PASS. Give a one-sentence reason."""


def discovery_model_id() -> str:
    return optional_env("COUNTERSIGN_DISCOVERY_MODEL_ID") or DEFAULT_DISCOVERY_MODEL


def discovery_route() -> str:
    """The route stamped on discovery records: provider and model."""

    if os.environ.get("COUNTERSIGN_MODEL_PROVIDER", "fake").lower() != "anthropic":
        return "fake"
    return f"anthropic:{discovery_model_id()}"


def resolve_discovery_provider() -> ModelProvider:
    """The discovery route's backend; fake unless the provider is anthropic."""

    provider_name = os.environ.get("COUNTERSIGN_MODEL_PROVIDER", "fake").lower()
    if provider_name != "anthropic":
        return provider_for(provider_name, None, None)
    return provider_for(provider_name, discovery_model_id(), optional_env("ANTHROPIC_BASE_URL"))


def _usage(
    response: ParsedResponse[ScreenVerdict] | ParsedResponse[SearchBrief],
    *,
    started: float,
    repairs: int,
) -> ModelUsage:
    return ModelUsage(
        model_id=response.model_id,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cache_read_input_tokens=response.cache_read_input_tokens,
        cache_creation_input_tokens=response.cache_creation_input_tokens,
        latency_ms=int((time.monotonic() - started) * 1000),
        repair_attempts=repairs,
        provider_request_id=response.request_id,
    )


@dataclass(frozen=True)
class BriefResult:
    brief: SearchBrief | None
    failure: str | None
    model_calls: int
    usage: ModelUsage | None = None


def derive_brief(provider: StructuredProvider, profile: str) -> BriefResult:
    """The brief for one profile: one call and, on a contract failure, one repair.

    Every model-level failure is an outcome, never an exception (ticket 005).
    """

    started = time.monotonic()
    user = f"Profile:\n{profile}"
    last = "no attempt"
    for attempt in range(BRIEF_MODEL_CALLS):
        prompt = user if attempt == 0 else f"{user}\n\nYour previous output failed: {last}"
        try:
            response = provider.parse(system=_BRIEF_SYSTEM, user=prompt, output_type=SearchBrief)
        except ContractFailure as exc:
            last = f"contract failure: {exc}"[:300]
            continue
        except ProviderRefusal:
            return BriefResult(None, "refused", attempt + 1)
        except ProviderUnavailable as exc:
            return BriefResult(None, f"provider unavailable: {exc}"[:300], attempt + 1)
        return BriefResult(
            response.parsed, None, attempt + 1, _usage(response, started=started, repairs=attempt)
        )
    return BriefResult(None, last, BRIEF_MODEL_CALLS)


@dataclass(frozen=True)
class ListingFacts:
    """What the screen reads of one candidate."""

    title: str
    company: str | None
    locations: list[str]
    department: str | None
    team: str | None
    excerpt: str | None


@dataclass(frozen=True)
class ScreenResult:
    decision: ScreenDecision
    reason: str
    failed_open: bool
    model_calls: int
    usage: ModelUsage | None = None


def _screen_user(facts: ListingFacts) -> str:
    lines = [f"Title: {facts.title}"]
    for label, value in (
        ("Company", facts.company),
        ("Location", "; ".join(facts.locations) or None),
        ("Department", facts.department),
        ("Team", facts.team),
    ):
        if value:
            lines.append(f"{label}: {value}")
    if facts.excerpt:
        lines.append(f"Description (start):\n{facts.excerpt}")
    return "Posting:\n" + "\n".join(lines)


def screen_posting(
    provider: StructuredProvider, profile: str | None, facts: ListingFacts
) -> ScreenResult:
    """The coarse screen for one candidate; every failure passes it (ADR-017)."""

    if profile is None:
        return ScreenResult(ScreenDecision.PASS, "screen failed: no operator profile", True, 0)
    started = time.monotonic()
    system = f"{_SCREEN_SYSTEM}\n\nProfile:\n{profile}"
    user = _screen_user(facts)
    last = "no attempt"
    for attempt in range(SCREEN_MODEL_CALLS):
        prompt = user if attempt == 0 else f"{user}\n\nYour previous output failed: {last}"
        try:
            response = provider.parse(system=system, user=prompt, output_type=ScreenVerdict)
        except ContractFailure as exc:
            last = f"contract failure: {exc}"[:200]
            continue
        except ProviderRefusal:
            return ScreenResult(ScreenDecision.PASS, "screen failed: refused", True, attempt + 1)
        except ProviderUnavailable as exc:
            reason = f"screen failed: provider unavailable: {exc}"[:300]
            return ScreenResult(ScreenDecision.PASS, reason, True, attempt + 1)
        verdict = response.parsed
        return ScreenResult(
            verdict.verdict,
            verdict.reason,
            False,
            attempt + 1,
            _usage(response, started=started, repairs=attempt),
        )
    return ScreenResult(
        ScreenDecision.PASS, f"screen failed: {last}"[:300], True, SCREEN_MODEL_CALLS
    )


@dataclass(frozen=True)
class Found:
    """A search candidate and the query whose results held it."""

    candidate: SearchCandidate
    query: str


@dataclass(frozen=True)
class SearchRun:
    accepted: list[Found]
    leads: list[Found]
    rejected: int
    searches: int
    model_calls: int
    failure: str | None


def search_prompt(brief: SearchBrief, regions: list[str]) -> str:
    payload = brief.model_dump(exclude={"regions"})
    return (
        f"Search brief:\n{json.dumps(payload, ensure_ascii=False)}\n"
        f"Search only in these regions: {', '.join(regions)}"
    )


def run_search(
    provider: SearchProvider,
    *,
    brief: SearchBrief,
    regions: list[str],
    max_searches: int,
    max_requests: int,
    on_response: Callable[[int, SearchResponse], None],
) -> SearchRun:
    """The search step: requests and continuations within both caps.

    Searches are counted across every request from each response's usage,
    and each request may use only what remains, so a continuation never
    gets a fresh allowance. A request that fails without a count is charged
    its whole allowance, since the provider may have searched before the
    failure. Each response is handed to `on_response` before anything else
    happens, so its queries are stored even if a later request fails.
    Model-level failures end the step as an outcome.
    """

    user = search_prompt(brief, regions)
    messages: list[dict[str, object]] = [{"role": "user", "content": user}]
    searches = calls = 0
    results: list[tuple[str, str]] = []
    final: list[SearchCandidate] | None = None
    failure: str | None = None
    while calls < max_requests:
        allowance = max_searches - searches
        try:
            response = provider.search(system=_SEARCH_SYSTEM, messages=messages, max_uses=allowance)
        except ProviderRefusal:
            calls += 1
            searches += allowance
            failure = "refused"
            break
        except (ContractFailure, ProviderUnavailable) as exc:
            calls += 1
            searches += allowance
            failure = str(exc)[:300]
            break
        calls += 1
        searches += response.searches
        on_response(calls, response)
        results.extend(response.results)
        if response.stop_reason == "pause_turn":
            # Each paused turn goes back as it came; the API joins
            # consecutive assistant messages and resumes after the last.
            messages.append({"role": "assistant", "content": response.content})
            continue
        if response.parsed is None:
            failure = f"no contract-valid output (stop_reason={response.stop_reason})"
            break
        final = response.parsed.candidates
        break
    if final is None and failure is None:
        failure = "request budget used up while the model was paused"

    accepted: list[Found] = []
    leads: list[Found] = []
    rejected = 0
    query_of = {url: query for query, url in results}
    result_urls = [url for _, url in results]
    for candidate in final or []:
        if not url_in_results(candidate.url, result_urls):
            rejected += 1
            continue
        found = Found(candidate, _query_for(candidate.url, query_of))
        (leads if restricted_host(candidate.url) is not None else accepted).append(found)
    return SearchRun(accepted, leads, rejected, searches, calls, failure)


def _query_for(url: str, query_of: dict[str, str]) -> str:
    if url in query_of:
        return query_of[url]
    for result_url, query in query_of.items():
        if url_in_results(url, [result_url]):
            return query
    return ""
